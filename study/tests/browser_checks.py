from __future__ import annotations

import os

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from playwright.sync_api import sync_playwright

from django.utils import timezone

from study.models import (
    Annotation,
    AnnotationKind,
    CardState,
    CardType,
    PhraseCategory,
    Rating,
    ReviewLog,
    ReviewSession,
)

from . import factories


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class MobileBrowserChecks(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_async_unsafe = os.environ.get("DJANGO_ALLOW_ASYNC_UNSAFE")
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        super().setUpClass()
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        super().tearDownClass()
        if cls.previous_async_unsafe is None:
            os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
        else:
            os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = cls.previous_async_unsafe

    def setUp(self):
        self.user = factories.make_user("browser-user", pin="482731")
        self.part = factories.make_part("orale")
        self.task = factories.make_task(self.part, "tache-3")
        self.theme = factories.make_theme("culture", task=self.task)
        self.first = factories.make_spine_card(
            user=self.user,
            theme=self.theme,
        )
        self.second = factories.make_spine_card(
            user=self.user,
            theme=self.theme,
        )
        self.context = self.browser.new_context(
            viewport={"width": 390, "height": 844},
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
        )
        self.page = self.context.new_page()
        self.page.goto(self.live_server_url + reverse("study:login"))
        self.page.locator("#id_username").fill("browser-user")
        self.page.locator("#id_pin").fill("482731")
        self.page.get_by_role("button", name="Continuer").click()
        self.page.wait_for_url(self.live_server_url + "/")

    def tearDown(self):
        self.context.close()

    def assert_no_horizontal_overflow(self):
        fits = self.page.evaluate(
            "document.documentElement.scrollWidth <= "
            "document.documentElement.clientWidth + 1"
        )
        self.assertTrue(fits, self.page.url)

    def save_current_prompt_highlight(self):
        prompt = self.page.locator("#card-front .prompt-text")
        prompt.evaluate(
            """
            element => {
              const range = document.createRange();
              range.selectNodeContents(element);
              const selection = window.getSelection();
              selection.removeAllRanges();
              selection.addRange(range);
              document.dispatchEvent(new Event("selectionchange"));
            }
            """
        )
        self.page.locator("[data-highlight-selection]").wait_for(
            state="visible"
        )
        with self.page.expect_response(
            lambda response: "/annotations/create/" in response.url
        ) as response_info:
            self.page.locator("[data-highlight-selection]").click()
        self.assertIn(response_info.value.status, (200, 201))

    def test_mobile_review_highlights_and_final_previous(self):
        for path in (
            reverse("study:dashboard"),
            reverse("study:settings"),
            reverse("study:response_detail", args=[self.first.response_id]),
            reverse("study:edit_response", args=[self.first.response_id]),
        ):
            self.page.goto(self.live_server_url + path)
            self.assert_no_horizontal_overflow()

        self.page.goto(
            self.live_server_url
            + reverse("study:review")
            + "?kind=spine&reset=1"
        )
        self.page.locator("#card-front .prompt-text").wait_for()
        first_prompt = self.page.locator(
            "#card-front .prompt-text"
        ).text_content()
        self.save_current_prompt_highlight()
        self.page.locator("#reveal").click()
        self.page.locator('[data-action="correct"]').click()
        self.page.wait_for_function(
            """
            previous => {
              const prompt = document.querySelector("#card-front .prompt-text");
              return prompt && prompt.textContent !== previous;
            }
            """,
            arg=first_prompt,
        )
        self.save_current_prompt_highlight()

        self.page.locator("#reveal").click()
        self.page.locator('[data-action="correct"]').click()
        self.page.locator("#done-zone:not(.hidden)").wait_for()
        previous = self.page.locator("#previous-card")
        self.assertTrue(previous.is_enabled())
        previous.click()
        self.page.locator("#previous-card-label:not(.hidden)").wait_for()
        self.assert_no_horizontal_overflow()

        highlights = Annotation.objects.filter(
            user=self.user,
            kind=AnnotationKind.HIGHLIGHT,
        )
        self.assertEqual(highlights.count(), 2)
        self.assertEqual(
            highlights.values("source_key").distinct().count(),
            2,
        )

    def test_mobile_review_recovers_a_rotated_presentation_token(self):
        self.page.goto(
            self.live_server_url
            + reverse("study:review")
            + "?kind=spine&reset=1"
        )
        prompt = self.page.locator("#card-front .prompt-text")
        prompt.wait_for()
        first_prompt = prompt.text_content()
        self.page.locator("#reveal").click()

        session = ReviewSession.load(self.user)
        active_card = session.current_card
        session.presentation_token = "replacement-token"
        session.save(update_fields=["presentation_token"])

        self.page.locator('[data-action="revisit"]').click()
        self.page.wait_for_function(
            """
            previous => {
              const current = document.querySelector("#card-front .prompt-text");
              return current && current.textContent !== previous;
            }
            """,
            arg=first_prompt,
        )

        active_card.refresh_from_db()
        self.assertTrue(active_card.needs_revisit)
        self.assertEqual(
            ReviewLog.objects.filter(card=active_card).count(),
            1,
        )

    def test_mobile_highlights_use_two_source_groups(self):
        Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.NOTE,
            body="Une note personnelle.",
        )
        Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.HIGHLIGHT,
            quote="Passage retenu dans une réponse.",
            source_path=reverse(
                "study:response_detail",
                args=[self.first.response_id],
            ),
            source_key="response:culture:p1:back",
            start_offset=1,
            end_offset=33,
        )
        Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.HIGHLIGHT,
            quote="Passage retenu dans une expression.",
            source_path=reverse("study:review") + "?kind=phrase",
            source_key="phrase:expr-1:phrase_production:back",
            start_offset=1,
            end_offset=35,
        )

        notes_url = self.live_server_url + reverse(
            "study:task_notes",
            args=[self.part.slug, self.task.slug],
        )
        self.page.goto(notes_url)
        self.page.locator(
            ".annotation-card__body",
            has_text="Une note personnelle.",
        ).wait_for()
        self.assertEqual(
            self.page.locator("#notes-tab").get_attribute("aria-selected"),
            "true",
        )
        self.assertEqual(
            self.page.get_by_text("Passage retenu dans une réponse.").count(),
            0,
        )

        self.page.locator("#highlights-tab").click()
        self.page.wait_for_url("**?tab=highlights")
        self.assertEqual(
            self.page.locator("#highlights-tab").get_attribute(
                "aria-selected"
            ),
            "true",
        )

        response_group = self.page.locator(
            '[aria-labelledby="highlights-responses-heading"]'
        )
        expression_group = self.page.locator(
            '[aria-labelledby="highlights-expressions-heading"]'
        )
        response_group.get_by_text(
            "Passage retenu dans une réponse."
        ).wait_for()
        expression_group.get_by_text(
            "Passage retenu dans une expression."
        ).wait_for()
        self.assert_no_horizontal_overflow()

    def test_mobile_annotation_search_study_and_weak_drill(self):
        Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.NOTE,
            title="Nuance utile",
            body="Le mot toujours est trop fort.",
            study_later=True,
        )
        Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.HIGHLIGHT,
            quote="Cependant, il faut reconnaître cette limite.",
            source_path=reverse(
                "study:response_detail",
                args=[self.first.response_id],
            ),
            start_offset=0,
            end_offset=45,
            study_later=True,
        )
        self.first.state = CardState.REVIEW
        self.first.due = timezone.now() + timezone.timedelta(days=20)
        self.first.interval_days = 8
        self.first.reps = 3
        self.first.last_rating = Rating.AGAIN
        self.first.save(
            update_fields=[
                "state",
                "due",
                "interval_days",
                "reps",
                "last_rating",
            ]
        )
        self.page.set_viewport_size({"width": 320, "height": 568})

        self.page.goto(
            self.live_server_url + reverse("study:annotation_search")
        )
        self.assert_no_horizontal_overflow()
        self.page.locator('input[name="q"]').fill("toujours")
        self.page.get_by_role("button", name="Rechercher").click()
        self.page.wait_for_url("**?q=toujours**")
        self.page.get_by_text("Le mot toujours est trop fort.").wait_for()
        self.assert_no_horizontal_overflow()

        self.page.goto(
            self.live_server_url + reverse("study:annotation_study")
        )
        self.page.locator("[data-study-card]:not(.hidden)").wait_for()
        self.assert_no_horizontal_overflow()
        for _ in range(2):
            self.page.locator("[data-study-reveal]").click()
            self.page.locator(
                "[data-study-card]:not(.hidden) [data-study-back]:not(.hidden)"
            ).wait_for()
            self.page.locator("[data-study-next]").click()
        self.page.locator("[data-study-done]:not(.hidden)").wait_for()
        self.assert_no_horizontal_overflow()

        self.page.goto(
            self.live_server_url
            + reverse(
                "study:task_review_hub",
                args=[self.part.slug, self.task.slug],
            )
        )
        self.page.get_by_text("Points à renforcer").wait_for()
        self.assert_no_horizontal_overflow()
        self.page.get_by_role("link", name="Lancer l'entraînement").click()
        self.page.locator("#card-front .prompt-text").wait_for()
        self.assert_no_horizontal_overflow()

    def test_mobile_expression_lots_and_highlighted_answers(self):
        category = PhraseCategory.objects.create(
            slug="browser-vocab",
            name="Vocabulaire mobile",
            content_key="test-category:browser-vocab",
            order=1,
        )
        prompt = self.first.response.prompts.get(is_canonical=True)
        for _ in range(16):
            phrase = factories.make_phrase(
                category=category,
                tier="response",
            )
            phrase.source_prompts.add(prompt)
            factories.make_phrase_card(user=self.user, phrase=phrase)

        shared_phrases = []
        for _ in range(16):
            phrase = factories.make_phrase(
                category=category,
                tier="shared",
            )
            shared_phrases.append(phrase)
            factories.make_phrase_card(user=self.user, phrase=phrase)
            factories.make_phrase_card(
                user=self.user,
                phrase=phrase,
                card_type=CardType.PHRASE_RECOGNITION,
            )

        self.page.set_viewport_size({"width": 320, "height": 568})
        response_url = reverse(
            "study:response_detail",
            args=[self.first.response_id],
        )
        self.page.goto(self.live_server_url + response_url)
        self.page.get_by_role(
            "link",
            name="Lot 1 · 10 expressions",
        ).wait_for()
        self.page.get_by_role(
            "link",
            name="Lot 2 · 6 expressions",
        ).wait_for()
        self.assert_no_horizontal_overflow()

        category_url = (
            reverse("study:phrases") + f"?category={category.slug}"
        )
        self.page.goto(self.live_server_url + category_url)
        self.page.get_by_text("Lots de 10 expressions maximum").wait_for()
        self.assertEqual(
            self.page.locator(".batch-card").count(),
            2,
        )
        self.assertEqual(
            self.page.locator(".phrase__ex mark").count(),
            len(shared_phrases),
        )
        self.assert_no_horizontal_overflow()

        self.page.locator(".batch-card").first.click()
        self.page.locator("#card-front").wait_for()
        self.assert_no_horizontal_overflow()
        self.page.locator("#reveal").click()
        highlighted = self.page.locator("#card-back mark")
        highlighted.wait_for()
        self.assertEqual(
            highlighted.text_content(),
            shared_phrases[0].anchor,
        )
        self.assert_no_horizontal_overflow()

    def test_mobile_comprehension_quiz_correction_and_results(self):
        test = factories.make_comprehension_test(question_count=2)
        self.page.set_viewport_size({"width": 320, "height": 568})

        self.page.goto(
            self.live_server_url + reverse("study:comprehension_overview")
        )
        self.page.get_by_role("heading", name="Compréhension écrite").wait_for()
        self.assert_no_horizontal_overflow()
        self.page.get_by_role("link", name="Découvrir le test").click()

        self.page.get_by_role("button", name="Commencer le test").click()
        self.page.get_by_role("heading", name="Question 1 sur 2").wait_for()
        self.assertEqual(
            self.page.get_by_text("English passage 1.").count(),
            0,
        )
        self.assert_no_horizontal_overflow()

        self.page.locator(".ce-choice", has_text="Choix B français 1").click()
        self.page.get_by_role("button", name="Valider ma réponse").click()
        self.page.get_by_role("heading", name="La bonne réponse était A.").wait_for()
        self.page.get_by_text(
            "Pourquoi votre choix B ne convient pas"
        ).wait_for()
        self.page.get_by_text("Voir la traduction anglaise").click()
        self.page.get_by_text("English passage 1.").wait_for()
        self.assert_no_horizontal_overflow()

        self.page.get_by_role("link", name="Question suivante").click()
        self.page.locator(".ce-choice", has_text="Choix A français 2").click()
        self.page.get_by_role("button", name="Valider ma réponse").click()
        self.page.get_by_role("link", name="Voir mes résultats").wait_for()
        self.assert_no_horizontal_overflow()

        self.page.get_by_role("link", name="Voir mes résultats").click()
        self.page.get_by_role("heading", name="Revoir les questions").wait_for()
        self.assertEqual(self.page.locator(".ce-review-item").count(), 2)
        self.assert_no_horizontal_overflow()
