"""Review-queue construction: what to study next, and how much is available.

Practice is unrestricted: every new card and every due review remains
available. Optional scopes narrow the queue to a task, category, or stable
15-card batch without introducing a daily cap.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from django.db.models import Q
from django.utils import timezone

from .models import Card, CardType, CardState, ReviewLog


BATCH_SIZE = 15


def _today_start(now: datetime) -> datetime:
    local = timezone.localtime(now)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def batch_ordering(scope: Optional[dict] = None) -> tuple[str, ...]:
    """Return the canonical ordering used to partition a scoped deck."""
    scope = scope or {}
    kind = scope.get("kind")
    if scope.get("category") or kind == "phrase":
        return ("card_type", "phrase__order", "phrase_id", "id")
    if scope.get("theme") or kind == "spine":
        return ("response__theme__order", "response_id", "id")
    return ("card_type", "response_id", "phrase_id", "id")


def scoped_cards(
    scope: Optional[dict] = None,
    *,
    user=None,
    include_suspended: bool = False,
):
    """A user's active cards narrowed to an optional deck scope."""
    qs = Card.objects.filter(user=user).select_related(
        "response__theme", "response__family", "phrase__category"
    )
    scope = scope or {}
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
        )

    qs = qs.distinct()
    try:
        batch_number = int(scope.get("batch", 0))
    except (TypeError, ValueError):
        batch_number = 0
    if batch_number > 0:
        start = (batch_number - 1) * BATCH_SIZE
        batch_ids = list(
            qs.order_by(*batch_ordering(scope)).values_list("pk", flat=True)[
                start : start + BATCH_SIZE
            ]
        )
        qs = qs.filter(pk__in=batch_ids)

    if not include_suspended:
        qs = qs.filter(suspended=False)
    return qs.distinct()


def queue_counts(
    scope: Optional[dict] = None,
    now: datetime | None = None,
    *,
    user=None,
) -> dict:
    """Counts driving the dashboard, deck pages and navigation badges."""
    now = now or timezone.now()
    start = _today_start(now)
    cards = scoped_cards(scope, user=user)

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

    limit_cards = scoped_cards(
        scope,
        user=user,
        include_suspended=True,
    )
    todays_logs = ReviewLog.objects.filter(
        user=user,
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

    new_total = cards.filter(state=CardState.NEW).count()
    review_due = review_due_total
    new_available = new_total

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
            {**(scope or {}), "kind": "revisit"},
            user=user,
        ).count(),
    }


def next_card(
    scope: Optional[dict] = None,
    now: datetime | None = None,
    exclude_card_ids: Iterable[int] | None = None,
    *,
    user=None,
):
    """Pick the next card to study, or ``None`` when nothing is due.

    Order: due learning/relearning (soonest first), then every due review,
    then every fresh card.
    """
    now = now or timezone.now()
    counts = queue_counts(scope, now, user=user)
    cards = scoped_cards(scope, user=user)
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


def resumable_card(
    card_id: int | None,
    scope: Optional[dict],
    now=None,
    *,
    user=None,
):
    """Return a saved unfinished card when it is still valid for this scope."""
    if not card_id:
        return None
    now = now or timezone.now()
    card = scoped_cards(scope, user=user).filter(pk=card_id).first()
    if card is None:
        return None
    if scope and scope.get("kind") == "revisit":
        return card
    if card.state == CardState.NEW:
        return card
    if card.due is not None and card.due <= now:
        return card
    return None
