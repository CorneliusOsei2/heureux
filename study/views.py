"""Views for the flashcards app."""

from __future__ import annotations

import json
import secrets
from urllib.parse import urlencode

from django.contrib.auth import (
    get_user_model,
    login as auth_login,
    logout as auth_logout,
)
from django.db import transaction
from django.db.utils import IntegrityError
from django.db.models import Count, Q
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from . import queue as queue_module
from .accounts import (
    authenticate_with_throttle,
    login_throttle_key,
    provision_user_study_data,
    reserve_throttled_action,
)
from .cards import card_payload, scope_from_request, scope_label
from .forms import RegistrationForm, UsernamePinForm
from .models import (
    Card,
    CardState,
    CardType,
    ExamPart,
    Family,
    Phrase,
    PhraseCategory,
    Prompt,
    Rating,
    Response,
    ReviewLog,
    ReviewSession,
    Task,
    Theme,
)
from .srs import review as apply_review, undo_last

MATURE_DAYS = 21
REVIEW_SCOPE_KEYS = (
    "kind",
    "part",
    "task",
    "theme",
    "family",
    "category",
    "response",
    "batch",
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
        {"form": form, "next": request.GET.get("next", "")},
    )


def register_view(request):
    if request.user.is_authenticated:
        return redirect("study:dashboard")
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
                return redirect("study:dashboard")
    return render(
        request,
        "study/auth/register.html",
        {"form": form},
    )


@require_POST
def logout_view(request):
    auth_logout(request)
    return redirect("study:login")


def deck_stats(qs, now=None) -> dict:
    now = now or timezone.now()
    total = qs.count()
    new = qs.filter(state=CardState.NEW).count()
    learning = qs.filter(
        state__in=[CardState.LEARNING, CardState.RELEARNING]
    ).count()
    review = qs.filter(state=CardState.REVIEW).count()
    mature = qs.filter(
        state=CardState.REVIEW, interval_days__gte=MATURE_DAYS
    ).count()
    due = qs.filter(
        state__in=[CardState.LEARNING, CardState.RELEARNING, CardState.REVIEW],
        due__lte=now,
    ).count()
    return {
        "total": total,
        "new": new,
        "learning": learning,
        "review": review,
        "mature": mature,
        "review_young": review - mature,
        "due": due,
        "seen": total - new,
        "pct": round(100 * (total - new) / total) if total else 0,
        "mature_pct": round(100 * mature / total) if total else 0,
    }


def _review_batches(scope: dict, user) -> list[dict]:
    """Describe stable 15-card lots and each lot's first-pass progress."""
    base_scope = {key: value for key, value in scope.items() if key != "batch"}
    rows = list(
        queue_module.scoped_cards(
            base_scope,
            user=user,
            include_suspended=True,
        )
        .order_by(*queue_module.batch_ordering(base_scope))
        .values("id", "state", "due", "suspended")
    )
    unique_rows = []
    seen_ids = set()
    for row in rows:
        if row["id"] not in seen_ids:
            seen_ids.add(row["id"])
            unique_rows.append(row)

    now = timezone.now()
    batches = []
    for number, start in enumerate(
        range(0, len(unique_rows), queue_module.BATCH_SIZE),
        start=1,
    ):
        rows_in_batch = unique_rows[start : start + queue_module.BATCH_SIZE]
        active_rows = [row for row in rows_in_batch if not row["suspended"]]
        seen_count = sum(
            row["state"] != CardState.NEW for row in active_rows
        )
        available_now = sum(
            row["state"] == CardState.NEW
            or (
                row["due"] is not None
                and row["due"] <= now
                and row["state"]
                in {
                    CardState.LEARNING,
                    CardState.RELEARNING,
                    CardState.REVIEW,
                }
            )
            for row in active_rows
        )
        if not active_rows:
            status = "unavailable"
            status_label = "Suspendu"
        elif seen_count == len(active_rows):
            status = "complete"
            status_label = "Terminé"
        elif seen_count:
            status = "in-progress"
            status_label = "En cours"
        else:
            status = "not-started"
            status_label = "À commencer"
        end = start + len(rows_in_batch)
        batch_scope = {**base_scope, "batch": str(number)}
        batches.append(
            {
                "number": number,
                "start": start + 1,
                "end": end,
                "card_count": len(rows_in_batch),
                "active_count": len(active_rows),
                "seen_count": seen_count,
                "available_now": available_now,
                "status": status,
                "status_label": status_label,
                "can_review": available_now > 0,
                "review_url": (
                    reverse("study:review") + "?" + urlencode(batch_scope)
                ),
            }
        )
    return batches


def _batch_index_url(scope: dict) -> str | None:
    """Return the category/theme page that owns a batch scope."""
    if scope.get("category"):
        if scope.get("part") and scope.get("task"):
            base = reverse(
                "study:task_phrases",
                args=[scope["part"], scope["task"]],
            )
        else:
            base = reverse("study:phrases")
        return base + "?" + urlencode({"category": scope["category"]})
    if scope.get("theme"):
        return reverse("study:theme_detail", args=[scope["theme"]])
    return None


