"""View tests: review flow, undo, revisit, health, PWA, smoke."""

from __future__ import annotations

import threading
from datetime import timedelta
from unittest.mock import patch

from django.test import (
    Client,
    TestCase,
    TransactionTestCase,
    override_settings,
    skipUnlessDBFeature,
)
from django.urls import reverse
from django.utils import timezone

from study import views as study_views
from study.models import CardState, Rating, ReviewLog, ReviewSession, Settings

from . import factories


class HealthTests(TestCase):
    def test_healthz_ok(self):
        r = self.client.get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

    @override_settings(ALLOWED_HOSTS=["heureux.onrender.com"])
    def test_healthz_survives_unknown_host(self):
        # Render's internal probe hits the service with an unpredictable Host
        # (often a private IP) over plain HTTP. The health check must still be
        # 200 rather than a DisallowedHost 400, or the deploy never goes live.
        r = self.client.get("/healthz", HTTP_HOST="10.222.26.203")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

    @override_settings(ALLOWED_HOSTS=["heureux.onrender.com"])
    def test_other_paths_still_validate_host(self):
        # Host validation must stay active for real traffic — only /healthz is
        # exempt, so an unknown Host on any other path is still rejected.
        r = self.client.get("/", HTTP_HOST="attacker.example")
        self.assertEqual(r.status_code, 400)


