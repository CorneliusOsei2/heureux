from __future__ import annotations

import importlib
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.apps import apps
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from study import content, queue, srs
from study.management.commands.import_content import Command
from study.models import (
    Annotation,
    AnnotationKind,
    Argument,
    Card,
    CardState,
    CardType,
    ContentImportState,
    Phrase,
    Prompt,
    Rating,
    Response,
    ReviewLog,
    ReviewSession,
    Theme,
)

from . import factories


class PhraseLotMigrationTests(TestCase):
    def test_retiering_clears_only_saved_phrase_batch_sessions(self):
        phrase_user = factories.make_user("phrase-session")
        phrase_card = factories.make_phrase_card(user=phrase_user)
        phrase_session = ReviewSession.objects.create(
            user=phrase_user,
            current_card=phrase_card,
            previous_card=phrase_card,
            scope={"kind": "phrase", "category": "liaisons", "batch": "2"},
            revisit_seen_card_ids=[phrase_card.pk],
            presentation_token="stale-token",
        )
        response_user = factories.make_user("response-session")
        response_card = factories.make_spine_card(user=response_user)
        response_session = ReviewSession.objects.create(
            user=response_user,
            current_card=response_card,
            scope={"kind": "spine", "theme": "culture", "batch": "2"},
            presentation_token="valid-token",
        )

        migration = importlib.import_module(
            "study.migrations.0017_reset_phrase_batch_sessions"
        )
        migration.reset_phrase_batch_sessions(apps, None)

        phrase_session.refresh_from_db()
        response_session.refresh_from_db()
        self.assertEqual(phrase_session.scope, {})
        self.assertIsNone(phrase_session.current_card_id)
        self.assertIsNone(phrase_session.previous_card_id)
        self.assertEqual(phrase_session.revisit_seen_card_ids, [])
        self.assertEqual(phrase_session.presentation_token, "")
        self.assertEqual(
            response_session.scope,
            {"kind": "spine", "theme": "culture", "batch": "2"},
        )
        self.assertEqual(response_session.current_card_id, response_card.pk)
        self.assertEqual(response_session.presentation_token, "valid-token")

    def test_resizing_phrase_lots_clears_saved_batch_sessions_again(self):
        phrase_user = factories.make_user("resized-phrase-session")
        phrase_card = factories.make_phrase_card(user=phrase_user)
        phrase_session = ReviewSession.objects.create(
            user=phrase_user,
            current_card=phrase_card,
            scope={"kind": "phrase", "category": "liaisons", "batch": "2"},
            presentation_token="stale-token",
        )
        response_user = factories.make_user("resized-response-session")
        response_card = factories.make_spine_card(user=response_user)
        response_session = ReviewSession.objects.create(
            user=response_user,
            current_card=response_card,
            scope={"kind": "spine", "theme": "culture", "batch": "2"},
            presentation_token="valid-token",
        )

        migration = importlib.import_module(
            "study.migrations.0019_subject_vocabulary_tier"
        )
        migration.reset_resized_phrase_batch_sessions(apps, None)

        phrase_session.refresh_from_db()
        response_session.refresh_from_db()
        self.assertEqual(phrase_session.scope, {})
        self.assertIsNone(phrase_session.current_card_id)
        self.assertEqual(phrase_session.presentation_token, "")
        self.assertEqual(
            response_session.scope,
            {"kind": "spine", "theme": "culture", "batch": "2"},
        )
        self.assertEqual(response_session.current_card_id, response_card.pk)


