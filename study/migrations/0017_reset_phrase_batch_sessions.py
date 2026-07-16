from django.db import migrations


def reset_phrase_batch_sessions(apps, schema_editor):
    ReviewSession = apps.get_model("study", "ReviewSession")
    stale_sessions = []
    for session in ReviewSession.objects.exclude(scope={}):
        scope = session.scope
        if not (
            isinstance(scope, dict)
            and scope.get("kind") == "phrase"
            and scope.get("batch")
        ):
            continue
        session.scope = {}
        session.current_card_id = None
        session.previous_card_id = None
        session.previous_review_id = None
        session.revisit_seen_card_ids = []
        session.presentation_token = ""
        stale_sessions.append(session)
    if stale_sessions:
        ReviewSession.objects.bulk_update(
            stale_sessions,
            [
                "scope",
                "current_card",
                "previous_card",
                "previous_review",
                "revisit_seen_card_ids",
                "presentation_token",
            ],
        )


class Migration(migrations.Migration):
    dependencies = [
        ("study", "0016_phrase_tiers_and_stable_lots"),
    ]

    operations = [
        migrations.RunPython(
            reset_phrase_batch_sessions,
            migrations.RunPython.noop,
        ),
    ]