class PWATests(TestCase):
    def test_manifest(self):
        r = self.client.get("/manifest.webmanifest")
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn("Heureux", body)
        self.assertIn('"start_url": "/review/"', body)

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
            "study:revisit_list",
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

    def _present(self, query=""):
        r = self.client.get(reverse("study:review_next") + query)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["done"])
        return r.json()

    def test_answer_advances_and_logs(self):
        presented = self._present()
        r = self.client.post(
            reverse("study:review_answer"),
            {
                "card_id": self.card.id,
                "action": "correct",
                "presentation_token": presented["presentation_token"],
                "elapsed_ms": 1200,
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["can_undo"])
        self.card.refresh_from_db()
        self.assertEqual(self.card.state, CardState.LEARNING)
        self.assertEqual(ReviewLog.objects.count(), 1)

    def test_review_page_has_only_revisit_and_correct_actions(self):
        r = self.client.get(reverse("study:review"))
        self.assertContains(r, 'data-action="revisit"', count=1)
        self.assertContains(r, 'data-action="correct"', count=1)
        self.assertNotContains(r, "Difficile")
        self.assertNotContains(r, "Facile")
        self.assertNotContains(r, "Suspendre")

    def test_revisit_marks_card_and_uses_again_schedule(self):
        presented = self._present()
        r = self.client.post(
            reverse("study:review_answer"),
            {
                "card_id": self.card.id,
                "action": "revisit",
                "presentation_token": presented["presentation_token"],
            },
        )
        self.assertEqual(r.status_code, 200)
        self.card.refresh_from_db()
        self.assertTrue(self.card.needs_revisit)
        self.assertIsNotNone(self.card.revisit_added_at)
        self.assertEqual(self.card.last_rating, Rating.AGAIN)
        self.assertEqual(ReviewLog.objects.get().rating, Rating.AGAIN)

    def test_correct_clears_revisit_mark(self):
        self.card.needs_revisit = True
        self.card.revisit_added_at = timezone.now()
        self.card.save(update_fields=["needs_revisit", "revisit_added_at"])
        presented = self._present()
        r = self.client.post(
            reverse("study:review_answer"),
            {
                "card_id": self.card.id,
                "action": "correct",
                "presentation_token": presented["presentation_token"],
            },
        )
        self.assertEqual(r.status_code, 200)
        self.card.refresh_from_db()
        self.assertFalse(self.card.needs_revisit)
        self.assertIsNone(self.card.revisit_added_at)
        self.assertEqual(self.card.last_rating, Rating.GOOD)
        session = ReviewSession.load()
        self.assertEqual(session.scope, {})
        self.assertIsNone(session.current_card_id)

    def test_revisit_list_is_accessible_and_removable(self):
        self.card.needs_revisit = True
        self.card.revisit_added_at = timezone.now()
        self.card.save(update_fields=["needs_revisit", "revisit_added_at"])
        url = reverse("study:revisit_list")
        r = self.client.get(url)
        self.assertContains(r, self.card.response.prompt)
        r = self.client.post(
            url,
            {"action": "remove", "card_id": self.card.id},
        )
        self.assertRedirects(r, url)
        self.card.refresh_from_db()
        self.assertFalse(self.card.needs_revisit)

    def test_unfinished_card_and_scope_resume(self):
        phrase_card = factories.make_phrase_card()
        self.client.get(reverse("study:review") + "?kind=phrase")
        first = self.client.get(reverse("study:review_next") + "?kind=phrase")
        self.assertEqual(first.json()["card_id"], phrase_card.id)
        session = ReviewSession.load()
        self.assertEqual(session.scope, {"kind": "phrase"})
        self.assertEqual(session.current_card_id, phrase_card.id)

        reopened = self.client.get(reverse("study:review"))
        self.assertEqual(reopened.context["scope"], {"kind": "phrase"})
        resumed = self.client.get(reverse("study:review_next"))
        self.assertEqual(resumed.json()["card_id"], phrase_card.id)

    def test_response_surfaces_show_only_argument_main_points(self):
        detail = self.client.get(
            reverse("study:response_detail", args=[self.card.response_id])
        )
        self.assertContains(detail, self.card.response.arguments.get().idea)
        self.assertNotContains(detail, "Exemple.")

        payload = self.client.get(reverse("study:review_next")).json()
        self.assertIn(self.card.response.arguments.get().idea, payload["back_html"])
        self.assertNotIn("Exemple.", payload["back_html"])

    def test_invalid_rating_rejected(self):
        presented = self._present()
        r = self.client.post(
            reverse("study:review_answer"),
            {
                "card_id": self.card.id,
                "rating": 9,
                "presentation_token": presented["presentation_token"],
            },
        )
        self.assertEqual(r.status_code, 400)

    def test_undo_restores_card(self):
        presented = self._present()
        self.client.post(
            reverse("study:review_answer"),
            {
                "card_id": self.card.id,
                "action": "correct",
                "presentation_token": presented["presentation_token"],
            },
        )
        r = self.client.post(reverse("study:review_undo"))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["undone"])
        self.card.refresh_from_db()
        self.assertEqual(self.card.state, CardState.NEW)
        self.assertEqual(ReviewLog.objects.count(), 0)

    def test_duplicate_presentation_is_rejected(self):
        presented = self._present()
        payload = {
            "card_id": self.card.id,
            "action": "correct",
            "presentation_token": presented["presentation_token"],
        }
        first = self.client.post(reverse("study:review_answer"), payload)
        second = self.client.post(reverse("study:review_answer"), payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(ReviewLog.objects.count(), 1)

    def test_repeated_next_preserves_the_active_presentation(self):
        first = self._present()
        second = self._present()
        self.assertEqual(second["card_id"], first["card_id"])
        self.assertEqual(
            second["presentation_token"],
            first["presentation_token"],
        )

    def test_answer_atomically_reserves_the_next_presentation(self):
        second_card = factories.make_spine_card()
        presented = self._present()
        answered = self.client.post(
            reverse("study:review_answer"),
            {
                "card_id": self.card.id,
                "action": "correct",
                "presentation_token": presented["presentation_token"],
            },
        ).json()

        session = ReviewSession.load()
        self.assertEqual(answered["card_id"], second_card.id)
        self.assertEqual(session.current_card_id, second_card.id)
        self.assertEqual(
            session.presentation_token,
            answered["presentation_token"],
        )

        repeated = self._present()
        self.assertEqual(repeated["card_id"], second_card.id)
        self.assertEqual(
            repeated["presentation_token"],
            answered["presentation_token"],
        )

    def test_revisit_pass_visits_each_marked_card_once(self):
        now = timezone.now()
        self.card.needs_revisit = True
        self.card.revisit_added_at = now - timedelta(minutes=1)
        self.card.save(update_fields=["needs_revisit", "revisit_added_at"])
        second = factories.make_spine_card(
            needs_revisit=True,
            revisit_added_at=now,
        )

        self.client.get(reverse("study:review") + "?kind=revisit")
        first = self._present("?kind=revisit")
        self.assertEqual(first["card_id"], self.card.id)
        next_state = self.client.post(
            reverse("study:review_answer"),
            {
                "kind": "revisit",
                "card_id": self.card.id,
                "action": "revisit",
                "presentation_token": first["presentation_token"],
            },
        ).json()
        self.assertFalse(next_state["done"])
        self.assertEqual(next_state["card_id"], second.id)

        finished = self.client.post(
            reverse("study:review_answer"),
            {
                "kind": "revisit",
                "card_id": second.id,
                "action": "revisit",
                "presentation_token": next_state["presentation_token"],
            },
        ).json()
        self.assertTrue(finished["done"])
        self.card.refresh_from_db()
        second.refresh_from_db()
        self.assertTrue(self.card.needs_revisit)
        self.assertTrue(second.needs_revisit)

    def test_undo_without_history_is_noop(self):
        r = self.client.post(reverse("study:review_undo"))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["undone"])