def current_streak(now=None, logs=None, user=None) -> int:
    """Consecutive days (up to today) with at least one review."""
    now = now or timezone.now()
    logs = ReviewLog.objects.filter(user=user) if logs is None else logs
    days = {
        timezone.localtime(dt).date()
        for dt in logs.values_list("reviewed_at", flat=True)
    }
    if not days:
        return 0
    today = timezone.localtime(now).date()
    cursor = today
    if cursor not in days:
        cursor = today - timezone.timedelta(days=1)
        if cursor not in days:
            return 0
    streak = 0
    while cursor in days:
        streak += 1
        cursor = cursor - timezone.timedelta(days=1)
    return streak


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


def _spine_theme_stats(theme, now, user):
    return deck_stats(
        Card.objects.active().filter(
            user=user,
            card_type=CardType.SPINE, response__theme=theme
        ),
        now,
    )


def _task_scope(task) -> dict:
    return {"part": task.part.slug, "task": task.slug}


def _task_cards(task, user=None, kind=None):
    scope = _task_scope(task)
    if kind:
        scope["kind"] = kind
    return queue_module.scoped_cards(scope, user=user)


def _task_phrases(task):
    return Phrase.objects.filter(
        source_prompts__theme__task=task
    ).distinct()


def _route_task(part_slug, task_slug):
    return get_object_or_404(
        Task.objects.select_related("part"),
        slug=task_slug,
        part__slug=part_slug,
    )


def _task_card(task, now, user):
    """Build a dashboard/part card for a single task."""
    if task.available:
        response_stats = deck_stats(_task_cards(task, user, "spine"), now)
        phrase_stats = deck_stats(_task_cards(task, user, "phrase"), now)
        stats = deck_stats(_task_cards(task, user), now)
        counts = queue_module.queue_counts(
            _task_scope(task),
            now,
            user=user,
        )
        phrase_counts = queue_module.queue_counts(
            {**_task_scope(task), "kind": "phrase"},
            now,
            user=user,
        )
        revisit_count = _task_cards(task, user).filter(
            needs_revisit=True
        ).count()
        theme_count = Theme.objects.filter(task=task).count()
        prompt_count = Prompt.objects.filter(theme__task=task).count()
        phrase_count = _task_phrases(task).count()
    else:
        response_stats = None
        phrase_stats = None
        stats = None
        counts = None
        phrase_counts = None
        revisit_count = 0
        theme_count = 0
        prompt_count = 0
        phrase_count = 0
    return {
        "task": task,
        "stats": stats,
        "response_stats": response_stats,
        "phrase_stats": phrase_stats,
        "counts": counts,
        "phrase_counts": phrase_counts,
        "revisit_count": revisit_count,
        "theme_count": theme_count,
        "prompt_count": prompt_count,
        "phrase_count": phrase_count,
    }


def _parts_with_task_cards(now, user):
    return [
        {
            "part": part,
            "tasks": [
                _task_card(task, now, user)
                for task in part.tasks.all()
            ],
        }
        for part in ExamPart.objects.prefetch_related("tasks")
    ]


def _phrase_deck_stats(now, user=None, task=None):
    cards = (
        _task_cards(task, user, "phrase")
        if task
        else Card.objects.active().filter(
            user=user,
            card_type__in=[
                CardType.PHRASE_PRODUCTION,
                CardType.PHRASE_RECOGNITION,
            ]
        )
    )
    return deck_stats(cards, now)


def dashboard(request):
    now = timezone.now()
    counts = queue_module.queue_counts(now=now, user=request.user)
    user_cards = Card.objects.active().filter(user=request.user)
    overall = deck_stats(user_cards, now)

    context = {
        "counts": counts,
        "parts": _parts_with_task_cards(now, request.user),
        "overall": overall,
        "streak": current_streak(now, user=request.user),
    }
    return render(request, "study/dashboard.html", context)


def _grouped_overview(request, area):
    now = timezone.now()
    user_cards = Card.objects.active().filter(user=request.user)
    context = {
        "area": area,
        "parts": _parts_with_task_cards(now, request.user),
        "overall": deck_stats(user_cards, now),
        "streak": current_streak(now, user=request.user),
    }
    if area == "review":
        session = ReviewSession.load(request.user)
        context.update(
            {
                "title": "Réviser",
                "eyebrow": "Mémoire active",
                "description": (
                    "Choisissez d'abord votre épreuve et votre tâche, "
                    "puis le type de cartes à travailler."
                ),
                "counts": queue_module.queue_counts(
                    now=now,
                    user=request.user,
                ),
                "revisit_count": user_cards.filter(
                    needs_revisit=True
                ).count(),
                "can_resume": bool(session.current_card_id),
            }
        )
    elif area == "expressions":
        context.update(
            {
                "title": "Expressions",
                "eyebrow": "Précision lexicale",
                "description": (
                    "Choisissez une tâche pour retrouver ses expressions, "
                    "son vocabulaire et ses nuances."
                ),
                "phrase_count": Phrase.objects.count(),
                "phrase_stats": _phrase_deck_stats(now, request.user),
                "phrase_counts": queue_module.queue_counts(
                    {"kind": "phrase"},
                    now,
                    user=request.user,
                ),
            }
        )
    else:
        context.update(
            {
                "title": "Stats",
                "eyebrow": "Progression",
                "description": (
                    "Choisissez une tâche pour consulter sa maîtrise, "
                    "son activité et ses prochaines révisions."
                ),
            }
        )
    return render(request, "study/grouped_overview.html", context)


@require_GET
def review_overview(request):
    return _grouped_overview(request, "review")


@require_GET
def expressions_overview(request):
    return _grouped_overview(request, "expressions")


