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

from study import srs, views as study_views
from study.models import (
    Card,
    CardState,
    Rating,
    ReviewLog,
    ReviewSession,
    Settings,
)

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
        self.user = factories.make_user("smoke")
        self.client.force_login(self.user)
        factories.make_content()

    def test_core_pages_render(self):
        names = [
            "study:dashboard",
            "study:review_overview",
            "study:expressions_overview",
            "study:stats_overview",
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
        nested_names = [
            "study:task_browse",
            "study:task_phrases",
            "study:task_review_hub",
            "study:task_revisit_list",
            "study:task_stats",
            "study:task_search",
        ]
        for name in nested_names:
            with self.subTest(name=name):
                response = self.client.get(
                    reverse(name, args=["orale", "tache-3"])
                )
                self.assertEqual(response.status_code, 200)

        family = factories.make_family()
        response = self.client.get(
            reverse(
                "study:task_family_detail",
                args=["orale", "tache-3", family.slug],
            )
        )
        self.assertEqual(response.status_code, 200)

    def test_top_level_tabs_group_content_by_part_and_task(self):
        written = factories.make_part("ecrite", available=False)
        written.name = "Expression écrite"
        written.save(update_fields=["name"])
        factories.make_task(written, "ecrit", available=False)
        destinations = (
            ("study:review_overview", "study:task_review_hub"),
            ("study:expressions_overview", "study:task_phrases"),
            ("study:stats_overview", "study:task_stats"),
        )

        for overview, destination in destinations:
            with self.subTest(overview=overview):
                response = self.client.get(reverse(overview))
                self.assertContains(response, "Expression orale")
                self.assertContains(response, "Expression écrite")
                self.assertContains(response, "Tache 3")
                self.assertContains(response, "À venir")
                self.assertContains(
                    response,
                    reverse(destination, args=["orale", "tache-3"]),
                )
                self.assertIsNone(response.context["content_task"])

    def test_primary_navigation_opens_grouped_hubs(self):
        response = self.client.get(
            reverse("study:task_detail", args=["orale", "tache-3"])
        )

        for name in (
            "study:review_overview",
            "study:expressions_overview",
            "study:stats_overview",
        ):
            self.assertContains(response, f'href="{reverse(name)}"')

    def test_review_overview_preserves_resume_shortcut(self):
        card = self.user.study_cards.first()
        session = ReviewSession.load(self.user)
        session.current_card = card
        session.scope = {"part": "orale", "task": "tache-3"}
        session.save(update_fields=["current_card", "scope"])

        response = self.client.get(reverse("study:review_overview"))

        self.assertContains(response, "Continuer là où je me suis arrêté")

    def test_task_hub_organizes_all_content(self):
        response = self.client.get(
            reverse("study:task_detail", args=["orale", "tache-3"])
        )
        self.assertContains(response, "Sujets &amp; réponses")
        self.assertContains(response, "Expressions &amp; vocabulaire")
        self.assertContains(response, "Révision")
        self.assertContains(
            response,
            'class="nav__primary-link',
            count=4,
        )
        for label in ("Accueil", "Réviser", "Expressions", "Stats"):
            self.assertContains(response, f">{label}</a>")
        self.assertContains(response, 'class="footer__inner"')

    def test_global_pages_do_not_false_highlight_task_navigation(self):
        response = self.client.get(reverse("study:browse"))
        self.assertNotContains(
            response,
            'class="nav__task is-active"',
        )

    def test_hierarchy_uses_expression_paths(self):
        url = reverse("study:task_detail", args=["orale", "tache-3"])
        self.assertEqual(url, "/expression/orale/tache-3/")
        response = self.client.get(
            "/epreuve/orale/tache-3/?source=bookmark"
        )
        self.assertRedirects(
            response,
            "/expression/orale/tache-3/?source=bookmark",
            status_code=301,
            fetch_redirect_response=False,
        )


class TaskOrganizationTests(TestCase):
    def setUp(self):
        self.user = factories.make_user("organizer")
        self.client.force_login(self.user)
        Settings.load(self.user)
        self.part = factories.make_part("orale")
        self.task = factories.make_task(self.part, "tache-3")
        self.theme = factories.make_theme("culture", task=self.task)
        self.response_card = factories.make_spine_card(theme=self.theme)
        self.phrase = factories.make_phrase()
        self.phrase.english_cue = "oral-task-only"
        self.phrase.save(update_fields=["english_cue"])
        self.phrase.source_prompts.add(
            self.response_card.response.prompts.first()
        )
        self.phrase_card = factories.make_phrase_card(phrase=self.phrase)

        other_part = factories.make_part("ecrit")
        other_task = factories.make_task(other_part, "tache-1")
        other_theme = factories.make_theme("economie", task=other_task)
        other_response = factories.make_spine_card(theme=other_theme).response
        self.other_phrase = factories.make_phrase()
        self.other_phrase.english_cue = "other-task-only"
        self.other_phrase.save(update_fields=["english_cue"])
        self.other_phrase.source_prompts.add(other_response.prompts.first())
        self.other_phrase_card = factories.make_phrase_card(
            phrase=self.other_phrase
        )

    def _task_url(self, name):
        return reverse(name, args=[self.part.slug, self.task.slug])

    def test_expression_page_is_limited_to_its_task(self):
        response = self.client.get(
            self._task_url("study:task_phrases"),
            {"category": self.phrase.category.slug},
        )
        self.assertContains(response, "oral-task-only")
        self.assertNotContains(response, "other-task-only")

    def test_task_search_is_limited_to_its_task(self):
        own = self.client.get(
            self._task_url("study:task_search"),
            {"q": "oral-task-only"},
        )
        other = self.client.get(
            self._task_url("study:task_search"),
            {"q": "other-task-only"},
        )
        self.assertEqual(own.context["result_count"], 1)
        self.assertEqual(other.context["result_count"], 0)

    def test_task_progress_and_revisit_include_phrase_cards(self):
        srs.review(self.phrase_card, Rating.GOOD)
        srs.review(self.other_phrase_card, Rating.GOOD)
        self.phrase_card.needs_revisit = True
        self.phrase_card.revisit_added_at = timezone.now()
        self.phrase_card.save(
            update_fields=["needs_revisit", "revisit_added_at"]
        )
        self.other_phrase_card.needs_revisit = True
        self.other_phrase_card.revisit_added_at = timezone.now()
        self.other_phrase_card.save(
            update_fields=["needs_revisit", "revisit_added_at"]
        )

        stats_response = self.client.get(
            self._task_url("study:task_stats")
        )
        revisit_response = self.client.get(
            self._task_url("study:task_revisit_list")
        )
        self.assertEqual(stats_response.context["total_reviews"], 1)
        self.assertEqual(revisit_response.context["revisit_count"], 1)
        self.assertContains(revisit_response, "Expressions &amp; vocabulaire")
        self.assertContains(revisit_response, self.phrase.expression)
        self.assertNotContains(
            revisit_response,
            self.other_phrase.expression,
        )

    def test_same_task_slug_in_another_part_does_not_leak(self):
        written_task = factories.make_task(
            factories.make_part("autre"),
            "tache-3",
        )
        written_theme = factories.make_theme(
            "technologie",
            task=written_task,
        )
        factories.make_spine_card(theme=written_theme)

        browse_response = self.client.get(
            self._task_url("study:task_browse")
        )
        stats_response = self.client.get(
            self._task_url("study:task_stats")
        )
        self.assertEqual(
            [
                item["theme"]
                for item in browse_response.context["themes"]
            ],
            [self.theme],
        )
        self.assertNotIn(
            written_theme,
            [
                item["theme"]
                for item in stats_response.context["themes"]
            ],
        )

    def test_task_family_page_keeps_the_originating_task_scope(self):
        shared_family = factories.make_family("shared-family")
        own = factories.make_spine_card(
            theme=self.theme,
            family=shared_family,
        )
        other = factories.make_spine_card(
            theme=factories.make_theme(
                "technologie",
                task=factories.make_task(
                    factories.make_part("autre"),
                    "tache-3",
                ),
            ),
            family=shared_family,
        )

        response = self.client.get(
            reverse(
                "study:task_family_detail",
                args=[self.part.slug, self.task.slug, shared_family.slug],
            )
        )
        prompt_ids = {
            row["prompt"].id for row in response.context["rows"]
        }
        self.assertIn(own.response.prompts.get().id, prompt_ids)
        self.assertNotIn(other.response.prompts.get().id, prompt_ids)

    def test_task_streak_uses_only_the_task_review_logs(self):
        srs.review(self.phrase_card, Rating.GOOD)
        ReviewLog.objects.filter(card=self.phrase_card).update(
            reviewed_at=timezone.now() - timedelta(days=1)
        )
        self.phrase_card.suspended = True
        self.phrase_card.save(update_fields=["suspended"])
        srs.review(self.other_phrase_card, Rating.GOOD)

        response = self.client.get(
            self._task_url("study:task_stats")
        )
        self.assertEqual(response.context["streak"], 1)

    def test_review_hub_groups_task_study_modes_and_resume(self):
        session = ReviewSession.load(self.user)
        session.current_card = self.response_card
        session.scope = {
            "part": self.part.slug,
            "task": self.task.slug,
        }
        session.save(update_fields=["current_card", "scope"])

        response = self.client.get(
            self._task_url("study:task_review_hub")
        )
        self.assertContains(response, "Réponses argumentées")
        self.assertContains(response, "Expressions &amp; vocabulaire")
        self.assertContains(response, "Ma liste à revoir")
        self.assertContains(
            response,
            "Continuer là où je me suis arrêté",
        )
        self.assertContains(
            response,
            "?kind=spine&amp;part=orale&amp;task=tache-3",
        )
        self.assertContains(
            response,
            "?kind=phrase&amp;part=orale&amp;task=tache-3",
        )
        self.assertContains(response, "Choisir un thème")
        self.assertContains(response, "Choisir un lot")

    def test_primary_navigation_resolves_same_slug_task_by_part(self):
        written_task = factories.make_task(
            factories.make_part("autre"),
            self.task.slug,
        )
        self.task.name = "Parcours oral"
        self.task.save(update_fields=["name"])
        written_task.name = "Parcours écrit"
        written_task.save(update_fields=["name"])

        response = self.client.get(
            reverse(
                "study:task_detail",
                args=[written_task.part.slug, written_task.slug],
            )
        )
        self.assertEqual(response.context["content_task"], written_task)
        self.assertContains(response, "Parcours écrit")


class CategoryBatchViewsTests(TestCase):
    def setUp(self):
        self.user = factories.make_user("batcher")
        self.client.force_login(self.user)
        first = factories.make_phrase_card(user=self.user)
        self.category = first.phrase.category
        self.phrase_cards = [first]
        for _ in range(15):
            phrase = factories.make_phrase(category=self.category)
            self.phrase_cards.append(
                factories.make_phrase_card(phrase=phrase, user=self.user)
            )

    def test_expression_category_displays_fifteen_card_lots(self):
        response = self.client.get(
            reverse("study:phrases"),
            {"category": self.category.slug},
        )

        self.assertEqual(len(response.context["review_batches"]), 2)
        self.assertEqual(
            response.context["review_batches"][0]["card_count"],
            15,
        )
        self.assertEqual(
            response.context["review_batches"][1]["card_count"],
            1,
        )
        self.assertContains(response, "Lots de 15 cartes")
        self.assertContains(response, "Lot 02")
        self.assertContains(response, "batch=2")

    def test_category_batch_review_selects_only_that_lot(self):
        params = {
            "kind": "phrase",
            "category": self.category.slug,
            "batch": "2",
        }
        page = self.client.get(reverse("study:review"), params)
        state = self.client.get(reverse("study:review_next"), params).json()

        self.assertContains(page, "Lot 2")
        self.assertEqual(state["card_id"], self.phrase_cards[15].id)
        self.assertEqual(state["counts"]["new_available"], 1)

    def test_batch_cards_show_in_progress_and_completed_states(self):
        future = timezone.now() + timedelta(days=5)
        first_batch = self.phrase_cards[:15]
        first_batch[0].state = CardState.LEARNING
        first_batch[0].due = future
        first_batch[0].save(update_fields=["state", "due"])

        in_progress = self.client.get(
            reverse("study:phrases"),
            {"category": self.category.slug},
        )

        self.assertEqual(
            in_progress.context["review_batches"][0]["status"],
            "in-progress",
        )
        self.assertContains(in_progress, "En cours")

        Card.objects.filter(
            pk__in=[card.pk for card in first_batch]
        ).update(state=CardState.REVIEW, due=future)
        complete = self.client.get(
            reverse("study:phrases"),
            {"category": self.category.slug},
        )

        completed_batch = complete.context["review_batches"][0]
        self.assertEqual(completed_batch["status"], "complete")
        self.assertEqual(completed_batch["seen_count"], 15)
        self.assertFalse(completed_batch["can_review"])
        self.assertContains(complete, "✓")
        self.assertContains(complete, "Terminé")

    def test_suspended_lot_is_visible_but_not_clickable(self):
        Card.objects.filter(
            pk__in=[card.pk for card in self.phrase_cards[:15]]
        ).update(suspended=True)

        response = self.client.get(
            reverse("study:phrases"),
            {"category": self.category.slug},
        )

        first_batch = response.context["review_batches"][0]
        self.assertEqual(first_batch["status"], "unavailable")
        self.assertFalse(first_batch["can_review"])
        self.assertContains(response, 'aria-disabled="true"')
        self.assertContains(response, "Suspendu")

    def test_finished_batch_offers_the_next_available_lot(self):
        response = self.client.get(
            reverse("study:review"),
            {
                "kind": "phrase",
                "category": self.category.slug,
                "batch": "1",
            },
        )

        self.assertEqual(response.context["next_batch"]["number"], 2)
        self.assertContains(response, "Passer au lot 2")
        self.assertContains(response, "Voir tous les lots")
        self.assertContains(response, "batch=2")

    def test_response_theme_displays_fifteen_card_lots(self):
        theme = factories.make_theme("education")
        for _ in range(16):
            factories.make_spine_card(theme=theme, user=self.user)

        response = self.client.get(
            reverse("study:theme_detail", args=[theme.slug])
        )

        self.assertEqual(len(response.context["review_batches"]), 2)
        self.assertEqual(
            [batch["card_count"] for batch in response.context["review_batches"]],
            [15, 1],
        )
        self.assertContains(response, "Lots de 15 cartes")


class ReviewFlowTests(TestCase):
    def setUp(self):
        self.user = factories.make_user("reviewer")
        self.client.force_login(self.user)
        s = Settings.load(self.user)
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
        session = ReviewSession.load(self.user)
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
        session = ReviewSession.load(self.user)
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

        session = ReviewSession.load(self.user)
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
        self.user = factories.make_user("concurrent")
        self.client.force_login(self.user)
        settings = Settings.load(self.user)
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

        def observed_locked_session(user):
            if threading.current_thread().name == "answer":
                answer_attempted_lock.set()
            session = original_locked_session(user)
            if threading.current_thread().name == "answer":
                answer_acquired_lock.set()
            return session

        def stale_next():
            try:
                client = Client()
                client.force_login(self.user)
                responses["stale"] = client.get(
                    reverse("study:review_next")
                )
            except BaseException as exc:  # pragma: no cover - thread handoff
                failures.append(exc)

        def answer():
            try:
                client = Client()
                client.force_login(self.user)
                responses["answer"] = client.post(
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

        session = ReviewSession.load(self.user)
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
    def setUp(self):
        self.user = factories.make_user("settings")
        self.client.force_login(self.user)

    def test_unsuspend_all(self):
        card = factories.make_spine_card(suspended=True)
        r = self.client.post(
            reverse("study:settings"), {"action": "unsuspend_all"}
        )
        self.assertEqual(r.status_code, 302)
        card.refresh_from_db()
        self.assertFalse(card.suspended)

    def test_settings_explain_unlimited_practice(self):
        response = self.client.get(reverse("study:settings"))

        self.assertContains(response, "Aucun plafond quotidien")
        self.assertNotContains(response, "new_cards_per_day")
        self.assertNotContains(response, "max_reviews_per_day")

    def test_reset_clears_progress(self):
        from study import srs

        card = factories.make_spine_card(
            needs_revisit=True,
            revisit_added_at=timezone.now(),
        )
        srs.review(card, Rating.GOOD)
        session = ReviewSession.load(self.user)
        session.current_card = card
        session.scope = {"kind": "spine"}
        session.save()
        r = self.client.post(reverse("study:settings"), {"action": "reset"})
        self.assertEqual(r.status_code, 302)
        card.refresh_from_db()
        self.assertEqual(card.state, CardState.NEW)
        self.assertEqual(ReviewLog.objects.count(), 0)
        self.assertFalse(card.needs_revisit)
        session = ReviewSession.load(self.user)
        self.assertEqual(session.scope, {})
        self.assertIsNone(session.current_card_id)
        self.assertEqual(session.presentation_token, "")
