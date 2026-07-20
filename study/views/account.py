"""Authentication, account recovery, and settings views."""

from __future__ import annotations


from django.contrib.auth import (
    get_user_model,
    login as auth_login,
    logout as auth_logout,
    update_session_auth_hash,
)
from django.db import transaction
from django.db.utils import IntegrityError
from django.db.models import Prefetch
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_POST

from ..accounts import (
    authenticate_with_throttle,
    generate_recovery_codes,
    login_throttle_key,
    provision_user_study_data,
    reset_pin_with_recovery,
    reserve_throttled_action,
)
from ..forms import (
    ChangePinForm,
    CurrentPinForm,
    DeleteAccountForm,
    RecoveryForm,
    RegistrationForm,
    ResetProgressForm,
    UsernamePinForm,
)
from ..models import (
    Annotation,
    Card,
    CardState,
    ComprehensionAnswer,
    ComprehensionAttempt,
    ComprehensionAttemptStatus,
    MemoryQuestionProgress,
    PersonalResponse,
    ReviewLog,
    ReviewSession,
    Settings,
)

from .review import (
    _locked_review_session,
    _save_review_session,
)

def _auth_redirect(request):
    candidate = request.POST.get("next") or request.GET.get("next")
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return reverse("study:dashboard")


def _auth_next_value(request):
    return request.POST.get("next") or request.GET.get("next", "")


@never_cache
@sensitive_post_parameters("pin")
def login_view(request):
    if request.user.is_authenticated:
        return redirect(_auth_redirect(request))
    form = UsernamePinForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        username = form.cleaned_data["username"]
        pin = form.cleaned_data["pin"]
        user, _ = authenticate_with_throttle(request, username, pin)
        if user is None:
            form.add_error(
                None,
                "Connexion impossible. Vérifiez vos identifiants ou réessayez plus tard.",
            )
        else:
            provision_user_study_data(user)
            auth_login(request, user)
            return redirect(_auth_redirect(request))
    return render(
        request,
        "study/auth/login.html",
        {"form": form, "next": _auth_next_value(request)},
    )


@never_cache
@sensitive_post_parameters("pin", "pin_confirm")
def register_view(request):
    if request.user.is_authenticated:
        return redirect(_auth_redirect(request))
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        throttle_key = login_throttle_key(
            request,
            "",
            purpose="registration",
        )
        if reserve_throttled_action(throttle_key):
            form.add_error(
                None,
                "Création temporairement indisponible. Réessayez plus tard.",
            )
        else:
            try:
                with transaction.atomic():
                    user = get_user_model().objects.create_user(
                        username=form.cleaned_data["username"],
                        password=form.cleaned_data["pin"],
                    )
                    provision_user_study_data(user)
                    recovery_codes = generate_recovery_codes(user)
            except IntegrityError:
                form.add_error(
                    "username",
                    "Ce nom d'utilisateur est déjà utilisé.",
                )
            else:
                auth_login(
                    request,
                    user,
                    backend="django.contrib.auth.backends.ModelBackend",
                )
                request.session["new_recovery_codes"] = recovery_codes
                request.session["post_recovery_redirect"] = _auth_redirect(
                    request
                )
                return redirect("study:recovery_codes")
    return render(
        request,
        "study/auth/register.html",
        {"form": form, "next": _auth_next_value(request)},
    )


@never_cache
@sensitive_post_parameters(
    "recovery_code",
    "new_pin",
    "new_pin_confirm",
)
def recover_account(request):
    if request.user.is_authenticated:
        return redirect(_auth_redirect(request))
    form = RecoveryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user, recovery_codes, _ = reset_pin_with_recovery(
            request,
            form.cleaned_data["username"],
            form.cleaned_data["recovery_code"],
            form.cleaned_data["new_pin"],
        )
        if user is None:
            form.add_error(
                None,
                "Récupération impossible. Vérifiez les informations ou "
                "réessayez plus tard.",
            )
        else:
            auth_login(
                request,
                user,
                backend="django.contrib.auth.backends.ModelBackend",
            )
            request.session["new_recovery_codes"] = recovery_codes
            request.session["post_recovery_redirect"] = _auth_redirect(request)
            return redirect("study:recovery_codes")
    return render(
        request,
        "study/auth/recover.html",
        {"form": form, "next": _auth_next_value(request)},
    )


@never_cache
def recovery_codes_view(request):
    codes = request.session.pop("new_recovery_codes", [])
    next_url = request.session.pop(
        "post_recovery_redirect",
        reverse("study:dashboard"),
    )
    return render(
        request,
        "study/auth/recovery_codes.html",
        {"recovery_codes": codes, "next_url": next_url},
    )


