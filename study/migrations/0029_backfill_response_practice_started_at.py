# Backfill only unambiguous direct response practice. Card.started_at cannot be
# copied wholesale because its historical backfill also includes annotations,
# personalization, and response-linked vocabulary.

from django.db import migrations
from django.db.models import Min, Q


def backfill_response_practice_started_at(apps, schema_editor):
    Card = apps.get_model("study", "Card")
    ReviewLog = apps.get_model("study", "ReviewLog")
    ReviewSession = apps.get_model("study", "ReviewSession")

    first_reviews = {
        row["card_id"]: row["first_review"]
        for row in ReviewLog.objects.filter(card__card_type="spine")
        .values("card_id")
        .annotate(first_review=Min("reviewed_at"))
    }
    practiced_cards = list(
        Card.objects.filter(card_type="spine").filter(
            Q(pk__in=first_reviews) | ~Q(state="new")
        )
    )
    for card in practiced_cards:
        card.response_practice_started_at = (
            first_reviews.get(card.pk)
            or card.last_reviewed
            or card.created_at
        )
    if practiced_cards:
        Card.objects.bulk_update(
            practiced_cards,
            ["response_practice_started_at"],
            batch_size=1000,
        )

    for session in ReviewSession.objects.all().iterator():
        for card_id in (session.current_card_id, session.previous_card_id):
            if not card_id:
                continue
            Card.objects.filter(
                pk=card_id,
                card_type="spine",
                response_practice_started_at__isnull=True,
            ).update(response_practice_started_at=session.updated_at)


class Migration(migrations.Migration):

    dependencies = [
        ("study", "0028_card_response_practice_started_at"),
    ]

    operations = [
        migrations.RunPython(
            backfill_response_practice_started_at,
            migrations.RunPython.noop,
        ),
    ]