@require_GET
def stats_overview(request):
    return _grouped_overview(request, "stats")


def part_detail(request, part_slug):
    part = get_object_or_404(ExamPart.objects.prefetch_related("tasks"), slug=part_slug)
    now = timezone.now()
    tasks = [
        _task_card(task, now, request.user)
        for task in part.tasks.all()
    ]
    if not part.available or not tasks:
        return render(
            request,
            "study/coming_soon.html",
            {"part": part, "task": None},
        )
    return render(
        request,
        "study/part_detail.html",
        {"part": part, "tasks": tasks},
    )


def task_detail(request, part_slug, task_slug):
    task = get_object_or_404(
        Task.objects.select_related("part"),
        slug=task_slug,
        part__slug=part_slug,
    )
    now = timezone.now()
    if not task.available:
        return render(
            request,
            "study/coming_soon.html",
            {"part": task.part, "task": task},
        )

    themes = []
    for theme in Theme.objects.filter(task=task):
        themes.append(
            {
                "theme": theme,
                "stats": _spine_theme_stats(theme, now, request.user),
                "prompt_count": Prompt.objects.filter(theme=theme).count(),
            }
        )
    scope = _task_scope(task)
    task_stats = deck_stats(_task_cards(task, request.user), now)
    response_stats = deck_stats(
        _task_cards(task, request.user, "spine"),
        now,
    )
    phrase_stats = _phrase_deck_stats(now, request.user, task)
    context = {
        "part": task.part,
        "task": task,
        "themes": themes,
        "stats": task_stats,
        "response_stats": response_stats,
        "phrase_stats": phrase_stats,
        "counts": queue_module.queue_counts(
            scope,
            now,
            user=request.user,
        ),
        "prompt_count": Prompt.objects.filter(theme__task=task).count(),
        "phrase_count": _task_phrases(task).count(),
        "phrase_category_count": _task_phrases(task)
        .values("category_id")
        .distinct()
        .count(),
    }
    return render(request, "study/task_detail.html", context)


# ---------------------------------------------------------------------------
# Review session
# ---------------------------------------------------------------------------


def _locked_review_session(user) -> ReviewSession:
    session, _ = ReviewSession.objects.select_for_update().get_or_create(
        user=user
    )
    return session


def _resolved_review_scope(
    request,
    session: ReviewSession,
) -> tuple[dict, bool]:
    """Use an explicit query scope, otherwise resume the saved one."""
    if request.GET.get("reset") == "1":
        return {}, True
    scope = scope_from_request(request)
    explicit = any(key in request.GET for key in REVIEW_SCOPE_KEYS)
    if explicit:
        return scope, True
    saved = session.scope
    return (saved if isinstance(saved, dict) else {}), False


def _save_review_session(
    session: ReviewSession,
    scope: dict,
    card=None,
    *,
    clear_pass=False,
    rotate_token=False,
) -> str:
    same_revisit_pass = (
        session.scope == scope and scope.get("kind") == "revisit"
    )
    same_presentation = (
        card is not None
        and session.scope == scope
        and session.current_card_id == card.id
        and session.presentation_token
    )
    if clear_pass or not same_revisit_pass:
        session.revisit_seen_card_ids = []
    session.scope = scope
    session.current_card = card
    if card is None:
        session.presentation_token = ""
    elif rotate_token or not same_presentation:
        session.presentation_token = secrets.token_urlsafe(24)
    session.save(
        update_fields=[
            "scope",
            "current_card",
            "revisit_seen_card_ids",
            "presentation_token",
            "updated_at",
        ]
    )
    return session.presentation_token


def review(request):
    with transaction.atomic():
        session = _locked_review_session(request.user)
        scope, explicit = _resolved_review_scope(request, session)
        if explicit and (
            session.scope != scope or request.GET.get("reset") == "1"
        ):
            _save_review_session(session, scope, clear_pass=True)
    counts = queue_module.queue_counts(scope, user=request.user)
    next_batch = None
    batch_index_url = None
    if scope.get("batch"):
        try:
            current_batch = int(scope["batch"])
        except (TypeError, ValueError):
            current_batch = 0
        next_batch = next(
            (
                batch
                for batch in _review_batches(scope, request.user)
                if batch["number"] > current_batch and batch["can_review"]
            ),
            None,
        )
        batch_index_url = _batch_index_url(scope)
    context = {
        "scope": scope,
        "scope_json": json.dumps(scope),
        "scope_label": scope_label(scope),
        "counts": counts,
        "is_revisit": scope.get("kind") == "revisit",
        "next_batch": next_batch,
        "batch_index_url": batch_index_url,
    }
    return render(request, "study/review.html", context)


