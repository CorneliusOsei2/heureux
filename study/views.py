"""Views for the flashcards app."""

from __future__ import annotations

import copy
import json
import re
import secrets
from urllib.parse import parse_qs, urlencode, urlsplit

from django.contrib.auth import (
    get_user_model,
    login as auth_login,
    logout as auth_logout,
    update_session_auth_hash,
)
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.utils import IntegrityError
from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from . import queue as queue_module
from .accounts import (
    authenticate_with_throttle,
    generate_recovery_codes,
    login_throttle_key,
    provision_user_study_data,
    reset_pin_with_recovery,
    reserve_throttled_action,
)
from .cards import card_payload, scope_from_request, scope_label
from .forms import (
    ChangePinForm,
    CurrentPinForm,
    DeleteAccountForm,
    NoteForm,
    PersonalResponseForm,
    RecoveryForm,
    RegistrationForm,
    ResetProgressForm,
    UsernamePinForm,
)
from .models import (
    Annotation,
    AnnotationKind,
    Card,
    CardState,
    CardType,
    ComprehensionAnswer,
    ComprehensionAttempt,
    ComprehensionAttemptStatus,
    ComprehensionChoice,
    ComprehensionMode,
    ComprehensionQuestion,
    ComprehensionTest,
    ExamPart,
    Family,
    Phrase,
    PhraseCategory,
    PhraseTier,
    PersonalResponse,
    Prompt,
    Rating,
    Response,
    ReviewLog,
    ReviewSession,
    Settings,
    Task,
    Theme,
)
from .personalization import effective_response
from .srs import review as apply_review, undo_last

