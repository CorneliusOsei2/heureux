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
    CardType,
    PhraseCategory,
    Prompt,
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
        self.assertIn('"start_url": "/"', body)

    def test_service_worker(self):
        r = self.client.get("/sw.js")
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn('var CACHE = "heureux-v54"', body)
        self.assertIn("study/css/app.css", body)
        self.assertIn("?v=48", body)
        self.assertIn("study/js/app.js", body)
        self.assertIn("?v=27", body)
        self.assertIn("study/js/translate.js", body)
        self.assertIn("study/js/annotations.js", body)
        self.assertIn("SKIP_WAITING", body)
        self.assertIn("no-store", r["Cache-Control"])
        self.assertEqual(r["Service-Worker-Allowed"], "/")

    def test_security_headers_block_inline_scripts_and_sensitive_capabilities(self):
        response = self.client.get("/login/")

        policy = response["Content-Security-Policy"]
        self.assertIn("script-src 'self'", policy)
        self.assertNotIn("'unsafe-inline'", policy.split("script-src", 1)[1].split(";", 1)[0])
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertIn("camera=()", response["Permissions-Policy"])

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
            "study:comprehension_hub",
            "study:comprehension_overview",
            "study:comprehension_oral_overview",
            "study:expression",
            "study:vocabulary",
            "study:notes_overview",
            "study:stats",
            "study:review",
            "study:search",
            "study:settings",
            "study:revisit_list",
        ]
        for name in names:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_vocabulary_hub_groups_four_paths_in_two_domains(self):
        subject_phrase = factories.make_phrase(tier="subject")
        subject_phrase.source_prompts.add(Prompt.objects.first())
        factories.make_phrase_card(phrase=subject_phrase, user=self.user)

        response = self.client.get(reverse("study:vocabulary"))

        self.assertTrue(response.context["vocabulary_landing"])
        self.assertContains(response, "data-vocabulary-domain", count=2)
        self.assertContains(response, "data-vocabulary-path", count=4)
        self.assertContains(response, 'id="comprehension-paths-title"')
        self.assertContains(response, 'id="expression-paths-title"')
        self.assertNotContains(response, 'id="expression-vocabulary"')
        self.assertNotContains(response, 'id="comprehension-vocabulary"')
        self.assertContains(
            response,
            reverse("study:vocabulary")
            + "?part=eo&amp;task=tache-3",
        )

    def test_written_comprehension_card_opens_only_test_decks(self):
        test = factories.make_comprehension_test(
            number=1,
            question_count=1,
        )
        phrase = factories.make_phrase(tier="comprehension")
        phrase.source_questions.add(test.questions.get(number=1))
        factories.make_phrase_card(phrase=phrase, user=self.user)

        landing = self.client.get(reverse("study:vocabulary"))
        self.assertContains(
            landing,
            reverse("study:vocabulary")
            + "?domain=comprehension&amp;mode=ce",
        )

        response = self.client.get(
            reverse("study:vocabulary"),
            {"domain": "comprehension", "mode": "ce"},
        )
        self.assertTrue(response.context["comprehension_directory"])
        self.assertFalse(response.context["vocabulary_landing"])
        self.assertContains(response, "Vocabulaire des tests")
        self.assertContains(response, test.title)
        self.assertNotContains(response, 'id="expression-vocabulary"')

    def test_legacy_gateways_redirect_to_canonical_areas(self):
        destinations = (
            ("study:review_overview", reverse("study:dashboard")),
            ("study:expressions_overview", reverse("study:vocabulary")),
            (
                "study:general_notes",
                reverse("study:notes_overview") + "?scope=general",
            ),
            ("study:browse", reverse("study:expression")),
            ("study:phrases", reverse("study:vocabulary")),
        )
        for legacy_name, canonical_url in destinations:
            with self.subTest(legacy_name=legacy_name):
                response = self.client.get(reverse(legacy_name))
                self.assertRedirects(
                    response,
                    canonical_url,
                    fetch_redirect_response=False,
                )

    def test_authenticated_pages_include_selection_translation_controls(self):
        response = self.client.get(reverse("study:dashboard"))

        self.assertContains(response, "Translate to English")
        self.assertContains(response, "data-copy-selection")
        self.assertContains(response, "data-note-selection")
        self.assertContains(response, "data-highlight-selection")
        self.assertContains(response, 'id="translation-panel"')
        self.assertContains(response, 'id="selection-note-panel"')
        self.assertContains(response, "study/js/translate.js")
        self.assertContains(response, "study/js/annotations.js")
        self.assertContains(response, 'rel="noopener noreferrer"')

    def test_primary_navigation_marks_each_personal_area_current(self):
        destinations = (
            ("study:dashboard", "study:dashboard"),
            ("study:comprehension_hub", "study:comprehension_hub"),
            ("study:expression", "study:expression"),
            ("study:vocabulary", "study:vocabulary"),
            ("study:notes_overview", "study:notes_overview"),
            ("study:stats", "study:stats"),
        )

        for page_name, navigation_name in destinations:
            with self.subTest(page=page_name):
                response = self.client.get(reverse(page_name))
                self.assertContains(
                    response,
                    f'href="{reverse(navigation_name)}" '
                    'class="nav__primary-link is-active" '
                    'aria-current="page"',
                )
                self.assertEqual(
                    response.content.decode().count(
                        'class="nav__primary-link is-active"'
                    ),
                    1,
                )

    def test_hierarchy_pages_render(self):
        self.assertEqual(
            self.client.get(reverse("study:part_detail", args=["eo"])).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                reverse("study:task_detail", args=["eo", "tache-3"])
            ).status_code,
            200,
        )
        nested_names = [
            "study:task_browse",
            "study:task_review_hub",
            "study:task_revisit_list",
        ]
        for name in nested_names:
            with self.subTest(name=name):
                response = self.client.get(
                    reverse(name, args=["eo", "tache-3"])
                )
                self.assertEqual(response.status_code, 200)

        redirects = {
            "study:task_phrases": (
                reverse("study:vocabulary") + "?part=eo&task=tache-3"
            ),
            "study:task_stats": (
                reverse("study:stats") + "?part=eo&task=tache-3"
            ),
            "study:task_search": (
                reverse("study:search") + "?part=eo&task=tache-3"
            ),
        }
        for name, expected in redirects.items():
            with self.subTest(name=name):
                response = self.client.get(
                    reverse(name, args=["eo", "tache-3"])
                )
                self.assertRedirects(
                    response,
                    expected,
                    fetch_redirect_response=False,
                )

        family = factories.make_family()
        response = self.client.get(
            reverse(
                "study:task_family_detail",
                args=["eo", "tache-3", family.slug],
            )
        )
        self.assertEqual(response.status_code, 200)

    def test_expression_hub_groups_content_by_part_and_task(self):
        written = factories.make_part("ee", available=False)
        written.name = "Expression écrite"
        written.save(update_fields=["name"])
        factories.make_task(written, "ecrit", available=False)
        response = self.client.get(reverse("study:expression"))

        self.assertContains(response, "Expression orale")
        self.assertContains(response, "Expression écrite")
        self.assertContains(response, "À venir")
        self.assertContains(
            response,
            reverse("study:part_detail", args=["eo"]),
        )
        self.assertIsNone(response.context["content_task"])

    def test_dashboard_presents_four_explicit_daily_activities(self):
        factories.make_comprehension_test()

        response = self.client.get(reverse("study:dashboard"))

        self.assertContains(response, 'class="daily-card card ', count=4)
        for label in (
            "Restituer des réponses",
            "Activer les mots et tournures",
            "Faire Test 1",
            "Revoir ce que tu as retenu",
        ):
            self.assertContains(response, label)
        self.assertContains(response, reverse("study:expression"))
        self.assertContains(response, reverse("study:vocabulary"))
        self.assertContains(response, reverse("study:comprehension_hub"))
        self.assertContains(response, reverse("study:notes_overview"))

    def test_comprehension_hub_is_the_parent_of_written_and_oral_paths(self):
        factories.make_comprehension_test()
        factories.make_comprehension_test(
            mode="orale",
            question_count=2,
        )

        response = self.client.get(reverse("study:comprehension_hub"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<h1>Compréhension</h1>", html=True)
        self.assertContains(response, ">Écrite</h3>")
        self.assertContains(response, ">Orale</h3>")
        self.assertContains(
            response,
            reverse("study:comprehension_overview"),
        )
        self.assertContains(
            response,
            reverse("study:comprehension_oral_overview"),
        )
        self.assertContains(response, "<b>8</b> groupes", html=True)
        self.assertNotContains(response, "Parcours en préparation")
        self.assertContains(
            response,
            f'href="{reverse("study:comprehension_hub")}" '
            'class="nav__primary-link is-active"',
        )

    def test_primary_navigation_opens_canonical_areas(self):
        response = self.client.get(
            reverse("study:task_detail", args=["eo", "tache-3"])
        )

        for name in (
            "study:dashboard",
            "study:comprehension_hub",
            "study:expression",
            "study:vocabulary",
            "study:notes_overview",
            "study:stats",
        ):
            self.assertContains(response, f'href="{reverse(name)}"')

    def test_dashboard_preserves_resume_shortcut(self):
        card = self.user.study_cards.first()
        session = ReviewSession.load(self.user)
        session.current_card = card
        session.scope = {
            "kind": "spine",
            "part": "eo",
            "task": "tache-3",
        }
        session.save(update_fields=["current_card", "scope"])

        response = self.client.get(reverse("study:dashboard"))

        self.assertContains(response, "Reprendre là où je me suis arrêté")

    def test_task_hub_uses_navigation_without_duplicate_modules(self):
        response = self.client.get(
            reverse("study:task_detail", args=["eo", "tache-3"])
        )
        self.assertContains(response, "Sujets &amp; réponses")
        self.assertNotContains(response, "Pratiquer les réponses")
        self.assertNotContains(response, 'class="task-modules"')
        self.assertContains(response, "Vocabulaire")
        self.assertContains(response, "Progression")
        self.assertContains(
            response,
            'class="nav__primary-link',
            count=6,
        )
        for label in (
            "Accueil",
            "Compréhension",
            "Expression",
            "Vocabulaire",
            "Notes",
            "Stats",
        ):
            self.assertContains(
                response,
                f'<span class="nav__item-label">{label}</span>',
            )
        self.assertContains(
            response,
            'class="nav__group nav__group--learn" role="group" '
            'aria-label="Apprendre"',
        )
        self.assertContains(
            response,
            'class="nav__group nav__group--tools" role="group" '
            'aria-label="Outils personnels"',
        )
        self.assertContains(response, 'class="nav__item-icon"', count=6)
        self.assertNotContains(response, 'class="footer__inner"')

    def test_global_pages_do_not_false_highlight_task_navigation(self):
        response = self.client.get(reverse("study:expression"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(
            response,
            'class="nav__task is-active"',
        )

    def test_hierarchy_uses_expression_paths(self):
        url = reverse("study:task_detail", args=["eo", "tache-3"])
        self.assertEqual(url, "/expression/eo/tache-3/")

    def test_all_skill_routes_use_canonical_codes(self):
        self.assertEqual(
            reverse("study:comprehension_overview"),
            "/comprehension/ce/",
        )
        self.assertEqual(
            reverse(
                "study:comprehension_question_study",
                args=["test-1", 2],
            ),
            "/comprehension/ce/test-1/question/2/",
        )
        self.assertEqual(
            reverse("study:comprehension_oral_overview"),
            "/comprehension/co/",
        )
        self.assertEqual(
            reverse(
                "study:comprehension_oral_question_study",
                args=["oral-test-1", 2],
            ),
            "/comprehension/co/oral-test-1/question/2/",
        )
        self.assertEqual(
            reverse("study:part_detail", args=["ee"]),
            "/expression/ee/",
        )
        self.assertEqual(
            reverse("study:task_detail", args=["eo", "tache-3"]),
            "/expression/eo/tache-3/",
        )

    def test_noncanonical_skill_paths_are_not_available(self):
        for path in (
            "/comprehension-ecrite/",
            "/comprehension-orale/",
            "/expression/orale/",
            "/expression/ecrit/",
            "/notes/orale/tache-3/",
            "/epreuve/orale/tache-3/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)


class TaskOrganizationTests(TestCase):
    def setUp(self):
        self.user = factories.make_user("organizer")
        self.client.force_login(self.user)
        Settings.load(self.user)
        self.part = factories.make_part("eo")
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

        other_part = factories.make_part("ee")
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
            reverse("study:vocabulary"),
            {
                "part": self.part.slug,
                "task": self.task.slug,
                "category": self.phrase.category.slug,
            },
        )
        self.assertContains(response, "oral-task-only")
        self.assertNotContains(response, "other-task-only")

    def test_expression_directory_uses_rich_subject_vocabulary(self):
        self.phrase.category.name = "Structurer et prendre position"
        self.phrase.category.save(update_fields=["name"])
        prompt = self.response_card.response.prompts.get(is_canonical=True)
        for lot_order in range(1, 51):
            subject_phrase = factories.make_phrase(
                tier="subject",
                lot_order=lot_order,
            )
            subject_phrase.source_prompts.add(prompt)
        topic_category = PhraseCategory.objects.create(
            slug="legacy-topic",
            name="Ancien vocabulaire thématique",
            content_key="test-category:legacy-topic",
            order=999,
        )
        legacy_phrase = factories.make_phrase(category=topic_category)
        legacy_phrase.source_prompts.add(prompt)

        response = self.client.get(
            reverse("study:vocabulary"),
            {"part": self.part.slug, "task": self.task.slug},
        )

        self.assertEqual(response.context["subject_prompt_count"], 1)
        self.assertEqual(response.context["subject_response_count"], 1)
        self.assertEqual(response.context["subject_vocabulary_count"], 50)
        self.assertContains(response, "Vocabulaire par sujet")
        self.assertContains(response, "50 vocabs")
        self.assertContains(response, "5 lots")
        self.assertContains(response, "data-subject-vocabulary-search")
        self.assertContains(response, prompt.text)
        self.assertContains(
            response,
            reverse(
                "study:response_detail",
                args=[prompt.response_id],
            )
            + f"?prompt={prompt.pk}#subject-vocabulary",
        )
        self.assertContains(
            response,
            f"kind=vocab&amp;response={prompt.response_id}&amp;batch=1",
        )
        self.assertNotContains(response, "Vocabulaire par thème")
        self.assertNotContains(response, topic_category.name)

    def test_task_search_is_limited_to_its_task(self):
        own = self.client.get(
            reverse("study:search"),
            {
                "part": self.part.slug,
                "task": self.task.slug,
                "q": "oral-task-only",
            },
        )
        other = self.client.get(
            reverse("study:search"),
            {
                "part": self.part.slug,
                "task": self.task.slug,
                "q": "other-task-only",
            },
        )
        self.assertEqual(own.context["result_count"], 1)
        self.assertEqual(other.context["result_count"], 0)

    def test_search_summarizes_broad_matches_instead_of_rendering_a_wall(self):
        prompt = self.response_card.response.prompts.get(is_canonical=True)
        for index in range(15):
            phrase = factories.make_phrase()
            phrase.english_cue = f"broad-search-match-{index}"
            phrase.save(update_fields=["english_cue"])
            phrase.source_prompts.add(prompt)

        response = self.client.get(
            reverse("study:search"),
            {
                "part": self.part.slug,
                "task": self.task.slug,
                "q": "broad-search-match",
            },
        )

        self.assertEqual(response.context["result_count"], 15)
        self.assertEqual(len(response.context["phrase_results"]), 12)
        self.assertTrue(response.context["results_truncated"])
        self.assertContains(response, "Les 12 premiers sont affichés")

    def test_task_progress_and_revisit_include_phrase_cards(self):
        local_phrase = factories.make_phrase(tier="response")
        local_phrase.source_prompts.add(
            self.response_card.response.prompts.first()
        )
        local_card = factories.make_phrase_card(
            phrase=local_phrase,
            user=self.user,
        )
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
        local_card.needs_revisit = True
        local_card.revisit_added_at = timezone.now()
        local_card.save(
            update_fields=["needs_revisit", "revisit_added_at"]
        )

        stats_response = self.client.get(
            reverse("study:stats"),
            {"part": self.part.slug, "task": self.task.slug},
        )
        revisit_response = self.client.get(
            self._task_url("study:task_revisit_list")
        )
        review_hub_response = self.client.get(
            self._task_url("study:task_review_hub")
        )
        self.assertEqual(stats_response.context["total_reviews"], 1)
        self.assertEqual(revisit_response.context["revisit_count"], 2)
        self.assertEqual(review_hub_response.context["revisit_count"], 0)
        self.assertContains(revisit_response, "Ma liste à revoir")
        self.assertContains(revisit_response, self.phrase.expression)
        self.assertContains(revisit_response, local_phrase.expression)
        self.assertNotContains(
            revisit_response,
            self.other_phrase.expression,
        )

    def test_stats_mastery_includes_every_vocabulary_tier(self):
        local_phrase = factories.make_phrase(tier="subject")
        local_phrase.source_prompts.add(
            self.response_card.response.prompts.first()
        )
        factories.make_phrase_card(phrase=local_phrase, user=self.user)

        global_response = self.client.get(reverse("study:stats"))
        task_response = self.client.get(
            reverse("study:stats"),
            {"part": self.part.slug, "task": self.task.slug},
        )

        self.assertEqual(
            global_response.context["overall"]["total"],
            Card.objects.current_content().filter(user=self.user).count(),
        )
        self.assertEqual(task_response.context["overall"]["total"], 3)

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
            reverse("study:stats"),
            {"part": self.part.slug, "task": self.task.slug},
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
            reverse("study:stats"),
            {"part": self.part.slug, "task": self.task.slug},
        )
        self.assertEqual(response.context["streak"], 1)

    def test_review_hub_groups_task_study_modes_and_resume(self):
        session = ReviewSession.load(self.user)
        session.current_card = self.response_card
        session.scope = {
            "kind": "spine",
            "part": self.part.slug,
            "task": self.task.slug,
        }
        session.save(update_fields=["current_card", "scope"])

        response = self.client.get(
            self._task_url("study:task_review_hub")
        )
        self.assertContains(response, "Entraînement mélangé")
        self.assertContains(response, "aucun vocabulaire mélangé")
        self.assertContains(response, "Ma liste à revoir")
        self.assertContains(
            response,
            "Reprendre l’entraînement en cours",
        )
        self.assertContains(
            response,
            "?kind=spine&amp;part=eo&amp;task=tache-3",
        )
        self.assertContains(response, "Choisir un thème")
        self.assertContains(response, "Réponses fragiles")

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
        first_recognition = factories.make_phrase_card(
            phrase=first.phrase,
            user=self.user,
            card_type=CardType.PHRASE_RECOGNITION,
        )
        self.phrase_pairs = [(first, first_recognition)]
        for _ in range(15):
            phrase = factories.make_phrase(category=self.category)
            self.phrase_pairs.append(
                (
                    factories.make_phrase_card(phrase=phrase, user=self.user),
                    factories.make_phrase_card(
                        phrase=phrase,
                        user=self.user,
                        card_type=CardType.PHRASE_RECOGNITION,
                    ),
                )
            )
        self.phrase_cards = [
            card for pair in self.phrase_pairs for card in pair
        ]

    def test_expression_category_displays_ten_expression_lots(self):
        response = self.client.get(
            reverse("study:vocabulary"),
            {"category": self.category.slug},
        )

        self.assertEqual(len(response.context["review_batches"]), 2)
        self.assertEqual(
            response.context["review_batches"][0]["phrase_count"],
            10,
        )
        self.assertEqual(
            response.context["review_batches"][1]["phrase_count"],
            6,
        )
        self.assertEqual(
            [batch["card_count"] for batch in response.context["review_batches"]],
            [20, 12],
        )
        self.assertContains(response, "Choisir un lot de 10")
        self.assertContains(response, "Lot 02")
        self.assertContains(response, "batch=2")

    def test_shared_category_does_not_mix_in_response_vocabulary(self):
        local_phrase = factories.make_phrase(
            category=self.category,
            tier="response",
        )
        local_phrase.english_cue = "response-only-vocabulary"
        local_phrase.save(update_fields=["english_cue"])
        factories.make_phrase_card(
            phrase=local_phrase,
            user=self.user,
        )

        response = self.client.get(
            reverse("study:vocabulary"),
            {"category": self.category.slug},
        )

        self.assertEqual(response.context["phrase_count"], 16)
        self.assertEqual(
            [
                batch["phrase_count"]
                for batch in response.context["review_batches"]
            ],
            [10, 6],
        )
        self.assertNotContains(response, "response-only-vocabulary")

    def test_category_batch_review_selects_only_that_lot(self):
        params = {
            "kind": "phrase",
            "category": self.category.slug,
            "batch": "2",
        }
        page = self.client.get(reverse("study:review"), params)
        state = self.client.get(reverse("study:review_next"), params).json()

        self.assertContains(page, "Lot 2")
        self.assertEqual(state["card_id"], self.phrase_pairs[10][0].id)
        self.assertEqual(state["counts"]["new_available"], 12)

    def test_batch_cards_show_in_progress_and_completed_states(self):
        future = timezone.now() + timedelta(days=5)
        first_batch = [
            card for pair in self.phrase_pairs[:10] for card in pair
        ]
        first_batch[0].state = CardState.LEARNING
        first_batch[0].due = future
        first_batch[0].save(update_fields=["state", "due"])

        in_progress = self.client.get(
            reverse("study:vocabulary"),
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
            reverse("study:vocabulary"),
            {"category": self.category.slug},
        )

        completed_batch = complete.context["review_batches"][0]
        self.assertEqual(completed_batch["status"], "complete")
        self.assertEqual(completed_batch["seen_count"], 10)
        self.assertFalse(completed_batch["can_review"])
        self.assertContains(complete, "Terminé")

    def test_suspended_lot_is_visible_but_not_clickable(self):
        Card.objects.filter(
            pk__in=[
                card.pk for pair in self.phrase_pairs[:10] for card in pair
            ]
        ).update(suspended=True)

        response = self.client.get(
            reverse("study:vocabulary"),
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

    def test_response_sheet_splits_local_vocabulary_into_expression_lots(self):
        response = factories.make_response()
        prompt = response.prompts.first()
        for _ in range(16):
            phrase = factories.make_phrase(tier="response")
            phrase.source_prompts.add(prompt)
            factories.make_phrase_card(
                phrase=phrase,
                user=self.user,
            )

        page = self.client.get(
            reverse("study:response_detail", args=[response.pk])
        )

        self.assertEqual(
            [
                batch["phrase_count"]
                for batch in page.context["phrase_batches"]
            ],
            [10, 6],
        )
        self.assertContains(page, "Lot 1 · 10 expressions")
        self.assertContains(page, "Lot 2 · 6 expressions")

    def test_response_sheet_offers_five_ten_card_subject_vocabulary_lots(self):
        response = factories.make_response()
        prompt = response.prompts.first()
        for _ in range(50):
            phrase = factories.make_phrase(tier="subject")
            phrase.source_prompts.add(prompt)
            factories.make_phrase_card(
                phrase=phrase,
                user=self.user,
            )

        page = self.client.get(
            reverse("study:response_detail", args=[response.pk])
        )

        self.assertEqual(page.context["vocabulary_count"], 50)
        self.assertEqual(len(page.context["subject_vocabulary"]), 10)
        self.assertEqual(
            [
                batch["phrase_count"]
                for batch in page.context["vocabulary_batches"]
            ],
            [10, 10, 10, 10, 10],
        )
        self.assertContains(page, "Pratiquer les vocabs")
        self.assertContains(
            page,
            f"kind=vocab&amp;response={response.pk}&amp;batch=1",
        )
        self.assertContains(page, "Lot 5 · Phrases modèles · 10 vocabs")
        state = self.client.get(
            reverse("study:review_next"),
            {
                "kind": "vocab",
                "response": str(response.pk),
                "batch": "1",
            },
        ).json()
        self.assertIn("Vocabulaire du sujet", state["front_html"])
        self.assertIn("Produisez le mot", state["front_html"])
        self.assertIn("Réponse française", state["back_html"])


class ResponsePromptNavigationTests(TestCase):
    def setUp(self):
        self.user = factories.make_user("prompt-navigator")
        self.client.force_login(self.user)
        self.part = factories.make_part()
        self.task = factories.make_task(part=self.part)
        self.first_theme = factories.make_theme(
            "navigation-premier",
            order=1,
            task=self.task,
        )
        self.second_theme = factories.make_theme(
            "navigation-second",
            order=2,
            task=self.task,
        )
        self.family = factories.make_family("navigation")
        self.first_response = factories.make_response(
            theme=self.first_theme,
            family=self.family,
        )
        self.middle_response = factories.make_response(
            theme=self.first_theme,
            family=self.family,
        )
        self.last_response = factories.make_response(
            theme=self.first_theme,
            family=self.family,
        )
        self.first_prompt = self._update_prompt(
            self.first_response,
            number=1,
            text="Premier sujet de navigation ?",
        )
        self.middle_prompt = self._update_prompt(
            self.middle_response,
            number=2,
            text="Sujet central de navigation ?",
        )
        self.last_prompt = self._update_prompt(
            self.last_response,
            number=3,
            text="Dernier sujet de navigation ?",
        )

    @staticmethod
    def _update_prompt(response, *, number, text):
        prompt = response.prompts.get(is_canonical=True)
        prompt.number = number
        prompt.text = text
        prompt.save(update_fields=["number", "text"])
        return prompt

    @staticmethod
    def _detail_url(prompt):
        return (
            reverse("study:response_detail", args=[prompt.response_id])
            + f"?prompt={prompt.pk}"
        )

    def test_previous_and_next_follow_prompt_order_within_theme(self):
        page = self.client.get(self._detail_url(self.middle_prompt))

        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["selected_prompt"], self.middle_prompt)
        self.assertEqual(page.context["previous_prompt"], self.first_prompt)
        self.assertEqual(page.context["next_prompt"], self.last_prompt)
        self.assertEqual(page.context["prompt_position"], 2)
        self.assertEqual(page.context["prompt_total"], 3)
        self.assertContains(
            page,
            f'<h1 class="detail-prompt">{self.middle_prompt.text}</h1>',
            html=True,
        )
        self.assertContains(page, self._detail_url(self.first_prompt), count=2)
        self.assertContains(page, self._detail_url(self.last_prompt), count=2)
        self.assertContains(page, "Sujet 2 sur 3", count=2)

    def test_navigation_has_correct_first_and_last_boundaries(self):
        first_page = self.client.get(self._detail_url(self.first_prompt))
        last_page = self.client.get(self._detail_url(self.last_prompt))

        self.assertIsNone(first_page.context["previous_prompt"])
        self.assertEqual(first_page.context["next_prompt"], self.middle_prompt)
        self.assertEqual(
            last_page.context["previous_prompt"],
            self.middle_prompt,
        )
        self.assertIsNone(last_page.context["next_prompt"])

    def test_alias_prompt_keeps_its_heading_theme_family_and_links(self):
        alias_family = factories.make_family("navigation-alias")
        alias = Prompt.objects.create(
            content_key="test-prompt:navigation-alias",
            response=self.first_response,
            theme=self.second_theme,
            family=alias_family,
            number=1,
            text="Sujet équivalent dans le second thème ?",
            is_canonical=False,
        )
        phrase = factories.make_phrase()
        phrase.source_prompts.add(alias)

        page = self.client.get(self._detail_url(alias))

        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["selected_prompt"], alias)
        self.assertEqual(page.context["task"], self.task)
        self.assertEqual(page.context["part"], self.part)
        self.assertIsNone(page.context["previous_prompt"])
        self.assertIsNone(page.context["next_prompt"])
        self.assertEqual(page.context["prompt_position"], 1)
        self.assertEqual(page.context["prompt_total"], 1)
        self.assertContains(
            page,
            f'<h1 class="detail-prompt">{alias.text}</h1>',
            html=True,
        )
        self.assertContains(page, self.second_theme.display_name)
        self.assertContains(page, alias_family.name)

        theme_page = self.client.get(
            reverse("study:theme_detail", args=[self.second_theme.slug])
        )
        family_page = self.client.get(
            reverse(
                "study:task_family_detail",
                args=[self.part.slug, self.task.slug, alias_family.slug],
            )
        )
        search_page = self.client.get(
            reverse("study:search"),
            {"q": "équivalent"},
        )
        phrases_page = self.client.get(
            reverse("study:vocabulary"),
            {"category": phrase.category.slug},
        )
        for origin_page in (
            theme_page,
            family_page,
            search_page,
            phrases_page,
        ):
            self.assertContains(origin_page, self._detail_url(alias))

    def test_invalid_or_mismatched_prompt_is_rejected(self):
        invalid = self.client.get(
            reverse(
                "study:response_detail",
                args=[self.first_response.pk],
            ),
            {"prompt": "not-an-id"},
        )
        mismatched = self.client.get(
            reverse(
                "study:response_detail",
                args=[self.middle_response.pk],
            ),
            {"prompt": self.first_prompt.pk},
        )

        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(mismatched.status_code, 400)


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
        self.assertEqual(
            presented["annotation_source_key"],
            f"response:{self.card.response.content_key}",
        )
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
        self.assertContains(r, 'id="previous-card"')

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

    def test_weak_area_drill_reviews_future_fragile_cards_once_per_pass(self):
        self.card.state = CardState.REVIEW
        self.card.due = timezone.now() + timedelta(days=30)
        self.card.interval_days = 10
        self.card.reps = 3
        self.card.last_rating = Rating.AGAIN
        self.card.save(
            update_fields=[
                "state",
                "due",
                "interval_days",
                "reps",
                "last_rating",
            ]
        )
        scope = "?kind=weak"
        page = self.client.get(reverse("study:review") + scope)
        self.assertContains(page, "Points à renforcer")

        presented = self._present(scope)
        self.assertEqual(presented["card_id"], self.card.id)
        result = self.client.post(
            reverse("study:review_answer"),
            {
                "kind": "weak",
                "card_id": self.card.id,
                "action": "correct",
                "presentation_token": presented["presentation_token"],
            },
        )

        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()["done"])
        self.assertTrue(result.json()["can_previous"])
        self.assertEqual(
            ReviewSession.load(self.user).revisit_seen_card_ids,
            [self.card.id],
        )

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
        self.assertEqual(session.scope, {"kind": "spine"})
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

    def test_learning_response_is_full_but_review_card_stays_concise(self):
        argument = self.card.response.arguments.get()
        argument.developpement = "Développement détaillé pour apprendre."
        argument.exemple = "Exemple concret pour apprendre."
        argument.consequence = "Conséquence logique pour apprendre."
        argument.save(
            update_fields=["developpement", "exemple", "consequence"]
        )

        detail = self.client.get(
            reverse("study:response_detail", args=[self.card.response_id])
        )
        self.assertContains(detail, argument.idea)
        self.assertContains(detail, argument.developpement)
        self.assertContains(detail, argument.exemple)
        self.assertContains(detail, argument.consequence)
        self.assertContains(detail, "Arguments développés")
        self.assertContains(detail, "Exemple concret")

        payload = self.client.get(reverse("study:review_next")).json()
        self.assertIn(argument.idea, payload["back_html"])
        self.assertNotIn(argument.developpement, payload["back_html"])
        self.assertNotIn(argument.exemple, payload["back_html"])
        self.assertNotIn(argument.consequence, payload["back_html"])

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
        self.assertEqual(second.json()["code"], "stale_presentation")
        self.assertEqual(ReviewLog.objects.count(), 1)

    def test_stale_token_can_recover_the_same_active_card(self):
        presented = self._present()
        session = ReviewSession.load(self.user)
        session.presentation_token = "replacement-token"
        session.save(update_fields=["presentation_token"])

        response = self.client.post(
            reverse("study:review_answer"),
            {
                "card_id": self.card.id,
                "action": "revisit",
                "presentation_token": presented["presentation_token"],
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "stale_presentation")
        self.assertEqual(response.json()["current_card_id"], self.card.id)
        self.assertEqual(
            response.json()["presentation_token"],
            "replacement-token",
        )
        self.assertEqual(ReviewLog.objects.count(), 0)

        recovered = self.client.post(
            reverse("study:review_answer"),
            {
                "card_id": self.card.id,
                "action": "revisit",
                "presentation_token": response.json()["presentation_token"],
            },
        )

        self.assertEqual(recovered.status_code, 200)
        self.card.refresh_from_db()
        self.assertTrue(self.card.needs_revisit)
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

    def test_previous_card_is_read_only_and_preserves_current_card(self):
        second_card = factories.make_spine_card()
        presented = self._present()
        unavailable = self.client.get(reverse("study:review_previous"))
        self.assertEqual(unavailable.status_code, 404)

        answered = self.client.post(
            reverse("study:review_answer"),
            {
                "card_id": self.card.id,
                "action": "correct",
                "presentation_token": presented["presentation_token"],
            },
        ).json()
        self.assertEqual(answered["card_id"], second_card.id)
        self.assertTrue(answered["can_previous"])

        session = ReviewSession.load(self.user)
        current_token = session.presentation_token
        previous = self.client.get(reverse("study:review_previous"))
        self.assertEqual(previous.status_code, 200)
        self.assertEqual(previous.json()["card_id"], self.card.id)
        self.assertEqual(
            previous.json()["annotation_source_key"],
            f"response:{self.card.response.content_key}",
        )
        self.assertIn(self.card.response.prompt, previous.json()["front_html"])

        session.refresh_from_db()
        self.assertEqual(session.current_card_id, second_card.id)
        self.assertEqual(session.presentation_token, current_token)
        self.assertEqual(ReviewLog.objects.count(), 1)

    def test_previous_card_remains_available_when_session_finishes(self):
        presented = self._present()

        finished = self.client.post(
            reverse("study:review_answer"),
            {
                "card_id": self.card.id,
                "action": "correct",
                "presentation_token": presented["presentation_token"],
            },
        ).json()

        self.assertTrue(finished["done"])
        self.assertTrue(finished["can_previous"])
        session = ReviewSession.load(self.user)
        self.assertEqual(session.scope, {"kind": "spine"})
        self.assertEqual(session.previous_card_id, self.card.id)
        self.assertIsNotNone(session.previous_review_id)
        previous = self.client.get(reverse("study:review_previous"))
        self.assertEqual(previous.status_code, 200)
        self.assertEqual(previous.json()["card_id"], self.card.id)

    def test_undo_clears_previous_card_pointer(self):
        factories.make_spine_card()
        presented = self._present()
        self.client.post(
            reverse("study:review_answer"),
            {
                "card_id": self.card.id,
                "action": "correct",
                "presentation_token": presented["presentation_token"],
            },
        )
        self.assertEqual(
            ReviewSession.load(self.user).previous_card_id,
            self.card.id,
        )

        undone = self.client.post(reverse("study:review_undo"))
        self.assertTrue(undone.json()["undone"])
        self.assertFalse(undone.json()["can_previous"])
        self.assertIsNone(ReviewSession.load(self.user).previous_card_id)
        self.assertIsNone(ReviewSession.load(self.user).previous_review_id)

    def test_undo_cannot_cross_review_scopes(self):
        phrase_card = factories.make_phrase_card(user=self.user)
        presented = self._present("?kind=spine")
        self.client.post(
            reverse("study:review_answer"),
            {
                "kind": "spine",
                "card_id": self.card.id,
                "action": "correct",
                "presentation_token": presented["presentation_token"],
            },
        )
        self.card.refresh_from_db()
        reviewed_state = self.card.state

        switched = self.client.get(
            reverse("study:review_next") + "?kind=phrase"
        ).json()
        self.assertEqual(switched["card_id"], phrase_card.id)
        undone = self.client.post(
            reverse("study:review_undo"),
            {"kind": "phrase"},
        )

        self.assertEqual(undone.status_code, 200)
        self.assertFalse(undone.json()["undone"])
        self.card.refresh_from_db()
        self.assertEqual(self.card.state, reviewed_state)
        self.assertEqual(ReviewLog.objects.filter(card=self.card).count(), 1)

    def test_stale_scope_cannot_undo_active_session(self):
        presented = self._present("?kind=spine")
        self.client.post(
            reverse("study:review_answer"),
            {
                "kind": "spine",
                "card_id": self.card.id,
                "action": "correct",
                "presentation_token": presented["presentation_token"],
            },
        )

        response = self.client.post(
            reverse("study:review_undo"),
            {"kind": "phrase"},
        )

        self.assertEqual(response.status_code, 409)
        self.card.refresh_from_db()
        self.assertNotEqual(self.card.state, CardState.NEW)
        self.assertEqual(ReviewLog.objects.count(), 1)

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
        self.client.get(reverse("study:review"))
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


class RecentSessionTests(TestCase):
    def setUp(self):
        self.user = factories.make_user("activity-owner")
        self.other = factories.make_user("activity-other")
        self.client.force_login(self.user)
        self.card = factories.make_spine_card(user=self.user)

    def _log(self, reviewed_at, rating=Rating.GOOD, *, user=None):
        card = (
            self.card
            if user is None or user == self.user
            else factories.make_spine_card(user=user)
        )
        return ReviewLog.objects.create(
            user=user or self.user,
            card=card,
            reviewed_at=reviewed_at,
            rating=rating,
            state_before=CardState.REVIEW,
            state_after=(
                CardState.RELEARNING
                if rating == Rating.AGAIN
                else CardState.REVIEW
            ),
            elapsed_ms=60000,
        )

    def test_stats_groups_recent_activity_and_keeps_it_private(self):
        now = timezone.now()
        self._log(now - timedelta(minutes=5))
        self._log(now - timedelta(minutes=20), Rating.AGAIN)
        self._log(now - timedelta(hours=2))
        self._log(now - timedelta(minutes=10), user=self.other)

        response = self.client.get(reverse("study:stats"))
        sessions = response.context["recent_sessions"]

        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0]["review_count"], 2)
        self.assertEqual(sessions[0]["accuracy"], 50)
        self.assertEqual(sessions[0]["study_minutes"], 2)
        self.assertEqual(sessions[1]["review_count"], 1)
        self.assertContains(response, "Dernières sessions")


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
            user=self.user,
            needs_revisit=True,
            revisit_added_at=timezone.now(),
            suspended=True,
        )
        srs.review(card, Rating.GOOD)
        session = ReviewSession.load(self.user)
        session.current_card = card
        session.scope = {"kind": "spine"}
        session.save()
        r = self.client.post(
            reverse("study:reset_progress"),
            {
                "current_pin": "123456",
                "confirmation": "REINITIALISER",
            },
        )
        self.assertEqual(r.status_code, 302)
        card.refresh_from_db()
        self.assertEqual(card.state, CardState.NEW)
        self.assertEqual(ReviewLog.objects.count(), 0)
        self.assertFalse(card.needs_revisit)
        self.assertFalse(card.suspended)
        session = ReviewSession.load(self.user)
        self.assertEqual(session.scope, {})
        self.assertIsNone(session.current_card_id)
        self.assertEqual(session.presentation_token, "")

    def test_reset_rejects_missing_server_side_confirmation(self):
        card = factories.make_spine_card(
            user=self.user,
            state=CardState.REVIEW,
            reps=4,
        )

        response = self.client.post(
            reverse("study:reset_progress"),
            {
                "current_pin": "123456",
                "confirmation": "non",
            },
        )

        self.assertEqual(response.status_code, 400)
        card.refresh_from_db()
        self.assertEqual(card.state, CardState.REVIEW)
        self.assertEqual(card.reps, 4)
