"""Progress tracking for cards and subject-specific study material."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable
from urllib.parse import urlsplit

from django.db.models import Q
from django.utils import timezone

from .models import (
    Annotation,
    AnnotationKind,
    Card,
    CardState,
    CardType,
    Phrase,
    PhraseTier,
    Prompt,
    Response,
)


SUBJECT_PATH_RE = re.compile(
    r"^/expression/(?P<part>orale|ecrite)/(?P<task>[-a-zA-Z0-9_]+)/"
    r"sujets/(?P<prompt_id>\d+)/$"
)
EXPRESSION_PART_BY_PATH = {
    "orale": "eo",
    "ecrite": "ee",
}
RESPONSE_SOURCE_PREFIX = "response:"
PHRASE_SOURCE_PREFIX = "phrase:"
PHRASE_SOURCE_RE = re.compile(r"^phrase:(?P<phrase_id>[^:]+):")
ANNOTATION_SURFACE_SUFFIXES = (":front", ":back")


@dataclass(frozen=True)
class ProgressSummary:
    status: str
    label: str
    total: int
    started: int
    completed: int
    percent: int


def progress_summary(
    *,
    total: int,
    started: int,
    completed: int,
) -> ProgressSummary:
    """Normalize collection progress into the app's three display states."""
    total = max(int(total), 0)
    started = min(max(int(started), 0), total)
    completed = min(max(int(completed), 0), total)
    started = max(started, completed)
    if total and completed == total:
        status = "done"
        label = "Terminé"
    elif started:
        status = "active"
        label = "En cours"
    else:
        status = "new"
        label = "À commencer"
    return ProgressSummary(
        status=status,
        label=label,
        total=total,
        started=started,
        completed=completed,
        percent=round(100 * completed / total) if total else 0,
    )


def combine_progress(
    progress_items: Iterable[ProgressSummary],
) -> ProgressSummary:
    """Combine child progress while preserving their underlying unit counts."""
    items = list(progress_items)
    return progress_summary(
        total=sum(item.total for item in items),
        started=sum(item.started for item in items),
        completed=sum(item.completed for item in items),
    )


def card_unit_progress(cards) -> ProgressSummary:
    """Summarize active cards, treating both directions as one phrase unit."""
    rows = cards.values(
        "id",
        "phrase_id",
        "state",
        "started_at",
        "suspended",
    )
    units = {}
    for row in rows:
        key = ("phrase", row["phrase_id"]) if row["phrase_id"] else ("card", row["id"])
        if not row["suspended"]:
            units.setdefault(key, []).append(row)
    return progress_summary(
        total=len(units),
        started=sum(
            any(
                row["state"] != CardState.NEW
                or row["started_at"] is not None
                for row in unit
            )
            for unit in units.values()
        ),
        completed=sum(
            all(row["state"] != CardState.NEW for row in unit)
            for unit in units.values()
        ),
    )


@dataclass(frozen=True)
class SubjectProgress:
    status: str
    label: str
    has_highlight: bool
    response_practice_started: bool
    vocabulary_total: int
    vocabulary_started: int
    vocabulary_completed: int
    vocabulary_mastered: int
    vocabulary_due: int

    @property
    def vocabulary_progress(self) -> ProgressSummary:
        return progress_summary(
            total=self.vocabulary_total,
            started=self.vocabulary_started,
            completed=self.vocabulary_completed,
        )


def mark_card_started(user, card: Card) -> None:
    """Persist the presented card's first activity timestamp."""
    at = timezone.now()
    if card.started_at is None:
        Card.objects.filter(
            pk=card.pk,
            user=user,
            started_at__isnull=True,
        ).update(started_at=at)
        card.started_at = at
    if (
        card.card_type == CardType.SPINE
        and card.response_practice_started_at is None
    ):
        Card.objects.filter(
            pk=card.pk,
            user=user,
            response_practice_started_at__isnull=True,
        ).update(response_practice_started_at=at)
        card.response_practice_started_at = at


def _response_content_key(source_key: str) -> str:
    if not source_key.startswith(RESPONSE_SOURCE_PREFIX):
        return ""
    content_key = source_key[len(RESPONSE_SOURCE_PREFIX) :]
    for suffix in ANNOTATION_SURFACE_SUFFIXES:
        if content_key.endswith(suffix):
            return content_key[: -len(suffix)]
    return content_key