MATURE_DAYS = 21
REVIEW_SCOPE_KEYS = (
    "kind",
    "content",
    "part",
    "task",
    "theme",
    "family",
    "category",
    "response",
    "test",
    "batch",
)
MAX_ANNOTATION_QUOTE_LENGTH = 5000
MAX_ANNOTATION_BODY_LENGTH = 20000
ANNOTATION_SOURCE_KEY_RE = re.compile(r"^[A-Za-z0-9:._-]{0,200}$")
FOCUSED_REVIEW_KINDS = {"revisit", "weak"}
RECENT_SESSION_GAP = timezone.timedelta(minutes=30)
FUNCTIONAL_PHRASE_CATEGORY_NAMES = frozenset(
    {
        "Structurer et prendre position",
        "Nuancer et comparer",
        "Cause, conséquence et évaluation",
        "Schémas d'argumentation",
    }
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
    """Describe stable lots and each lot's first-pass progress."""
    base_scope = {key: value for key, value in scope.items() if key != "batch"}
    rows = list(
        queue_module.scoped_cards(
            base_scope,
            user=user,
            include_suspended=True,
        )
        .order_by(*queue_module.batch_ordering(base_scope))
        .values("id", "phrase_id", "state", "due", "suspended")
    )
    phrase_batches = queue_module._uses_phrase_batches(base_scope)
    if phrase_batches:
        grouped_rows = {}
        for row in rows:
            grouped_rows.setdefault(row["phrase_id"], []).append(row)
        units = list(grouped_rows.values())
    else:
        units = [[row] for row in rows]

    now = timezone.now()
    size = queue_module.batch_size(base_scope)
    batches = []
    for number, start in enumerate(
        range(0, len(units), size),
        start=1,
    ):
        units_in_batch = units[start : start + size]
        active_units = [
            [row for row in unit if not row["suspended"]]
            for unit in units_in_batch
        ]
        active_units = [unit for unit in active_units if unit]
        started_count = sum(
            any(row["state"] != CardState.NEW for row in unit)
            for unit in active_units
        )
        completed_count = sum(
            all(row["state"] != CardState.NEW for row in unit)
            for unit in active_units
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
            for unit in active_units
            for row in unit
        )
        if not active_units:
            status = "unavailable"
            status_label = "Suspendu"
        elif completed_count == len(active_units):
            status = "complete"
            status_label = "Terminé"
        elif started_count:
            status = "in-progress"
            status_label = "En cours"
        else:
            status = "not-started"
            status_label = "À commencer"
        end = start + len(units_in_batch)
        batch_scope = {**base_scope, "batch": str(number)}
        batches.append(
            {
                "number": number,
                "start": start + 1,
                "end": end,
                "card_count": sum(len(unit) for unit in units_in_batch),
                "phrase_count": (
                    len(units_in_batch) if phrase_batches else None
                ),
                "active_count": len(active_units),
                "seen_count": completed_count,
                "started_count": started_count,
                "available_now": available_now,
                "phrase_batch": phrase_batches,
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
    if scope.get("response"):
        return reverse("study:response_detail", args=[scope["response"]])
    if scope.get("category"):
        query = {"category": scope["category"]}
        if scope.get("part") and scope.get("task"):
            query.update(
                {"part": scope["part"], "task": scope["task"]}
            )
        return reverse("study:vocabulary") + "?" + urlencode(query)
    if scope.get("test"):
        return (
            reverse("study:vocabulary")
            + "?"
            + urlencode({"test": scope["test"]})
        )
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


def recent_review_sessions(logs, *, limit=8) -> list[dict]:
    """Group recent review logs into focused sessions separated by 30 minutes."""
    recent_logs = list(
        logs.select_related(
            "card__response__theme",
            "card__phrase",
        ).order_by("-reviewed_at")[:400]
    )
    sessions = []
    current = None
    for log in recent_logs:
        if (
            current is None
            or current["started_at"] - log.reviewed_at > RECENT_SESSION_GAP
        ):
            if len(sessions) >= limit:
                break
            current = {
                "started_at": log.reviewed_at,
                "ended_at": log.reviewed_at,
                "review_count": 0,
                "correct_count": 0,
                "revisit_count": 0,
                "response_count": 0,
                "phrase_count": 0,
                "elapsed_ms": 0,
                "topics_set": set(),
            }
            sessions.append(current)

        current["started_at"] = log.reviewed_at
        current["review_count"] += 1
        current["elapsed_ms"] += log.elapsed_ms
        if log.rating == Rating.AGAIN:
            current["revisit_count"] += 1
        else:
            current["correct_count"] += 1
        if log.card.response_id:
            current["response_count"] += 1
            current["topics_set"].add(log.card.response.theme.display_name)
        else:
            current["phrase_count"] += 1
            current["topics_set"].add("Expressions")

    for session in sessions:
        session["accuracy"] = round(
            100 * session["correct_count"] / session["review_count"]
        )
        session["study_minutes"] = (
            max(1, round(session["elapsed_ms"] / 60000))
            if session["elapsed_ms"]
            else None
        )
        topics = sorted(session.pop("topics_set"))
        session["topics"] = topics[:3]
        session["extra_topics"] = max(0, len(topics) - 3)
    return sessions


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
        is_active=True,
        tier=PhraseTier.SHARED,
        source_prompts__is_active=True,
        source_prompts__theme__is_active=True,
        source_prompts__theme__task=task,
    ).distinct()


def _route_task(part_slug, task_slug):
    return get_object_or_404(
        Task.objects.select_related("part"),
        slug=task_slug,
        part__slug=part_slug,
        is_active=True,
        part__is_active=True,
    )


def _task_card(task, now, user):
    """Build a dashboard/part card for a single task."""
    if task.available:
        response_stats = deck_stats(_task_cards(task, user, "spine"), now)
        phrase_stats = deck_stats(_task_cards(task, user, "phrase"), now)
        functional_phrase_stats = deck_stats(
            _task_cards(task, user, "phrase").filter(
                phrase__tier=PhraseTier.SHARED,
                phrase__category__name__in=FUNCTIONAL_PHRASE_CATEGORY_NAMES,
            ),
            now,
        )
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
        revisit_count = _task_cards(task, user, "revisit").count()
        theme_count = Theme.objects.filter(task=task, is_active=True).count()
        prompt_count = Prompt.objects.filter(
            theme__task=task,
            is_active=True,
        ).count()
        phrase_count = _task_phrases(task).count()
        functional_phrase_count = _task_phrases(task).filter(
            category__name__in=FUNCTIONAL_PHRASE_CATEGORY_NAMES
        ).count()
        subject_vocabulary_count = Phrase.objects.filter(
            is_active=True,
            tier=PhraseTier.SUBJECT,
            source_prompts__is_active=True,
            source_prompts__theme__is_active=True,
            source_prompts__theme__task=task,
        ).distinct().count()
    else:
        response_stats = None
        phrase_stats = None
        functional_phrase_stats = None
        stats = None
        counts = None
        phrase_counts = None
        revisit_count = 0
        theme_count = 0
        prompt_count = 0
        phrase_count = 0
        functional_phrase_count = 0
        subject_vocabulary_count = 0
    return {
        "task": task,
        "stats": stats,
        "response_stats": response_stats,
        "phrase_stats": phrase_stats,
        "functional_phrase_stats": functional_phrase_stats,
        "counts": counts,
        "phrase_counts": phrase_counts,
        "revisit_count": revisit_count,
        "theme_count": theme_count,
        "prompt_count": prompt_count,
        "phrase_count": phrase_count,
        "functional_phrase_count": functional_phrase_count,
        "subject_vocabulary_count": subject_vocabulary_count,
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
        for part in ExamPart.objects.filter(is_active=True).prefetch_related(
            Prefetch("tasks", queryset=Task.objects.filter(is_active=True))
        )
    ]


def _phrase_deck_stats(now, user=None, task=None):
    cards = (
        _task_cards(task, user, "phrase")
        if task
        else queue_module.scoped_cards({"kind": "phrase"}, user=user)
    )
    return deck_stats(cards, now)


COMPREHENSION_GROUP_SIZE = 5
COMPREHENSION_GROUP_COUNT = 8
COMPREHENSION_ROUTE_NAMES = {
    ComprehensionMode.ECRITE: {
        "overview": "study:comprehension_overview",
        "test": "study:comprehension_test",
        "study": "study:comprehension_question_study",
        "start": "study:comprehension_start",
        "question": "study:comprehension_question",
        "results": "study:comprehension_results",
    },
    ComprehensionMode.ORALE: {
        "overview": "study:comprehension_oral_overview",
        "test": "study:comprehension_oral_test",
        "study": "study:comprehension_oral_question_study",
        "start": "study:comprehension_oral_start",
        "question": "study:comprehension_oral_question",
        "results": "study:comprehension_oral_results",
    },
}


def _prepare_comprehension_test(test):
    routes = COMPREHENSION_ROUTE_NAMES[test.mode]
    test.group_number = (
        _comprehension_group_number(test.number)
        if test.mode == ComprehensionMode.ECRITE
        else None
    )
    test.overview_route = routes["overview"]
    test.detail_route = routes["test"]
    test.study_route = routes["study"]
    test.start_route = routes["start"]
    test.question_route = routes["question"]
    test.results_route = routes["results"]
    test.mode_title = f"Compréhension {test.get_mode_display().lower()}"
    test.source_label = (
        "Document"
        if test.mode == ComprehensionMode.ECRITE
        else "Dialogue"
    )
    test.source_instruction = (
        "Lisez le texte"
        if test.mode == ComprehensionMode.ECRITE
        else "Lisez le dialogue"
    )
    return test


def _comprehension_test_cards(user, *, mode=None, published_only=False):
    attempts = (
        ComprehensionAttempt.objects.filter(user=user)
        .annotate(
            answer_total=Count("answers"),
        )
        .order_by("-started_at", "-pk")
    )
    tests = ComprehensionTest.objects.filter(
        Q(is_active=True) | Q(attempts__user=user)
    ).distinct()
    if mode:
        tests = tests.filter(mode=mode)
    if published_only:
        tests = tests.filter(is_active=True, is_published=True)
    tests = list(
        tests.annotate(
            active_question_count=Count(
                "questions",
                filter=Q(questions__is_active=True),
                distinct=True,
            )
        ).prefetch_related(
            Prefetch("attempts", queryset=attempts, to_attr="user_attempts")
        )
    )
    for test in tests:
        _prepare_comprehension_test(test)
        test.active_attempt = next(
            (
                attempt
                for attempt in test.user_attempts
                if attempt.status == ComprehensionAttemptStatus.IN_PROGRESS
            ),
            None,
        )
        test.completed_attempts = [
            attempt
            for attempt in test.user_attempts
            if attempt.status == ComprehensionAttemptStatus.COMPLETED
        ]
        test.latest_attempt = (
            test.completed_attempts[0] if test.completed_attempts else None
        )
        test.best_attempt = max(
            test.completed_attempts,
            key=lambda attempt: (attempt.percentage, attempt.score or 0),
            default=None,
        )
        test.active_answered_count = (
            test.active_attempt.answer_total if test.active_attempt else 0
        )
        test.attempt_question_count = (
            test.active_attempt.total_questions
            if test.active_attempt
            else test.active_question_count
        )
    return tests


def _comprehension_mode_summary(tests, *, group_count=0):
    available_tests = [
        test
        for test in tests
        if test.is_active and test.is_published
    ]
    active_test = next(
        (test for test in available_tests if test.active_attempt),
        None,
    )
    completed_attempts = [
        attempt
        for test in tests
        for attempt in test.completed_attempts
    ]
    next_test = active_test or next(
        (
            test
            for test in available_tests
            if not test.completed_attempts
        ),
        available_tests[0] if available_tests else None,
    )
    return {
        "group_count": group_count,
        "test_count": len(tests),
        "available_test_count": len(available_tests),
        "path_available": bool(tests),
        "completed_test_count": sum(
            bool(test.completed_attempts) for test in tests
        ),
        "active_attempt": (
            active_test.active_attempt if active_test else None
        ),
        "active_answered_count": (
            active_test.active_answered_count if active_test else 0
        ),
        "active_question_count": (
            active_test.attempt_question_count if active_test else 0
        ),
        "best_percentage": max(
            (attempt.percentage for attempt in completed_attempts),
            default=None,
        ),
        "next_test": next_test,
        "next_test_url": (
            reverse(next_test.detail_route, args=[next_test.slug])
            if next_test
            else ""
        ),
        "active_attempt_url": (
            reverse(
                active_test.question_route,
                args=[
                    active_test.slug,
                    active_test.active_attempt.pk,
                    active_test.active_attempt.current_question,
                ],
            )
            if active_test
            else ""
        ),
    }


def _comprehension_summary(user):
    tests = [
        test
        for test in _comprehension_test_cards(user)
        if (
            (test.is_active and test.is_published)
            or test.user_attempts
        )
    ]
    written_tests = [
        test for test in tests if test.mode == ComprehensionMode.ECRITE
    ]
    oral_tests = [
        test for test in tests if test.mode == ComprehensionMode.ORALE
    ]
    summary = _comprehension_mode_summary(
        tests,
        group_count=COMPREHENSION_GROUP_COUNT,
    )
    summary["ecrite"] = _comprehension_mode_summary(
        written_tests,
        group_count=COMPREHENSION_GROUP_COUNT,
    )
    summary["orale"] = _comprehension_mode_summary(oral_tests)
    return summary


def _home_expression_paths(parts):
    paths = []
    for item in parts:
        available_tasks = [
            task
            for task in item["tasks"]
            if task["task"].available
        ]
        paths.append(
            {
                **item,
                "available": bool(item["part"].available and available_tasks),
                "available_task_count": len(available_tasks),
                "prompt_count": sum(
                    task["prompt_count"] for task in available_tasks
                ),
                "seen": sum(
                    task["stats"]["seen"] for task in available_tasks
                ),
                "total": sum(
                    task["stats"]["total"] for task in available_tasks
                ),
                "due": sum(
                    task["stats"]["due"] for task in available_tasks
                ),
            }
        )
    paths.sort(
        key=lambda item: (
            {"ee": 0, "eo": 1}.get(
                item["part"].slug,
                2,
            ),
            item["part"].order,
        )
    )
    return paths


def _vocabulary_expression_paths(now, user):
    part_items = _parts_with_task_cards(now, user)
    path_specs = (
        {
            "title": "Écrite",
            "short_name": "EE",
            "slugs": {"ee"},
            "fallback_emoji": "✍️",
        },
        {
            "title": "Orale",
            "short_name": "EO",
            "slugs": {"eo"},
            "fallback_emoji": "🎙️",
        },
    )
    paths = []
    for spec in path_specs:
        item = next(
            (
                candidate
                for candidate in part_items
                if candidate["part"].slug in spec["slugs"]
            ),
            None,
        )
        vocabulary_tasks = []
        if item:
            vocabulary_tasks = [
                task_item
                for task_item in item["tasks"]
                if task_item["task"].available
                and (
                    task_item["functional_phrase_count"]
                    + task_item["subject_vocabulary_count"]
                )
            ]
        available = bool(
            item and item["part"].available and vocabulary_tasks
        )
        url = ""
        if available:
            if len(vocabulary_tasks) == 1:
                task = vocabulary_tasks[0]["task"]
                url = reverse("study:vocabulary") + "?" + urlencode(
                    {"part": task.part.slug, "task": task.slug}
                )
            else:
                url = reverse(
                    "study:part_detail",
                    args=[item["part"].slug],
                )
        paths.append(
            {
                "title": spec["title"],
                "short_name": spec["short_name"],
                "emoji": (
                    item["part"].emoji
                    if item
                    else spec["fallback_emoji"]
                ),
                "available": available,
                "url": url,
                "task_count": len(vocabulary_tasks),
                "prompt_count": sum(
                    task_item["prompt_count"]
                    for task_item in vocabulary_tasks
                ),
                "vocabulary_count": sum(
                    task_item["functional_phrase_count"]
                    + task_item["subject_vocabulary_count"]
                    for task_item in vocabulary_tasks
                ),
            }
        )
    return paths


def dashboard(request):
    now = timezone.now()
    expression_counts = queue_module.queue_counts(
        {"content": "spine"},
        now=now,
        user=request.user,
    )
    vocabulary_counts = queue_module.queue_counts(
        {"content": "vocabulary"},
        now=now,
        user=request.user,
    )
    user_cards = Card.objects.current_content().filter(
        user=request.user,
        suspended=False,
    )
    overall = deck_stats(user_cards, now)
    parts = _parts_with_task_cards(now, request.user)
    session = ReviewSession.load(request.user)

    context = {
        "expression_counts": expression_counts,
        "vocabulary_counts": vocabulary_counts,
        "parts": parts,
        "expression_paths": _home_expression_paths(parts),
        "overall": overall,
        "streak": current_streak(now, user=request.user),
        "comprehension": _comprehension_summary(request.user),
        "notes_to_study": Annotation.objects.filter(
            user=request.user,
            study_later=True,
        ).count(),
        "can_resume_review": bool(session.current_card_id),
        "resume_scope_label": (
            scope_label(session.scope)
            if session.current_card_id and isinstance(session.scope, dict)
            else ""
        ),
    }
    return render(request, "study/dashboard.html", context)


def _redirect_to(request, route_name, **scope):
    query = request.GET.copy()
    for key, value in scope.items():
        if value:
            query[key] = value
    target = reverse(route_name)
    if query:
        target = f"{target}?{query.urlencode()}"
    return redirect(target)


@require_GET
def redirect_home(request):
    return _redirect_to(request, "study:dashboard")


@require_GET
def redirect_expression(request):
    return _redirect_to(request, "study:expression")


@require_GET
def redirect_vocabulary(request, part_slug=None, task_slug=None):
    if part_slug is not None or task_slug is not None:
        task = _route_task(part_slug, task_slug)
        part_slug = task.part.slug
        task_slug = task.slug
    return _redirect_to(
        request,
        "study:vocabulary",
        part=part_slug,
        task=task_slug,
    )


@require_GET
def redirect_search(request, part_slug=None, task_slug=None):
    if part_slug is not None or task_slug is not None:
        task = _route_task(part_slug, task_slug)
        part_slug = task.part.slug
        task_slug = task.slug
    return _redirect_to(
        request,
        "study:search",
        part=part_slug,
        task=task_slug,
    )


@require_GET
def redirect_notes(request, part_slug=None, task_slug=None):
    scope = {}
    if part_slug and task_slug:
        task = _route_task(part_slug, task_slug)
        scope = {"part": task.part.slug, "task": task.slug}
    elif part_slug or task_slug:
        return HttpResponseBadRequest("Incomplete notes scope.")
    else:
        scope = {"scope": "general"}
    return _redirect_to(request, "study:notes_overview", **scope)


@require_GET
def redirect_stats(request, part_slug=None, task_slug=None):
    if part_slug is not None or task_slug is not None:
        task = _route_task(part_slug, task_slug)
        part_slug = task.part.slug
        task_slug = task.slug
    return _redirect_to(
        request,
        "study:stats",
        part=part_slug,
        task=task_slug,
    )


@require_GET
def expression_hub(request):
    now = timezone.now()
    parts = _parts_with_task_cards(now, request.user)
    paths = _home_expression_paths(parts)
    available_paths = [path for path in paths if path["available"]]
    return render(
        request,
        "study/expression_hub.html",
        {
            "paths": paths,
            "prompt_count": sum(
                path["prompt_count"] for path in available_paths
            ),
            "card_total": sum(path["total"] for path in available_paths),
            "card_seen": sum(path["seen"] for path in available_paths),
            "card_due": sum(path["due"] for path in available_paths),
        },
    )


def _grouped_overview(request, area):
    now = timezone.now()
    user_cards = queue_module.scoped_cards(user=request.user)
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
                "revisit_count": queue_module.scoped_cards(
                    {"kind": "revisit"},
                    user=request.user,
                ).count(),
                "weak_count": queue_module.queue_counts(
                    {"kind": "weak"},
                    now,
                    user=request.user,
                )["weak_total"],
                "can_resume": bool(session.current_card_id),
            }
        )
    elif area == "expressions":
        functional_cards = queue_module.scoped_cards(
            {"kind": "phrase"},
            user=request.user,
        ).filter(
            phrase__tier=PhraseTier.SHARED,
            phrase__category__name__in=FUNCTIONAL_PHRASE_CATEGORY_NAMES,
        )
        context.update(
            {
                "title": "Expressions",
                "eyebrow": "Précision lexicale",
                "description": (
                    "Choisissez une tâche pour retrouver ses expressions "
                    "transversales et les 50 vocabs de chaque sujet."
                ),
                "phrase_count": Phrase.objects.filter(
                    is_active=True,
                    tier=PhraseTier.SHARED,
                    category__name__in=FUNCTIONAL_PHRASE_CATEGORY_NAMES,
                ).count(),
                "subject_phrase_count": Phrase.objects.filter(
                    is_active=True,
                    tier=PhraseTier.SUBJECT,
                ).count(),
                "phrase_stats": deck_stats(functional_cards, now),
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


# ---------------------------------------------------------------------------
# Compréhension écrite
# ---------------------------------------------------------------------------

@require_GET
def comprehension_hub(request):
    return render(
        request,
        "study/comprehension_hub.html",
        {"comprehension": _comprehension_summary(request.user)},
    )


def _comprehension_group_number(test_number):
    return ((test_number - 1) // COMPREHENSION_GROUP_SIZE) + 1


def _comprehension_groups(tests):
    groups = []
    for number in range(1, COMPREHENSION_GROUP_COUNT + 1):
        start = ((number - 1) * COMPREHENSION_GROUP_SIZE) + 1
        end = start + COMPREHENSION_GROUP_SIZE - 1
        group_tests = [
            test
            for test in tests
            if start <= test.number <= end
        ]
        published = [
            test
            for test in group_tests
            if test.is_active and test.is_published
        ]
        groups.append(
            {
                "number": number,
                "start": start,
                "end": end,
                "tests": group_tests,
                "available_count": len(published),
                "completed_count": sum(
                    bool(test.completed_attempts) for test in group_tests
                ),
                "active_attempt": next(
                    (
                        test.active_attempt
                        for test in published
                        if test.active_attempt
                    ),
                    None,
                ),
            }
        )
    return groups


def _comprehension_question_snapshot(
    question,
    selected_choice=None,
    *,
    choices=None,
):
    choices = choices if choices is not None else question.choices.all()
    choices = [
        {
            "id": choice.pk,
            "letter": choice.letter,
            "text_fr": choice.text_fr,
            "text_en": choice.text_en,
            "rationale": choice.rationale,
            "is_correct": choice.is_correct,
        }
        for choice in choices
    ]
    return {
        "id": question.pk,
        "content_key": question.content_key,
        "number": question.number,
        "passage_fr": question.passage_fr,
        "passage_en": question.passage_en,
        "prompt_fr": question.prompt_fr,
        "prompt_en": question.prompt_en,
        "correct_explanation": question.correct_explanation,
        "choices": choices,
        "selected_letter": selected_choice.letter if selected_choice else "",
    }


def _build_comprehension_test_snapshot(test):
    questions = (
        test.questions.filter(is_active=True)
        .prefetch_related(
            Prefetch(
                "choices",
                queryset=ComprehensionChoice.objects.filter(is_active=True),
            )
        )
        .order_by("number")
    )
    return {
        "questions": [
            _comprehension_question_snapshot(question)
            for question in questions
        ]
    }


def _comprehension_attempt_questions(attempt):
    snapshot = attempt.content_snapshot
    if isinstance(snapshot, dict):
        questions = snapshot.get("questions")
        if (
            isinstance(questions, list)
            and questions
            and all(
                isinstance(question, dict)
                and question.get("id")
                and question.get("choices")
                for question in questions
            )
        ):
            return questions

    answers = {
        answer.question_id: answer
        for answer in attempt.answers.select_related("selected_choice")
    }
    questions = (
        attempt.test.questions.filter(
            Q(is_active=True) | Q(pk__in=answers)
        )
        .prefetch_related("choices")
        .order_by("number")
    )
    serialized = []
    for question in questions:
        answer = answers.get(question.pk)
        if answer and answer.question_snapshot:
            question_data = copy.deepcopy(answer.question_snapshot)
            question_data.setdefault("id", question.pk)
            question_data.setdefault("content_key", question.content_key)
            for choice_data, choice in zip(
                question_data.get("choices", []),
                question.choices.all(),
            ):
                choice_data.setdefault("id", choice.pk)
        else:
            available_choices = [
                choice
                for choice in question.choices.all()
                if answer or choice.is_active
            ]
            question_data = _comprehension_question_snapshot(
                question,
                answer.selected_choice if answer else None,
                choices=available_choices,
            )
        serialized.append(question_data)

    attempt.content_snapshot = {"questions": serialized}
    attempt.total_questions = len(serialized)
    attempt.save(update_fields=["content_snapshot", "total_questions"])
    return serialized


def _snapshot_with_selected_choice(question, selected_letter):
    snapshot = copy.deepcopy(question)
    snapshot["selected_letter"] = selected_letter
    return snapshot


def _comprehension_answer_snapshot(answer):
    snapshot = answer.question_snapshot
    if (
        isinstance(snapshot, dict)
        and snapshot.get("choices")
        and snapshot.get("number")
    ):
        return snapshot
    for question in _comprehension_attempt_questions(answer.attempt):
        if question["id"] == answer.question_id:
            return _snapshot_with_selected_choice(
                question,
                answer.selected_choice.letter,
            )
    return _comprehension_question_snapshot(
        answer.question,
        answer.selected_choice,
    )


@require_GET
def comprehension_overview(request):
    tests = _comprehension_test_cards(
        request.user,
        mode=ComprehensionMode.ECRITE,
    )
    published = [
        test
        for test in tests
        if test.is_active and test.is_published
    ]
    completed_attempts = [
        attempt
        for test in published
        for attempt in test.completed_attempts
    ]
    return render(
        request,
        "study/comprehension_overview.html",
        {
            "groups": _comprehension_groups(tests),
            "published_count": len(published),
            "completed_count": sum(
                bool(test.completed_attempts) for test in published
            ),
            "best_percentage": max(
                (attempt.percentage for attempt in completed_attempts),
                default=None,
            ),
        },
    )


@require_GET
def comprehension_oral_overview(request):
    tests = _comprehension_test_cards(
        request.user,
        mode=ComprehensionMode.ORALE,
    )
    published = [
        test
        for test in tests
        if test.is_active and test.is_published
    ]
    completed_attempts = [
        attempt
        for test in published
        for attempt in test.completed_attempts
    ]
    return render(
        request,
        "study/comprehension_oral_overview.html",
        {
            "tests": tests,
            "published_count": len(published),
            "completed_count": sum(
                bool(test.completed_attempts) for test in published
            ),
            "best_percentage": max(
                (attempt.percentage for attempt in completed_attempts),
                default=None,
            ),
        },
    )


@require_GET
def comprehension_group_detail(request, group_number):
    if not 1 <= group_number <= COMPREHENSION_GROUP_COUNT:
        raise Http404

    tests = _comprehension_test_cards(
        request.user,
        mode=ComprehensionMode.ECRITE,
    )
    group = _comprehension_groups(tests)[group_number - 1]
    tests_by_number = {
        test.number: test
        for test in group["tests"]
    }
    group["slots"] = [
        {
            "number": number,
            "test": tests_by_number.get(number),
        }
        for number in range(group["start"], group["end"] + 1)
    ]
    return render(
        request,
        "study/comprehension_group.html",
        {"group": group},
    )


@require_GET
def comprehension_test_detail(
    request,
    test_slug,
    mode=ComprehensionMode.ECRITE,
):
    test = next(
        (
            item
            for item in _comprehension_test_cards(request.user, mode=mode)
            if item.slug == test_slug
            and (
                (item.is_active and item.is_published)
                or item.user_attempts
            )
        ),
        None,
    )
    if test is None:
        raise Http404
    questions = []
    if test.is_active and test.is_published:
        question_qs = (
            test.questions.filter(is_active=True)
            .prefetch_related(
                Prefetch(
                    "choices",
                    queryset=ComprehensionChoice.objects.filter(
                        is_active=True,
                    ),
                )
            )
            .order_by("number")
        )
        progress_attempt = test.active_attempt or test.latest_attempt
        answers = (
            {
                answer.question_id: answer
                for answer in progress_attempt.answers.all()
            }
            if progress_attempt
            else {}
        )
        questions = [
            {
                "question": question,
                "answer": answers.get(question.pk),
            }
            for question in question_qs
        ]
    return render(
        request,
        "study/comprehension_test.html",
        {
            "test": test,
            "questions": questions,
            "attempt_history": test.completed_attempts[:6],
        },
    )


@require_GET
def comprehension_question_study(
    request,
    test_slug,
    number,
    mode=ComprehensionMode.ECRITE,
):
    test = next(
        (
            item
            for item in _comprehension_test_cards(
                request.user,
                mode=mode,
                published_only=True,
            )
            if item.slug == test_slug
        ),
        None,
    )
    if test is None:
        raise Http404

    questions = list(
        test.questions.filter(is_active=True)
        .prefetch_related(
            Prefetch(
                "choices",
                queryset=ComprehensionChoice.objects.filter(
                    is_active=True,
                ),
            )
        )
        .order_by("number")
    )
    try:
        position = next(
            index
            for index, question in enumerate(questions)
            if question.number == number
        )
    except StopIteration as error:
        raise Http404 from error

    question = questions[position]
    choices = list(question.choices.all())
    correct_choice = next(
        (choice for choice in choices if choice.is_correct),
        None,
    )
    if correct_choice is None:
        raise Http404

    return render(
        request,
        "study/comprehension_question_study.html",
        {
            "test": test,
            "question": question,
            "choices": choices,
            "correct_choice": correct_choice,
            "position": position + 1,
            "total_questions": len(questions),
            "previous_question": (
                questions[position - 1] if position > 0 else None
            ),
            "next_question": (
                questions[position + 1]
                if position + 1 < len(questions)
                else None
            ),
        },
    )


def _comprehension_question_url(attempt, number):
    _prepare_comprehension_test(attempt.test)
    return reverse(
        attempt.test.question_route,
        args=[attempt.test.slug, attempt.pk, number],
    )


def _comprehension_test_url(test):
    _prepare_comprehension_test(test)
    return reverse(test.detail_route, args=[test.slug])


def _comprehension_overview_url(test):
    _prepare_comprehension_test(test)
    return reverse(test.overview_route)


def _comprehension_results_url(attempt):
    _prepare_comprehension_test(attempt.test)
    return reverse(
        attempt.test.results_route,
        args=[attempt.test.slug, attempt.pk],
    )


def _sync_comprehension_attempt_position(attempt):
    if attempt.status != ComprehensionAttemptStatus.IN_PROGRESS:
        return None
    questions = _comprehension_attempt_questions(attempt)
    answered_question_ids = set(
        attempt.answers.values_list("question_id", flat=True)
    )
    next_question = next(
        (
            question
            for question in questions
            if question["id"] not in answered_question_ids
        ),
        None,
    )
    if next_question:
        if attempt.current_question != next_question["number"]:
            attempt.current_question = next_question["number"]
            attempt.save(update_fields=["current_question", "updated_at"])
        return next_question["number"]

    attempt.status = ComprehensionAttemptStatus.COMPLETED
    attempt.score = attempt.answers.filter(is_correct=True).count()
    attempt.total_questions = len(questions)
    attempt.completed_at = timezone.now()
    attempt.save(
        update_fields=[
            "status",
            "score",
            "total_questions",
            "completed_at",
            "updated_at",
        ]
    )
    return None


@require_POST
def comprehension_start(
    request,
    test_slug,
    mode=ComprehensionMode.ECRITE,
):
    action = request.POST.get("action", "continue")
    if action not in {"continue", "restart", "errors"}:
        return HttpResponseBadRequest("Action de test invalide.")

    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=request.user.pk)
        test = get_object_or_404(
            ComprehensionTest.objects.select_for_update(),
            slug=test_slug,
            mode=mode,
            is_active=True,
            is_published=True,
        )
        _prepare_comprehension_test(test)
        active_attempt = (
            ComprehensionAttempt.objects.select_for_update()
            .filter(
                user=request.user,
                test=test,
                status=ComprehensionAttemptStatus.IN_PROGRESS,
            )
            .first()
        )
        focused_snapshot = None
        if action == "errors" and active_attempt is None:
            source_attempt_id = request.POST.get("attempt_id", "")
            if not source_attempt_id.isdecimal():
                return HttpResponseBadRequest(
                    "Tentative source invalide."
                )
            source_attempt = get_object_or_404(
                ComprehensionAttempt.objects.select_for_update(),
                pk=source_attempt_id,
                user=request.user,
                test=test,
                status=ComprehensionAttemptStatus.COMPLETED,
            )
            wrong_question_ids = set(
                source_attempt.answers.filter(
                    is_correct=False,
                ).values_list("question_id", flat=True)
            )
            focused_questions = [
                copy.deepcopy(question)
                for question in _comprehension_attempt_questions(
                    source_attempt
                )
                if question["id"] in wrong_question_ids
            ]
            if not focused_questions:
                return redirect(_comprehension_results_url(source_attempt))
            focused_snapshot = {
                "practice_mode": "errors",
                "source_attempt_id": source_attempt.pk,
                "questions": focused_questions,
            }
        if active_attempt and action == "restart":
            active_attempt.status = ComprehensionAttemptStatus.ABANDONED
            active_attempt.completed_at = timezone.now()
            active_attempt.save(update_fields=["status", "completed_at", "updated_at"])
            active_attempt = None

        if active_attempt is None:
            content_snapshot = (
                focused_snapshot
                if focused_snapshot is not None
                else _build_comprehension_test_snapshot(test)
            )
            questions = content_snapshot["questions"]
            if not questions:
                return HttpResponseBadRequest("Ce test ne contient aucune question.")
            active_attempt = ComprehensionAttempt.objects.create(
                user=request.user,
                test=test,
                current_question=questions[0]["number"],
                total_questions=len(questions),
                content_snapshot=content_snapshot,
            )
        resume_number = _sync_comprehension_attempt_position(active_attempt)

    if resume_number is None:
        return redirect(_comprehension_results_url(active_attempt))
    return redirect(
        _comprehension_question_url(
            active_attempt,
            resume_number,
        )
    )


def _comprehension_attempt(request, test_slug, attempt_id, mode):
    attempt = get_object_or_404(
        ComprehensionAttempt.objects.select_related("test"),
        pk=attempt_id,
        user=request.user,
        test__slug=test_slug,
        test__mode=mode,
    )
    _prepare_comprehension_test(attempt.test)
    return attempt


def _comprehension_question_context(attempt, question_number, error=""):
    questions = _comprehension_attempt_questions(attempt)
    if not questions:
        return None
    question_index = next(
        (
            index
            for index, question in enumerate(questions)
            if question["number"] == question_number
        ),
        None,
    )
    if question_index is None:
        return None

    answers = {
        answer.question_id: answer
        for answer in attempt.answers.select_related(
            "question",
            "selected_choice",
        )
    }
    next_unanswered = next(
        (
            question
            for question in questions
            if question["id"] not in answers
        ),
        None,
    )
    question = questions[question_index]
    answer = answers.get(question["id"])
    if (
        answer is None
        and next_unanswered
        and question["id"] != next_unanswered["id"]
    ):
        return {
            "redirect_number": next_unanswered["number"],
        }

    question_model = ComprehensionQuestion.objects.filter(
        pk=question["id"],
        test=attempt.test,
    ).first()
    if question_model is None:
        return None
    display_question = question
    display_choices = question["choices"]
    selected_choice = answer.selected_choice if answer else None
    selected_letter = selected_choice.letter if selected_choice else ""
    if answer and answer.question_snapshot:
        display_question = _comprehension_answer_snapshot(answer)
        display_choices = display_question["choices"]
        selected_letter = display_question.get("selected_letter") or selected_letter
        selected_choice = next(
            (
                choice
                for choice in display_choices
                if choice["letter"] == selected_letter
            ),
            None,
        )
    correct_choice = next(
        (
            choice
            for choice in display_choices
            if choice["is_correct"]
        ),
        None,
    )
    navigator = [
        {
            "number": item["number"],
            "is_answered": item["id"] in answers,
            "is_current": item["id"] == question["id"],
            "is_available": (
                item["id"] in answers
                or next_unanswered is None
                or item["id"] == next_unanswered["id"]
            ),
        }
        for item in questions
    ]
    return {
        "attempt": attempt,
        "test": attempt.test,
        "group_number": attempt.test.group_number,
        "question": display_question,
        "question_model": question_model,
        "choices": display_choices,
        "answer": answer,
        "selected_choice": selected_choice,
        "selected_letter": selected_letter,
        "correct_choice": correct_choice,
        "answered_count": len(answers),
        "total_questions": len(questions),
        "position": question_index + 1,
        "previous_number": (
            questions[question_index - 1]["number"] if question_index else None
        ),
        "next_number": (
            questions[question_index + 1]["number"]
            if answer and question_index + 1 < len(questions)
            else None
        ),
        "navigator": navigator,
        "answer_error": error,
    }


@require_http_methods(["GET", "POST"])
def comprehension_question(
    request,
    test_slug,
    attempt_id,
    number,
    mode=ComprehensionMode.ECRITE,
):
    attempt = _comprehension_attempt(
        request,
        test_slug,
        attempt_id,
        mode,
    )
    if (
        not attempt.test.is_active
        or not attempt.test.is_published
    ):
        return redirect(_comprehension_overview_url(attempt.test))
    if (
        attempt.status == ComprehensionAttemptStatus.COMPLETED
        and not (
            request.method == "GET"
            and request.GET.get("correction") == "1"
        )
    ):
        return redirect(_comprehension_results_url(attempt))
    if attempt.status == ComprehensionAttemptStatus.ABANDONED:
        return redirect(_comprehension_test_url(attempt.test))

    context = _comprehension_question_context(attempt, number)
    if context is None:
        with transaction.atomic():
            locked_attempt = get_object_or_404(
                ComprehensionAttempt.objects.select_for_update().select_related(
                    "test"
                ),
                pk=attempt.pk,
                user=request.user,
            )
            resume_number = _sync_comprehension_attempt_position(locked_attempt)
        if resume_number is None:
            return redirect(_comprehension_results_url(attempt))
        return redirect(
            _comprehension_question_url(locked_attempt, resume_number)
        )
    if context.get("redirect_number"):
        return redirect(
            _comprehension_question_url(
                attempt,
                context["redirect_number"],
            )
        )
    if request.method != "POST":
        return render(request, "study/comprehension_question.html", context)

    if context["answer"] is not None:
        return redirect(_comprehension_question_url(attempt, number))
    selected_choice_id = request.POST.get("choice")
    selected_choice_data = next(
        (
            choice
            for choice in context["choices"]
            if str(choice["id"]) == selected_choice_id
        ),
        None,
    )
    selected_choice = (
        ComprehensionChoice.objects.filter(
            pk=selected_choice_id,
            question=context["question_model"],
        ).first()
        if (
            selected_choice_data
            and selected_choice_id
            and selected_choice_id.isdecimal()
        )
        else None
    )
    if selected_choice is None:
        context["answer_error"] = "Choisissez une réponse."
        return render(
            request,
            "study/comprehension_question.html",
            context,
            status=400,
        )

    with transaction.atomic():
        locked_attempt = get_object_or_404(
            ComprehensionAttempt.objects.select_for_update(),
            pk=attempt.pk,
            user=request.user,
        )
        if locked_attempt.status == ComprehensionAttemptStatus.COMPLETED:
            return redirect(
                f"{_comprehension_question_url(locked_attempt, number)}"
                "?correction=1"
            )
        if locked_attempt.status != ComprehensionAttemptStatus.IN_PROGRESS:
            return redirect(_comprehension_test_url(attempt.test))
        ComprehensionAnswer.objects.get_or_create(
            attempt=locked_attempt,
            question=context["question_model"],
            defaults={
                "selected_choice": selected_choice,
                "is_correct": selected_choice_data["is_correct"],
                "question_snapshot": _snapshot_with_selected_choice(
                    context["question"],
                    selected_choice_data["letter"],
                ),
            },
        )
        next_question_number = _sync_comprehension_attempt_position(locked_attempt)
        if next_question_number is None:
            return redirect(
                f"{_comprehension_question_url(locked_attempt, number)}"
                "?correction=1"
            )

    return redirect(_comprehension_question_url(attempt, number))


@require_GET
def comprehension_results(
    request,
    test_slug,
    attempt_id,
    mode=ComprehensionMode.ECRITE,
):
    attempt = _comprehension_attempt(
        request,
        test_slug,
        attempt_id,
        mode,
    )
    if attempt.status == ComprehensionAttemptStatus.IN_PROGRESS:
        return redirect(
            _comprehension_question_url(attempt, attempt.current_question)
        )
    if attempt.status != ComprehensionAttemptStatus.COMPLETED:
        return redirect(_comprehension_test_url(attempt.test))

    submitted_answers = list(
        attempt.answers.select_related(
            "question",
            "selected_choice",
        )
        .prefetch_related("question__choices")
        .order_by("question__number")
    )
    review_items = []
    for answer in submitted_answers:
        question = _comprehension_answer_snapshot(answer)
        choices = question["choices"]
        review_items.append(
            {
                "question": question,
                "choices": choices,
                "answer": answer,
                "selected_letter": (
                    question.get("selected_letter")
                    or answer.selected_choice.letter
                ),
                "correct_choice": next(
                    (choice for choice in choices if choice["is_correct"]),
                    None,
                ),
            }
        )
    return render(
        request,
        "study/comprehension_results.html",
        {
            "attempt": attempt,
            "test": attempt.test,
            "group_number": attempt.test.group_number,
            "review_items": review_items,
            "wrong_count": attempt.total_questions - (attempt.score or 0),
            "is_error_practice": (
                isinstance(attempt.content_snapshot, dict)
                and attempt.content_snapshot.get("practice_mode") == "errors"
            ),
            "has_vocabulary": Phrase.objects.filter(
                is_active=True,
                tier=PhraseTier.COMPREHENSION,
                source_questions__test=attempt.test,
            ).exists(),
        },
    )


# ---------------------------------------------------------------------------
# Notes and highlights
# ---------------------------------------------------------------------------


def _annotation_counts(user):
    rows = (
        Annotation.objects.filter(user=user)
        .values("task_id", "kind", "study_later")
        .annotate(total=Count("id"))
    )
    counts = {}
    for row in rows:
        task_counts = counts.setdefault(
            row["task_id"],
            {"notes": 0, "highlights": 0, "study": 0, "total": 0},
        )
        key = (
            "highlights"
            if row["kind"] == AnnotationKind.HIGHLIGHT
            else "notes"
        )
        task_counts[key] += row["total"]
        if row["study_later"]:
            task_counts["study"] += row["total"]
        task_counts["total"] += row["total"]
    return counts


@require_http_methods(["GET", "POST"])
def notes_overview(request):
    part_slug = (request.GET.get("part") or "").strip()
    task_slug = (request.GET.get("task") or "").strip()
    if bool(part_slug) != bool(task_slug):
        return HttpResponseBadRequest(
            "A notes task filter requires both part and task."
        )
    task = (
        _route_task(part_slug, task_slug)
        if part_slug and task_slug
        else None
    )
    general_only = request.GET.get("scope") == "general"
    return _notes_scope(
        request,
        task,
        aggregate=task is None and not general_only,
    )


def _annotation_scope_url(task=None):
    if task:
        return (
            reverse("study:notes_overview")
            + "?"
            + urlencode(
                {
                    "part": task.part.slug,
                    "task": task.slug,
                }
            )
        )
    return reverse("study:notes_overview") + "?scope=general"


def _annotation_tab_url(task, kind):
    tab = (
        "highlights"
        if kind == AnnotationKind.HIGHLIGHT
        else "notes"
    )
    return f"{_annotation_scope_url(task)}&tab={tab}"


def _highlight_groups(highlights):
    groups = {
        "responses": {
            "key": "responses",
            "title": "Sujets & réponses",
            "description": "Passages retenus dans les fiches et cartes de réponses.",
            "items": [],
        },
        "expressions": {
            "key": "expressions",
            "title": "Expressions",
            "description": "Passages retenus dans les fiches et cartes d'expressions.",
            "items": [],
        },
    }
    for highlight in highlights:
        source_key = highlight.source_key or ""
        if source_key.startswith("phrase:"):
            group_key = "expressions"
        elif source_key.startswith("response:"):
            group_key = "responses"
        else:
            source = urlsplit(highlight.source_path or "")
            source_query = parse_qs(source.query)
            is_expression = (
                source.path.endswith("/expressions/")
                or source.path == reverse("study:vocabulary")
                or source_query.get("kind") == ["phrase"]
            )
            group_key = "expressions" if is_expression else "responses"
        groups[group_key]["items"].append(highlight)
    return [groups["responses"], groups["expressions"]]


def _notes_scope(request, task=None, *, aggregate=False):
    annotations = Annotation.objects.filter(user=request.user)
    if not aggregate:
        annotations = annotations.filter(task=task)
    query = (request.GET.get("q") or "").strip()
    if query:
        annotations = annotations.filter(
            Q(title__icontains=query)
            | Q(body__icontains=query)
            | Q(quote__icontains=query)
            | Q(source_title__icontains=query)
        )
    active_tab = (
        request.GET.get("tab")
        if request.GET.get("tab") in {"notes", "highlights"}
        else "notes"
    )
    if request.method == "POST":
        active_tab = "notes"
        instance = Annotation(
            user=request.user,
            task=task,
            kind=AnnotationKind.NOTE,
        )
        form = NoteForm(request.POST, instance=instance)
        if form.is_valid():
            note = form.save()
            return redirect(
                _annotation_tab_url(task, AnnotationKind.NOTE)
                + f"#note-{note.id}"
            )
    else:
        form = NoteForm()
    notes = list(annotations.filter(kind=AnnotationKind.NOTE))
    highlights = list(annotations.filter(kind=AnnotationKind.HIGHLIGHT))
    counts = _annotation_counts(request.user)
    task_filters = []
    for filter_task in Task.objects.select_related("part").filter(
        Q(is_active=True) | Q(annotations__user=request.user)
    ).distinct().order_by("part__order", "order"):
        task_filters.append(
            {
                "task": filter_task,
                "count": counts.get(filter_task.pk, {}).get("total", 0),
            }
        )
    if task:
        scope_query = urlencode(
            {"part": task.part.slug, "task": task.slug}
        )
    elif not aggregate:
        scope_query = "scope=general"
    else:
        scope_query = ""
    return render(
        request,
        "study/notes_list.html",
        {
            "part": task.part if task else None,
            "task": task,
            "scope_title": (
                task.name
                if task
                else (
                    "Toutes mes notes"
                    if aggregate
                    else "Notes générales"
                )
            ),
            "notes": notes,
            "highlights": highlights,
            "highlight_groups": _highlight_groups(highlights),
            "active_tab": active_tab,
            "study_count": annotations.filter(study_later=True).count(),
            "form": form,
            "aggregate": aggregate,
            "query": query,
            "task_filters": task_filters,
            "general_count": counts.get(None, {}).get("total", 0),
            "scope_query": scope_query,
            "tab_url_prefix": (
                f"?{scope_query}&" if scope_query else "?"
            ),
        },
    )


def task_notes(request, part_slug, task_slug):
    return _notes_scope(request, _route_task(part_slug, task_slug))


def general_notes(request):
    return _notes_scope(request)


def _annotation_anchor(annotation):
    prefix = (
        "highlight"
        if annotation.kind == AnnotationKind.HIGHLIGHT
        else "note"
    )
    return f"{prefix}-{annotation.id}"


@require_GET
def annotation_search(request):
    query = (request.GET.get("q") or "").strip()
    kind = (request.GET.get("kind") or "").strip()
    study_only = request.GET.get("study") == "1"
    task_id = (request.GET.get("task") or "").strip()

    annotations = Annotation.objects.filter(user=request.user).select_related(
        "task__part"
    )
    if query:
        annotations = annotations.filter(
            Q(title__icontains=query)
            | Q(body__icontains=query)
            | Q(quote__icontains=query)
            | Q(source_title__icontains=query)
        )
    if kind in AnnotationKind.values:
        annotations = annotations.filter(kind=kind)
    else:
        kind = ""
    if study_only:
        annotations = annotations.filter(study_later=True)
    if task_id.isdigit():
        task_id = int(task_id)
        annotations = annotations.filter(task_id=task_id)
    else:
        task_id = None

    result_count = annotations.count()
    results = list(annotations[:100])
    for annotation in results:
        annotation.notes_url = (
            _annotation_tab_url(annotation.task, annotation.kind)
            + "#"
            + _annotation_anchor(annotation)
        )
    task_options = (
        Task.objects.filter(annotations__user=request.user)
        .select_related("part")
        .distinct()
        .order_by("part__order", "order", "name")
    )
    return render(
        request,
        "study/annotation_search.html",
        {
            "query": query,
            "selected_kind": kind,
            "study_only": study_only,
            "selected_task_id": task_id,
            "task_options": task_options,
            "results": results,
            "result_count": result_count,
            "result_limit_reached": result_count > len(results),
        },
    )


@require_GET
def annotation_study(request, part_slug=None, task_slug=None):
    task = (
        _route_task(part_slug, task_slug)
        if part_slug is not None and task_slug is not None
        else None
    )
    general_only = request.GET.get("scope") == "general"
    annotations = Annotation.objects.filter(
        user=request.user,
        study_later=True,
    ).select_related("task__part")
    if task:
        annotations = annotations.filter(task=task)
    elif general_only:
        annotations = annotations.filter(task__isnull=True)
    items = list(annotations.order_by("-updated_at", "-id"))
    return render(
        request,
        "study/annotation_study.html",
        {
            "part": task.part if task else None,
            "task": task,
            "items": items,
            "scope_title": (
                task.name
                if task
                else (
                    "Notes générales"
                    if general_only
                    else "Toutes mes notes"
                )
            ),
            "back_url": (
                _annotation_scope_url(task)
                if task
                else (
                    reverse("study:notes_overview") + "?scope=general"
                    if general_only
                    else reverse("study:notes_overview")
                )
            ),
        },
    )


def _safe_source_path(value):
    value = (value or "").strip()
    parsed = urlsplit(value)
    if (
        not value
        or parsed.scheme
        or parsed.netloc
        or not parsed.path.startswith("/")
    ):
        raise ValueError("Invalid source path.")
    path = parsed.path
    if parsed.query:
        path += "?" + parsed.query
    return path[:500]


def _annotation_task(value):
    if not value:
        return None
    try:
        task_id = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid task.") from error
    return get_object_or_404(
        Task.objects.select_related("part"),
        pk=task_id,
        is_active=True,
    )


def _annotation_source_key(value):
    value = (value or "").strip()
    if not ANNOTATION_SOURCE_KEY_RE.fullmatch(value):
        raise ValueError("Invalid annotation source key.")
    return value


def _annotation_overlap_ids(value):
    if value is None:
        return None
    if not value:
        return []
    parts = value.split(",")
    if len(parts) > 100:
        raise ValueError("Too many overlapping highlights.")
    ids = [int(part) for part in parts]
    if any(pk <= 0 for pk in ids):
        raise ValueError("Invalid overlapping highlight.")
    return list(dict.fromkeys(ids))


def _annotation_overlap_revisions(value):
    if value is None:
        return None
    revisions = json.loads(value)
    if not isinstance(revisions, dict) or len(revisions) > 100:
        raise ValueError("Invalid highlight revisions.")
    parsed = {}
    for raw_id, revision in revisions.items():
        pk = int(raw_id)
        if pk <= 0 or not isinstance(revision, str) or len(revision) > 64:
            raise ValueError("Invalid highlight revision.")
        parsed[pk] = revision
    return parsed


def _annotation_source_scope(source_path):
    base_path = source_path.split("?", 1)[0]
    if re.fullmatch(r"/response/\d+/", base_path):
        return (
            base_path,
            Q(source_path=base_path)
            | Q(source_path__startswith=f"{base_path}?"),
        )
    return source_path, Q(source_path=source_path)


@require_GET
def annotations_for_source(request):
    try:
        source_path = _safe_source_path(request.GET.get("source_path"))
    except ValueError:
        return HttpResponseBadRequest("Invalid source path.")
    _, source_filter = _annotation_source_scope(source_path)
    highlights = list(
        Annotation.objects.filter(
            source_filter,
            user=request.user,
            kind=AnnotationKind.HIGHLIGHT,
        ).values(
            "id",
            "quote",
            "source_key",
            "start_offset",
            "end_offset",
            "prefix",
            "suffix",
            "updated_at",
        )
    )
    for highlight in highlights:
        highlight["revision"] = highlight.pop("updated_at").isoformat()
        highlight["delete_url"] = reverse(
            "study:annotation_delete",
            args=[highlight["id"]],
        )
    return JsonResponse({"highlights": highlights})


@require_POST
def annotation_create(request):
    kind = (request.POST.get("kind") or "").strip()
    if kind not in AnnotationKind.values:
        return HttpResponseBadRequest("Invalid annotation kind.")
    quote = request.POST.get("quote") or ""
    body = (request.POST.get("body") or "").strip()
    if not quote.strip():
        return JsonResponse(
            {"error": "Sélectionnez du texte avant de continuer."},
            status=400,
        )
    if len(quote) > MAX_ANNOTATION_QUOTE_LENGTH:
        return JsonResponse(
            {"error": "La sélection est trop longue."},
            status=400,
        )
    if len(body) > MAX_ANNOTATION_BODY_LENGTH:
        return JsonResponse(
            {"error": "La note est trop longue."},
            status=400,
        )
    try:
        task = _annotation_task(request.POST.get("task_id"))
        source_path = _safe_source_path(request.POST.get("source_path"))
        source_path, source_filter = _annotation_source_scope(source_path)
        source_key = _annotation_source_key(request.POST.get("source_key"))
        overlap_ids = _annotation_overlap_ids(request.POST.get("overlap_ids"))
        overlap_revisions = _annotation_overlap_revisions(
            request.POST.get("overlap_revisions")
        )
        start_offset = int(request.POST.get("start_offset", ""))
        end_offset = int(request.POST.get("end_offset", ""))
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Invalid annotation data.")
    if start_offset < 0 or end_offset <= start_offset:
        return HttpResponseBadRequest("Invalid annotation offsets.")
    if overlap_ids and (
        overlap_revisions is None
        or set(overlap_ids) != set(overlap_revisions)
    ):
        return HttpResponseBadRequest("Invalid highlight revisions.")

    values = {
        "task": task,
        "quote": quote,
        "source_title": (request.POST.get("source_title") or "")[:300],
        "prefix": (request.POST.get("prefix") or "")[-160:],
        "suffix": (request.POST.get("suffix") or "")[:160],
        "body": body,
    }
    removed_ids = []
    try:
        if kind == AnnotationKind.HIGHLIGHT:
            with transaction.atomic():
                candidates = Annotation.objects.select_for_update().filter(
                    source_filter,
                    user=request.user,
                    kind=kind,
                    source_key=source_key,
                )
                if overlap_ids is None:
                    candidates = candidates.filter(
                        start_offset__lt=end_offset,
                        end_offset__gt=start_offset,
                    )
                    overlapping = list(
                        candidates.order_by("-updated_at", "-id")
                    )
                else:
                    overlapping = list(
                        candidates.filter(id__in=overlap_ids).order_by(
                            "-updated_at",
                            "-id",
                        )
                    )
                    if overlap_revisions is not None and (
                        len(overlapping) != len(overlap_ids)
                        or any(
                            item.updated_at.isoformat()
                            != overlap_revisions[item.id]
                            for item in overlapping
                        )
                    ):
                        return JsonResponse(
                            {
                                "error": (
                                    "Ce surlignage a changé dans un autre onglet. "
                                    "Réessayez avec la version actualisée."
                                )
                            },
                            status=409,
                        )
                    exact_retry = (
                        candidates.filter(
                            start_offset=start_offset,
                            end_offset=end_offset,
                        )
                        .exclude(id__in=overlap_ids)
                        .first()
                    )
                    if exact_retry:
                        if exact_retry.quote != quote:
                            return JsonResponse(
                                {
                                    "error": (
                                        "Les surlignages de cette page ont changé. "
                                        "Supprimez le passage en conflit avant de "
                                        "réessayer."
                                    )
                                },
                                status=409,
                            )
                        overlapping.append(exact_retry)
                annotation = next(
                    (
                        item
                        for item in overlapping
                        if item.start_offset == start_offset
                        and item.end_offset == end_offset
                    ),
                    overlapping[0] if overlapping else None,
                )
                created = annotation is None
                if created:
                    annotation = Annotation(
                        user=request.user,
                        kind=kind,
                        source_path=source_path,
                        source_key=source_key,
                        start_offset=start_offset,
                        end_offset=end_offset,
                        **values,
                    )
                else:
                    annotation.task = task
                    annotation.quote = quote
                    annotation.source_title = values["source_title"]
                    annotation.prefix = values["prefix"]
                    annotation.suffix = values["suffix"]
                    annotation.body = body
                    annotation.start_offset = start_offset
                    annotation.end_offset = end_offset
                    annotation.study_later = any(
                        item.study_later for item in overlapping
                    )
                annotation.full_clean(validate_constraints=False)
                try:
                    with transaction.atomic():
                        annotation.save()
                except IntegrityError:
                    concurrent = candidates.filter(
                        start_offset=start_offset,
                        end_offset=end_offset,
                    ).first()
                    if (
                        not created
                        or overlapping
                        or concurrent is None
                        or concurrent.quote != quote
                    ):
                        return JsonResponse(
                            {
                                "error": (
                                    "Les surlignages de cette page ont changé. "
                                    "Actualisez la page puis réessayez."
                                )
                            },
                            status=409,
                        )
                    annotation = concurrent
                    created = False
                removed_ids = [
                    item.id for item in overlapping if item.id != annotation.id
                ]
                if removed_ids:
                    Annotation.objects.filter(
                        user=request.user,
                        id__in=removed_ids,
                    ).delete()
        else:
            annotation = Annotation(
                user=request.user,
                kind=kind,
                source_path=source_path,
                source_key=source_key,
                start_offset=start_offset,
                end_offset=end_offset,
                **values,
            )
            annotation.full_clean()
            annotation.save()
            created = True
    except ValidationError as error:
        return JsonResponse(
            {"error": " ".join(error.messages)},
            status=400,
        )
    return JsonResponse(
        {
            "id": annotation.id,
            "created": created,
            "removed_ids": removed_ids,
            "revision": annotation.updated_at.isoformat(),
            "delete_url": reverse(
                "study:annotation_delete",
                args=[annotation.id],
            ),
            "notes_url": (
                _annotation_tab_url(task, annotation.kind)
                + "#"
                + _annotation_anchor(annotation)
            ),
        },
        status=201 if created else 200,
    )


def _annotation_redirect(request, annotation):
    candidate = request.POST.get("next")
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return _annotation_tab_url(annotation.task, annotation.kind)


@require_POST
def annotation_update(request, pk):
    annotation = get_object_or_404(
        Annotation,
        pk=pk,
        user=request.user,
        kind=AnnotationKind.NOTE,
    )
    form = NoteForm(request.POST, instance=annotation)
    if not form.is_valid():
        return JsonResponse(
            {"error": "Corrigez la note avant de l'enregistrer."},
            status=400,
        )
    form.save()
    return redirect(_annotation_redirect(request, annotation) + f"#note-{pk}")


@require_POST
def annotation_study_toggle(request, pk):
    annotation = get_object_or_404(
        Annotation,
        pk=pk,
        user=request.user,
    )
    value = request.POST.get("study_later")
    if value not in {"0", "1"}:
        return HttpResponseBadRequest("Invalid study status.")
    annotation.study_later = value == "1"
    annotation.save(update_fields=["study_later", "updated_at"])
    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse(
            {
                "study_later": annotation.study_later,
                "id": annotation.pk,
            }
        )
    return redirect(_annotation_redirect(request, annotation))


@require_POST
def annotation_delete(request, pk):
    annotation = get_object_or_404(
        Annotation,
        pk=pk,
        user=request.user,
    )
    target = _annotation_redirect(request, annotation)
    annotation.delete()
    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse({"deleted": True})
    return redirect(target)


def part_detail(request, part_slug):
    part = get_object_or_404(
        ExamPart.objects.filter(is_active=True).prefetch_related(
            Prefetch("tasks", queryset=Task.objects.filter(is_active=True))
        ),
        slug=part_slug,
    )
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
        is_active=True,
        part__is_active=True,
    )
    now = timezone.now()
    if not task.available:
        return render(
            request,
            "study/coming_soon.html",
            {"part": task.part, "task": task},
        )

    themes = []
    for theme in Theme.objects.filter(task=task, is_active=True):
        themes.append(
            {
                "theme": theme,
                "stats": _spine_theme_stats(theme, now, request.user),
                "prompt_count": Prompt.objects.filter(
                    theme=theme,
                    is_active=True,
                ).count(),
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
            {**scope, "kind": "spine"},
            now,
            user=request.user,
        ),
        "prompt_count": Prompt.objects.filter(
            theme__task=task,
            is_active=True,
        ).count(),
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
    """Use an explicit request scope, otherwise resume the saved one."""
    data = request.POST if request.method == "POST" else request.GET
    if request.method == "GET" and data.get("reset") == "1":
        return {"kind": "spine"}, True
    scope = scope_from_request(request)
    explicit = any(key in data for key in REVIEW_SCOPE_KEYS)
    if explicit:
        return scope, True
    saved = session.scope
    if isinstance(saved, dict) and (saved or session.current_card_id):
        return saved, False
    return {"kind": "spine"}, True


def _save_review_session(
    session: ReviewSession,
    scope: dict,
    card=None,
    *,
    clear_pass=False,
    rotate_token=False,
) -> str:
    scope_changed = session.scope != scope
    same_focused_pass = (
        session.scope == scope
        and scope.get("kind") in FOCUSED_REVIEW_KINDS
    )
    same_presentation = (
        card is not None
        and session.scope == scope
        and session.current_card_id == card.id
        and session.presentation_token
    )
    if clear_pass or not same_focused_pass:
        session.revisit_seen_card_ids = []
    if clear_pass or scope_changed:
        session.previous_card = None
        session.previous_review = None
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
            "previous_card",
            "previous_review",
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
            or (
                scope.get("kind") in FOCUSED_REVIEW_KINDS
                and not session.current_card_id
            )
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
        "is_weak": scope.get("kind") == "weak",
        "is_focused_drill": scope.get("kind") in FOCUSED_REVIEW_KINDS,
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
        if (
            session.scope == scope
            and scope.get("kind") in FOCUSED_REVIEW_KINDS
        )
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
        _save_review_session(session, scope)
        if scope.get("kind") in FOCUSED_REVIEW_KINDS and seen_card_ids:
            counts["due_reviews"] = 0
            counts["review_due"] = 0
            counts["total_due"] = 0
        return {
            "done": True,
            "counts": counts,
            "revisit_count": counts["revisit_total"],
            "can_previous": bool(session.previous_card_id),
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
        "annotation_source_key": payload["annotation_source_key"],
        "presentation_token": presentation_token,
        "counts": counts,
        "revisit_count": counts["revisit_total"],
        "can_previous": bool(session.previous_card_id),
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
        "annotation_source_key": payload["annotation_source_key"],
        "presentation_token": presentation_token,
        "counts": counts,
        "revisit_count": counts["revisit_total"],
        "can_previous": bool(session.previous_card_id),
    }


@require_GET
def review_hub(request, part_slug, task_slug):
    task = _route_task(part_slug, task_slug)
    part = task.part
    now = timezone.now()
    scope = {"part": part.slug, "task": task.slug}
    response_scope = {**scope, "kind": "spine"}
    cards = queue_module.scoped_cards(
        response_scope,
        user=request.user,
    )
    response_stats = deck_stats(
        cards,
        now,
    )
    response_counts = queue_module.queue_counts(
        response_scope,
        now,
        user=request.user,
    )
    weak_counts = queue_module.queue_counts(
        {**scope, "kind": "weak", "content": "spine"},
        now,
        user=request.user,
    )
    session = ReviewSession.load(request.user)
    saved_scope = session.scope if isinstance(session.scope, dict) else {}
    can_resume = bool(
        session.current_card_id
        and saved_scope.get("part") == part.slug
        and saved_scope.get("task") == task.slug
        and (
            saved_scope.get("kind") == "spine"
            or saved_scope.get("content") == "spine"
        )
    )
    themes = []
    for theme in Theme.objects.filter(task=task, is_active=True):
        theme_scope = {
            **response_scope,
            "theme": theme.slug,
        }
        themes.append(
            {
                "theme": theme,
                "stats": deck_stats(
                    queue_module.scoped_cards(
                        theme_scope,
                        user=request.user,
                    ),
                    now,
                ),
                "counts": queue_module.queue_counts(
                    theme_scope,
                    now,
                    user=request.user,
                ),
            }
        )
    return render(
        request,
        "study/review_hub.html",
        {
            "part": part,
            "task": task,
            "counts": response_counts,
            "response_stats": response_stats,
            "response_due": response_counts["total_due"],
            "revisit_count": queue_module.scoped_cards(
                {**scope, "kind": "revisit", "content": "spine"},
                user=request.user,
            ).count(),
            "weak_count": weak_counts["weak_total"],
            "can_resume": can_resume,
            "themes": themes,
        },
    )


@require_GET
def review_next(request):
    with transaction.atomic():
        session = _locked_review_session(request.user)
        scope, _ = _resolved_review_scope(request, session)
        state = _queue_state_locked(scope, request, session)
    state["can_undo"] = bool(
        session.scope == scope and session.previous_review_id
    )
    return JsonResponse(state)


@require_GET
def review_previous(request):
    with transaction.atomic():
        session = _locked_review_session(request.user)
        scope, _ = _resolved_review_scope(request, session)
        if session.scope != scope or not session.previous_card_id:
            return JsonResponse(
                {"error": "Aucune carte précédente dans cette session."},
                status=404,
            )
        card = get_object_or_404(
            Card,
            pk=session.previous_card_id,
            user=request.user,
        )
        payload = card_payload(card)
        front = render_to_string(
            "study/partials/card_front.html",
            payload,
            request,
        )
        back = render_to_string(
            "study/partials/card_back.html",
            payload,
            request,
        )
    return JsonResponse(
        {
            "card_id": card.id,
            "front_html": front,
            "back_html": back,
            "annotation_source_key": payload["annotation_source_key"],
        }
    )


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

    presentation_token = request.POST.get("presentation_token", "")
    with transaction.atomic():
        session = _locked_review_session(request.user)
        scope, _ = _resolved_review_scope(request, session)
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
            conflict = {
                "error": "Cette carte a déjà été traitée ou remplacée.",
                "code": "stale_presentation",
                "current_card_id": session.current_card_id,
            }
            if (
                session.current_card_id == card_id
                and session.scope == scope
                and session.presentation_token
            ):
                conflict["presentation_token"] = session.presentation_token
            return JsonResponse(
                conflict,
                status=409,
            )

        card = get_object_or_404(
            Card.objects.select_for_update(),
            pk=card_id,
            user=request.user,
        )
        if scope.get("kind") in FOCUSED_REVIEW_KINDS:
            seen = list(session.revisit_seen_card_ids or [])
            if card.id not in seen:
                seen.append(card.id)
            session.revisit_seen_card_ids = seen
        session.current_card = None
        session.presentation_token = ""
        _, review_log = apply_review(
            card,
            rating,
            elapsed_ms=elapsed_ms,
            return_log=True,
        )
        session.previous_card = card
        session.previous_review = review_log
        session.save(
            update_fields=[
                "current_card",
                "previous_card",
                "previous_review",
                "revisit_seen_card_ids",
                "presentation_token",
                "updated_at",
            ]
        )
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
    """Revert this session's exact previous review and re-present its card."""
    with transaction.atomic():
        session = _locked_review_session(request.user)
        scope, _ = _resolved_review_scope(request, session)
        if session.scope != scope:
            return JsonResponse(
                {"error": "Cette session de révision a changé."},
                status=409,
            )
        card = None
        if session.previous_review_id and session.previous_card_id:
            card = undo_last(
                request.user,
                log_id=session.previous_review_id,
                card_id=session.previous_card_id,
            )
        if card is None:
            state = _queue_state_locked(scope, request, session)
            state["can_undo"] = False
            state["undone"] = False
            return JsonResponse(state)
        session.previous_card = None
        session.previous_review = None
        state = _card_state_locked(card, scope, request, session)
    state["can_undo"] = False
    state["undone"] = True
    return JsonResponse(state)


# ---------------------------------------------------------------------------
# Revisit list
# ---------------------------------------------------------------------------


def revisit_list(request, part_slug=None, task_slug=None):
    """Persistent list of cards marked with the Revisit review action."""
    task = (
        _route_task(part_slug, task_slug)
        if part_slug and task_slug
        else None
    )
    scope = _task_scope(task) if task else {}
    content = (request.GET.get("content") or "").strip()
    if content in {"spine", "vocabulary"}:
        scope["content"] = content
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
    if content:
        redirect_url += "?" + urlencode({"content": content})
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
        .prefetch_related(
            "response__prompts",
            "phrase__source_prompts__theme",
            "phrase__source_questions__test",
        )
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
            phrase = card.phrase
            if phrase.tier == PhraseTier.COMPREHENSION:
                source_question = next(
                    iter(phrase.source_questions.all()),
                    None,
                )
                item_url = (
                    reverse("study:vocabulary")
                    + "?"
                    + urlencode(
                        {"test": source_question.test.slug}
                    )
                    + f"#phrase-{phrase.phrase_id}"
                    if source_question
                    else reverse("study:vocabulary")
                )
            elif phrase.tier == PhraseTier.SUBJECT:
                source_prompt = next(
                    iter(phrase.source_prompts.all()),
                    None,
                )
                item_url = (
                    reverse(
                        "study:response_detail",
                        args=[source_prompt.response_id],
                    )
                    + "?"
                    + urlencode({"prompt": source_prompt.pk})
                    + "#subject-vocabulary"
                    if source_prompt
                    else reverse("study:vocabulary")
                )
            else:
                item_url = (
                    reverse("study:vocabulary")
                    + "?"
                    + urlencode({"category": phrase.category.slug})
                    + f"#phrase-{phrase.phrase_id}"
                )
            items.append(
                {
                    "card": card,
                    "kind": "Vocabulaire",
                    "title": phrase.expression,
                    "meta": phrase.english_cue,
                    "url": item_url,
                }
            )
    response_items = [item for item in items if item["kind"] == "Réponse"]
    phrase_items = [item for item in items if item["kind"] == "Vocabulaire"]
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
            "content": content,
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
        task_qs = Task.objects.select_related("part").filter(
            slug=task_slug,
            is_active=True,
            part__is_active=True,
        )
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
    for part in ExamPart.objects.filter(is_active=True).prefetch_related(
        Prefetch("tasks", queryset=Task.objects.filter(is_active=True))
    ):
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

    theme_qs = Theme.objects.select_related("task__part").filter(is_active=True)
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
                "prompt_count": Prompt.objects.filter(
                    theme=theme,
                    is_active=True,
                ).count(),
            }
        )
    family_qs = Family.objects.filter(is_active=True)
    if scope.get("task"):
        family_qs = family_qs.filter(
            prompts__is_active=True,
            prompts__theme__task__slug=scope["task"],
            prompts__theme__task__part__slug=scope["part"],
        )
    elif scope.get("part"):
        family_qs = family_qs.filter(
            prompts__is_active=True,
            prompts__theme__task__part__slug=scope["part"]
        )
    families = family_qs.annotate(
        n=Count(
            "prompts",
            filter=Q(prompts__is_active=True),
            distinct=True,
        )
    ).order_by("order")
    prompt_qs = Prompt.objects.filter(is_active=True)
    response_qs = Response.objects.filter(is_active=True)
    phrase_qs = Phrase.objects.filter(is_active=True)
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
        Theme.objects.select_related("task__part"),
        slug=slug,
        is_active=True,
    )
    now = timezone.now()
    prompts = (
        Prompt.objects.filter(theme=theme, is_active=True)
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
    family = get_object_or_404(Family, slug=slug, is_active=True)
    task = (
        _route_task(part_slug, task_slug)
        if part_slug is not None and task_slug is not None
        else Task.objects.select_related("part")
        .filter(
            is_active=True,
            themes__is_active=True,
            themes__prompts__is_active=True,
            themes__prompts__family=family,
        )
        .distinct()
        .order_by("part__order", "order")
        .first()
    )
    prompt_qs = Prompt.objects.filter(family=family, is_active=True)
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
    response_content = effective_response(response, request.user)
    prompts = list(
        response.prompts.filter(
            is_active=True,
            theme__is_active=True,
        ).select_related(
            "theme__task__part",
            "family",
        )
    )
    prompt_id = (request.GET.get("prompt") or "").strip()
    if prompt_id:
        if not prompt_id.isdigit():
            return HttpResponseBadRequest("Invalid prompt.")
        selected_prompt = next(
            (prompt for prompt in prompts if prompt.pk == int(prompt_id)),
            None,
        )
        if selected_prompt is None:
            return HttpResponseBadRequest(
                "Prompt does not belong to this response."
            )
    else:
        selected_prompt = next(
            (prompt for prompt in prompts if prompt.is_canonical),
            prompts[0] if prompts else None,
        )
    if selected_prompt is None:
        return HttpResponseBadRequest("Response has no active prompt.")

    navigation_prompts = Prompt.objects.filter(
        is_active=True,
        theme__is_active=True,
        theme_id=selected_prompt.theme_id,
    ).select_related("theme")
    navigation_prompts = list(
        navigation_prompts.order_by("number", "pk")
    )
    prompt_index = next(
        index
        for index, prompt in enumerate(navigation_prompts)
        if prompt.pk == selected_prompt.pk
    )
    previous_prompt = (
        navigation_prompts[prompt_index - 1] if prompt_index > 0 else None
    )
    next_prompt = (
        navigation_prompts[prompt_index + 1]
        if prompt_index + 1 < len(navigation_prompts)
        else None
    )

    card = Card.objects.filter(
        user=request.user,
        card_type=CardType.SPINE,
        response=response,
    ).first()
    related_phrases = (
        Phrase.objects.filter(
            source_prompts__response=response,
            is_active=True,
        )
        .exclude(tier=PhraseTier.SUBJECT)
        .distinct()
        .select_related("category")
    )
    phrase_batches = _review_batches(
        {"kind": "phrase", "response": str(response.pk)},
        request.user,
    )
    subject_vocabulary = (
        Phrase.objects.filter(
            source_prompts__response=response,
            is_active=True,
            tier=PhraseTier.SUBJECT,
        )
        .distinct()
        .select_related("category")
        .order_by("lot_order", "phrase_id")
    )
    vocabulary_count = subject_vocabulary.count()
    vocabulary_batches = _review_batches(
        {"kind": "vocab", "response": str(response.pk)},
        request.user,
    )
    vocabulary_lot_labels = (
        "Mots clés",
        "Collocations",
        "Expressions et idiomes",
        "Tournures pour l'oral",
        "Phrases modèles",
    )
    for batch, label in zip(vocabulary_batches, vocabulary_lot_labels):
        batch["label"] = label
    first_vocabulary_batch = next(
        (batch for batch in vocabulary_batches if batch["can_review"]),
        vocabulary_batches[0] if vocabulary_batches else None,
    )
    return render(
        request,
        "study/response_detail.html",
        {
            "response": response,
            "selected_prompt": selected_prompt,
            "previous_prompt": previous_prompt,
            "next_prompt": next_prompt,
            "prompt_position": prompt_index + 1,
            "prompt_total": len(navigation_prompts),
            "task": selected_prompt.theme.task,
            "part": (
                selected_prompt.theme.task.part
                if selected_prompt.theme.task
                else None
            ),
            "response_content": response_content,
            "arguments": response_content.arguments,
            "prompts": prompts,
            "card": card,
            "related_phrases": related_phrases,
            "phrase_batches": phrase_batches,
            "subject_vocabulary": list(subject_vocabulary[:10]),
            "vocabulary_count": vocabulary_count,
            "vocabulary_batches": vocabulary_batches,
            "vocabulary_review_url": (
                first_vocabulary_batch["review_url"]
                if first_vocabulary_batch
                else None
            ),
            "can_edit_response": response.prompts.filter(
                is_active=True,
                theme__task__slug="tache-3",
                theme__task__part__slug="eo",
            ).exists(),
            "personal_saved": request.GET.get("saved") == "1",
            "personal_reset": request.GET.get("reset") == "1",
        },
    )


def edit_response(request, pk):
    response = get_object_or_404(
        Response.objects.filter(
            is_active=True,
            prompts__is_active=True,
            prompts__theme__task__slug="tache-3",
            prompts__theme__task__part__slug="eo",
        )
        .select_related("theme__task__part", "family")
        .prefetch_related("arguments")
        .distinct(),
        pk=pk,
    )
    personal = PersonalResponse.objects.filter(
        user=request.user,
        response=response,
    ).first()
    if request.method == "POST" and request.POST.get("action") == "reset":
        if personal is not None:
            personal.delete()
        return redirect(
            reverse("study:response_detail", args=[response.pk]) + "?reset=1"
        )

    form = PersonalResponseForm(
        response,
        request.user,
        request.POST or None,
    )
    if request.method == "POST" and form.is_valid():
        PersonalResponse.objects.update_or_create(
            user=request.user,
            response=response,
            defaults=form.personal_defaults(),
        )
        return redirect(
            reverse("study:response_detail", args=[response.pk]) + "?saved=1"
        )

    argument_fields = []
    for order in form.argument_orders:
        argument_fields.append(
            {
                "order": order,
                "fields": [
                    form[f"argument_{order}_{key}"]
                    for key, _label, _rows in form.argument_parts
                ],
            }
        )
    return render(
        request,
        "study/response_edit.html",
        {
            "response": response,
            "task": response.theme.task,
            "part": response.theme.task.part,
            "form": form,
            "argument_fields": argument_fields,
            "has_personal_response": personal is not None,
        },
    )


def phrases(request, part_slug=None, task_slug=None):
    part_slug = part_slug or (request.GET.get("part") or "").strip()
    task_slug = task_slug or (request.GET.get("task") or "").strip()
    vocabulary_domain = (request.GET.get("domain") or "").strip()
    vocabulary_mode = (request.GET.get("mode") or "").strip()
    if bool(part_slug) != bool(task_slug):
        return HttpResponseBadRequest("Choose both a part and a task.")
    if vocabulary_domain not in {"", "comprehension"}:
        return HttpResponseBadRequest("Unknown vocabulary domain.")
    if vocabulary_mode not in {"", "ce"}:
        return HttpResponseBadRequest("Unknown vocabulary mode.")
    if vocabulary_mode and vocabulary_domain != "comprehension":
        return HttpResponseBadRequest(
            "A vocabulary mode requires a domain."
        )
    task = (
        _route_task(part_slug, task_slug)
        if part_slug and task_slug
        else None
    )
    if task and not task.available:
        return render(
            request,
            "study/coming_soon.html",
            {"part": task.part, "task": task},
        )
    functional_names = FUNCTIONAL_PHRASE_CATEGORY_NAMES
    category_descriptions = {
        "Structurer et prendre position": (
            "Reformuler le sujet, annoncer ton avis et guider clairement "
            "l'examinateur."
        ),
        "Nuancer et comparer": (
            "Éviter les réponses trop absolues et confronter plusieurs "
            "points de vue."
        ),
        "Cause, conséquence et évaluation": (
            "Expliquer pourquoi, montrer les effets et porter un jugement "
            "précis."
        ),
        "Schémas d'argumentation": (
            "Construire des arguments complets avec des tournures "
            "réutilisables."
        ),
    }
    category_slug = request.GET.get("category", "").strip()
    test_slug = request.GET.get("test", "").strip()
    if vocabulary_domain and (
        task or category_slug or test_slug
    ):
        return HttpResponseBadRequest(
            "Choose either a vocabulary domain or a deck."
        )
    if category_slug and test_slug:
        return HttpResponseBadRequest(
            "Choose either a category or a comprehension test."
        )
    selected = None
    selected_test = None
    all_phrases = (
        Phrase.objects.filter(
            is_active=True,
            tier=PhraseTier.SHARED,
        )
        .select_related("category")
        .prefetch_related("source_prompts__theme")
    )
    if task:
        all_phrases = all_phrases.filter(
            source_prompts__theme__task=task
        ).distinct()
    categories = list(
        PhraseCategory.objects.filter(
            is_active=True,
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
            category.phrase_count + queue_module.PHRASE_BATCH_SIZE - 1
        ) // queue_module.PHRASE_BATCH_SIZE
        category.is_functional = category.name in functional_names
        category.learning_description = category_descriptions.get(
            category.name,
            "Expressions réutilisables dans plusieurs réponses.",
        )

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
    elif test_slug:
        selected_test = get_object_or_404(
            ComprehensionTest,
            slug=test_slug,
            mode=ComprehensionMode.ECRITE,
            is_active=True,
            is_published=True,
        )
        phrase_qs = (
            Phrase.objects.filter(
                is_active=True,
                tier=PhraseTier.COMPREHENSION,
                source_questions__test=selected_test,
                source_questions__is_active=True,
            )
            .select_related("category")
            .prefetch_related("source_questions__test")
            .distinct()
            .order_by("category__order", "lot_order", "phrase_id")
        )

    grouped = []
    review_batches = []
    first_review_batch = None
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
        first_review_batch = next(
            (batch for batch in review_batches if batch["can_review"]),
            None,
        )
    elif selected_test:
        for category in PhraseCategory.objects.filter(
            phrases__in=phrase_qs,
            is_active=True,
        ).distinct().order_by("order"):
            grouped.append(
                {
                    "category": category,
                    "phrases": list(phrase_qs.filter(category=category)),
                }
            )
        review_batches = _review_batches(
            {"kind": "vocab", "test": selected_test.slug},
            request.user,
        )
        first_review_batch = next(
            (batch for batch in review_batches if batch["can_review"]),
            review_batches[0] if review_batches else None,
        )

    functional_categories = [
        category
        for category in categories
        if category.is_functional
    ]
    comprehension_directory = vocabulary_domain == "comprehension"
    vocabulary_landing = not (
        selected
        or selected_test
        or task
        or comprehension_directory
    )
    subject_theme_groups = []
    subject_prompt_count = 0
    subject_response_count = 0
    subject_vocabulary_count = 0
    comprehension_decks = []
    comprehension_vocabulary_count = 0
    if not selected and not selected_test:
        subject_prompts = (
            Prompt.objects.filter(
                is_active=True,
                response__is_active=True,
                theme__is_active=True,
            )
            .select_related("theme", "family", "response")
            .annotate(
                vocabulary_count=Count(
                    "phrases",
                    filter=Q(
                        phrases__is_active=True,
                        phrases__tier=PhraseTier.SUBJECT,
                    ),
                    distinct=True,
                )
            )
            .filter(vocabulary_count__gt=0)
        )
        subject_vocabulary = Phrase.objects.filter(
            is_active=True,
            tier=PhraseTier.SUBJECT,
            source_prompts__is_active=True,
            source_prompts__theme__is_active=True,
        )
        if task:
            subject_prompts = subject_prompts.filter(theme__task=task)
            subject_vocabulary = subject_vocabulary.filter(
                source_prompts__theme__task=task
            )
        subject_prompts = list(
            subject_prompts.order_by("theme__order", "number", "pk")
        )
        subject_prompt_count = len(subject_prompts)
        subject_response_ids = set()
        current_group = None
        for prompt in subject_prompts:
            prompt.vocabulary_batch_count = (
                prompt.vocabulary_count
                + queue_module.PHRASE_BATCH_SIZE
                - 1
            ) // queue_module.PHRASE_BATCH_SIZE
            subject_response_ids.add(prompt.response_id)
            if (
                current_group is None
                or current_group["theme"].pk != prompt.theme_id
            ):
                current_group = {
                    "theme": prompt.theme,
                    "prompts": [],
                    "response_ids": set(),
                }
                subject_theme_groups.append(current_group)
            current_group["prompts"].append(prompt)
            current_group["response_ids"].add(prompt.response_id)
        for group in subject_theme_groups:
            group["deck_count"] = len(group.pop("response_ids"))
        subject_response_count = len(subject_response_ids)
        subject_vocabulary_count = subject_vocabulary.distinct().count()

        tests = (
            ComprehensionTest.objects.filter(
                mode=ComprehensionMode.ECRITE,
                is_active=True,
                is_published=True,
            )
            .annotate(
                vocabulary_count=Count(
                    "questions__vocabulary",
                    filter=Q(
                        questions__vocabulary__is_active=True,
                        questions__vocabulary__tier=PhraseTier.COMPREHENSION,
                    ),
                    distinct=True,
                )
            )
            .filter(vocabulary_count__gt=0)
            .order_by("number")
        )
        for test in tests:
            deck_scope = {"kind": "vocab", "test": test.slug}
            cards = queue_module.scoped_cards(
                deck_scope,
                user=request.user,
            )
            comprehension_decks.append(
                {
                    "test": test,
                    "vocabulary_count": test.vocabulary_count,
                    "batch_count": (
                        test.vocabulary_count
                        + queue_module.PHRASE_BATCH_SIZE
                        - 1
                    )
                    // queue_module.PHRASE_BATCH_SIZE,
                    "stats": deck_stats(cards, timezone.now()),
                    "counts": queue_module.queue_counts(
                        deck_scope,
                        user=request.user,
                    ),
                }
            )
            comprehension_vocabulary_count += test.vocabulary_count

    expression_vocabulary_paths = (
        _vocabulary_expression_paths(timezone.now(), request.user)
        if vocabulary_landing
        else []
    )

    vocabulary_scope = {"content": "vocabulary"}
    vocabulary_cards = queue_module.scoped_cards(
        vocabulary_scope,
        user=request.user,
    )
    vocabulary_counts = queue_module.queue_counts(
        vocabulary_scope,
        user=request.user,
    )
    vocabulary_revisit_count = queue_module.scoped_cards(
        {"kind": "revisit", "content": "vocabulary"},
        user=request.user,
    ).count()
    vocabulary_weak_count = queue_module.queue_counts(
        {"kind": "weak", "content": "vocabulary"},
        user=request.user,
    )["weak_total"]

    return render(
        request,
        "study/phrases.html",
        {
            "part": task.part if task else None,
            "task": task,
            "categories": categories,
            "functional_categories": functional_categories,
            "vocabulary_landing": vocabulary_landing,
            "comprehension_directory": comprehension_directory,
            "expression_vocabulary_paths": expression_vocabulary_paths,
            "functional_phrase_count": sum(
                category.phrase_count
                for category in functional_categories
            ),
            "first_category": (
                functional_categories[0]
                if functional_categories
                else None
            ),
            "subject_theme_groups": subject_theme_groups,
            "subject_prompt_count": subject_prompt_count,
            "subject_response_count": subject_response_count,
            "subject_vocabulary_count": subject_vocabulary_count,
            "comprehension_decks": comprehension_decks,
            "comprehension_vocabulary_count": (
                comprehension_vocabulary_count
            ),
            "vocabulary_stats": deck_stats(
                vocabulary_cards,
                timezone.now(),
            ),
            "vocabulary_counts": vocabulary_counts,
            "vocabulary_revisit_count": vocabulary_revisit_count,
            "vocabulary_weak_count": vocabulary_weak_count,
            "grouped": grouped,
            "review_batches": review_batches,
            "first_review_batch": first_review_batch,
            "batch_size": queue_module.PHRASE_BATCH_SIZE,
            "selected": selected,
            "selected_test": selected_test,
            "phrase_count": (
                selected.phrase_count
                if selected
                else (
                    phrase_qs.count()
                    if selected_test
                    else sum(
                    category.phrase_count
                    for category in functional_categories
                    )
                )
            ),
        },
    )


def search(request, part_slug=None, task_slug=None):
    part_slug = part_slug or (request.GET.get("part") or "").strip()
    task_slug = task_slug or (request.GET.get("task") or "").strip()
    task = (
        _route_task(part_slug, task_slug)
        if part_slug and task_slug
        else None
    )
    query = request.GET.get("q", "").strip()
    prompt_results = []
    phrase_results = []
    comprehension_results = []
    prompt_result_count = 0
    phrase_result_count = 0
    comprehension_result_count = 0
    result_limit = 12
    if query:
        prompt_qs = Prompt.objects.filter(is_active=True).filter(
            Q(text__icontains=query) | Q(response__body__icontains=query)
        )
        phrase_qs = Phrase.objects.filter(
            Q(is_active=True),
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
        prompt_result_count = prompt_qs.count()
        phrase_result_count = phrase_qs.count()
        prompt_results = list(
            prompt_qs
            .select_related("response", "theme", "family")
            .order_by("theme__order", "number")[:result_limit]
        )
        phrase_results = list(
            phrase_qs
            .select_related("category")
            .order_by("order")[:result_limit]
        )
        if not task:
            comprehension_qs = (
                ComprehensionQuestion.objects.filter(
                    test__is_active=True,
                    test__is_published=True,
                    is_active=True,
                )
                .filter(
                    Q(passage_fr__icontains=query)
                    | Q(prompt_fr__icontains=query)
                    | Q(passage_en__icontains=query)
                    | Q(prompt_en__icontains=query)
                    | Q(choices__text_fr__icontains=query)
                    | Q(choices__text_en__icontains=query)
                )
                .select_related("test")
                .distinct()
                .order_by("test__number", "number")
            )
            comprehension_result_count = comprehension_qs.count()
            comprehension_results = list(
                comprehension_qs[:result_limit]
            )
    result_count = (
        prompt_result_count
        + phrase_result_count
        + comprehension_result_count
    )
    visible_result_count = (
        len(prompt_results)
        + len(phrase_results)
        + len(comprehension_results)
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
            "comprehension_results": comprehension_results,
            "prompt_result_count": prompt_result_count,
            "phrase_result_count": phrase_result_count,
            "comprehension_result_count": comprehension_result_count,
            "result_count": result_count,
            "visible_result_count": visible_result_count,
            "results_truncated": result_count > visible_result_count,
            "prompt_total": (
                Prompt.objects.filter(
                    theme__task=task,
                    is_active=True,
                ).count()
                if task
                else Prompt.objects.filter(is_active=True).count()
            ),
            "phrase_total": (
                _task_phrases(task).count()
                if task
                else Phrase.objects.filter(is_active=True).count()
            ),
        },
    )


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def _stats_scope_cards(scope, user):
    cards = Card.objects.current_content().filter(user=user)
    if not scope:
        return cards

    response_ids = queue_module.scoped_cards(
        {**scope, "content": "spine"},
        user=user,
        include_suspended=True,
    ).values("pk")
    vocabulary_ids = queue_module.scoped_cards(
        {**scope, "content": "vocabulary"},
        user=user,
        include_suspended=True,
    ).values("pk")
    return cards.filter(
        Q(pk__in=response_ids) | Q(pk__in=vocabulary_ids)
    ).distinct()


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

    scoped_history_cards = _stats_scope_cards(scope, request.user)
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
    reviews_30_days = sum(d["count"] for d in daily)

    heat = []
    for offset in range(89, -1, -1):
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
    mastery_percentage = (
        round(100 * overall["mature"] / overall["total"])
        if overall["total"]
        else 0
    )

    theme_qs = Theme.objects.select_related("task__part").filter(is_active=True)
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
        "reviews_30_days": reviews_30_days,
        "heat": heat,
        "reviews_90_days": sum(cell["count"] for cell in heat),
        "retention": retention,
        "mature_total": mature_total,
        "forecast": forecast,
        "max_forecast": max_forecast,
        "forecast_total": sum(item["count"] for item in forecast),
        "overall": overall,
        "mastery_percentage": mastery_percentage,
        "themes": themes,
        "streak": current_streak(now, logs_base, request.user),
        "total_reviews": logs_base.count(),
        "reviews_today": per_day.get(today, 0),
        "recent_sessions": recent_review_sessions(logs_base),
        "expression_weak_count": queue_module.queue_counts(
            {**scope, "kind": "weak", "content": "spine"},
            now,
            user=request.user,
        )["weak_total"],
        "vocabulary_weak_count": queue_module.queue_counts(
            {
                **scope,
                "kind": "weak",
                "content": "vocabulary",
            },
            now,
            user=request.user,
        )["weak_total"],
        **filters,
    }
    return render(request, "study/stats.html", context)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


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
        )
        ReviewLog.objects.filter(user=request.user).delete()
        ComprehensionAttempt.objects.filter(user=request.user).delete()
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
        "version": 2,
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
