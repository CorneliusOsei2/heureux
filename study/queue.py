"""Review-queue construction: what to study next, and how much is due.

Honors the daily new-card and review limits from :class:`Settings`, always
lets time-sensitive learning/relearning cards through, supports optional deck
scoping (a theme, family or phrase category), and reports the counts used by
the dashboard and nav badges.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from django.utils import timezone

from .models import Card, CardType, CardState, ReviewLog, Settings


def _today_start(now: datetime) -> datetime:
    local = timezone.localtime(now)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def scoped_cards(scope: Optional[dict] = None):
    """Active cards narrowed to an optional deck scope."""
    qs = Card.objects.active().select_related(
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
    if scope.get("part"):
        qs = qs.filter(response__theme__task__part__slug=scope["part"])
    if scope.get("task"):
        qs = qs.filter(response__theme__task__slug=scope["task"])
    if scope.get("theme"):
        qs = qs.filter(response__theme__slug=scope["theme"])
    if scope.get("family"):
        qs = qs.filter(response__family__slug=scope["family"])
    if scope.get("category"):
        qs = qs.filter(phrase__category__slug=scope["category"])
    return qs


def queue_counts(scope: Optional[dict] = None, now: datetime | None = None) -> dict:
    """Counts driving the dashboard, deck pages and navigation badges."""
    now = now or timezone.now()
    start = _today_start(now)
    settings = Settings.load()

    todays_logs = ReviewLog.objects.filter(reviewed_at__gte=start)
    new_done_today = todays_logs.filter(state_before=CardState.NEW).count()
    reviews_done_today = todays_logs.filter(
        state_before__in=[CardState.REVIEW, CardState.RELEARNING]
    ).count()

    cards = scoped_cards(scope)
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
    }


def next_card(scope: Optional[dict] = None, now: datetime | None = None):
    """Pick the next card to study, or ``None`` when nothing is due.

    Order: due learning/relearning (soonest first), then due reviews within the
    daily cap, then fresh new cards within the daily cap.
    """
    now = now or timezone.now()
    counts = queue_counts(scope, now)
    cards = scoped_cards(scope)

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
