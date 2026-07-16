"""Tests for unrestricted review-queue construction, ordering, and scoping."""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from study import queue as q, srs
from study.models import CardState, CardType, PhraseTier, Rating, ReviewLog

from .factories import (
    make_part,
    make_phrase,
    make_phrase_card,
    make_spine_card,
    make_task,
    make_theme,
)


class QueueCountsTests(TestCase):
    def setUp(self):
        self.now = timezone.now()

    def test_all_new_cards_are_available(self):
        for _ in range(5):
            make_spine_card()
        counts = q.queue_counts(now=self.now)
        self.assertEqual(counts["new_total"], 5)
        self.assertEqual(counts["new_available"], 5)

    def test_all_due_reviews_are_available(self):
        for _ in range(8):
            make_spine_card(
                state=CardState.REVIEW,
                due=self.now - timedelta(days=1),
                interval_days=5,
            )
        counts = q.queue_counts(now=self.now)
        self.assertEqual(counts["review_due_total"], 8)
        self.assertEqual(counts["review_due"], 8)

    def test_suspended_cards_are_excluded(self):
        make_spine_card(suspended=True)
        counts = q.queue_counts(now=self.now)
        self.assertEqual(counts["new_total"], 0)

    def test_completed_new_today_does_not_cap_remaining_cards(self):
        card = make_spine_card()
        srs.review(card, Rating.GOOD)  # logs a NEW review today
        for _ in range(3):
            make_spine_card()
        counts = q.queue_counts(now=timezone.now())
        self.assertEqual(counts["new_done_today"], 1)
        self.assertEqual(counts["new_available"], 3)

    def test_suspending_reviewed_card_does_not_hide_other_new_cards(self):
        reviewed = make_spine_card()
        srs.review(reviewed, Rating.GOOD)
        reviewed.suspended = True
        reviewed.save(update_fields=["suspended"])
        for _ in range(3):
            make_spine_card()

        counts = q.queue_counts(now=timezone.now())
        self.assertEqual(counts["new_done_today"], 1)
        self.assertEqual(counts["new_available"], 3)

    def test_theme_scope_filters(self):
        make_spine_card(theme=make_theme(slug="culture", order=1))
        make_spine_card(theme=make_theme(slug="sante", order=2))
        counts = q.queue_counts({"theme": "culture"}, now=self.now)
        self.assertEqual(counts["new_total"], 1)

    def test_phrase_kind_scope_filters(self):
        make_spine_card()
        make_phrase_card()
        counts = q.queue_counts({"kind": "phrase"}, now=self.now)
        self.assertEqual(counts["new_total"], 1)

    def test_task_scope_includes_responses_and_linked_phrases(self):
        task = make_task(part=make_part("orale"), slug="tache-3")
        theme = make_theme(slug="culture", task=task)
        response_card = make_spine_card(theme=theme)
        phrase = make_phrase()
        phrase.source_prompts.add(response_card.response.prompts.first())
        phrase_card = make_phrase_card(phrase=phrase)

        other_task = make_task(part=make_part("ecrit"), slug="tache-1")
        make_spine_card(theme=make_theme(slug="economie", task=other_task))

        scope = {"part": "orale", "task": "tache-3"}
        ids = set(q.scoped_cards(scope).values_list("id", flat=True))
        self.assertEqual(ids, {response_card.id, phrase_card.id})

    def test_part_and_task_must_match_the_same_phrase_source(self):
        oral_t1 = make_task(part=make_part("orale"), slug="tache-1")
        oral_theme = make_theme(slug="culture", task=oral_t1)
        oral_response = make_spine_card(theme=oral_theme).response

        written_t3 = make_task(part=make_part("ecrit"), slug="tache-3")
        written_theme = make_theme(slug="economie", task=written_t3)
        written_response = make_spine_card(theme=written_theme).response

        phrase = make_phrase()
        phrase.source_prompts.add(
            oral_response.prompts.first(),
            written_response.prompts.first(),
        )
        phrase_card = make_phrase_card(phrase=phrase)

        self.assertNotIn(
            phrase_card.id,
            q.scoped_cards(
                {"part": "orale", "task": "tache-3"}
            ).values_list("id", flat=True),
        )

    def test_task_availability_ignores_reviews_from_other_tasks(self):
        oral_task = make_task(part=make_part("orale"), slug="tache-3")
        oral_theme = make_theme(slug="culture", task=oral_task)
        make_spine_card(theme=oral_theme)
        make_spine_card(theme=oral_theme)

        other_task = make_task(part=make_part("ecrit"), slug="tache-1")
        other_card = make_spine_card(
            theme=make_theme(slug="economie", task=other_task)
        )
        srs.review(other_card, Rating.GOOD)

        counts = q.queue_counts(
            {"part": "orale", "task": "tache-3"},
            now=timezone.now(),
        )
        self.assertEqual(counts["new_available"], 2)

    def test_revisit_scope_ignores_due_dates(self):
        future = make_spine_card(
            state=CardState.REVIEW,
            due=self.now + timedelta(days=30),
            interval_days=10,
            needs_revisit=True,
            revisit_added_at=self.now,
        )
        make_spine_card()
        counts = q.queue_counts({"kind": "revisit"}, now=self.now)
        self.assertEqual(counts["revisit_total"], 1)
        self.assertEqual(counts["total_due"], 1)
        self.assertEqual(
            q.next_card({"kind": "revisit"}, now=self.now).id,
            future.id,
        )

    def test_weak_scope_uses_current_and_repeated_recent_difficulty(self):
        current_difficulty = make_spine_card(
            state=CardState.REVIEW,
            due=self.now + timedelta(days=30),
            interval_days=10,
            reps=3,
            last_rating=Rating.AGAIN,
        )
        repeated_difficulty = make_spine_card(
            state=CardState.REVIEW,
            due=self.now + timedelta(days=30),
            interval_days=10,
            reps=5,
            last_rating=Rating.GOOD,
        )
        recovered = make_spine_card(
            state=CardState.REVIEW,
            due=self.now + timedelta(days=30),
            interval_days=10,
            reps=3,
            last_rating=Rating.GOOD,
        )
        make_spine_card()

        for card, failures in (
            (repeated_difficulty, 2),
            (recovered, 1),
        ):
            for offset in range(failures):
                ReviewLog.objects.create(
                    card=card,
                    reviewed_at=self.now - timedelta(days=offset + 1),
                    rating=Rating.AGAIN,
                    state_before=CardState.REVIEW,
                    state_after=CardState.RELEARNING,
                )

        weak_ids = set(
            q.scoped_cards({"kind": "weak"}).values_list("id", flat=True)
        )
        self.assertEqual(
            weak_ids,
            {current_difficulty.id, repeated_difficulty.id},
        )
        counts = q.queue_counts({"kind": "weak"}, now=self.now)
        self.assertEqual(counts["weak_total"], 2)
        self.assertEqual(counts["total_due"], 2)
        self.assertEqual(
            q.next_card({"kind": "weak"}, now=self.now).id,
            repeated_difficulty.id,
        )

    def test_response_scope_selects_only_its_linked_phrase_cards(self):
        response = make_spine_card().response
        linked_phrase = make_phrase()
        linked_phrase.source_prompts.add(response.prompts.first())
        linked_card = make_phrase_card(phrase=linked_phrase)
        make_phrase_card()

        scope = {"kind": "phrase", "response": str(response.id)}
        counts = q.queue_counts(scope, now=self.now)
        self.assertEqual(counts["new_total"], 1)
        self.assertEqual(q.next_card(scope, now=self.now).id, linked_card.id)

    def test_response_local_vocabulary_only_enters_its_response_deck(self):
        response = make_spine_card().response
        phrase = make_phrase(tier=PhraseTier.RESPONSE)
        phrase.source_prompts.add(response.prompts.first())
        production = make_phrase_card(phrase=phrase)
        recognition = make_phrase_card(
            phrase=phrase,
            card_type=CardType.PHRASE_RECOGNITION,
        )

        self.assertFalse(
            q.scoped_cards({"kind": "phrase"}).filter(pk=production.pk).exists()
        )
        response_cards = q.scoped_cards(
            {"kind": "phrase", "response": str(response.pk)}
        )
        self.assertTrue(response_cards.filter(pk=production.pk).exists())
        self.assertFalse(response_cards.filter(pk=recognition.pk).exists())

    def test_category_batch_scope_contains_at_most_fifteen_cards(self):
        cards = [make_phrase_card() for _ in range(32)]
        scope = {
            "kind": "phrase",
            "category": cards[0].phrase.category.slug,
            "batch": "2",
        }

        batch_ids = list(
            q.scoped_cards(scope).order_by("id").values_list("id", flat=True)
        )

        self.assertEqual(batch_ids, [card.id for card in cards[15:30]])
        self.assertEqual(q.queue_counts(scope, now=self.now)["new_available"], 15)

    def test_batch_membership_does_not_shift_when_a_card_is_suspended(self):
        cards = [make_phrase_card() for _ in range(20)]
        cards[0].suspended = True
        cards[0].save(update_fields=["suspended"])
        scope = {
            "kind": "phrase",
            "category": cards[0].phrase.category.slug,
            "batch": "2",
        }

        batch_ids = list(
            q.scoped_cards(scope).order_by("id").values_list("id", flat=True)
        )

        self.assertEqual(batch_ids, [card.id for card in cards[15:]])

    def test_phrase_lot_contains_fifteen_expressions_and_keeps_twins_together(self):
        phrases = [make_phrase() for _ in range(16)]
        pairs = [
            (
                make_phrase_card(phrase=phrase),
                make_phrase_card(
                    phrase=phrase,
                    card_type=CardType.PHRASE_RECOGNITION,
                ),
            )
            for phrase in phrases
        ]
        scope = {
            "kind": "phrase",
            "category": phrases[0].category.slug,
            "batch": "1",
        }

        lot = q.scoped_cards(scope)

        self.assertEqual(lot.values("phrase_id").distinct().count(), 15)
        self.assertEqual(lot.count(), 30)
        self.assertEqual(
            set(lot.values_list("pk", flat=True)),
            {card.pk for pair in pairs[:15] for card in pair},
        )

    def test_phrase_lot_membership_uses_stable_lot_order(self):
        phrases = [make_phrase() for _ in range(16)]
        cards = [make_phrase_card(phrase=phrase) for phrase in phrases]
        scope = {
            "kind": "phrase",
            "category": phrases[0].category.slug,
            "batch": "1",
        }
        expected = set(
            q.scoped_cards(scope).values_list("pk", flat=True)
        )

        for index, phrase in enumerate(reversed(phrases), start=1):
            phrase.order = index
            phrase.save(update_fields=["order"])

        self.assertEqual(
            set(q.scoped_cards(scope).values_list("pk", flat=True)),
            expected,
        )
        self.assertNotIn(cards[-1].pk, expected)

    def test_new_phrase_twins_are_presented_next_to_each_other(self):
        first = make_phrase()
        second = make_phrase(category=first.category)
        first_production = make_phrase_card(phrase=first)
        first_recognition = make_phrase_card(
            phrase=first,
            card_type=CardType.PHRASE_RECOGNITION,
        )
        make_phrase_card(phrase=second)
        make_phrase_card(
            phrase=second,
            card_type=CardType.PHRASE_RECOGNITION,
        )
        scope = {
            "kind": "phrase",
            "category": first.category.slug,
            "batch": "1",
        }

        self.assertEqual(q.next_card(scope).pk, first_production.pk)
        first_production.state = CardState.REVIEW
        first_production.due = self.now + timedelta(days=1)
        first_production.save(update_fields=["state", "due"])

        self.assertEqual(q.next_card(scope).pk, first_recognition.pk)


class NextCardTests(TestCase):
    def setUp(self):
        self.now = timezone.now()

    def test_due_learning_beats_new(self):
        make_spine_card()  # NEW
        learn = make_spine_card(
            state=CardState.LEARNING,
            due=self.now - timedelta(minutes=1),
            learning_step=0,
        )
        self.assertEqual(q.next_card(now=self.now).id, learn.id)

    def test_returns_none_when_nothing_due(self):
        make_spine_card(
            state=CardState.REVIEW,
            due=self.now + timedelta(days=3),
            interval_days=10,
        )
        self.assertIsNone(q.next_card(now=self.now))

    def test_new_card_served_last(self):
        new = make_spine_card()
        card = q.next_card(now=self.now)
        self.assertEqual(card.id, new.id)
        self.assertEqual(card.state, CardState.NEW)