@require_POST
def logout_view(request):
    auth_logout(request)
    return redirect("study:login")


def _settings_context(request, **overrides):
    context = {
        "was_reset": request.GET.get("reset") == "1",
        "was_unsuspended": request.GET.get("unsuspended") == "1",
        "pin_changed": request.GET.get("pin_changed") == "1",
        "suspended_count": Card.objects.filter(
            user=request.user,
            suspended=True,
        ).count(),
        "change_pin_form": ChangePinForm(
            request.user,
            auto_id="id_change_pin_%s",
        ),
        "recovery_codes_form": CurrentPinForm(
            request.user,
            auto_id="id_recovery_codes_%s",
        ),
        "reset_form": ResetProgressForm(
            request.user,
            auto_id="id_reset_progress_%s",
        ),
        "delete_account_form": DeleteAccountForm(
            request.user,
            auto_id="id_delete_account_%s",
        ),
    }
    context.update(overrides)
    return context


def _render_settings(request, *, status=200, **overrides):
    return render(
        request,
        "study/settings.html",
        _settings_context(request, **overrides),
        status=status,
    )


def settings_view(request):
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "unsuspend_all":
            Card.objects.filter(
                user=request.user,
                suspended=True,
            ).update(suspended=False)
            return redirect(reverse("study:settings") + "?unsuspended=1")
        return HttpResponseBadRequest("Invalid settings action.")
    return _render_settings(request)


@require_POST
@sensitive_post_parameters("current_pin", "new_pin", "new_pin_confirm")
def change_pin(request):
    form = ChangePinForm(
        request.user,
        request.POST,
        auto_id="id_change_pin_%s",
    )
    if not form.is_valid():
        return _render_settings(
            request,
            status=400,
            change_pin_form=form,
        )
    request.user.set_password(form.cleaned_data["new_pin"])
    request.user.save(update_fields=["password"])
    update_session_auth_hash(request, request.user)
    return redirect(reverse("study:settings") + "?pin_changed=1")


@require_POST
@sensitive_post_parameters("current_pin")
def regenerate_recovery_codes(request):
    form = CurrentPinForm(
        request.user,
        request.POST,
        auto_id="id_recovery_codes_%s",
    )
    if not form.is_valid():
        return _render_settings(
            request,
            status=400,
            recovery_codes_form=form,
        )
    request.session["new_recovery_codes"] = generate_recovery_codes(
        request.user
    )
    request.session["post_recovery_redirect"] = reverse("study:settings")
    return redirect("study:recovery_codes")


@require_POST
@sensitive_post_parameters("current_pin")
def reset_progress(request):
    form = ResetProgressForm(
        request.user,
        request.POST,
        auto_id="id_reset_progress_%s",
    )
    if not form.is_valid():
        return _render_settings(
            request,
            status=400,
            reset_form=form,
        )
    with transaction.atomic():
        session = _locked_review_session(request.user)
        Card.objects.filter(user=request.user).update(
            state=CardState.NEW,
            due=None,
            interval_days=0.0,
            ease=2.5,
            reps=0,
            lapses=0,
            learning_step=0,
            last_reviewed=None,
            last_rating=None,
            needs_revisit=False,
            revisit_added_at=None,
            suspended=False,
            started_at=None,
            response_practice_started_at=None,
        )
        ReviewLog.objects.filter(user=request.user).delete()
        ComprehensionAttempt.objects.filter(user=request.user).delete()
        MemoryQuestionProgress.objects.filter(user=request.user).delete()
        _save_review_session(session, {}, clear_pass=True)
    return redirect(reverse("study:settings") + "?reset=1")