@skipUnlessDBFeature("has_select_for_update")
class ReviewConcurrencyTests(TransactionTestCase):
    def setUp(self):
        settings = Settings.load()
        settings.new_cards_per_day = 10
        settings.max_reviews_per_day = 100
        settings.save()
        self.card = factories.make_spine_card()
        self.next_card = factories.make_spine_card()

    def test_stale_next_cannot_resurrect_a_graded_card(self):
        initial = self.client.get(reverse("study:review_next")).json()
        stale_selected_card = threading.Event()
        answer_attempted_lock = threading.Event()
        answer_acquired_lock = threading.Event()
        release_stale = threading.Event()
        failures = []
        responses = {}

        original_save_session = study_views._save_review_session
        original_locked_session = study_views._locked_review_session

        def delayed_save_session(session, scope, card=None, **kwargs):
            if threading.current_thread().name == "stale-next":
                if card is None or card.id != self.card.id:
                    raise AssertionError("Stale request did not select card A")
                stale_selected_card.set()
                if not release_stale.wait(timeout=10):
                    raise TimeoutError("Timed out waiting to release stale request")
            return original_save_session(
                session,
                scope,
                card,
                **kwargs,
            )

        def observed_locked_session():
            if threading.current_thread().name == "answer":
                answer_attempted_lock.set()
            session = original_locked_session()
            if threading.current_thread().name == "answer":
                answer_acquired_lock.set()
            return session

        def stale_next():
            try:
                responses["stale"] = Client().get(
                    reverse("study:review_next")
                )
            except BaseException as exc:  # pragma: no cover - thread handoff
                failures.append(exc)

        def answer():
            try:
                responses["answer"] = Client().post(
                    reverse("study:review_answer"),
                    {
                        "card_id": self.card.id,
                        "action": "correct",
                        "presentation_token": initial["presentation_token"],
                    },
                )
            except BaseException as exc:  # pragma: no cover - thread handoff
                failures.append(exc)

        with (
            patch.object(
                study_views,
                "_save_review_session",
                side_effect=delayed_save_session,
            ),
            patch.object(
                study_views,
                "_locked_review_session",
                side_effect=observed_locked_session,
            ),
        ):
            stale_thread = threading.Thread(
                target=stale_next,
                name="stale-next",
            )
            answer_thread = threading.Thread(target=answer, name="answer")
            stale_thread.start()
            self.assertTrue(stale_selected_card.wait(timeout=10))
            answer_thread.start()
            self.assertTrue(answer_attempted_lock.wait(timeout=10))
            self.assertFalse(answer_acquired_lock.wait(timeout=0.2))
            release_stale.set()
            stale_thread.join(timeout=10)
            answer_thread.join(timeout=10)

        self.assertFalse(stale_thread.is_alive())
        self.assertFalse(answer_thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(responses["stale"].status_code, 200)
        self.assertEqual(responses["answer"].status_code, 200)
        self.assertEqual(ReviewLog.objects.count(), 1)

        session = ReviewSession.load()
        self.assertEqual(session.current_card_id, self.next_card.id)
        duplicate = self.client.post(
            reverse("study:review_answer"),
            {
                "card_id": self.card.id,
                "action": "correct",
                "presentation_token": initial["presentation_token"],
            },
        )
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(ReviewLog.objects.count(), 1)


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

        card = factories.make_spine_card(
            needs_revisit=True,
            revisit_added_at=timezone.now(),
        )
        srs.review(card, Rating.GOOD)
        session = ReviewSession.load()
        session.current_card = card
        session.scope = {"kind": "spine"}
        session.save()
        r = self.client.post(reverse("study:settings"), {"action": "reset"})
        self.assertEqual(r.status_code, 302)
        card.refresh_from_db()
        self.assertEqual(card.state, CardState.NEW)
        self.assertEqual(ReviewLog.objects.count(), 0)
        self.assertFalse(card.needs_revisit)
        session = ReviewSession.load()
        self.assertEqual(session.scope, {})
        self.assertIsNone(session.current_card_id)
        self.assertEqual(session.presentation_token, "")
