"""Views for the flashcards app."""

from __future__ import annotations

import json
import secrets

from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from . import queue as queue_module
from .cards import card_payload, scope_from_request, scope_label
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
    Settings,
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
)


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


def current_streak(now=None) -> int:
    """Consecutive days (up to today) with at least one review."""
    now = now or timezone.now()
    days = {
        timezone.localtime(dt).date()
        for dt in ReviewLog.objects.values_list("reviewed_at", flat=True)
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


def _spine_theme_stats(theme, now):
    return deck_stats(
        Card.objects.active().filter(
            card_type=CardType.SPINE, response__theme=theme
        ),
        now,
    )


def _task_card(task, now):
    """Build a dashboard/part card for a single task."""
    if task.available:
        stats = deck_stats(
            Card.objects.active().filter(
                card_type=CardType.SPINE, response__theme__task=task
            ),
            now,
        )
        theme_count = Theme.objects.filter(task=task).count()
    else:
        stats = None
        theme_count = 0
    return {"task": task, "stats": stats, "theme_count": theme_count}


def _phrase_deck_stats(now):
    return deck_stats(
        Card.objects.active().filter(
            card_type__in=[
                CardType.PHRASE_PRODUCTION,
                CardType.PHRASE_RECOGNITION,
            ]
        ),
        now,
    )


def dashboard(request):
    now = timezone.now()
    counts = queue_module.queue_counts(now=now)

    parts = []
    for part in ExamPart.objects.prefetch_related("tasks"):
        tasks = [_task_card(task, now) for task in part.tasks.all()]
        parts.append({"part": part, "tasks": tasks})

    overall = deck_stats(Card.objects.active(), now)

    context = {
        "counts": counts,
        "parts": parts,
        "phrase_stats": _phrase_deck_stats(now),
        "overall": overall,
        "streak": current_streak(now),
        "phrase_category_count": PhraseCategory.objects.count(),
    }
    return render(request, "study/dashboard.html", context)


def part_detail(request, part_slug):
    part = get_object_or_404(ExamPart.objects.prefetch_related("tasks"), slug=part_slug)
    now = timezone.now()
    tasks = [_task_card(task, now) for task in part.tasks.all()]
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
                "stats": _spine_theme_stats(theme, now),
                "prompt_count": Prompt.objects.filter(theme=theme).count(),
            }
        )
    task_stats = deck_stats(
        Card.objects.active().filter(
            card_type=CardType.SPINE, response__theme__task=task
        ),
        now,
    )
    context = {
        "part": task.part,
        "task": task,
        "themes": themes,
        "stats": task_stats,
        "phrase_stats": _phrase_deck_stats(now),
        "phrase_category_count": PhraseCategory.objects.count(),
    }
    return render(request, "study/task_detail.html", context)


# ---------------------------------------------------------------------------
# Review session
# ---------------------------------------------------------------------------


def _locked_review_session() -> ReviewSession:
    session, _ = ReviewSession.objects.select_for_update().get_or_create(pk=1)
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
        session = _locked_review_session()
        scope, explicit = _resolved_review_scope(request, session)
        if explicit and (
            session.scope != scope or request.GET.get("reset") == "1"
        ):
            _save_review_session(session, scope, clear_pass=True)
    counts = queue_module.queue_counts(scope)
    context = {
        "scope": scope,
        "scope_json": json.dumps(scope),
        "scope_label": scope_label(scope),
        "counts": counts,
        "is_revisit": scope.get("kind") == "revisit",
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
        card = queue_module.resumable_card(session.current_card_id, scope, now)
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
        )
    counts = queue_module.queue_counts(scope, now)
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
    counts = queue_module.queue_counts(scope, now)
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
def review_next(request):
    with transaction.atomic():
        session = _locked_review_session()
        scope, _ = _resolved_review_scope(request, session)
        state = _queue_state_locked(scope, request, session)
    state["can_undo"] = ReviewLog.objects.exists()
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
        session = _locked_review_session()
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

        card = get_object_or_404(Card.objects.select_for_update(), pk=card_id)
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
        session = _locked_review_session()
        card = undo_last()
        if card is None:
            state = _queue_state_locked(scope, request, session)
            state["can_undo"] = False
            state["undone"] = False
            return JsonResponse(state)
        state = _card_state_locked(card, scope, request, session)
    state["can_undo"] = ReviewLog.objects.exists()
    state["undone"] = True
    return JsonResponse(state)