def _queue_state_locked(
    scope: dict,
    request,
    session: ReviewSession,
) -> dict:
    now = timezone.now()
    card = None
    if session.scope == scope:
        card = queue_module.resumable_card(
            session.current_card_id,
            scope,
            now,
            user=request.user,
        )
    seen_card_ids = (
        session.revisit_seen_card_ids
        if session.scope == scope and scope.get("kind") == "revisit"
        else []
    )
    if card is None:
        card = queue_module.next_card(
            scope,
            now,
            exclude_card_ids=seen_card_ids,
            user=request.user,
        )
    counts = queue_module.queue_counts(scope, now, user=request.user)
    if card is None:
        # A finished scoped deck is no longer something to resume on next launch.
        _save_review_session(session, {}, clear_pass=True)
        if scope.get("kind") == "revisit" and seen_card_ids:
            counts["due_reviews"] = 0
            counts["review_due"] = 0
            counts["total_due"] = 0
        return {
            "done": True,
            "counts": counts,
            "revisit_count": counts["revisit_total"],
        }

    presentation_token = _save_review_session(session, scope, card)
    payload = card_payload(card)
    front = render_to_string("study/partials/card_front.html", payload, request)
    back = render_to_string("study/partials/card_back.html", payload, request)
    return {
        "done": False,
        "card_id": card.id,
        "card_type": card.card_type,
        "state": card.state,
        "state_label": card.get_state_display(),
        "is_new": card.is_new,
        "front_html": front,
        "back_html": back,
        "presentation_token": presentation_token,
        "counts": counts,
        "revisit_count": counts["revisit_total"],
    }


def _card_state_locked(
    card,
    scope: dict,
    request,
    session: ReviewSession,
) -> dict:
    """Build the review payload for a specific card (used after an undo)."""
    now = timezone.now()
    counts = queue_module.queue_counts(scope, now, user=request.user)
    payload = card_payload(card)
    front = render_to_string("study/partials/card_front.html", payload, request)
    back = render_to_string("study/partials/card_back.html", payload, request)
    presentation_token = _save_review_session(
        session,
        scope,
        card,
        rotate_token=True,
    )
    return {
        "done": False,
        "card_id": card.id,
        "card_type": card.card_type,
        "state": card.state,
        "state_label": card.get_state_display(),
        "is_new": card.is_new,
        "front_html": front,
        "back_html": back,
        "presentation_token": presentation_token,
        "counts": counts,
        "revisit_count": counts["revisit_total"],
    }


@require_GET
def review_hub(request, part_slug, task_slug):
    task = _route_task(part_slug, task_slug)
    part = task.part
    now = timezone.now()
    scope = {"part": part.slug, "task": task.slug}
    cards = _task_cards(task, request.user).exclude(suspended=True)
    response_stats = deck_stats(
        cards.filter(card_type=CardType.SPINE),
        now,
    )
    phrase_stats = deck_stats(
        cards.filter(phrase__isnull=False),
        now,
    )
    response_counts = queue_module.queue_counts(
        {**scope, "kind": "spine"},
        now,
        user=request.user,
    )
    phrase_counts = queue_module.queue_counts(
        {**scope, "kind": "phrase"},
        now,
        user=request.user,
    )
    session = ReviewSession.load(request.user)
    saved_scope = session.scope if isinstance(session.scope, dict) else {}
    can_resume = bool(
        session.current_card_id
        and saved_scope.get("part") == part.slug
        and saved_scope.get("task") == task.slug
    )
    return render(
        request,
        "study/review_hub.html",
        {
            "part": part,
            "task": task,
            "counts": queue_module.queue_counts(
                scope,
                now,
                user=request.user,
            ),
            "response_stats": response_stats,
            "phrase_stats": phrase_stats,
            "response_due": response_counts["total_due"],
            "phrase_due": phrase_counts["total_due"],
            "revisit_count": cards.filter(needs_revisit=True).count(),
            "can_resume": can_resume,
        },
    )


@require_GET
def review_next(request):
    with transaction.atomic():
        session = _locked_review_session(request.user)
        scope, _ = _resolved_review_scope(request, session)
        state = _queue_state_locked(scope, request, session)
    state["can_undo"] = ReviewLog.objects.filter(user=request.user).exists()
    return JsonResponse(state)


@require_POST
def review_answer(request):
    try:
        card_id = int(request.POST.get("card_id", ""))
        elapsed_ms = int(request.POST.get("elapsed_ms", "0") or 0)
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Invalid parameters.")

    action = (request.POST.get("action") or "").strip()
    if action:
        ratings = {"revisit": Rating.AGAIN, "correct": Rating.GOOD}
        rating = ratings.get(action)
        if rating is None:
            return HttpResponseBadRequest("Invalid action.")
    else:
        try:
            rating = int(request.POST.get("rating", ""))
        except (TypeError, ValueError):
            return HttpResponseBadRequest("Invalid parameters.")

    if rating not in Rating.values:
        return HttpResponseBadRequest("Invalid rating.")

    scope = scope_from_request(request)
    presentation_token = request.POST.get("presentation_token", "")
    with transaction.atomic():
        session = _locked_review_session(request.user)
        if (
            not presentation_token
            or session is None
            or session.current_card_id != card_id
            or session.scope != scope
            or not secrets.compare_digest(
                session.presentation_token,
                presentation_token,
            )
        ):
            return JsonResponse(
                {"error": "Cette carte a déjà été traitée ou remplacée."},
                status=409,
            )

        card = get_object_or_404(
            Card.objects.select_for_update(),
            pk=card_id,
            user=request.user,
        )
        if scope.get("kind") == "revisit":
            seen = list(session.revisit_seen_card_ids or [])
            if card.id not in seen:
                seen.append(card.id)
            session.revisit_seen_card_ids = seen
        session.current_card = None
        session.presentation_token = ""
        session.save(
            update_fields=[
                "current_card",
                "revisit_seen_card_ids",
                "presentation_token",
                "updated_at",
            ]
        )
        apply_review(card, rating, elapsed_ms=elapsed_ms)
        if action == "revisit" or (not action and rating == Rating.AGAIN):
            card.needs_revisit = True
            card.revisit_added_at = timezone.now()
            card.save(update_fields=["needs_revisit", "revisit_added_at"])
        elif action == "correct" or (not action and rating == Rating.GOOD):
            card.needs_revisit = False
            card.revisit_added_at = None
            card.save(update_fields=["needs_revisit", "revisit_added_at"])

        state = _queue_state_locked(scope, request, session)
    state["can_undo"] = True
    state["action"] = action or str(rating)
    return JsonResponse(state)