def subject_progress_by_response(user, response_ids) -> dict[int, SubjectProgress]:
    """Calculate sujet progress from direct, material-specific activity."""
    response_ids = {
        int(response_id)
        for response_id in response_ids
        if str(response_id).isdigit() and int(response_id) > 0
    }
    if not response_ids:
        return {}

    progress = {
        response_id: {
            "has_highlight": False,
            "response_practice_started": False,
            "vocabulary_total": 0,
            "vocabulary_started": 0,
            "vocabulary_completed": 0,
            "vocabulary_mastered": 0,
            "vocabulary_due": 0,
        }
        for response_id in response_ids
    }
    started_response_ids = Card.objects.current_content().filter(
        Q(response_practice_started_at__isnull=False)
        | ~Q(state=CardState.NEW),
        user=user,
        card_type=CardType.SPINE,
        response_id__in=response_ids,
    ).values_list("response_id", flat=True)
    for response_id in started_response_ids:
        progress[response_id]["response_practice_started"] = True

    now = timezone.now()
    vocabulary_cards = (
        Card.objects.active()
        .filter(
            user=user,
            phrase__tier=PhraseTier.SUBJECT,
            phrase__source_prompts__is_active=True,
            phrase__source_prompts__response_id__in=response_ids,
        )
        .values(
            "id",
            "state",
            "started_at",
            "interval_days",
            "due",
            "phrase__source_prompts__response_id",
        )
        .distinct()
    )
    for card in vocabulary_cards:
        response_id = card["phrase__source_prompts__response_id"]
        values = progress[response_id]
        values["vocabulary_total"] += 1
        if (
            card["started_at"] is not None
            or card["state"] != CardState.NEW
        ):
            values["vocabulary_started"] += 1
        if card["state"] != CardState.NEW:
            values["vocabulary_completed"] += 1
        if (
            card["state"] == CardState.REVIEW
            and card["interval_days"] >= 21
        ):
            values["vocabulary_mastered"] += 1
        if (
            card["state"]
            in {CardState.LEARNING, CardState.RELEARNING, CardState.REVIEW}
            and card["due"] is not None
            and card["due"] <= now
        ):
            values["vocabulary_due"] += 1

    response_by_content_key = dict(
        Response.objects.filter(
            pk__in=response_ids,
            is_active=True,
        ).values_list("content_key", "pk")
    )
    highlight_rows = list(
        Annotation.objects.filter(
            user=user,
            kind=AnnotationKind.HIGHLIGHT,
        )
        .filter(
            Q(source_key__startswith=RESPONSE_SOURCE_PREFIX)
            | Q(source_key__startswith=PHRASE_SOURCE_PREFIX)
            | Q(source_path__contains="/sujets/")
        )
        .values("source_path", "source_key")
    )
    path_matches = [
        (
            row,
            SUBJECT_PATH_RE.fullmatch(urlsplit(row["source_path"]).path),
        )
        for row in highlight_rows
    ]
    phrase_matches = [
        (row, PHRASE_SOURCE_RE.match(row["source_key"]))
        for row in highlight_rows
    ]
    prompt_ids = {
        int(match.group("prompt_id"))
        for _row, match in path_matches
        if match
    }
    prompt_rows = Prompt.objects.filter(
        pk__in=prompt_ids,
        is_active=True,
        response_id__in=response_ids,
        response__is_active=True,
    ).values(
        "pk",
        "response_id",
        "theme__task__part__slug",
        "theme__task__slug",
    )
    prompt_references = {
        row["pk"]: row
        for row in prompt_rows
    }
    phrase_ids = {
        match.group("phrase_id")
        for _row, match in phrase_matches
        if match
    }
    response_ids_by_subject_phrase = {}
    for phrase_id, response_id in Phrase.objects.filter(
        phrase_id__in=phrase_ids,
        tier=PhraseTier.SUBJECT,
        is_active=True,
        source_prompts__is_active=True,
        source_prompts__response_id__in=response_ids,
    ).values_list("phrase_id", "source_prompts__response_id"):
        response_ids_by_subject_phrase.setdefault(phrase_id, set()).add(
            response_id
        )
    for row, path_match in path_matches:
        matched_response_ids = set()
        if path_match and not row["source_key"]:
            prompt = prompt_references.get(int(path_match.group("prompt_id")))
            if (
                prompt
                and prompt["theme__task__part__slug"]
                == EXPRESSION_PART_BY_PATH[path_match.group("part")]
                and prompt["theme__task__slug"] == path_match.group("task")
            ):
                matched_response_ids.add(prompt["response_id"])
        content_key = _response_content_key(row["source_key"])
        if content_key in response_by_content_key:
            matched_response_ids.add(response_by_content_key[content_key])
        phrase_match = PHRASE_SOURCE_RE.match(row["source_key"])
        if phrase_match:
            matched_response_ids.update(
                response_ids_by_subject_phrase.get(
                    phrase_match.group("phrase_id"),
                    set(),
                )
            )
        for response_id in matched_response_ids:
            progress[response_id]["has_highlight"] = True

    results = {}
    for response_id, values in progress.items():
        if (
            values["vocabulary_total"]
            and values["vocabulary_completed"]
            == values["vocabulary_total"]
        ):
            status = "done"
            label = "Terminé"
        elif (
            values["has_highlight"]
            or values["response_practice_started"]
            or values["vocabulary_started"]
        ):
            status = "active"
            label = "En cours"
        else:
            status = "new"
            label = "À commencer"
        results[response_id] = SubjectProgress(
            status=status,
            label=label,
            **values,
        )
    return results


def summarize_subject_progress(
    progress_items: Iterable[SubjectProgress],
) -> dict[str, int | str | ProgressSummary]:
    """Return deck-compatible totals for a group of sujets."""
    items = list(progress_items)
    summary = progress_summary(
        total=len(items),
        started=sum(item.status != "new" for item in items),
        completed=sum(item.status == "done" for item in items),
    )
    active = sum(item.status == "active" for item in items)
    mature = sum(
        bool(item.vocabulary_total)
        and item.vocabulary_mastered == item.vocabulary_total
        for item in items
    )
    return {
        "progress": summary,
        "status": summary.status,
        "status_label": summary.label,
        "total": summary.total,
        "new": summary.total - summary.started,
        "started_new": active,
        "learning": 0,
        "review": summary.completed,
        "completed": summary.completed,
        "mature": mature,
        "review_young": summary.completed - mature,
        "due": sum(item.vocabulary_due for item in items),
        "seen": summary.started,
        "reviewed": summary.completed,
        "pct": (
            round(100 * summary.started / summary.total)
            if summary.total
            else 0
        ),
        "completed_pct": summary.percent,
        "mature_pct": (
            round(100 * mature / summary.total) if summary.total else 0
        ),
    }
