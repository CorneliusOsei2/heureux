from __future__ import annotations

import os

from django.contrib.sessions.models import Session
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
    PersonalResponse,
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
        self.part = factories.make_part("eo")
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
        overflowing = self.page.locator("body *").evaluate_all(
            """
            elements => elements
              .filter(element => {
                const rect = element.getBoundingClientRect();
                return rect.right > document.documentElement.clientWidth + 1 ||
                  rect.left < -1;
              })
              .slice(0, 6)
              .map(element => ({
                tag: element.tagName,
                className: element.className,
                text: (element.textContent || "").trim().slice(0, 80),
                right: Math.round(element.getBoundingClientRect().right),
              }))
            """
        )
        self.assertTrue(fits, f"{self.page.url}: {overflowing}")

    def test_primary_navigation_is_structured_on_mobile_and_desktop(self):
        self.page.set_viewport_size({"width": 320, "height": 568})
        toggle = self.page.get_by_role("button", name="Ouvrir le menu")

        toggle.click()

        navigation = self.page.locator("#primary-navigation")
        navigation.get_by_text("Apprendre", exact=True).wait_for()
        navigation.get_by_text("Mes outils", exact=True).wait_for()
        self.assertEqual(
            navigation.locator(".nav__primary-link").count(),
            6,
        )
        self.assertEqual(
            navigation.get_by_role(
                "link",
                name="Accueil",
                exact=True,
            ).get_attribute("aria-current"),
            "page",
        )
        navigation.get_by_text("Vue d'ensemble", exact=True).wait_for()
        navigation.get_by_text("Tous les mots et tournures", exact=True).wait_for()
        navigation.get_by_text("Notes et surlignages", exact=True).wait_for()
        navigation.get_by_text("Suivre mes progrès", exact=True).wait_for()
        self.assert_no_horizontal_overflow()

        self.page.keyboard.press("Escape")
        navigation.wait_for(state="hidden")
        self.assertEqual(toggle.get_attribute("aria-expanded"), "false")

        for width in (761, 800, 900, 901, 1024):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": 768})
                navigation.get_by_role(
                    "link",
                    name="Vocabulaire",
                    exact=True,
                ).wait_for()
                self.assertFalse(toggle.is_visible())
                self.assert_no_horizontal_overflow()

    def test_subject_vocabulary_directory_searches_rich_decks(self):
        first_prompt = self.first.response.prompts.get(is_canonical=True)
        second_prompt = self.second.response.prompts.get(is_canonical=True)
        first_prompt.text = "Faut-il voyager pour découvrir le monde ?"
        first_prompt.save(update_fields=["text"])
        second_prompt.text = "Les réseaux sociaux rapprochent-ils les jeunes ?"
        second_prompt.save(update_fields=["text"])
        first_vocabulary = factories.make_phrase(tier="subject")
        first_vocabulary.source_prompts.add(first_prompt)
        second_vocabulary = factories.make_phrase(tier="subject")
        second_vocabulary.source_prompts.add(second_prompt)

        self.page.goto(
            self.live_server_url
            + reverse("study:vocabulary")
            + f"?part={self.part.slug}&task={self.task.slug}"
        )

        self.page.get_by_role(
            "heading",
            name="Vocabulaire par sujet",
            exact=True,
        ).wait_for()
        search = self.page.get_by_role(
            "searchbox",
            name="Rechercher un sujet",
        )
        search.fill("reseaux")

        self.page.get_by_text("1 sujet trouvé", exact=True).wait_for()
        directory = self.page.locator("[data-subject-vocabulary-directory]")
        self.assertEqual(
            directory.locator(
                "[data-subject-vocabulary-row]:not([hidden])"
            ).count(),
            1,
        )
        self.page.get_by_text(second_prompt.text, exact=True).wait_for()
        directory.locator(
            "[data-subject-vocabulary-row]:not([hidden])"
        ).get_by_role("link", name="Pratiquer", exact=True).wait_for()
        self.assert_no_horizontal_overflow()

        search.fill("")
        self.page.get_by_text("2 sujets", exact=True).wait_for()
        self.assertEqual(
            directory.locator(
                "[data-subject-vocabulary-row]:not([hidden])"
            ).count(),
            2,
        )
        self.assertIsNone(
            directory.locator("[data-subject-theme]").get_attribute("open")
        )

    def test_vocabulary_status_cards_fit_large_counts_cleanly(self):
        self.page.set_viewport_size({"width": 1280, "height": 800})
        self.page.goto(
            self.live_server_url + reverse("study:vocabulary")
        )
        cards = self.page.locator(".vocabulary-status")
        self.assertEqual(cards.count(), 3)
        self.assertEqual(
            cards.locator(".vocabulary-status__icon").count(),
            3,
        )

        first = cards.first
        first.locator(".vocabulary-status__value").evaluate(
            "(element) => { element.textContent = '8\\u202f346'; }"
        )
        layout = first.evaluate(
            """
            card => {
              const value = card.querySelector('.vocabulary-status__value');
              const copy = card.querySelector('.vocabulary-status__copy');
              const valueRect = value.getBoundingClientRect();
              const copyRect = copy.getBoundingClientRect();
              const style = getComputedStyle(card);
              return {
                fits: card.scrollWidth <= card.clientWidth + 1,
                valueFits: valueRect.right <= copyRect.right + 1,
                fontFamily: getComputedStyle(value).fontFamily,
                borders: [
                  style.borderTopColor,
                  style.borderRightColor,
                  style.borderBottomColor,
                  style.borderLeftColor,
                ],
                pseudoContent: getComputedStyle(card, '::before').content,
              };
            }
            """
        )
        self.assertTrue(layout["fits"])
        self.assertTrue(layout["valueFits"])
        self.assertNotIn("Georgia", layout["fontFamily"])
        self.assertEqual(len(set(layout["borders"])), 1)
        self.assertEqual(layout["pseudoContent"], "none")

        path_cards = self.page.locator("[data-vocabulary-path]")
        self.assertEqual(path_cards.count(), 4)
        path_heights = path_cards.evaluate_all(
            "cards => cards.map(card => card.getBoundingClientRect().height)"
        )
        self.assertLessEqual(max(path_heights), 130)
        self.assertLessEqual(max(path_heights) - min(path_heights), 1)
        comprehension_grid = self.page.locator(
            ".vocabulary-domain--comprehension .expression-paths"
        ).bounding_box()
        expression_heading = self.page.locator(
            ".vocabulary-domain--expression .vocabulary-domain__heading"
        ).bounding_box()
        self.assertLessEqual(
            expression_heading["y"]
            - (comprehension_grid["y"] + comprehension_grid["height"]),
            64,
        )

        self.page.set_viewport_size({"width": 320, "height": 700})
        self.assert_no_horizontal_overflow()
        mobile_value_fits = first.evaluate(
            """
            card => {
              const value = card.querySelector('.vocabulary-status__value');
              const copy = card.querySelector('.vocabulary-status__copy');
              return value.getBoundingClientRect().right <=
                copy.getBoundingClientRect().right + 1;
            }
            """
        )
        self.assertTrue(mobile_value_fits)

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

    def select_prompt(self, *, start=None, end=None):
        prompt = self.page.locator("#card-front .prompt-text")
        prompt.evaluate(
            """
            (element, offsets) => {
              const range = document.createRange();
              if (offsets.start === null) {
                range.selectNodeContents(element);
              } else {
                const walker = document.createTreeWalker(
                  element,
                  NodeFilter.SHOW_TEXT
                );
                const boundary = target => {
                  let node;
                  let offset = target;
                  while ((node = walker.nextNode())) {
                    if (offset <= node.data.length) return [node, offset];
                    offset -= node.data.length;
                  }
                  throw new Error("Selection offset is outside the prompt.");
                };
                const startBoundary = boundary(offsets.start);
                walker.currentNode = element;
                const endBoundary = boundary(offsets.end);
                range.setStart(startBoundary[0], startBoundary[1]);
                range.setEnd(endBoundary[0], endBoundary[1]);
              }
              const selection = window.getSelection();
              selection.removeAllRanges();
              selection.addRange(range);
              document.dispatchEvent(new Event("selectionchange"));
            }
            """,
            {"start": start, "end": end},
        )
        self.page.locator("[data-highlight-selection]").wait_for(
            state="visible"
        )

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

    def test_mobile_highlight_expands_then_toggles_off(self):
        self.page.goto(
            self.live_server_url
            + reverse("study:review")
            + "?kind=spine&reset=1"
        )
        prompt = self.page.locator("#card-front .prompt-text")
        prompt.wait_for()
        self.page.wait_for_load_state("networkidle")
        prompt_text = prompt.text_content()
        highlight_button = self.page.locator("[data-highlight-selection]")

        self.select_prompt(start=0, end=12)
        with self.page.expect_response(
            lambda response: "/annotations/create/" in response.url
        ):
            highlight_button.click()
        prompt.locator("mark.user-highlight").wait_for()

        self.select_prompt(start=6, end=len(prompt_text))
        self.assertEqual(
            highlight_button.get_attribute("aria-label"),
            "Highlight selected text",
        )
        with self.page.expect_response(
            lambda response: "/annotations/create/" in response.url
        ):
            highlight_button.click()
        self.page.wait_for_function(
            """
            expected => {
              const marks = document.querySelectorAll(
                "#card-front .prompt-text mark.user-highlight"
              );
              return marks.length === 1 && marks[0].textContent === expected;
            }
            """,
            arg=prompt_text,
        )
        highlight = Annotation.objects.get(
            user=self.user,
            kind=AnnotationKind.HIGHLIGHT,
        )
        self.assertEqual(highlight.quote, prompt_text)

        self.select_prompt()
        self.assertEqual(
            highlight_button.get_attribute("aria-label"),
            "Unhighlight selected text",
        )
        with self.page.expect_response(
            lambda response: (
                "/annotations/" in response.url
                and "/delete/" in response.url
            )
        ):
            highlight_button.click()
        self.page.wait_for_function(
            """
            !document.querySelector(
              "#card-front .prompt-text mark.user-highlight"
            )
            """
        )
        self.assertFalse(
            Annotation.objects.filter(
                user=self.user,
                kind=AnnotationKind.HIGHLIGHT,
            ).exists()
        )

    def test_selection_toolbar_stays_open_until_outside_click(self):
        self.page.goto(
            self.live_server_url
            + reverse("study:review")
            + "?kind=spine&reset=1"
        )
        prompt = self.page.locator("#card-front .prompt-text")
        prompt.wait_for()
        self.page.wait_for_load_state("networkidle")
        self.select_prompt(start=0, end=12)
        toolbar = self.page.locator("[data-selection-translate]")

        with self.page.expect_response(
            lambda response: "/annotations/create/" in response.url
        ):
            self.page.locator("[data-highlight-selection]").click()
        prompt.locator("mark.user-highlight").wait_for()
        self.assertTrue(toolbar.is_visible())

        self.page.locator("[data-copy-selection]").click()
        self.assertTrue(toolbar.is_visible())

        self.page.locator("[data-translate-selection]").click()
        self.page.locator("[data-translation-panel]").wait_for()
        self.assertTrue(toolbar.is_visible())
        self.page.locator("[data-translation-close]").click()
        self.assertTrue(toolbar.is_visible())

        self.page.locator("[data-note-selection]").click()
        self.page.locator("[data-note-panel]").wait_for()
        self.assertTrue(toolbar.is_visible())
        self.page.locator("[data-note-cancel]").click()
        self.assertTrue(toolbar.is_visible())

        self.page.locator(".review__top").click(position={"x": 4, "y": 4})
        toolbar.wait_for(state="hidden")

    def test_expired_session_does_not_fake_unhighlight_success(self):
        self.page.goto(
            self.live_server_url
            + reverse("study:review")
            + "?kind=spine&reset=1"
        )
        prompt = self.page.locator("#card-front .prompt-text")
        prompt.wait_for()
        self.page.wait_for_load_state("networkidle")
        self.save_current_prompt_highlight()
        prompt.locator("mark.user-highlight").wait_for()

        self.select_prompt()
        highlight_button = self.page.locator("[data-highlight-selection]")
        self.assertEqual(
            highlight_button.get_attribute("aria-label"),
            "Unhighlight selected text",
        )
        session_key = next(
            cookie["value"]
            for cookie in self.context.cookies()
            if cookie["name"] == "sessionid"
        )
        Session.objects.filter(session_key=session_key).delete()

        with self.page.expect_response(
            lambda response: (
                "/annotations/" in response.url
                and "/delete/" in response.url
            )
        ):
            highlight_button.click()
        self.page.locator(
            "[data-annotation-toast]",
            has_text="Votre session a expiré",
        ).wait_for()
        self.assertEqual(prompt.locator("mark.user-highlight").count(), 1)
        self.assertTrue(
            Annotation.objects.filter(
                user=self.user,
                kind=AnnotationKind.HIGHLIGHT,
            ).exists()
        )

    def test_personalized_response_keeps_unchanged_text_highlighted(self):
        response = self.first.response
        detail_url = reverse("study:response_detail", args=[response.id])
        self.page.goto(self.live_server_url + detail_url)
        target = self.page.locator(".arg__part p").first
        target.wait_for()
        quote = target.text_content()
        target.evaluate(
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
        highlight_button = self.page.locator("[data-highlight-selection]")
        highlight_button.wait_for(state="visible")
        with self.page.expect_response(
            lambda browser_response: (
                "/annotations/create/" in browser_response.url
            )
        ):
            highlight_button.click()
        target.locator("mark.user-highlight").wait_for()
        saved = Annotation.objects.get(
            user=self.user,
            kind=AnnotationKind.HIGHLIGHT,
        )

        argument = response.arguments.get()
        PersonalResponse.objects.create(
            user=self.user,
            response=response,
            reformulation=(
                "Une nouvelle reformulation beaucoup plus longue déplace "
                "le reste de la réponse."
            ),
            position="Ma position personnelle ajoute encore du texte.",
            position_claire="Une introduction personnelle détaillée.",
            arguments=[
                {
                    "order": argument.order,
                    "idea": "Mon idée personnalisée.",
                    "developpement": "Un développement ajouté avant l'exemple.",
                    "exemple": argument.exemple,
                    "consequence": "Une conséquence personnalisée.",
                }
            ],
            nuance="Ma nuance personnelle.",
            conclusion="Ma conclusion personnelle.",
        )

        self.page.goto(self.live_server_url + detail_url + "?saved=1")
        restored = self.page.locator(
            "mark.user-highlight",
            has_text=quote,
        )
        restored.wait_for()
        self.assertEqual(restored.text_content(), quote)
        saved.refresh_from_db()
        self.assertEqual(saved.quote, quote)

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

        notes_url = (
            self.live_server_url
            + reverse("study:notes_overview")
            + f"?part={self.part.slug}&task={self.task.slug}"
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
        self.page.wait_for_url("**tab=highlights")
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
            self.page.locator("[data-study-keep]").click()
        self.page.locator("[data-study-done]:not(.hidden)").wait_for()
        self.assert_no_horizontal_overflow()

        self.page.goto(
            self.live_server_url
            + reverse(
                "study:task_review_hub",
                args=[self.part.slug, self.task.slug],
            )
        )
        self.page.get_by_text("Réponses fragiles").wait_for()
        self.assert_no_horizontal_overflow()
        self.page.get_by_role("link", name="Entraîner").click()
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

        for _ in range(50):
            phrase = factories.make_phrase(
                category=category,
                tier="subject",
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
        vocabulary_lots = self.page.locator(
            ".response-batches--vocabulary .response-batch"
        )
        self.assertEqual(vocabulary_lots.count(), 5)
        self.assertEqual(
            self.page.locator(
                ".response-batches--expressions .response-batch"
            ).count(),
            2,
        )
        lot_layouts = vocabulary_lots.evaluate_all(
            """
            lots => lots.map(lot => {
              const style = getComputedStyle(lot);
              return {
                fits: lot.scrollWidth <= lot.clientWidth + 1,
                height: lot.getBoundingClientRect().height,
                borders: [
                  style.borderTopColor,
                  style.borderRightColor,
                  style.borderBottomColor,
                  style.borderLeftColor,
                ],
                pseudoContent: getComputedStyle(lot, '::before').content,
              };
            })
            """
        )
        self.assertTrue(all(item["fits"] for item in lot_layouts))
        self.assertTrue(all(item["height"] <= 72 for item in lot_layouts))
        self.assertTrue(
            all(len(set(item["borders"])) == 1 for item in lot_layouts)
        )
        self.assertTrue(
            all(item["pseudoContent"] == "none" for item in lot_layouts)
        )
        self.assert_no_horizontal_overflow()

        category_url = (
            reverse("study:vocabulary") + f"?category={category.slug}"
        )
        self.page.goto(self.live_server_url + category_url)
        self.page.get_by_role(
            "heading",
            name="Choisir un lot de 10",
        ).wait_for()
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

    def test_home_daily_activity_cards_stay_compact(self):
        factories.make_comprehension_test()
        dashboard_url = self.live_server_url + reverse("study:dashboard")

        self.page.set_viewport_size({"width": 1110, "height": 700})
        self.page.goto(dashboard_url)
        desktop_heights = self.page.locator(".daily-card").evaluate_all(
            "cards => cards.map(card => card.getBoundingClientRect().height)"
        )
        self.assertTrue(desktop_heights)
        self.assertLessEqual(max(desktop_heights), 300)
        self.assertEqual(
            self.page.locator(".daily-card").first.evaluate(
                "card => getComputedStyle(card, '::before').content"
            ),
            "none",
        )
        label_colors = self.page.locator(".daily-card .eyebrow").evaluate_all(
            "labels => labels.map(label => getComputedStyle(label).color)"
        )
        self.assertEqual(len(set(label_colors)), 4)
        self.assert_no_horizontal_overflow()

        self.page.set_viewport_size({"width": 320, "height": 568})
        views_metric = self.page.locator(".home-hero__metrics dd").nth(1)
        views_metric.evaluate(
            """
            element => {
              element.firstChild.textContent = "159";
              element.querySelector(".hero-metric__total").textContent = "/ 9700";
            }
            """
        )
        self.assertLessEqual(
            views_metric.evaluate("element => element.scrollWidth"),
            views_metric.evaluate("element => element.clientWidth"),
        )
        mobile_heights = self.page.locator(".daily-card").evaluate_all(
            "cards => cards.map(card => card.getBoundingClientRect().height)"
        )
        self.assertLessEqual(max(mobile_heights), 320)
        self.assert_no_horizontal_overflow()

    def test_stats_dashboard_stays_balanced_on_desktop_and_mobile(self):
        self.page.set_viewport_size({"width": 1110, "height": 700})
        self.page.goto(self.live_server_url + reverse("study:stats"))

        grid_box = self.page.locator(".stats-kpis").bounding_box()
        tiles = self.page.locator(".stats-kpi")
        self.assertEqual(tiles.count(), 3)
        last_tile_box = tiles.last.bounding_box()
        self.assertAlmostEqual(
            last_tile_box["x"] + last_tile_box["width"],
            grid_box["x"] + grid_box["width"],
            delta=1,
        )
        chart_panels = self.page.locator(".stats-chart-grid .stats-panel")
        self.assertEqual(chart_panels.count(), 2)
        self.assertAlmostEqual(
            chart_panels.first.bounding_box()["y"],
            chart_panels.last.bounding_box()["y"],
            delta=1,
        )
        self.assertEqual(
            self.page.locator(".stats-theme").first.evaluate(
                "row => getComputedStyle(row, '::before').content"
            ),
            "none",
        )
        self.assert_no_horizontal_overflow()

        self.page.set_viewport_size({"width": 320, "height": 568})
        self.assertEqual(
            self.page.locator(".stats-kpis").evaluate(
                "grid => getComputedStyle(grid).gridTemplateColumns.split(' ').length"
            ),
            1,
        )
        self.assertLessEqual(
            self.page.locator(".stats-hero").evaluate(
                "hero => hero.scrollWidth"
            ),
            self.page.locator(".stats-hero").evaluate(
                "hero => hero.clientWidth"
            ),
        )
        self.assert_no_horizontal_overflow()

    def test_mobile_comprehension_quiz_correction_and_results(self):
        test = factories.make_comprehension_test(question_count=2)
        self.page.set_viewport_size({"width": 320, "height": 568})

        self.page.goto(self.live_server_url + reverse("study:dashboard"))
        self.page.get_by_role("button", name="Ouvrir le menu").click()
        self.page.get_by_role(
            "link",
            name="Compréhension",
            exact=True,
        ).click()
        self.page.get_by_role(
            "heading",
            name="Compréhension",
            exact=True,
        ).wait_for()
        self.assert_no_horizontal_overflow()

        self.page.locator(
            ".expression-path--available",
            has_text="Écrite",
        ).click()
        self.page.get_by_role(
            "heading",
            name="Compréhension écrite",
            exact=True,
        ).wait_for()
        self.assertEqual(self.page.locator(".ce-group-card").count(), 8)
        self.assert_no_horizontal_overflow()

        self.page.get_by_role("link", name="Groupe 1").click()
        self.page.get_by_role(
            "heading",
            name="Groupe 01",
        ).wait_for()
        self.assertEqual(self.page.locator(".ce-group-test-row").count(), 5)
        self.assert_no_horizontal_overflow()

        self.page.get_by_role("link", name="Découvrir le test").click()
        self.page.get_by_role("heading", name=test.title).wait_for()
        self.assertEqual(
            self.page.locator(".ce-study-question-row").count(),
            2,
        )
        self.assert_no_horizontal_overflow()

        self.page.locator(".ce-study-question-row").first.click()
        self.page.get_by_text("Choix et correction").wait_for()
        self.assertFalse(
            self.page.get_by_text("Correct explanation 1.").is_visible()
        )
        self.page.get_by_text(
            "Voir les choix et explications en anglais"
        ).click()
        self.page.get_by_text("Correct explanation 1.").wait_for()
        self.assert_no_horizontal_overflow()

        self.page.get_by_role("button", name="Pratiquer ce test").click()
        self.page.get_by_role("heading", name="Question 1 sur 2").wait_for()
        self.assertEqual(
            self.page.get_by_text("English passage 1.").count(),
            0,
        )
        self.assert_no_horizontal_overflow()
        self.page.locator(".ce-choice", has_text="Choix B français 1").click()
        self.page.get_by_role("heading", name="La bonne réponse était A.").wait_for()
        explanation = self.page.locator(".ce-rationales--explanation")
        self.assertTrue(explanation.evaluate("element => element.open"))
        answer_box = self.page.locator(".ce-correction__answer").bounding_box()
        explanation_box = explanation.bounding_box()
        self.assertGreaterEqual(
            explanation_box["y"] - answer_box["y"] - answer_box["height"],
            8,
        )
        self.page.get_by_text(
            "Pourquoi votre choix B ne convient pas"
        ).wait_for()
        self.page.get_by_text("Voir la traduction anglaise").click()
        self.page.get_by_text("English passage 1.").wait_for()
        self.assert_no_horizontal_overflow()

        self.page.get_by_role("link", name="Question suivante").click()
        correct_choice = self.page.locator(
            ".ce-choice",
            has_text="Choix A français 2",
        )
        correct_choice.focus()
        correct_choice.press("Enter")
        self.page.get_by_role("link", name="Voir mes résultats").wait_for()
        self.assert_no_horizontal_overflow()

        self.page.get_by_role("link", name="Voir mes résultats").click()
        self.page.get_by_role("heading", name="Revoir les questions").wait_for()
        self.assertEqual(self.page.locator(".ce-review-item").count(), 2)
        self.assert_no_horizontal_overflow()