@require_POST
def review_undo(request):
    """Revert the most recent review and re-present that card."""
    scope = scope_from_request(request)
    with transaction.atomic():
        session = _locked_review_session(request.user)
        card = undo_last(request.user)
        if card is None:
            state = _queue_state_locked(scope, request, session)
            state["can_undo"] = False
            state["undone"] = False
            return JsonResponse(state)
        state = _card_state_locked(card, scope, request, session)
    state["can_undo"] = ReviewLog.objects.filter(user=request.user).exists()
    state["undone"] = True
    return JsonResponse(state)


# ---------------------------------------------------------------------------
# Revisit list
# ---------------------------------------------------------------------------


def revisit_list(request, part_slug=None, task_slug=None):
    """Persistent list of cards marked with the Revisit review action."""
    task = (
        _route_task(part_slug, task_slug)
        if part_slug is not None and task_slug is not None
        else None
    )
    scope = _task_scope(task) if task else {}
    revisit_scope = {**scope, "kind": "revisit"}
    revisit_cards = queue_module.scoped_cards(
        revisit_scope,
        user=request.user,
    )
    redirect_url = (
        reverse(
            "study:task_revisit_list",
            args=[task.part.slug, task.slug],
        )
        if task
        else reverse("study:revisit_list")
    )
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "remove":
            try:
                card_id = int(request.POST.get("card_id", ""))
            except (TypeError, ValueError):
                return HttpResponseBadRequest("Invalid card.")
            revisit_cards.filter(pk=card_id).update(
                needs_revisit=False,
                revisit_added_at=None,
            )
        elif action == "clear":
            revisit_cards.update(
                needs_revisit=False,
                revisit_added_at=None,
            )
        else:
            return HttpResponseBadRequest("Invalid action.")
        return redirect(redirect_url)

    cards = list(
        revisit_cards
        .select_related(
            "response__theme",
            "response__family",
            "phrase__category",
        )
        .prefetch_related("response__prompts")
        .order_by("revisit_added_at", "id")
    )
    items = []
    for card in cards:
        if card.response_id:
            canonical = card.response.canonical_prompt
            items.append(
                {
                    "card": card,
                    "kind": "Réponse",
                    "title": canonical.text if canonical else card.response.prompt,
                    "meta": (
                        f"{card.response.theme.emoji} "
                        f"{card.response.theme.display_name} · "
                        f"{card.response.family.name}"
                    ),
                    "url": reverse("study:response_detail", args=[card.response_id]),
                }
            )
        else:
            items.append(
                {
                    "card": card,
                    "kind": "Expression",
                    "title": card.phrase.expression,
                    "meta": card.phrase.english_cue,
                    "url": (
                        (
                            reverse(
                                "study:task_phrases",
                                args=[task.part.slug, task.slug],
                            )
                            if task
                            else reverse("study:phrases")
                        )
                        + f"?category={card.phrase.category.slug}"
                        + f"#phrase-{card.phrase.phrase_id}"
                    ),
                }
            )
    response_items = [item for item in items if item["kind"] == "Réponse"]
    phrase_items = [item for item in items if item["kind"] == "Expression"]
    revisit_groups = [
        {
            "title": "Réponses argumentées",
            "description": "Positions et arguments à consolider",
            "items": response_items,
        },
        {
            "title": "Expressions & vocabulaire",
            "description": "Tournures et nuances à remémoriser",
            "items": phrase_items,
        },
    ]
    return render(
        request,
        "study/revisit_list.html",
        {
            "part": task.part if task else None,
            "task": task,
            "items": items,
            "revisit_groups": [
                group for group in revisit_groups if group["items"]
            ],
            "revisit_count": len(items),
            "review_scope_qs": urlencode(revisit_scope),
        },
    )


# ---------------------------------------------------------------------------
# Browse
# ---------------------------------------------------------------------------


