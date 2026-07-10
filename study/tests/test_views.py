"""View tests: review flow, undo, suspend, health, PWA, smoke."""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from study.models import CardState, Rating, ReviewLog, Settings

from . import factories


class HealthTests(TestCase):
    def test_healthz_ok(self):
        r = self.client.get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")


class PWATests(TestCase):
    def test_manifest(self):
        r = self.client.get("/manifest.webmanifest")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Heureux", r.content.decode())

    def test_service_worker(self):
        r = self.client.get("/sw.js")
        self.assertEqual(r.status_code, 200)
        self.assertIn("CACHE", r.content.decode())

    def test_offline_page(self):
        self.assertEqual(self.client.get("/offline/").status_code, 200)


class SmokeTests(TestCase):
    def setUp(self):
        factories.make_content()

    def test_core_pages_render(self):
        names = [
            "study:dashboard",
            "study:review",
            "study:browse",
            "study:phrases",
            "study:search",
            "study:stats",
            "study:settings",
        ]
        for name in names:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_hierarchy_pages_render(self):
        self.assertEqual(
            self.client.get(reverse("study:part_detail", args=["orale"])).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                reverse("study:task_detail", args=["orale", "tache-3"])
            ).status_code,
            200,
        )


class ReviewFlowTests(TestCase):
    def setUp(self):
        s = Settings.load()
        s.new_cards_per_day = 10
        s.max_reviews_per_day = 100
        s.save()
        self.card = factories.make_spine_card()

    def test_answer_advances_and_logs(self):
        r = self.client.post(
            reverse("study:review_answer"),
            {"card_id": self.card.id, "rating": Rating.GOOD, "elapsed_ms": 1200},
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["can_undo"])
        self.card.refresh_from_db()
        self.assertEqual(self.card.state, CardState.LEARNING)
        self.assertEqual(ReviewLog.objects.count(), 1)

    def test_invalid_rating_rejected(self):
        r = self.client.post(
            reverse("study:review_answer"),
            {"card_id": self.card.id, "rating": 9},
        )
        self.assertEqual(r.status_code, 400)

    def test_undo_restores_card(self):
        self.client.post(
            reverse("study:review_answer"),
            {"card_id": self.card.id, "rating": Rating.GOOD},
        )
        r = self.client.post(reverse("study:review_undo"))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["undone"])
        self.card.refresh_from_db()
        self.assertEqual(self.card.state, CardState.NEW)
        self.assertEqual(ReviewLog.objects.count(), 0)

    def test_undo_without_history_is_noop(self):
        r = self.client.post(reverse("study:review_undo"))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["undone"])

    def test_suspend_removes_card(self):
        r = self.client.post(
            reverse("study:review_suspend"), {"card_id": self.card.id}
        )
        self.assertEqual(r.status_code, 200)
        self.card.refresh_from_db()
        self.assertTrue(self.card.suspended)


class SettingsActionTests(TestCase):
    def test_unsuspend_all(self):
        card = factories.make_spine_card(suspended=True)
        r = self.client.post(
            reverse("study:settings"), {"action": "unsuspend_all"}
        )
        self.assertEqual(r.status_code, 302)
        card.refresh_from_db()
        self.assertFalse(card.suspended)

    def test_reset_clears_progress(self):
        from study import srs

        card = factories.make_spine_card()
        srs.review(card, Rating.GOOD)
        r = self.client.post(reverse("study:settings"), {"action": "reset"})
        self.assertEqual(r.status_code, 302)
        card.refresh_from_db()
        self.assertEqual(card.state, CardState.NEW)
        self.assertEqual(ReviewLog.objects.count(), 0)
