"""Tests for review-queue construction: caps, ordering, scoping."""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from study import queue as q, srs
from study.models import CardState, Rating, Settings

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
        s = Settings.load()
        s.new_cards_per_day = 2
        s.max_reviews_per_day = 5
        s.save()

    def test_new_cards_are_capped(self):
        for _ in range(5):
            make_spine_card()
        counts = q.queue_counts(now=self.now)
        self.assertEqual(counts["new_total"], 5)
        self.assertEqual(counts["new_available"], 2)

    def test_due_reviews_are_capped(self):
        for _ in range(8):
            make_spine_card(
                state=CardState.REVIEW,
                due=self.now - timedelta(days=1),
                interval_days=5,
            )
        counts = q.queue_counts(now=self.now)
        self.assertEqual(counts["review_due_total"], 8)
        self.assertEqual(counts["review_due"], 5)

    def test_suspended_cards_are_excluded(self):
        make_spine_card(suspended=True)
        counts = q.queue_counts(now=self.now)
        self.assertEqual(counts["new_total"], 0)

    def test_completed_new_today_reduces_availability(self):
        card = make_spine_card()
        srs.review(card, Rating.GOOD)  # logs a NEW review today
        for _ in range(3):
            make_spine_card()
        counts = q.queue_counts(now=timezone.now())
        self.assertEqual(counts["new_done_today"], 1)
        self.assertEqual(counts["new_available"], 1)  # min(3, 2 - 1)

    def test_suspending_reviewed_card_does_not_restore_daily_capacity(self):
        reviewed = make_spine_card()
        srs.review(reviewed, Rating.GOOD)
        reviewed.suspended = True
        reviewed.save(update_fields=["suspended"])
        for _ in range(3):
            make_spine_card()

        counts = q.queue_counts(now=timezone.now())
        self.assertEqual(counts["new_done_today"], 1)
        self.assertEqual(counts["new_available"], 1)

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

    def test_task_daily_limit_ignores_reviews_from_other_tasks(self):
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

    def test_revisit_scope_ignores_due_dates_and_daily_caps(self):
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


class NextCardTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        Settings.load()

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