# ---------------------------------------------------------------------------
# Revisit list
# ---------------------------------------------------------------------------


def revisit_list(request):
    """Persistent list of cards marked with the Revisit review action."""
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "remove":
            try:
                card_id = int(request.POST.get("card_id", ""))
            except (TypeError, ValueError):
                return HttpResponseBadRequest("Invalid card.")
            Card.objects.filter(pk=card_id).update(
                needs_revisit=False,
                revisit_added_at=None,
            )
        elif action == "clear":
            Card.objects.filter(needs_revisit=True).update(
                needs_revisit=False,
                revisit_added_at=None,
            )
        else:
            return HttpResponseBadRequest("Invalid action.")
        return redirect("study:revisit_list")

    cards = list(
        Card.objects.active()
        .filter(needs_revisit=True)
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
                        reverse("study:phrases")
                        + f"#{card.phrase.category.slug}"
                    ),
                }
            )
    return render(
        request,
        "study/revisit_list.html",
        {"items": items, "revisit_count": len(items)},
    )


# ---------------------------------------------------------------------------
# Browse
# ---------------------------------------------------------------------------


def _scope_filters(request):
    """Shared part/task filter context for Browse and Stats.

    Parses ``?part=`` / ``?task=`` (a task implies its part), builds the chip
    data with per-part/task card counts, and returns the effective scope so the
    page can offer a scoped review.
    """
    part_slug = (request.GET.get("part") or "").strip()
    task_slug = (request.GET.get("task") or "").strip()

    if task_slug:
        task = Task.objects.select_related("part").filter(slug=task_slug).first()
        if task:
            part_slug = task.part.slug
        else:
            task_slug = ""

    active = Card.objects.active().filter(card_type=CardType.SPINE)
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
        scope = {"task": task_slug}
        review_qs = f"task={task_slug}"
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
    }


def browse(request):
    now = timezone.now()
    filters = _scope_filters(request)
    scope = filters["scope"]

    theme_qs = Theme.objects.select_related("task__part").all()
    if scope.get("task"):
        theme_qs = theme_qs.filter(task__slug=scope["task"])
    elif scope.get("part"):
        theme_qs = theme_qs.filter(task__part__slug=scope["part"])

    themes = []
    for theme in theme_qs:
        stats = deck_stats(
            Card.objects.active().filter(
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
    families = Family.objects.annotate(n=Count("prompts")).order_by("order")
    categories = PhraseCategory.objects.annotate(n=Count("phrases")).order_by(
        "order"
    )
    context = {
        "themes": themes,
        "families": families,
        "categories": categories,
        "theme_count": len(themes),
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
            card_type=CardType.SPINE, response__theme=theme
        ),
        now,
    )
    return render(
        request,
        "study/theme_detail.html",
        {"theme": theme, "rows": rows, "stats": stats},
    )


def family_detail(request, slug):
    family = get_object_or_404(Family, slug=slug)
    prompts = (
        Prompt.objects.filter(family=family)
        .select_related("response", "theme", "family")
        .order_by("theme__order", "number")
    )
    spine_cards = {
        card.response_id: card
        for card in Card.objects.filter(card_type=CardType.SPINE)
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
        {"family": family, "rows": rows},
    )


def response_detail(request, pk):
    response = get_object_or_404(
        Response.objects.select_related("theme", "family"), pk=pk
    )
    arguments = list(response.arguments.all())
    prompts = list(response.prompts.select_related("theme").all())
    card = Card.objects.filter(
        card_type=CardType.SPINE, response=response
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
            "arguments": arguments,
            "prompts": prompts,
            "card": card,
            "related_phrases": related_phrases,
        },
    )


