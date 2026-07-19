"""Persistent first-activity tracking for cards and their owning subjects."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from django.utils import timezone

from .models import Card, CardType, PhraseTier, Response

RESPONSE_PATH_RE = re.compile(r"^/response/(?P<response_id>\d+)/$")
RESPONSE_SOURCE_PREFIX = "response:"
ANNOTATION_SURFACE_SUFFIXES = (":front", ":back")


def mark_response_started(user, response_ids, *, at=None) -> None:
    """Mark response cards without changing their spaced-repetition state."""
    response_ids = {
        int(response_id)
        for response_id in response_ids
        if str(response_id).isdigit() and int(response_id) > 0
    }
    if not response_ids:
        return
    Card.objects.filter(
        user=user,
        card_type=CardType.SPINE,
        response_id__in=response_ids,
        started_at__isnull=True,
    ).update(started_at=at or timezone.now())


def mark_card_started(user, card: Card, scope: dict | None = None) -> None:
    """Persist presentation and propagate genuinely subject-owned activity."""
    at = timezone.now()
    if card.started_at is None:
        Card.objects.filter(
            pk=card.pk,
            user=user,
            started_at__isnull=True,
        ).update(started_at=at)
        card.started_at = at

    response_ids = set()
    if card.response_id:
        response_ids.add(card.response_id)
    elif card.phrase_id and card.phrase.tier == PhraseTier.SUBJECT:
        scope_response = str((scope or {}).get("response") or "")
        if scope_response.isdigit():
            response_ids.add(int(scope_response))
        else:
            response_ids.update(
                card.phrase.source_prompts.filter(
                    is_active=True,
                    response__is_active=True,
                ).values_list("response_id", flat=True)
            )

    mark_response_started(user, response_ids, at=at)


def annotation_response_ids(source_path: str, source_key: str = "") -> set[int]:
    """Resolve the response represented by a static or review annotation."""
    parsed = urlsplit(source_path)
    response_ids = set()

    path_match = RESPONSE_PATH_RE.fullmatch(parsed.path)
    if path_match:
        response_ids.add(int(path_match.group("response_id")))

    scoped_response = parse_qs(parsed.query).get("response", [])
    if scoped_response and scoped_response[0].isdigit():
        response_ids.add(int(scoped_response[0]))

    if source_key.startswith(RESPONSE_SOURCE_PREFIX):
        content_key = source_key[len(RESPONSE_SOURCE_PREFIX) :]
        for suffix in ANNOTATION_SURFACE_SUFFIXES:
            if content_key.endswith(suffix):
                content_key = content_key[: -len(suffix)]
                break
        response_id = (
            Response.objects.filter(
                content_key=content_key,
                is_active=True,
            )
            .values_list("pk", flat=True)
            .first()
        )
        if response_id:
            response_ids.add(response_id)

    return response_ids


def mark_annotation_subject_started(
    user,
    source_path: str,
    source_key: str = "",
) -> None:
    mark_response_started(
        user,
        annotation_response_ids(source_path, source_key),
    )