@require_GET
def export_account(request):
    cards = []
    for card in (
        Card.objects.filter(user=request.user)
        .select_related("response", "phrase")
        .order_by("pk")
    ):
        cards.append(
            {
                "id": card.pk,
                "card_type": card.card_type,
                "response_key": (
                    card.response.content_key if card.response_id else None
                ),
                "phrase_id": (
                    card.phrase.phrase_id if card.phrase_id else None
                ),
                "state": card.state,
                "due": card.due,
                "interval_days": card.interval_days,
                "ease": card.ease,
                "reps": card.reps,
                "lapses": card.lapses,
                "learning_step": card.learning_step,
                "last_reviewed": card.last_reviewed,
                "last_rating": card.last_rating,
                "needs_revisit": card.needs_revisit,
                "revisit_added_at": card.revisit_added_at,
                "suspended": card.suspended,
                "started_at": card.started_at,
                "response_practice_started_at": (
                    card.response_practice_started_at
                ),
                "created_at": card.created_at,
            }
        )
    review_logs = list(
        ReviewLog.objects.filter(user=request.user)
        .order_by("reviewed_at", "pk")
        .values(
            "card_id",
            "reviewed_at",
            "rating",
            "state_before",
            "state_after",
            "interval_before",
            "interval_after",
            "ease_before",
            "ease_after",
            "elapsed_ms",
            "card_before",
        )
    )
    annotations = list(
        Annotation.objects.filter(user=request.user)
        .order_by("created_at", "pk")
        .values(
            "id",
            "task_id",
            "kind",
            "title",
            "body",
            "quote",
            "source_path",
            "source_key",
            "source_title",
            "start_offset",
            "end_offset",
            "prefix",
            "suffix",
            "study_later",
            "created_at",
            "updated_at",
        )
    )
    personal_responses = [
        {
            "response_key": personal.response.content_key,
            "reformulation": personal.reformulation,
            "position": personal.position,
            "position_claire": personal.position_claire,
            "arguments": personal.arguments,
            "nuance": personal.nuance,
            "conclusion": personal.conclusion,
            "created_at": personal.created_at,
            "updated_at": personal.updated_at,
        }
        for personal in PersonalResponse.objects.filter(
            user=request.user
        ).select_related("response")
    ]
    comprehension_attempts = []
    for attempt in (
        ComprehensionAttempt.objects.filter(user=request.user)
        .select_related("test")
        .prefetch_related(
            Prefetch(
                "answers",
                queryset=ComprehensionAnswer.objects.select_related(
                    "question",
                    "selected_choice",
                ),
            )
        )
    ):
        comprehension_attempts.append(
            {
                "test": attempt.test.slug,
                "status": attempt.status,
                "current_question": attempt.current_question,
                "score": attempt.score,
                "total_questions": attempt.total_questions,
                "content_snapshot": (
                    attempt.content_snapshot
                    if attempt.status == ComprehensionAttemptStatus.COMPLETED
                    else {}
                ),
                "started_at": attempt.started_at,
                "updated_at": attempt.updated_at,
                "completed_at": attempt.completed_at,
                "answers": [
                    {
                        "question_key": answer.question.content_key,
                        "selected_choice": answer.selected_choice.letter,
                        "is_correct": answer.is_correct,
                        "question_snapshot": answer.question_snapshot,
                        "submitted_at": answer.submitted_at,
                    }
                    for answer in attempt.answers.all()
                ],
            }
        )
    session = ReviewSession.load(request.user)
    settings = Settings.load(request.user)
    payload = {
        "format": "heureux-account-export",
        "version": 3,
        "exported_at": timezone.now(),
        "account": {
            "username": request.user.get_username(),
            "date_joined": request.user.date_joined,
        },
        "settings": {
            "new_cards_per_day": settings.new_cards_per_day,
            "max_reviews_per_day": settings.max_reviews_per_day,
        },
        "review_session": {
            "current_card_id": session.current_card_id,
            "previous_card_id": session.previous_card_id,
            "previous_review_id": session.previous_review_id,
            "scope": session.scope,
            "revisit_seen_card_ids": session.revisit_seen_card_ids,
            "updated_at": session.updated_at,
        },
        "cards": cards,
        "review_logs": review_logs,
        "annotations": annotations,
        "personal_responses": personal_responses,
        "comprehension_attempts": comprehension_attempts,
        "memory_question_progress": [
            {
                "memory_number": item.memory_number,
                "question_key": item.question_key,
                "completed_at": item.completed_at,
            }
            for item in MemoryQuestionProgress.objects.filter(
                user=request.user
            ).order_by("memory_number", "completed_at", "pk")
        ],
    }
    response = JsonResponse(
        payload,
        json_dumps_params={"ensure_ascii": False, "indent": 2},
    )
    response["Content-Disposition"] = (
        f'attachment; filename="heureux-{request.user.get_username()}.json"'
    )
    return response


@require_POST
@sensitive_post_parameters("current_pin")
def delete_account(request):
    form = DeleteAccountForm(
        request.user,
        request.POST,
        auto_id="id_delete_account_%s",
    )
    if not form.is_valid():
        return _render_settings(
            request,
            status=400,
            delete_account_form=form,
        )
    user = request.user
    with transaction.atomic():
        user.delete()
    auth_logout(request)
    return redirect(reverse("study:login") + "?deleted=1")
