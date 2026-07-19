# Backfill Card.started_at.
#
# Split out of 0025 so the started_at index (created in 0025) lives in its own
# transaction. PostgreSQL refuses "CREATE INDEX ... because it has pending
# trigger events" when the index is built in the same transaction as the bulk
# UPDATEs this backfill performs; SQLite does not enforce that, so the combined
# migration only failed in production.

import re
from urllib.parse import parse_qs, urlsplit

from django.db import migrations
from django.db.models import Min, Q


RESPONSE_PATH_RE = re.compile(r"^/response/(?P<response_id>\d+)/$")


def backfill_card_started_at(apps, schema_editor):
    Card = apps.get_model("study", "Card")
    ReviewLog = apps.get_model("study", "ReviewLog")
    ReviewSession = apps.get_model("study", "ReviewSession")
    Annotation = apps.get_model("study", "Annotation")
    PersonalResponse = apps.get_model("study", "PersonalResponse")
    Response = apps.get_model("study", "Response")

    first_reviews = {
        row["card_id"]: row["first_review"]
        for row in ReviewLog.objects.values("card_id").annotate(
            first_review=Min("reviewed_at")
        )
    }
    reviewed_cards = list(
        Card.objects.filter(
            Q(pk__in=first_reviews) | ~Q(state="new")
        )
    )
    for card in reviewed_cards:
        card.started_at = (
            first_reviews.get(card.pk)
            or card.last_reviewed
            or card.created_at
        )
    if reviewed_cards:
        Card.objects.bulk_update(
            reviewed_cards,
            ["started_at"],
            batch_size=1000,
        )

    for session in ReviewSession.objects.exclude(
        current_card_id__isnull=True
    ).iterator():
        Card.objects.filter(
            pk=session.current_card_id,
            started_at__isnull=True,
        ).update(started_at=session.updated_at)

    response_starts = {}

    def remember(user_id, response_id, started_at):
        if not user_id or not response_id or not started_at:
            return
        key = (user_id, response_id)
        current = response_starts.get(key)
        if current is None or started_at < current:
            response_starts[key] = started_at

    for user_id, response_id, started_at in Card.objects.filter(
        card_type="spine",
        started_at__isnull=False,
    ).values_list("user_id", "response_id", "started_at"):
        remember(user_id, response_id, started_at)

    for user_id, started_at, response_id in Card.objects.filter(
        phrase__tier__in=["response", "subject"],
        started_at__isnull=False,
        phrase__source_prompts__is_active=True,
    ).values_list(
        "user_id",
        "started_at",
        "phrase__source_prompts__response_id",
    ):
        remember(user_id, response_id, started_at)

    response_keys = dict(
        Response.objects.values_list("content_key", "pk")
    )
    for annotation in Annotation.objects.exclude(
        user_id__isnull=True
    ).iterator():
        parsed = urlsplit(annotation.source_path)
        response_ids = set()
        match = RESPONSE_PATH_RE.fullmatch(parsed.path)
        if match:
            response_ids.add(int(match.group("response_id")))
        scoped_response = parse_qs(parsed.query).get("response", [])
        if scoped_response and scoped_response[0].isdigit():
            response_ids.add(int(scoped_response[0]))
        if annotation.source_key.startswith("response:"):
            content_key = annotation.source_key[len("response:") :]
            for suffix in (":front", ":back"):
                if content_key.endswith(suffix):
                    content_key = content_key[: -len(suffix)]
                    break
            response_id = response_keys.get(content_key)
            if response_id:
                response_ids.add(response_id)
        for response_id in response_ids:
            remember(
                annotation.user_id,
                response_id,
                annotation.created_at,
            )

    for personal in PersonalResponse.objects.all().iterator():
        remember(
            personal.user_id,
            personal.response_id,
            personal.created_at,
        )

    spine_cards = list(
        Card.objects.filter(
            card_type="spine",
            user_id__isnull=False,
        )
    )
    changed = []
    for card in spine_cards:
        started_at = response_starts.get((card.user_id, card.response_id))
        if started_at and (
            card.started_at is None or started_at < card.started_at
        ):
            card.started_at = started_at
            changed.append(card)
    if changed:
        Card.objects.bulk_update(
            changed,
            ["started_at"],
            batch_size=1000,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("study", "0025_card_started_at"),
    ]

    operations = [
        migrations.RunPython(
            backfill_card_started_at,
            migrations.RunPython.noop,
        ),
    ]