def _scope_filters(request, forced_task=None):
    """Shared part/task filter context for Browse and Stats.

    Parses ``?part=`` / ``?task=`` (a task implies its part), builds the chip
    data with per-part/task card counts, and returns the effective scope so the
    page can offer a scoped review.
    """
    part_slug = (request.GET.get("part") or "").strip()
    task_slug = (request.GET.get("task") or "").strip()
    selected_task = forced_task
    if forced_task:
        part_slug = forced_task.part.slug
        task_slug = forced_task.slug

    if task_slug and not forced_task:
        task_qs = Task.objects.select_related("part").filter(slug=task_slug)
        if part_slug:
            task_qs = task_qs.filter(part__slug=part_slug)
        selected_task = task_qs.first()
        if selected_task:
            part_slug = selected_task.part.slug
        else:
            task_slug = ""

    active = Card.objects.active().filter(
        user=request.user,
        card_type=CardType.SPINE,
    )
    filter_parts = []
    active_part_tasks = []
    for part in ExamPart.objects.prefetch_related("tasks"):
        filter_parts.append(
            {
                "slug": part.slug,
                "short_name": part.short_name,
                "count": active.filter(response__theme__task__part=part).count(),
                "active": part_slug == part.slug,
            }
        )
        if part_slug == part.slug:
            for task in part.tasks.all():
                active_part_tasks.append(
                    {
                        "slug": task.slug,
                        "name": task.name,
                        "count": active.filter(response__theme__task=task).count(),
                        "active": task_slug == task.slug,
                    }
                )

    if task_slug:
        scope = {"part": part_slug, "task": task_slug}
        review_qs = f"part={part_slug}&task={task_slug}"
    elif part_slug:
        scope = {"part": part_slug}
        review_qs = f"part={part_slug}"
    else:
        scope = {}
        review_qs = ""

    return {
        "filter_base": request.path,
        "filter_parts": filter_parts,
        "active_part": part_slug,
        "active_task": task_slug,
        "active_part_tasks": active_part_tasks,
        "review_scope_qs": review_qs,
        "scope_label": scope_label(scope),
        "scope": scope,
        "task": selected_task,
        "part": selected_task.part if selected_task else None,
        "task_locked": forced_task is not None,
    }


def browse(request, part_slug=None, task_slug=None):
    now = timezone.now()
    forced_task = (
        _route_task(part_slug, task_slug)
        if part_slug is not None and task_slug is not None
        else None
    )
    if forced_task and not forced_task.available:
        return render(
            request,
            "study/coming_soon.html",
            {"part": forced_task.part, "task": forced_task},
        )
    filters = _scope_filters(request, forced_task)
    scope = filters["scope"]

    theme_qs = Theme.objects.select_related("task__part").all()
    if scope.get("task"):
        theme_qs = theme_qs.filter(
            task__slug=scope["task"],
            task__part__slug=scope["part"],
        )
    elif scope.get("part"):
        theme_qs = theme_qs.filter(task__part__slug=scope["part"])

    themes = []
    for theme in theme_qs:
        stats = deck_stats(
            Card.objects.active().filter(
                user=request.user,
                card_type=CardType.SPINE, response__theme=theme
            ),
            now,
        )
        themes.append(
            {
                "theme": theme,
                "stats": stats,
                "prompt_count": Prompt.objects.filter(theme=theme).count(),
            }
        )
    family_qs = Family.objects.all()
    if scope.get("task"):
        family_qs = family_qs.filter(
            prompts__theme__task__slug=scope["task"],
            prompts__theme__task__part__slug=scope["part"],
        )
    elif scope.get("part"):
        family_qs = family_qs.filter(
            prompts__theme__task__part__slug=scope["part"]
        )
    families = family_qs.annotate(
        n=Count("prompts", distinct=True)
    ).order_by("order")
    prompt_qs = Prompt.objects.all()
    response_qs = Response.objects.all()
    phrase_qs = Phrase.objects.all()
    if scope.get("task"):
        prompt_qs = prompt_qs.filter(
            theme__task__slug=scope["task"],
            theme__task__part__slug=scope["part"],
        )
        response_qs = response_qs.filter(
            theme__task__slug=scope["task"],
            theme__task__part__slug=scope["part"],
        )
        phrase_qs = phrase_qs.filter(
            source_prompts__theme__task__slug=scope["task"],
            source_prompts__theme__task__part__slug=scope["part"],
        ).distinct()
    elif scope.get("part"):
        prompt_qs = prompt_qs.filter(theme__task__part__slug=scope["part"])
        response_qs = response_qs.filter(
            theme__task__part__slug=scope["part"]
        )
        phrase_qs = phrase_qs.filter(
            source_prompts__theme__task__part__slug=scope["part"]
        ).distinct()
    context = {
        "themes": themes,
        "families": families,
        "theme_count": len(themes),
        "prompt_count": prompt_qs.count(),
        "response_count": response_qs.count(),
        "phrase_count": phrase_qs.count(),
        **filters,
    }
    return render(request, "study/browse.html", context)


def theme_detail(request, slug):
    theme = get_object_or_404(
        Theme.objects.select_related("task__part"), slug=slug
    )
    now = timezone.now()
    prompts = (
        Prompt.objects.filter(theme=theme)
        .select_related("response", "response__theme", "family")
        .order_by("number")
    )
    spine_cards = {
        card.response_id: card
        for card in Card.objects.filter(
            user=request.user,
            card_type=CardType.SPINE, response__theme=theme
        )
    }
    rows = [
        {
            "prompt": prompt,
            "card": spine_cards.get(prompt.response_id),
            "is_alias": not prompt.is_canonical,
        }
        for prompt in prompts
    ]
    stats = deck_stats(
        Card.objects.active().filter(
            user=request.user,
            card_type=CardType.SPINE, response__theme=theme
        ),
        now,
    )
    review_scope = {"kind": "spine", "theme": theme.slug}
    if theme.task:
        review_scope.update(
            {
                "part": theme.task.part.slug,
                "task": theme.task.slug,
            }
        )
    return render(
        request,
        "study/theme_detail.html",
        {
            "theme": theme,
            "task": theme.task,
            "part": theme.task.part if theme.task else None,
            "rows": rows,
            "stats": stats,
            "review_batches": _review_batches(review_scope, request.user),
        },
    )


