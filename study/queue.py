"""Review-queue construction: what to study next, and how much is due.

Honors the daily new-card and review limits from :class:`Settings`, always
lets time-sensitive learning/relearning cards through, supports optional deck
scoping (a theme, family or phrase category), and reports the counts used by
the dashboard and nav badges.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from django.db.models import Q
from django.utils import timezone

from .models import Card, CardType, CardState, ReviewLog, Settings


def _today_start(now: datetime) -> datetime:
    local = timezone.localtime(now)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def scoped_cards(
    scope: Optional[dict] = None,
    *,
    include_suspended: bool = False,
):
    """Active cards narrowed to an optional deck scope."""
    qs = (
        Card.objects.all()
        if include_suspended
        else Card.objects.active()
    ).select_related(
        "response__theme", "response__family", "phrase__category"
    )
    if not scope:
        return qs
    kind = scope.get("kind")
    if kind == "spine":
        qs = qs.filter(card_type=CardType.SPINE)
    elif kind == "phrase":
        qs = qs.filter(
            card_type__in=[
                CardType.PHRASE_PRODUCTION,
                CardType.PHRASE_RECOGNITION,
            ]
        )
    elif kind == "revisit":
        qs = qs.filter(needs_revisit=True)
    relation_filters = {
        "part": (
            "response__theme__task__part__slug",
            "phrase__source_prompts__theme__task__part__slug",
        ),
        "task": (
            "response__theme__task__slug",
            "phrase__source_prompts__theme__task__slug",
        ),
        "theme": (
            "response__theme__slug",
            "phrase__source_prompts__theme__slug",
        ),
        "family": (
            "response__family__slug",
            "phrase__source_prompts__family__slug",
        ),
    }
    response_scope = Q()
    phrase_scope = Q()
    has_relation_scope = False
    for key, (response_lookup, phrase_lookup) in relation_filters.items():
        if scope.get(key):
            has_relation_scope = True
            response_scope &= Q(**{response_lookup: scope[key]})
            phrase_scope &= Q(**{phrase_lookup: scope[key]})
    if has_relation_scope:
        qs = qs.filter(response_scope | phrase_scope)
    if scope.get("category"):
        qs = qs.filter(phrase__category__slug=scope["category"])
    if scope.get("response"):
        qs = qs.filter(
            phrase__source_prompts__response_id=scope["response"]
        ).distinct()
    return qs.distinct()


def queue_counts(scope: Optional[dict] = None, now: datetime | None = None) -> dict:
    """Counts driving the dashboard, deck pages and navigation badges."""
    now = now or timezone.now()
    start = _today_start(now)
    settings = Settings.load()
    cards = scoped_cards(scope)

    if scope and scope.get("kind") == "revisit":
        revisit_total = cards.count()
        return {
            "due_reviews": revisit_total,
            "learning_due": 0,
            "review_due": revisit_total,
            "review_due_total": revisit_total,
            "new_available": 0,
            "new_total": 0,
            "new_done_today": 0,
            "reviews_done_today": 0,
            "total_due": revisit_total,
            "revisit_total": revisit_total,
        }

    limit_cards = scoped_cards(scope, include_suspended=True)
    todays_logs = ReviewLog.objects.filter(
        reviewed_at__gte=start,
        card_id__in=limit_cards.values("pk"),
    )
    new_done_today = todays_logs.filter(state_before=CardState.NEW).count()
    reviews_done_today = todays_logs.filter(
        state_before__in=[CardState.REVIEW, CardState.RELEARNING]
    ).count()

    learning_due = cards.filter(
        state__in=[CardState.LEARNING, CardState.RELEARNING], due__lte=now
    ).count()
    review_due_total = cards.filter(
        state=CardState.REVIEW, due__lte=now
    ).count()

    review_remaining = max(0, settings.max_reviews_per_day - reviews_done_today)
    review_due = min(review_due_total, review_remaining)

    new_total = cards.filter(state=CardState.NEW).count()
    new_remaining = max(0, settings.new_cards_per_day - new_done_today)
    new_available = min(new_total, new_remaining)

    due_reviews = learning_due + review_due
    return {
        "due_reviews": due_reviews,
        "learning_due": learning_due,
        "review_due": review_due,
        "review_due_total": review_due_total,
        "new_available": new_available,
        "new_total": new_total,
        "new_done_today": new_done_today,
        "reviews_done_today": reviews_done_today,
        "total_due": due_reviews + new_available,
        "revisit_total": scoped_cards(
            {**(scope or {}), "kind": "revisit"}
        ).count(),
    }


def next_card(
    scope: Optional[dict] = None,
    now: datetime | None = None,
    exclude_card_ids: Iterable[int] | None = None,
):
    """Pick the next card to study, or ``None`` when nothing is due.

    Order: due learning/relearning (soonest first), then due reviews within the
    daily cap, then fresh new cards within the daily cap.
    """
    now = now or timezone.now()
    counts = queue_counts(scope, now)
    cards = scoped_cards(scope)
    if exclude_card_ids:
        cards = cards.exclude(pk__in=list(exclude_card_ids))

    if scope and scope.get("kind") == "revisit":
        return cards.order_by("revisit_added_at", "id").first()

    learning = (
        cards.filter(
            state__in=[CardState.LEARNING, CardState.RELEARNING], due__lte=now
        )
        .order_by("due")
        .first()
    )
    if learning is not None:
        return learning

    if counts["review_due"] > 0:
        review = (
            cards.filter(state=CardState.REVIEW, due__lte=now)
            .order_by("due")
            .first()
        )
        if review is not None:
            return review

    if counts["new_available"] > 0:
        return cards.filter(state=CardState.NEW).order_by("id").first()

    return None


def resumable_card(card_id: int | None, scope: Optional[dict], now=None):
    """Return a saved unfinished card when it is still valid for this scope."""
    if not card_id:
        return None
    now = now or timezone.now()
    card = scoped_cards(scope).filter(pk=card_id).first()
    if card is None:
        return None
    if scope and scope.get("kind") == "revisit":
        return card
    if card.state == CardState.NEW:
        return card
    if card.due is not None and card.due <= now:
        return card
    return None