class CardStartedAtMigrationTests(TestCase):
    def test_backfill_combines_review_session_annotation_and_vocab_activity(self):
        user = factories.make_user("started-at-backfill")
        task = factories.make_task()
        theme = factories.make_theme("started-backfill", task=task)

        reviewed = factories.make_spine_card(user=user, theme=theme)
        reviewed_at = timezone.now() - timedelta(days=6)
        ReviewLog.objects.create(
            user=user,
            card=reviewed,
            reviewed_at=reviewed_at,
            rating=Rating.GOOD,
            state_before=CardState.NEW,
            state_after=CardState.LEARNING,
        )

        annotated = factories.make_spine_card(user=user, theme=theme)
        annotation_at = timezone.now() - timedelta(days=5)
        Annotation.objects.create(
            user=user,
            task=task,
            kind=AnnotationKind.HIGHLIGHT,
            quote="Passage déjà surligné",
            source_path=f"/response/{annotated.response_id}/",
            start_offset=0,
            end_offset=24,
            created_at=annotation_at,
        )

        vocabulary_response = factories.make_response(theme=theme)
        vocabulary_spine = Card.objects.create(
            user=user,
            card_type=CardType.SPINE,
            response=vocabulary_response,
        )
        vocabulary_phrase = factories.make_phrase(tier="subject")
        vocabulary_phrase.source_prompts.add(
            vocabulary_response.prompts.get(is_canonical=True)
        )
        vocabulary_at = timezone.now() - timedelta(days=4)
        factories.make_phrase_card(
            user=user,
            phrase=vocabulary_phrase,
            started_at=vocabulary_at,
        )

        session_card = factories.make_spine_card(user=user, theme=theme)
        session = ReviewSession.objects.create(
            user=user,
            current_card=session_card,
        )

        migration = importlib.import_module(
            "study.migrations.0026_backfill_card_started_at"
        )
        migration.backfill_card_started_at(apps, None)

        reviewed.refresh_from_db()
        annotated.refresh_from_db()
        vocabulary_spine.refresh_from_db()
        session_card.refresh_from_db()
        self.assertEqual(reviewed.started_at, reviewed_at)
        self.assertEqual(annotated.started_at, annotation_at)
        self.assertEqual(vocabulary_spine.started_at, vocabulary_at)
        self.assertEqual(session_card.started_at, session.updated_at)

        practice_migration = importlib.import_module(
            "study.migrations.0029_backfill_response_practice_started_at"
        )
        practice_migration.backfill_response_practice_started_at(apps, None)
        reviewed.refresh_from_db()
        annotated.refresh_from_db()
        vocabulary_spine.refresh_from_db()
        session_card.refresh_from_db()
        self.assertEqual(
            reviewed.response_practice_started_at,
            reviewed_at,
        )
        self.assertIsNone(annotated.response_practice_started_at)
        self.assertIsNone(vocabulary_spine.response_practice_started_at)
        self.assertEqual(
            session_card.response_practice_started_at,
            session.updated_at,
        )