def phrases(request):
    category_slug = request.GET.get("category", "").strip()
    categories = PhraseCategory.objects.all().order_by("order")
    selected = None
    phrase_qs = Phrase.objects.select_related("category").prefetch_related(
        "source_prompts__theme"
    )
    if category_slug:
        selected = get_object_or_404(PhraseCategory, slug=category_slug)
        phrase_qs = phrase_qs.filter(category=selected)

    grouped = []
    for category in categories:
        items = [p for p in phrase_qs if p.category_id == category.id]
        if items:
            grouped.append({"category": category, "phrases": items})

    return render(
        request,
        "study/phrases.html",
        {"categories": categories, "grouped": grouped, "selected": selected},
    )


def search(request):
    query = request.GET.get("q", "").strip()
    prompt_results = []
    phrase_results = []
    if query:
        prompt_results = (
            Prompt.objects.filter(
                Q(text__icontains=query) | Q(response__body__icontains=query)
            )
            .select_related("response", "theme", "family")
            .order_by("theme__order", "number")[:60]
        )
        phrase_results = (
            Phrase.objects.filter(
                Q(expression__icontains=query)
                | Q(english_cue__icontains=query)
                | Q(example__icontains=query)
                | Q(note__icontains=query)
            )
            .select_related("category")
            .order_by("order")[:60]
        )
    return render(
        request,
        "study/search.html",
        {
            "query": query,
            "prompt_results": prompt_results,
            "phrase_results": phrase_results,
            "result_count": len(prompt_results) + len(phrase_results),
        },
    )


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def stats(request):
    now = timezone.now()
    today = timezone.localtime(now).date()
    filters = _scope_filters(request)
    scope = filters["scope"]

    logs_base = ReviewLog.objects.all()
    if scope.get("task"):
        logs_base = logs_base.filter(card__response__theme__task__slug=scope["task"])
    elif scope.get("part"):
        logs_base = logs_base.filter(
            card__response__theme__task__part__slug=scope["part"]
        )
    active_cards = queue_module.scoped_cards(scope)

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
        theme_qs = theme_qs.filter(task__slug=scope["task"])
    elif scope.get("part"):
        theme_qs = theme_qs.filter(task__part__slug=scope["part"])
    themes = [
        {
            "theme": theme,
            "stats": deck_stats(
                Card.objects.active().filter(
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
        "streak": current_streak(now),
        "total_reviews": logs_base.count(),
        "reviews_today": per_day.get(today, 0),
        **filters,
    }
    return render(request, "study/stats.html", context)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def settings_view(request):
    settings = Settings.load()
    if request.method == "POST":
        action = request.POST.get("action", "save")
        if action == "reset":
            with transaction.atomic():
                session = _locked_review_session()
                Card.objects.update(
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
                ReviewLog.objects.all().delete()
                _save_review_session(session, {}, clear_pass=True)
            return redirect(reverse("study:settings") + "?reset=1")
        if action == "unsuspend_all":
            Card.objects.filter(suspended=True).update(suspended=False)
            return redirect(reverse("study:settings") + "?unsuspended=1")
        try:
            settings.new_cards_per_day = max(
                0, int(request.POST.get("new_cards_per_day", 15))
            )
            settings.max_reviews_per_day = max(
                0, int(request.POST.get("max_reviews_per_day", 200))
            )
            settings.save()
        except (TypeError, ValueError):
            return HttpResponseBadRequest("Invalid settings.")
        return redirect(reverse("study:settings") + "?saved=1")

    return render(
        request,
        "study/settings.html",
        {
            "settings": settings,
            "saved": request.GET.get("saved") == "1",
            "was_reset": request.GET.get("reset") == "1",
            "was_unsuspended": request.GET.get("unsuspended") == "1",
            "suspended_count": Card.objects.filter(suspended=True).count(),
        },
    )