def family_detail(
    request,
    slug,
    part_slug=None,
    task_slug=None,
):
    family = get_object_or_404(Family, slug=slug)
    task = (
        _route_task(part_slug, task_slug)
        if part_slug is not None and task_slug is not None
        else Task.objects.select_related("part")
        .filter(themes__prompts__family=family)
        .distinct()
        .order_by("part__order", "order")
        .first()
    )
    prompt_qs = Prompt.objects.filter(family=family)
    if part_slug is not None and task_slug is not None:
        prompt_qs = prompt_qs.filter(theme__task=task)
    prompts = (
        prompt_qs
        .select_related("response", "theme", "family")
        .order_by("theme__order", "number")
    )
    response_ids = prompts.values_list("response_id", flat=True)
    spine_cards = {
        card.response_id: card
        for card in Card.objects.filter(
            user=request.user,
            card_type=CardType.SPINE,
            response_id__in=response_ids,
        )
    }
    rows = [
        {
            "prompt": prompt,
            "card": spine_cards.get(prompt.response_id),
            "is_alias": not prompt.is_canonical,
        }
        for prompt in prompts
    ]
    return render(
        request,
        "study/family_detail.html",
        {
            "family": family,
            "task": task,
            "part": task.part if task else None,
            "rows": rows,
        },
    )


def response_detail(request, pk):
    response = get_object_or_404(
        Response.objects.select_related(
            "theme__task__part",
            "family",
        ),
        pk=pk,
    )
    arguments = list(response.arguments.all())
    prompts = list(response.prompts.select_related("theme").all())
    card = Card.objects.filter(
        user=request.user,
        card_type=CardType.SPINE,
        response=response,
    ).first()
    related_phrases = (
        Phrase.objects.filter(source_prompts__response=response)
        .distinct()
        .select_related("category")
    )
    return render(
        request,
        "study/response_detail.html",
        {
            "response": response,
            "task": response.theme.task,
            "part": response.theme.task.part if response.theme.task else None,
            "arguments": arguments,
            "prompts": prompts,
            "card": card,
            "related_phrases": related_phrases,
        },
    )


def phrases(request, part_slug=None, task_slug=None):
    task = (
        _route_task(part_slug, task_slug)
        if part_slug is not None and task_slug is not None
        else None
    )
    if task and not task.available:
        return render(
            request,
            "study/coming_soon.html",
            {"part": task.part, "task": task},
        )
    category_slug = request.GET.get("category", "").strip()
    selected = None
    all_phrases = Phrase.objects.select_related("category").prefetch_related(
        "source_prompts__theme"
    )
    if task:
        all_phrases = all_phrases.filter(
            source_prompts__theme__task=task
        ).distinct()
    categories = list(
        PhraseCategory.objects.filter(
            phrases__in=all_phrases
        ).distinct().order_by("order")
    )
    phrase_scope = {"kind": "phrase"}
    if task:
        phrase_scope.update({"part": task.part.slug, "task": task.slug})
    category_card_counts = dict(
        queue_module.scoped_cards(
            phrase_scope,
            user=request.user,
            include_suspended=True,
        )
        .order_by()
        .values("phrase__category_id")
        .annotate(total=Count("id", distinct=True))
        .values_list("phrase__category_id", "total")
    )
    for category in categories:
        category.phrase_count = all_phrases.filter(
            category=category
        ).count()
        category.card_count = category_card_counts.get(category.id, 0)
        category.batch_count = (
            category.card_count + queue_module.BATCH_SIZE - 1
        ) // queue_module.BATCH_SIZE

    phrase_qs = all_phrases.none()
    if category_slug:
        selected = next(
            (
                category
                for category in categories
                if category.slug == category_slug
            ),
            None,
        )
        if selected is None:
            return HttpResponseBadRequest("Unknown phrase category.")
        phrase_qs = all_phrases.filter(category=selected)

    grouped = []
    review_batches = []
    if selected:
        grouped.append(
            {
                "category": selected,
                "phrases": list(phrase_qs),
            }
        )
        review_batches = _review_batches(
            {**phrase_scope, "category": selected.slug},
            request.user,
        )
    functional_names = {
        "Structurer et prendre position",
        "Nuancer et comparer",
        "Cause, conséquence et évaluation",
        "Schémas d'argumentation",
    }

    return render(
        request,
        "study/phrases.html",
        {
            "part": task.part if task else None,
            "task": task,
            "categories": categories,
            "functional_categories": [
                category
                for category in categories
                if category.name in functional_names
            ],
            "topic_categories": [
                category
                for category in categories
                if category.name not in functional_names
            ],
            "grouped": grouped,
            "review_batches": review_batches,
            "batch_size": queue_module.BATCH_SIZE,
            "selected": selected,
            "phrase_count": (
                selected.phrase_count
                if selected
                else sum(category.phrase_count for category in categories)
            ),
        },
    )