class NonDestructiveImportTests(TestCase):
    def _response_data(self, response, *, body_hash="b" * 64, body="Updated"):
        prompt = response.prompts.get(is_canonical=True)
        argument = response.arguments.get(order=1)
        return content.ResponseData(
            content_key=response.content_key,
            body_hash=body_hash,
            theme=response.theme.name,
            family=response.family.name,
            prompt=prompt.text,
            reformulation=response.reformulation,
            position=response.position,
            position_claire=response.position_claire,
            nuance=response.nuance,
            conclusion=response.conclusion,
            body=body,
            body_html=f"<p>{body}</p>",
            arguments=[
                content.ArgumentData(
                    order=argument.order,
                    idea=argument.idea,
                    developpement=argument.developpement,
                    exemple=argument.exemple,
                    consequence=argument.consequence,
                )
            ],
            prompts=[
                content.PromptData(
                    content_key=prompt.content_key,
                    theme=prompt.theme.name,
                    number=prompt.number,
                    text=prompt.text,
                    family=prompt.family.name,
                    is_canonical=True,
                )
            ],
        )

    def test_response_text_edit_preserves_card_and_review_history(self):
        user = factories.make_user("import-user")
        card = factories.make_spine_card(user=user)
        srs.review(card, Rating.GOOD)
        response = card.response
        response_id = response.pk
        card_id = card.pk
        log_id = ReviewLog.objects.get().pk
        data = self._response_data(response)

        imported = Command()._import_responses(
            [data],
            {response.theme.name: response.theme},
            {response.family.name: response.family},
        )

        response.refresh_from_db()
        card.refresh_from_db()
        self.assertEqual(imported[data.content_key].pk, response_id)
        self.assertEqual(response.body, "Updated")
        self.assertEqual(card.pk, card_id)
        self.assertEqual(card.reps, 1)
        self.assertTrue(ReviewLog.objects.filter(pk=log_id, card=card).exists())

    def test_unchanged_response_skips_response_and_argument_updates(self):
        response = factories.make_response()
        data = self._response_data(response)
        command = Command()
        theme_map = {response.theme.name: response.theme}
        family_map = {response.family.name: response.family}
        command._import_responses([data], theme_map, family_map)

        with (
            patch.object(
                Response.objects,
                "bulk_update",
                wraps=Response.objects.bulk_update,
            ) as response_bulk_update,
            patch.object(
                Argument.objects,
                "bulk_update",
                wraps=Argument.objects.bulk_update,
            ) as argument_bulk_update,
        ):
            command._import_responses([data], theme_map, family_map)

        response_bulk_update.assert_not_called()
        argument_bulk_update.assert_not_called()

    def test_removed_response_is_archived_without_deleting_private_state(self):
        user = factories.make_user("archive-user")
        card = factories.make_spine_card(user=user)
        srs.review(card, Rating.GOOD)
        response = card.response

        Command()._import_responses([], {}, {})

        response.refresh_from_db()
        self.assertFalse(response.is_active)
        self.assertTrue(Card.objects.filter(pk=card.pk).exists())
        self.assertTrue(ReviewLog.objects.filter(card_id=card.pk).exists())
        self.assertFalse(
            queue.scoped_cards(user=user).filter(pk=card.pk).exists()
        )

    def test_response_split_copies_existing_schedule_to_new_card(self):
        user = factories.make_user("split-user")
        source_card = factories.make_spine_card(user=user)
        source_card.state = CardState.REVIEW
        source_card.reps = 7
        source_card.interval_days = 12
        source_card.last_reviewed = timezone.now()
        source_card.save()
        response = source_card.response
        canonical = response.prompts.get(is_canonical=True)
        split_prompt = Prompt.objects.create(
            content_key=f"{response.theme.slug}:p999",
            theme=response.theme,
            number=999,
            response=response,
            family=response.family,
            text="Split prompt",
        )
        first = self._response_data(response)
        first.prompts = [
            content.PromptData(
                content_key=canonical.content_key,
                theme=canonical.theme.name,
                number=canonical.number,
                text=canonical.text,
                family=canonical.family.name,
                is_canonical=True,
            )
        ]
        second = self._response_data(response)
        second.content_key = split_prompt.content_key
        second.prompt = split_prompt.text
        second.prompts = [
            content.PromptData(
                content_key=split_prompt.content_key,
                theme=split_prompt.theme.name,
                number=split_prompt.number,
                text=split_prompt.text,
                family=split_prompt.family.name,
                is_canonical=True,
            )
        ]
        command = Command()
        theme_map = {response.theme.name: response.theme}
        family_map = {response.family.name: response.family}

        response_map = command._import_responses(
            [first, second], theme_map, family_map
        )
        command._import_prompts(
            [first, second], response_map, theme_map, family_map
        )
        command._sync_cards(response_map, user=user)
        command._reconcile_response_cards(response_map)

        split_card = Card.objects.get(
            user=user,
            response=response_map[second.content_key],
        )
        self.assertNotEqual(split_card.response_id, response.pk)
        self.assertEqual(split_card.state, CardState.REVIEW)
        self.assertEqual(split_card.reps, 7)
        self.assertEqual(split_card.interval_days, 12)

    def test_response_merge_keeps_most_recent_schedule(self):
        user = factories.make_user("merge-user")
        target_card = factories.make_spine_card(user=user)
        source_card = factories.make_spine_card(user=user)
        now = timezone.now()
        Card.objects.filter(pk=target_card.pk).update(
            state=CardState.REVIEW,
            reps=3,
            interval_days=4,
            last_reviewed=now - timedelta(days=2),
            started_at=now - timedelta(days=10),
        )
        Card.objects.filter(pk=source_card.pk).update(
            state=CardState.REVIEW,
            reps=9,
            interval_days=21,
            last_reviewed=now,
            started_at=now - timedelta(days=3),
            subject_completed_at=now - timedelta(days=1),
        )
        target_card.refresh_from_db()
        source_card.refresh_from_db()
        target_response = target_card.response
        source_response = source_card.response
        data = self._response_data(target_response)
        data.prompts.append(
            content.PromptData(
                content_key=source_response.prompts.get(
                    is_canonical=True
                ).content_key,
                theme=source_response.theme.name,
                number=source_response.prompts.get(is_canonical=True).number,
                text=source_response.prompt,
                family=source_response.family.name,
                is_canonical=False,
            )
        )
        command = Command()
        theme_map = {
            target_response.theme.name: target_response.theme,
            source_response.theme.name: source_response.theme,
        }
        family_map = {
            target_response.family.name: target_response.family,
            source_response.family.name: source_response.family,
        }

        response_map = command._import_responses(
            [data], theme_map, family_map
        )
        command._import_prompts(
            [data], response_map, theme_map, family_map
        )
        command._sync_cards(response_map, user=user)
        command._reconcile_response_cards(response_map)

        target_card.refresh_from_db()
        source_response.refresh_from_db()
        self.assertEqual(target_card.reps, 9)
        self.assertEqual(target_card.interval_days, 21)
        self.assertEqual(
            target_card.subject_completed_at,
            now - timedelta(days=1),
        )
        self.assertEqual(
            target_card.started_at,
            now - timedelta(days=10),
        )
        self.assertFalse(source_response.is_active)

    def test_phrase_merge_keeps_the_strongest_existing_schedule(self):
        user = factories.make_user("phrase-merge-user")
        target_phrase = factories.make_phrase()
        target_phrase.phrase_id = "ED15"
        target_phrase.save(update_fields=["phrase_id"])
        source_phrase = factories.make_phrase(
            category=target_phrase.category,
            tier="response",
        )
        source_phrase.phrase_id = "W25"
        source_phrase.save(update_fields=["phrase_id"])
        target_card = factories.make_phrase_card(
            user=user,
            phrase=target_phrase,
            started_at=timezone.now() - timedelta(days=10),
        )
        source_started_at = timezone.now() - timedelta(days=3)
        source_card = factories.make_phrase_card(
            user=user,
            phrase=source_phrase,
            state=CardState.REVIEW,
            reps=8,
            interval_days=19,
            last_reviewed=timezone.now(),
            needs_revisit=True,
            started_at=source_started_at,
        )

        Command()._reconcile_phrase_cards()

        target_card.refresh_from_db()
        source_card.refresh_from_db()
        self.assertEqual(target_card.reps, 8)
        self.assertEqual(target_card.interval_days, 19)
        self.assertTrue(target_card.needs_revisit)
        self.assertLess(target_card.started_at, source_started_at)
        self.assertEqual(source_card.reps, 8)

    def test_local_phrase_keeps_recognition_progress_on_production_card(self):
        user = factories.make_user("direction-merge-user")
        phrase = factories.make_phrase(tier="response")
        production = factories.make_phrase_card(user=user, phrase=phrase)
        recognition = factories.make_phrase_card(
            user=user,
            phrase=phrase,
            card_type="phrase_recog",
            state=CardState.REVIEW,
            reps=11,
            interval_days=24,
            last_reviewed=timezone.now(),
            needs_revisit=True,
        )

        Command()._reconcile_local_phrase_directions()

        production.refresh_from_db()
        recognition.refresh_from_db()
        self.assertEqual(production.reps, 11)
        self.assertEqual(production.interval_days, 24)
        self.assertTrue(production.needs_revisit)
        self.assertEqual(recognition.reps, 11)

    def test_bulk_phrase_import_preserves_identity_and_replaces_sources(self):
        response = factories.make_response()
        prompt = response.prompts.get(is_canonical=True)
        old_response = factories.make_response()
        old_prompt = old_response.prompts.get(is_canonical=True)
        phrase = factories.make_phrase(tier="response", lot_order=777)
        phrase.phrase_id = "BULK1"
        phrase.save(update_fields=["phrase_id"])
        phrase.source_prompts.add(old_prompt)
        user = factories.make_user("bulk-phrase-import")
        card = factories.make_phrase_card(
            user=user,
            phrase=phrase,
            state=CardState.REVIEW,
            reps=6,
            interval_days=12,
        )
        phrase_id = phrase.pk
        data = content.PhraseData(
            phrase_id="BULK1",
            tier="subject",
            category="Mots clés du sujet",
            english_cue="updated cue",
            expression="mise à jour",
            anchor="mise à jour",
            example="Une mise à jour utile.",
            note="Updated note.",
            sources_raw=f"{prompt.theme.name} P{prompt.number}",
            sources=((prompt.theme.name, prompt.number),),
            order=1500,
        )
        new_data = content.PhraseData(
            phrase_id="BULK2",
            tier="subject",
            category="Mots clés du sujet",
            english_cue="new cue",
            expression="nouveau terme",
            anchor="nouveau terme",
            example="Un nouveau terme utile.",
            note="New note.",
            sources_raw=f"{prompt.theme.name} P{prompt.number}",
            sources=((prompt.theme.name, prompt.number),),
            order=1501,
        )

        command = Command()
        prompt_index = {(prompt.theme.name, prompt.number): prompt}
        command._import_phrases([data, new_data], prompt_index)
        with patch.object(
            Phrase.objects,
            "bulk_update",
            wraps=Phrase.objects.bulk_update,
        ) as phrase_bulk_update:
            command._import_phrases([data, new_data], prompt_index)
        phrase_bulk_update.assert_not_called()

        phrase.refresh_from_db()
        card.refresh_from_db()
        self.assertEqual(phrase.pk, phrase_id)
        self.assertEqual(phrase.tier, "subject")
        self.assertEqual(phrase.english_cue, "updated cue")
        self.assertEqual(phrase.lot_order, 777)
        self.assertEqual(list(phrase.source_prompts.all()), [prompt])
        self.assertEqual(card.reps, 6)
        self.assertEqual(card.interval_days, 12)
        imported_new = phrase.__class__.objects.get(phrase_id="BULK2")
        self.assertGreater(imported_new.lot_order, phrase.lot_order)
        self.assertEqual(list(imported_new.source_prompts.all()), [prompt])

        command._import_phrases([], {})

        phrase.refresh_from_db()
        imported_new.refresh_from_db()
        card.refresh_from_db()
        self.assertFalse(phrase.is_active)
        self.assertFalse(imported_new.is_active)
        self.assertEqual(card.reps, 6)

    def test_card_sync_creates_only_production_for_subject_vocabulary(self):
        user = factories.make_user("subject-card-sync")
        response = factories.make_response()
        shared = factories.make_phrase(tier="shared")
        subject = factories.make_phrase(tier="subject")

        Command()._sync_cards(
            {response.content_key: response},
            user=user,
        )

        self.assertTrue(
            Card.objects.filter(
                user=user,
                response=response,
                card_type=CardType.SPINE,
            ).exists()
        )
        self.assertEqual(
            set(
                Card.objects.filter(user=user, phrase=shared).values_list(
                    "card_type",
                    flat=True,
                )
            ),
            {
                CardType.PHRASE_PRODUCTION,
                CardType.PHRASE_RECOGNITION,
            },
        )
        self.assertEqual(
            list(
                Card.objects.filter(user=user, phrase=subject).values_list(
                    "card_type",
                    flat=True,
                )
            ),
            [CardType.PHRASE_PRODUCTION],
        )

    def test_archived_task_keeps_notes_and_manual_delete_uncategorizes_them(self):
        user = factories.make_user("note-owner")
        part = factories.make_part()
        task = factories.make_task(part)
        annotation = Annotation.objects.create(
            user=user,
            task=task,
            kind=AnnotationKind.NOTE,
            body="Keep this note.",
        )

        Command()._import_sections([])

        task.refresh_from_db()
        annotation.refresh_from_db()
        self.assertFalse(task.is_active)
        self.assertEqual(annotation.task_id, task.pk)

        task.delete()
        annotation.refresh_from_db()
        self.assertIsNone(annotation.task_id)

    def test_parser_emits_unique_immutable_keys_and_full_hashes(self):
        responses = content.parse_responses()
        prompt_keys = [
            prompt.content_key
            for response in responses
            for prompt in response.prompts
        ]

        self.assertEqual(len({item.content_key for item in responses}), 130)
        self.assertEqual(len(set(prompt_keys)), 167)
        self.assertTrue(all(len(item.body_hash) == 64 for item in responses))


class ImportFingerprintTests(TestCase):
    def test_if_changed_skips_an_already_loaded_bundle(self):
        call_command("import_content", stdout=StringIO())
        marker = ContentImportState.objects.get(pk="bundled")
        self.assertEqual(marker.fingerprint, Command._source_fingerprint())

        theme = Theme.objects.get(slug="culture")
        theme.display_name = "Local sentinel"
        theme.save(update_fields=["display_name"])
        output = StringIO()

        call_command("import_content", if_changed=True, stdout=output)

        theme.refresh_from_db()
        self.assertEqual(theme.display_name, "Local sentinel")
        self.assertIn("import skipped", output.getvalue())
