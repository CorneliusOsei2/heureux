"""Spaced-repetition review flow views."""

from __future__ import annotations

import json
import secrets
from urllib.parse import urlencode

from django.db import transaction
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .. import queue as queue_module
from ..cards import card_payload, scope_from_request, scope_label
from ..models import (
    Card,
    ComprehensionTest,
    ExamPart,
    PhraseTier,
    Rating,
    Response,
    ReviewSession,
    Theme,
)
from ..progress import mark_card_started, subject_progress_by_response
from ..routing import (
    comprehension_vocabulary_url,
    prompt_detail_url,
    response_detail_url,
    review_url,
    theme_detail_url,
)
from ..srs import review as apply_review, undo_last

from .common import (
    _review_batches,
    _route_task,
    _task_scope,
    deck_stats,
)

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


FOCUSED_REVIEW_KINDS = {"revisit", "weak"}


def _review_card_payload(card, user):
    payload = card_payload(card)
    if card.response_id:
        payload["subject_progress"] = subject_progress_by_response(
            user,
            {card.response_id},
        )[card.response_id]
    return payload


def _batch_index_url(scope: dict) -> str | None:
    """Return the category/theme page that owns a batch scope."""
    if scope.get("response"):
        response = get_object_or_404(Response, pk=scope["response"])
        return response_detail_url(response)
    if scope.get("category"):
        if scope.get("part") and scope.get("task"):
            return reverse(
                "study:task_vocabulary_category",
                args=[scope["part"], scope["task"], scope["category"]],
            )
        return reverse("study:vocabulary_category", args=[scope["category"]])
    if scope.get("test"):
        test = get_object_or_404(
            ComprehensionTest,
            slug=scope["test"],
            is_active=True,
        )
        return comprehension_vocabulary_url(test=test)
    if scope.get("theme"):
        theme = get_object_or_404(
            Theme.objects.select_related("task__part"),
            slug=scope["theme"],
            task__isnull=False,
        )
        return theme_detail_url(theme)
    return None


def _locked_review_session(user) -> ReviewSession:
    session, _ = ReviewSession.objects.select_for_update().get_or_create(
        user=user
    )
    return session


def _resolved_review_scope(
    request,
    session: ReviewSession,
    route_scope: dict | None = None,
) -> tuple[dict, bool]:
    """Use an explicit request scope, otherwise resume the saved one."""
    data = request.POST if request.method == "POST" else request.GET
    if request.method == "GET" and data.get("reset") == "1":
        return {"kind": "spine", **(route_scope or {})}, True
    scope = scope_from_request(request)
    if route_scope:
        scope.update(route_scope)
    explicit = any(key in data for key in REVIEW_SCOPE_KEYS)
    explicit = explicit or bool(route_scope)
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


def review(
    request,
    part_slug=None,
    task_slug=None,
    comprehension_mode=None,
    test_slug=None,
):
    route_scope = {}
    if task_slug is not None:
        task = _route_task(part_slug, task_slug)
        route_scope = {"part": task.part.slug, "task": task.slug}
    elif part_slug is not None:
        part = get_object_or_404(ExamPart, slug=part_slug, is_active=True)
        route_scope = {"part": part.slug}
    if test_slug is not None:
        test = get_object_or_404(
            ComprehensionTest,
            slug=test_slug,
            mode=comprehension_mode,
            is_active=True,
            is_published=True,
        )
        route_scope = {"kind": "vocab", "test": test.slug}

    path_scope_keys = {"part", "task", "test"}
    if path_scope_keys.intersection(request.GET):
        raise Http404

    with transaction.atomic():
        session = _locked_review_session(request.user)
        scope, explicit = _resolved_review_scope(
            request,
            session,
            route_scope=route_scope,
        )
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


def _active_card_payload(
    card,
    counts: dict,
    presentation_token,
    session: ReviewSession,
    request,
) -> dict:
    """Serialize an active review card into the client review payload."""
    payload = _review_card_payload(card, request.user)
    return {
        "done": False,
        "card_id": card.id,
        "card_type": card.card_type,
        "state": card.state,
        "state_label": card.get_state_display(),
        "is_new": card.is_new,
        "front_html": render_to_string(
            "study/partials/card_front.html", payload, request
        ),
        "back_html": render_to_string(
            "study/partials/card_back.html", payload, request
        ),
        "annotation_source_key": payload["annotation_source_key"],
        "presentation_token": presentation_token,
        "counts": counts,
        "revisit_count": counts["revisit_total"],
        "can_previous": bool(session.previous_card_id),
    }


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

    mark_card_started(request.user, card)
    presentation_token = _save_review_session(session, scope, card)
    return _active_card_payload(
        card, counts, presentation_token, session, request
    )


def _card_state_locked(
    card,
    scope: dict,
    request,
    session: ReviewSession,
) -> dict:
    """Build the review payload for a specific card (used after an undo)."""
    now = timezone.now()
    mark_card_started(request.user, card)
    counts = queue_module.queue_counts(scope, now, user=request.user)
    presentation_token = _save_review_session(
        session,
        scope,
        card,
        rotate_token=True,
    )
    return _active_card_payload(
        card, counts, presentation_token, session, request
    )


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
                "review_url": review_url(theme_scope),
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
            "resume_url": reverse(
                "study:task_review",
                args=[part.slug, task.slug],
            ),
            "start_review_url": review_url(response_scope),
            "weak_review_url": review_url(
                {**scope, "kind": "weak", "content": "spine"}
            ),
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
        payload = _review_card_payload(card, request.user)
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


def revisit_list(request, part_slug=None, task_slug=None):
    """Persistent list of cards marked with the Revisit review action."""
    if bool(task_slug) and not part_slug:
        raise Http404
    task = _route_task(part_slug, task_slug) if task_slug else None
    part = (
        task.part
        if task
        else (
            get_object_or_404(ExamPart, slug=part_slug, is_active=True)
            if part_slug
            else None
        )
    )
    scope = _task_scope(task) if task else ({"part": part.slug} if part else {})
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
        else (
            reverse("study:part_revisit_list", args=[part.slug])
            if part
            else reverse("study:revisit_list")
        )
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
                    "url": response_detail_url(card.response),
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
                    comprehension_vocabulary_url(test=source_question.test)
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
                    prompt_detail_url(source_prompt)
                    + "#subject-vocabulary"
                    if source_prompt
                    else reverse("study:vocabulary")
                )
            else:
                item_url = (
                    reverse(
                        "study:vocabulary_category",
                        args=[phrase.category.slug],
                    )
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
            "part": part,
            "task": task,
            "items": items,
            "revisit_groups": [
                group for group in revisit_groups if group["items"]
            ],
            "revisit_count": len(items),
            "review_url": review_url(revisit_scope),
            "content": content,
        },
    )
