"""Tests for the SM-2 scheduling engine and undo."""

from __future__ import annotations

from django.test import TestCase
from django.utils import timezone

from study import srs
from study.models import CardState, Rating, ReviewLog

from .factories import make_spine_card


class ComputeTests(TestCase):
    """The pure `compute` function — no database mutation."""

    def setUp(self):
        self.now = timezone.now()

    def _c(self, **kw):
        base = dict(
            state=CardState.NEW,
            interval_days=0.0,
            ease=srs.STARTING_EASE,
            learning_step=0,
            lapses=0,
            rating=Rating.GOOD,
            now=self.now,
        )
        base.update(kw)
        return srs.compute(**base)

    def test_new_again_stays_first_learning_step(self):
        s = self._c(state=CardState.NEW, rating=Rating.AGAIN)
        self.assertEqual(s.state, CardState.LEARNING)
        self.assertEqual(s.learning_step, 0)
        self.assertAlmostEqual(
            (s.due - self.now).total_seconds(),
            srs.LEARNING_STEPS_MIN[0] * 60,
            delta=2,
        )

    def test_new_good_advances_then_graduates(self):
        step1 = self._c(state=CardState.NEW, rating=Rating.GOOD)
        self.assertEqual(step1.state, CardState.LEARNING)
        self.assertEqual(step1.learning_step, 1)

        grad = self._c(
            state=CardState.LEARNING, learning_step=1, rating=Rating.GOOD
        )
        self.assertEqual(grad.state, CardState.REVIEW)
        self.assertEqual(grad.interval_days, srs.GRADUATING_INTERVAL_DAYS)

    def test_new_easy_graduates_immediately(self):
        s = self._c(state=CardState.NEW, rating=Rating.EASY)
        self.assertEqual(s.state, CardState.REVIEW)
        self.assertEqual(s.interval_days, srs.EASY_INTERVAL_DAYS)

    def test_review_good_multiplies_by_ease(self):
        s = self._c(
            state=CardState.REVIEW, interval_days=10, ease=2.5, rating=Rating.GOOD
        )
        self.assertEqual(s.state, CardState.REVIEW)
        self.assertEqual(s.interval_days, 25)  # 10 * 2.5
        self.assertEqual(s.ease, 2.5)

    def test_review_hard_uses_hard_factor_and_lowers_ease(self):
        s = self._c(
            state=CardState.REVIEW, interval_days=10, ease=2.5, rating=Rating.HARD
        )
        self.assertEqual(s.interval_days, 12)  # 10 * 1.2
        self.assertAlmostEqual(s.ease, 2.5 + srs.HARD_EASE_DELTA, places=4)

    def test_review_again_lapses_into_relearning(self):
        s = self._c(
            state=CardState.REVIEW, interval_days=30, ease=2.5, lapses=0,
            rating=Rating.AGAIN,
        )
        self.assertEqual(s.state, CardState.RELEARNING)
        self.assertEqual(s.lapses, 1)
        self.assertAlmostEqual(s.ease, 2.5 + srs.AGAIN_EASE_DELTA, places=4)

    def test_interval_always_grows_by_at_least_one_day(self):
        s = self._c(
            state=CardState.REVIEW, interval_days=1, ease=1.3, rating=Rating.GOOD
        )
        self.assertGreaterEqual(s.interval_days, 2)

    def test_interval_is_capped(self):
        s = self._c(
            state=CardState.REVIEW, interval_days=10000, ease=2.5,
            rating=Rating.GOOD,
        )
        self.assertLessEqual(s.interval_days, srs.MAX_INTERVAL_DAYS)

    def test_ease_never_below_minimum(self):
        s = self._c(
            state=CardState.REVIEW, interval_days=10, ease=1.3, rating=Rating.HARD
        )
        self.assertGreaterEqual(s.ease, srs.MIN_EASE)


class ReviewAndUndoTests(TestCase):
    def test_review_persists_and_logs_with_snapshot(self):
        card = make_spine_card()
        srs.review(card, Rating.GOOD)
        card.refresh_from_db()
        self.assertEqual(card.state, CardState.LEARNING)
        self.assertEqual(card.reps, 1)
        log = ReviewLog.objects.get()
        self.assertEqual(log.state_before, CardState.NEW)
        self.assertEqual(log.card_before["state"], CardState.NEW)

    def test_undo_restores_card_and_deletes_log(self):
        card = make_spine_card()
        srs.review(card, Rating.GOOD)
        restored = srs.undo_last()
        self.assertEqual(restored.id, card.id)
        card.refresh_from_db()
        self.assertEqual(card.state, CardState.NEW)
        self.assertEqual(card.reps, 0)
        self.assertIsNone(card.due)
        self.assertEqual(ReviewLog.objects.count(), 0)

    def test_undo_is_multi_level(self):
        card = make_spine_card()
        srs.review(card, Rating.GOOD)  # NEW -> LEARNING step 1
        srs.review(card, Rating.GOOD)  # LEARNING -> REVIEW
        self.assertEqual(ReviewLog.objects.count(), 2)
        srs.undo_last()
        card.refresh_from_db()
        self.assertEqual(card.state, CardState.LEARNING)
        srs.undo_last()
        card.refresh_from_db()
        self.assertEqual(card.state, CardState.NEW)
        self.assertEqual(ReviewLog.objects.count(), 0)

    def test_undo_with_no_history_returns_none(self):
        self.assertIsNone(srs.undo_last())

    def test_preview_intervals_has_all_four_labels(self):
        card = make_spine_card()
        previews = srs.preview_intervals(card)
        self.assertEqual(set(previews.keys()), set(int(r) for r in Rating))
        self.assertTrue(all(isinstance(v, str) and v for v in previews.values()))