def search(request, part_slug=None, task_slug=None):
    task = (
        _route_task(part_slug, task_slug)
        if part_slug is not None and task_slug is not None
        else None
    )
    query = request.GET.get("q", "").strip()
    prompt_results = []
    phrase_results = []
    if query:
        prompt_qs = Prompt.objects.filter(
            Q(text__icontains=query) | Q(response__body__icontains=query)
        )
        phrase_qs = Phrase.objects.filter(
            Q(expression__icontains=query)
            | Q(english_cue__icontains=query)
            | Q(example__icontains=query)
            | Q(note__icontains=query)
        )
        if task:
            prompt_qs = prompt_qs.filter(theme__task=task)
            phrase_qs = phrase_qs.filter(
                source_prompts__theme__task=task
            ).distinct()
        prompt_results = (
            prompt_qs
            .select_related("response", "theme", "family")
            .order_by("theme__order", "number")[:60]
        )
        phrase_results = (
            phrase_qs
            .select_related("category")
            .order_by("order")[:60]
        )
    return render(
        request,
        "study/search.html",
        {
            "part": task.part if task else None,
            "task": task,
            "query": query,
            "prompt_results": prompt_results,
            "phrase_results": phrase_results,
            "result_count": len(prompt_results) + len(phrase_results),
            "prompt_total": (
                Prompt.objects.filter(theme__task=task).count()
                if task
                else Prompt.objects.count()
            ),
            "phrase_total": (
                _task_phrases(task).count()
                if task
                else Phrase.objects.count()
            ),
        },
    )


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def stats(request, part_slug=None, task_slug=None):
    now = timezone.now()
    today = timezone.localtime(now).date()
    forced_task = (
        _route_task(part_slug, task_slug)
        if part_slug is not None and task_slug is not None
        else None
    )
    filters = _scope_filters(request, forced_task)
    scope = filters["scope"]

    scoped_history_cards = queue_module.scoped_cards(
        scope,
        user=request.user,
        include_suspended=True,
    )
    active_cards = scoped_history_cards.filter(suspended=False)
    logs_base = ReviewLog.objects.filter(user=request.user)
    if scope:
        logs_base = logs_base.filter(
            card_id__in=scoped_history_cards.values("pk")
        )

    since = now - timezone.timedelta(days=90)
    logs = logs_base.filter(reviewed_at__gte=since)
    per_day: dict = {}
    for reviewed_at in logs.values_list("reviewed_at", flat=True):
        day = timezone.localtime(reviewed_at).date()
        per_day[day] = per_day.get(day, 0) + 1

    daily = []
    for offset in range(29, -1, -1):
        day = today - timezone.timedelta(days=offset)
        daily.append({"date": day, "count": per_day.get(day, 0)})
    max_daily = max((d["count"] for d in daily), default=0) or 1

    heat = []
    for offset in range(90, -1, -1):
        day = today - timezone.timedelta(days=offset)
        count = per_day.get(day, 0)
        level = min(4, 1 + count // 15) if count else 0
        heat.append({"date": day, "count": count, "level": level})

    mature_logs = logs_base.filter(
        reviewed_at__gte=now - timezone.timedelta(days=30),
        interval_before__gte=MATURE_DAYS,
    )
    mature_total = mature_logs.count()
    mature_pass = mature_logs.exclude(rating=Rating.AGAIN).count()
    retention = round(100 * mature_pass / mature_total) if mature_total else None

    forecast = []
    active = active_cards.filter(
        state__in=[CardState.REVIEW, CardState.LEARNING, CardState.RELEARNING]
    )
    for offset in range(0, 14):
        day = today + timezone.timedelta(days=offset)
        start = timezone.make_aware(
            timezone.datetime.combine(day, timezone.datetime.min.time())
        )
        end = start + timezone.timedelta(days=1)
        if offset == 0:
            count = active.filter(due__lt=end).count()
        else:
            count = active.filter(due__gte=start, due__lt=end).count()
        forecast.append({"date": day, "count": count})
    max_forecast = max((f["count"] for f in forecast), default=0) or 1

    overall = deck_stats(active_cards, now)

    theme_qs = Theme.objects.select_related("task__part").all()
    if scope.get("task"):
        theme_qs = theme_qs.filter(
            task__slug=scope["task"],
            task__part__slug=scope["part"],
        )
    elif scope.get("part"):
        theme_qs = theme_qs.filter(task__part__slug=scope["part"])
    themes = [
        {
            "theme": theme,
            "stats": deck_stats(
                Card.objects.active().filter(
                    user=request.user,
                    card_type=CardType.SPINE, response__theme=theme
                ),
                now,
            ),
        }
        for theme in theme_qs
    ]

    context = {
        "daily": daily,
        "max_daily": max_daily,
        "heat": heat,
        "retention": retention,
        "mature_total": mature_total,
        "forecast": forecast,
        "max_forecast": max_forecast,
        "overall": overall,
        "themes": themes,
        "streak": current_streak(now, logs_base, request.user),
        "total_reviews": logs_base.count(),
        "reviews_today": per_day.get(today, 0),
        **filters,
    }
    return render(request, "study/stats.html", context)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def settings_view(request):
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "reset":
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
                )
                ReviewLog.objects.filter(user=request.user).delete()
                _save_review_session(session, {}, clear_pass=True)
            return redirect(reverse("study:settings") + "?reset=1")
        if action == "unsuspend_all":
            Card.objects.filter(
                user=request.user,
                suspended=True,
            ).update(suspended=False)
            return redirect(reverse("study:settings") + "?unsuspended=1")
        return HttpResponseBadRequest("Invalid settings action.")

    return render(
        request,
        "study/settings.html",
        {
            "was_reset": request.GET.get("reset") == "1",
            "was_unsuspended": request.GET.get("unsuspended") == "1",
            "suspended_count": Card.objects.filter(
                user=request.user,
                suspended=True,
            ).count(),
        },
    )
