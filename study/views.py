"""Views for the flashcards app."""

from __future__ import annotations

import json
import re
import secrets
from urllib.parse import urlencode, urlsplit

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
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_POST

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
    "part",
    "task",
    "theme",
    "family",
    "category",
    "response",
    "batch",
)
MAX_ANNOTATION_QUOTE_LENGTH = 5000
MAX_ANNOTATION_BODY_LENGTH = 20000
ANNOTATION_SOURCE_KEY_RE = re.compile(r"^[A-Za-z0-9:._-]{0,200}$")
FOCUSED_REVIEW_KINDS = {"revisit", "weak"}
RECENT_SESSION_GAP = timezone.timedelta(minutes=30)


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
    batches = []
    for number, start in enumerate(
        range(0, len(units), queue_module.BATCH_SIZE),
        start=1,
    ):
        units_in_batch = units[start : start + queue_module.BATCH_SIZE]
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
        theme_count = Theme.objects.filter(task=task, is_active=True).count()
        prompt_count = Prompt.objects.filter(
            theme__task=task,
            is_active=True,
        ).count()
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


def dashboard(request):
    now = timezone.now()
    counts = queue_module.queue_counts(now=now, user=request.user)
    user_cards = queue_module.scoped_cards(user=request.user)
    overall = deck_stats(user_cards, now)

    context = {
        "counts": counts,
        "parts": _parts_with_task_cards(now, request.user),
        "overall": overall,
        "streak": current_streak(now, user=request.user),
        "weak_count": queue_module.queue_counts(
            {"kind": "weak"},
            now,
            user=request.user,
        )["weak_total"],
    }
    return render(request, "study/dashboard.html", context)


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
        context.update(
            {
                "title": "Expressions",
                "eyebrow": "Précision lexicale",
                "description": (
                    "Choisissez une tâche pour retrouver ses expressions, "
                    "son vocabulaire et ses nuances."
                ),
                "phrase_count": Phrase.objects.filter(
                    is_active=True,
                    tier=PhraseTier.SHARED,
                ).count(),
                "response_phrase_count": Phrase.objects.filter(
                    is_active=True,
                    tier=PhraseTier.RESPONSE,
                ).count(),
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


@require_GET
def notes_overview(request):
    counts = _annotation_counts(request.user)
    parts = []
    visible_tasks = Task.objects.filter(
        Q(is_active=True) | Q(annotations__user=request.user)
    ).distinct()
    visible_parts = (
        ExamPart.objects.filter(
            Q(is_active=True) | Q(tasks__in=visible_tasks)
        )
        .distinct()
        .prefetch_related(Prefetch("tasks", queryset=visible_tasks))
    )
    for part in visible_parts:
        tasks = []
        for task in part.tasks.all():
            tasks.append(
                {
                    "task": task,
                    "counts": counts.get(
                        task.id,
                        {
                            "notes": 0,
                            "highlights": 0,
                            "study": 0,
                            "total": 0,
                        },
                    ),
                }
            )
        parts.append({"part": part, "tasks": tasks})
    general_counts = counts.get(
        None,
        {"notes": 0, "highlights": 0, "study": 0, "total": 0},
    )
    return render(
        request,
        "study/notes_overview.html",
        {
            "parts": parts,
            "general_counts": general_counts,
            "total_annotations": sum(
                item["total"] for item in counts.values()
            ),
            "study_count": sum(item["study"] for item in counts.values()),
        },
    )


def _annotation_scope_url(task=None):
    if task:
        return reverse(
            "study:task_notes",
            args=[task.part.slug, task.slug],
        )
    return reverse("study:general_notes")


def _notes_scope(request, task=None):
    annotations = Annotation.objects.filter(user=request.user, task=task)
    if request.method == "POST":
        instance = Annotation(
            user=request.user,
            task=task,
            kind=AnnotationKind.NOTE,
        )
        form = NoteForm(request.POST, instance=instance)
        if form.is_valid():
            note = form.save()
            return redirect(_annotation_scope_url(task) + f"#note-{note.id}")
    else:
        form = NoteForm()
    return render(
        request,
        "study/notes_list.html",
        {
            "part": task.part if task else None,
            "task": task,
            "scope_title": task.name if task else "Notes générales",
            "notes": annotations.filter(kind=AnnotationKind.NOTE),
            "highlights": annotations.filter(
                kind=AnnotationKind.HIGHLIGHT
            ),
            "study_count": annotations.filter(study_later=True).count(),
            "form": form,
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
            _annotation_scope_url(annotation.task)
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
    annotations = Annotation.objects.filter(
        user=request.user,
        study_later=True,
    ).select_related("task__part")
    if task:
        annotations = annotations.filter(task=task)
    items = list(annotations.order_by("-updated_at", "-id"))
    return render(
        request,
        "study/annotation_study.html",
        {
            "part": task.part if task else None,
            "task": task,
            "items": items,
            "scope_title": task.name if task else "Toutes mes notes",
            "back_url": (
                _annotation_scope_url(task)
                if task
                else reverse("study:notes_overview")
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


@require_GET
def annotations_for_source(request):
    try:
        source_path = _safe_source_path(request.GET.get("source_path"))
    except ValueError:
        return HttpResponseBadRequest("Invalid source path.")
    highlights = Annotation.objects.filter(
        user=request.user,
        kind=AnnotationKind.HIGHLIGHT,
        source_path=source_path,
    ).values(
        "id",
        "quote",
        "source_key",
        "start_offset",
        "end_offset",
        "prefix",
        "suffix",
    )
    return JsonResponse({"highlights": list(highlights)})


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
        source_key = _annotation_source_key(request.POST.get("source_key"))
        start_offset = int(request.POST.get("start_offset", ""))
        end_offset = int(request.POST.get("end_offset", ""))
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Invalid annotation data.")
    if start_offset < 0 or end_offset <= start_offset:
        return HttpResponseBadRequest("Invalid annotation offsets.")

    values = {
        "task": task,
        "quote": quote,
        "source_title": (request.POST.get("source_title") or "")[:300],
        "prefix": (request.POST.get("prefix") or "")[-160:],
        "suffix": (request.POST.get("suffix") or "")[:160],
        "body": body,
    }
    try:
        if kind == AnnotationKind.HIGHLIGHT:
            annotation, created = Annotation.objects.update_or_create(
                user=request.user,
                kind=kind,
                source_path=source_path,
                source_key=source_key,
                start_offset=start_offset,
                end_offset=end_offset,
                defaults=values,
            )
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
            "notes_url": _annotation_scope_url(task),
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
    return _annotation_scope_url(annotation.task)


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
            scope,
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
    weak_counts = queue_module.queue_counts(
        {**scope, "kind": "weak"},
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
            "weak_count": weak_counts["weak_total"],
            "can_resume": can_resume,
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
    scope = scope_from_request(request)
    with transaction.atomic():
        session = _locked_review_session(request.user)
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
    scope = scope_from_request(request)
    with transaction.atomic():
        session = _locked_review_session(request.user)
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
        response.prompts.filter(is_active=True).select_related("theme")
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
        .distinct()
        .select_related("category")
    )
    phrase_batches = _review_batches(
        {"kind": "phrase", "response": str(response.pk)},
        request.user,
    )
    return render(
        request,
        "study/response_detail.html",
        {
            "response": response,
            "task": response.theme.task,
            "part": response.theme.task.part if response.theme.task else None,
            "response_content": response_content,
            "arguments": response_content.arguments,
            "prompts": prompts,
            "card": card,
            "related_phrases": related_phrases,
            "phrase_batches": phrase_batches,
            "can_edit_response": response.prompts.filter(
                is_active=True,
                theme__task__slug="tache-3",
                theme__task__part__slug="orale",
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
            prompts__theme__task__part__slug="orale",
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
            category.phrase_count + queue_module.BATCH_SIZE - 1
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
        "recent_sessions": recent_review_sessions(logs_base),
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
        "change_pin_form": ChangePinForm(request.user),
        "recovery_codes_form": CurrentPinForm(request.user),
        "reset_form": ResetProgressForm(request.user),
        "delete_account_form": DeleteAccountForm(request.user),
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
    form = ChangePinForm(request.user, request.POST)
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
    form = CurrentPinForm(request.user, request.POST)
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
    form = ResetProgressForm(request.user, request.POST)
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
    session = ReviewSession.load(request.user)
    settings = Settings.load(request.user)
    payload = {
        "format": "heureux-account-export",
        "version": 1,
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
    form = DeleteAccountForm(request.user, request.POST)
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
