"""Spaced-repetition scheduling (SM-2 family, Anki-style).

A single entry point, :func:`review`, advances a card's state given a rating
and writes a :class:`~study.models.ReviewLog`. :func:`preview_intervals`
computes the "next interval" label shown on each answer button without
mutating anything. Keeping the pure computation in :func:`compute` lets both
share identical logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.utils import timezone

from .models import Card, CardState, Rating, ReviewLog

# Tunable constants (Anki-like defaults).
LEARNING_STEPS_MIN = (1, 10)
RELEARNING_STEPS_MIN = (10,)
GRADUATING_INTERVAL_DAYS = 1
EASY_INTERVAL_DAYS = 4
STARTING_EASE = 2.5
MIN_EASE = 1.3
EASY_BONUS = 1.3
HARD_FACTOR = 1.2
INTERVAL_MODIFIER = 1.0
LAPSE_INTERVAL_FACTOR = 0.0
MIN_INTERVAL_DAYS = 1
MAX_INTERVAL_DAYS = 365
AGAIN_EASE_DELTA = -0.20
HARD_EASE_DELTA = -0.15
EASY_EASE_DELTA = 0.15


@dataclass
class Schedule:
    state: str
    due: datetime
    interval_days: float
    ease: float
    learning_step: int
    lapses: int


def _clamp_ease(ease: float) -> float:
    return max(MIN_EASE, round(ease, 4))


def _cap_interval(days: float) -> int:
    return int(max(MIN_INTERVAL_DAYS, min(MAX_INTERVAL_DAYS, round(days))))


def compute(
    *,
    state: str,
    interval_days: float,
    ease: float,
    learning_step: int,
    lapses: int,
    rating: int,
    now: datetime,
) -> Schedule:
    """Return the next scheduling state for a card. Pure function."""
    if state in (CardState.NEW, CardState.LEARNING):
        return _compute_learning(
            steps=LEARNING_STEPS_MIN,
            relearn=False,
            interval_days=interval_days,
            ease=ease,
            learning_step=learning_step if state == CardState.LEARNING else 0,
            lapses=lapses,
            rating=rating,
            now=now,
        )
    if state == CardState.RELEARNING:
        return _compute_learning(
            steps=RELEARNING_STEPS_MIN,
            relearn=True,
            interval_days=interval_days,
            ease=ease,
            learning_step=learning_step,
            lapses=lapses,
            rating=rating,
            now=now,
        )
    return _compute_review(
        interval_days=interval_days,
        ease=ease,
        lapses=lapses,
        rating=rating,
        now=now,
    )


def _compute_learning(
    *,
    steps,
    relearn: bool,
    interval_days: float,
    ease: float,
    learning_step: int,
    lapses: int,
    rating: int,
    now: datetime,
) -> Schedule:
    ease = _clamp_ease(ease)
    step = min(learning_step, len(steps) - 1)

    def stay_at(index: int) -> Schedule:
        return Schedule(
            state=CardState.RELEARNING if relearn else CardState.LEARNING,
            due=now + timedelta(minutes=steps[index]),
            interval_days=interval_days,
            ease=ease,
            learning_step=index,
            lapses=lapses,
        )

    def graduate(interval: float) -> Schedule:
        return Schedule(
            state=CardState.REVIEW,
            due=now + timedelta(days=interval),
            interval_days=float(interval),
            ease=ease,
            learning_step=0,
            lapses=lapses,
        )

    if rating == Rating.AGAIN:
        return stay_at(0)
    if rating == Rating.HARD:
        return stay_at(step)
    if rating == Rating.EASY:
        if relearn:
            interval = _cap_interval(
                max(interval_days * LAPSE_INTERVAL_FACTOR, MIN_INTERVAL_DAYS)
            )
            return graduate(interval)
        return graduate(EASY_INTERVAL_DAYS)

    # GOOD
    next_step = step + 1
    if next_step < len(steps):
        return stay_at(next_step)
    if relearn:
        interval = _cap_interval(
            max(interval_days * LAPSE_INTERVAL_FACTOR, MIN_INTERVAL_DAYS)
        )
        return graduate(interval)
    return graduate(GRADUATING_INTERVAL_DAYS)


def _compute_review(
    *,
    interval_days: float,
    ease: float,
    lapses: int,
    rating: int,
    now: datetime,
) -> Schedule:
    ease = _clamp_ease(ease)

    if rating == Rating.AGAIN:
        return Schedule(
            state=CardState.RELEARNING,
            due=now + timedelta(minutes=RELEARNING_STEPS_MIN[0]),
            interval_days=interval_days,
            ease=_clamp_ease(ease + AGAIN_EASE_DELTA),
            learning_step=0,
            lapses=lapses + 1,
        )

    if rating == Rating.HARD:
        new_ease = _clamp_ease(ease + HARD_EASE_DELTA)
        interval = interval_days * HARD_FACTOR * INTERVAL_MODIFIER
    elif rating == Rating.EASY:
        new_ease = _clamp_ease(ease + EASY_EASE_DELTA)
        interval = interval_days * new_ease * EASY_BONUS * INTERVAL_MODIFIER
    else:  # GOOD
        new_ease = ease
        interval = interval_days * ease * INTERVAL_MODIFIER

    interval = max(interval, interval_days + 1)
    interval = _cap_interval(interval)
    return Schedule(
        state=CardState.REVIEW,
        due=now + timedelta(days=interval),
        interval_days=float(interval),
        ease=new_ease,
        learning_step=0,
        lapses=lapses,
    )


def review(
    card: Card, rating: int, now: datetime | None = None, elapsed_ms: int = 0
) -> Schedule:
    """Apply a rating to a card, persist it, and log the review."""
    now = now or timezone.now()
    rating = int(rating)

    before_state = card.state
    before_interval = card.interval_days
    before_ease = card.ease

    sched = compute(
        state=card.state,
        interval_days=card.interval_days,
        ease=card.ease,
        learning_step=card.learning_step,
        lapses=card.lapses,
        rating=rating,
        now=now,
    )

    card.state = sched.state
    card.due = sched.due
    card.interval_days = sched.interval_days
    card.ease = sched.ease
    card.learning_step = sched.learning_step
    card.lapses = sched.lapses
    card.reps += 1
    card.last_reviewed = now
    card.last_rating = rating
    card.save(
        update_fields=[
            "state",
            "due",
            "interval_days",
            "ease",
            "learning_step",
            "lapses",
            "reps",
            "last_reviewed",
            "last_rating",
        ]
    )

    ReviewLog.objects.create(
        card=card,
        reviewed_at=now,
        rating=rating,
        state_before=before_state,
        state_after=sched.state,
        interval_before=before_interval,
        interval_after=sched.interval_days,
        ease_before=before_ease,
        ease_after=sched.ease,
        elapsed_ms=max(0, int(elapsed_ms)),
    )
    return sched


def format_interval(delta: timedelta) -> str:
    """Human-friendly interval label, e.g. '10 m', '1 j', '3 mois'."""
    seconds = max(delta.total_seconds(), 60)
    minutes = seconds / 60
    if minutes < 60:
        return f"{round(minutes)} min"
    hours = minutes / 60
    if hours < 24:
        return f"{round(hours)} h"
    days = hours / 24
    if days < 30:
        return f"{round(days)} j"
    if days < 365:
        return f"{round(days / 30, 1):g} mois"
    return f"{round(days / 365, 1):g} an(s)"


def preview_intervals(card: Card, now: datetime | None = None) -> dict[int, str]:
    """Return {rating: label} for the four answer buttons."""
    now = now or timezone.now()
    labels: dict[int, str] = {}
    for rating in (Rating.AGAIN, Rating.HARD, Rating.GOOD, Rating.EASY):
        sched = compute(
            state=card.state,
            interval_days=card.interval_days,
            ease=card.ease,
            learning_step=card.learning_step,
            lapses=card.lapses,
            rating=int(rating),
            now=now,
        )
        labels[int(rating)] = format_interval(sched.due - now)
    return labels
