from __future__ import annotations

import os

from django.contrib.sessions.models import Session
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from playwright.sync_api import sync_playwright

from django.utils import timezone

from study.content import load_sections
from study.management.commands.import_content import Command
from study.models import (
    Annotation,
    AnnotationKind,
    CardState,
    CardType,
    ComprehensionMode,
    PhraseCategory,
    PersonalResponse,
    Rating,
    ReviewLog,
    ReviewSession,
)
from study.routing import response_detail_url, theme_detail_url

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

    def test_dynamic_content_icons_load_from_the_svg_sprite(self):
        self.page.goto(
            self.live_server_url
            + reverse(
                "study:task_detail",
                args=[self.part.slug, self.task.slug],
            )
        )

        icon = self.page.locator(".title-with-icon__glyph .ui-icon")
        icon.wait_for()
        use = icon.locator("use")
        self.assertTrue(
            use.get_attribute("href").endswith(
                f"#icon-{self.task.icon}"
            )
        )
        self.assertTrue(
            use.evaluate(
                """
                element => {
                  const box = element.getBBox();
                  return box.width > 0 && box.height > 0;
                }
                """
            )
        )

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
            + reverse(
                "study:task_phrases",
                args=[self.part.slug, self.task.slug],
            )
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
            lambda response: reverse("study:annotation_create") in response.url
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
        first_prompt = self.first.response.prompts.get(is_canonical=True)
        for path in (
            reverse("study:dashboard"),
            reverse("study:settings"),
            response_detail_url(self.first.response),
            reverse(
                "study:edit_response",
                args=[self.part.slug, self.task.slug, first_prompt.pk],
            ),
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
            lambda response: reverse("study:annotation_create") in response.url
        ):
            highlight_button.click()
        prompt.locator("mark.user-highlight").wait_for()

        self.select_prompt(start=6, end=len(prompt_text))
        self.assertEqual(
            highlight_button.get_attribute("aria-label"),
            "Highlight selected text",
        )
        with self.page.expect_response(
            lambda response: reverse("study:annotation_create") in response.url
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
                "/notes/" in response.url
                and "/supprimer/" in response.url
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
            lambda response: reverse("study:annotation_create") in response.url
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

    def test_selection_toolbar_reads_with_premium_french_voice(self):
        self.context.add_init_script(
            """
            (() => {
              window.__speechClickTask = false;
              document.addEventListener("click", () => {
                window.__speechClickTask = true;
                setTimeout(() => {
                  window.__speechClickTask = false;
                }, 0);
              }, true);
              const voices = [
                {
                  name: "Audrey Premium",
                  voiceURI: "com.apple.voice.premium.fr-FR.Audrey",
                  lang: "fr-FR",
                  localService: true,
                  default: false,
                },
                {
                  name: "English",
                  voiceURI: "english",
                  lang: "en-US",
                  localService: true,
                  default: true,
                },
              ];
              const synthesis = {
                getVoices: () => voices,
                addEventListener: () => {},
                cancel: () => {
                  window.__speechCancelCount =
                    (window.__speechCancelCount || 0) + 1;
                },
                resume: () => {},
                speak: utterance => {
                  if (!window.__speechClickTask) return;
                  window.__spokenFrench = {
                    text: utterance.text,
                    lang: utterance.lang,
                    rate: utterance.rate,
                    voice: utterance.voice && utterance.voice.name,
                    startedInClick: window.__speechClickTask,
                  };
                },
              };
              class FakeUtterance {
                constructor(text) {
                  this.text = text;
                  this.lang = "";
                  this.rate = 1;
                  this.pitch = 1;
                  this.voice = null;
                }
              }
              Object.defineProperty(window, "speechSynthesis", {
                configurable: true,
                value: synthesis,
              });
              Object.defineProperty(window, "SpeechSynthesisUtterance", {
                configurable: true,
                value: FakeUtterance,
              });
            })();
            """
        )
        self.page.goto(
            self.live_server_url
            + reverse("study:review")
            + "?kind=spine&reset=1"
        )
        prompt = self.page.locator("#card-front .prompt-text")
        prompt.wait_for()
        self.page.wait_for_load_state("networkidle")
        self.select_prompt(start=0, end=12)
        selected = self.page.evaluate("window.getSelection().toString().trim()")
        read_button = self.page.locator("[data-read-selection]")

        read_button.click()
        self.page.wait_for_function("() => Boolean(window.__spokenFrench)")
        self.assertEqual(read_button.get_attribute("aria-pressed"), "true")
        self.assertEqual(
            self.page.evaluate("window.__spokenFrench"),
            {
                "text": selected,
                "lang": "fr-FR",
                "rate": 0.92,
                "voice": "Audrey Premium",
                "startedInClick": True,
            },
        )

        read_button.click()
        self.assertEqual(read_button.get_attribute("aria-pressed"), "false")

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
                "/notes/" in response.url
                and "/supprimer/" in response.url
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

    def test_legacy_response_highlight_renders_inside_new_annotation_root(self):
        response = self.first.response
        detail_url = response_detail_url(response)
        quote = response.arguments.get().exemple
        Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.HIGHLIGHT,
            quote=quote,
            source_path=detail_url,
            source_key="",
            start_offset=0,
            end_offset=len(quote),
        )

        self.page.goto(self.live_server_url + detail_url)
        self.page.wait_for_load_state("networkidle")
        restored = self.page.locator(
            ".answer-columns mark.user-highlight",
            has_text=quote,
        )

        restored.wait_for(timeout=5000)
        self.assertEqual(restored.text_content(), quote)

    def test_personalized_response_keeps_unchanged_text_highlighted(self):
        response = self.first.response
        detail_url = response_detail_url(response)
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
                reverse("study:annotation_create") in browser_response.url
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

    def test_mobile_highlights_group_by_date_with_source_chips(self):
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
            source_path=response_detail_url(self.first.response),
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
            + reverse(
                "study:task_notes",
                args=[self.part.slug, self.task.slug],
            )
        )
        self.page.goto(notes_url)
        self.page.locator(
            ".annotation-card__body",
            has_text="Une note personnelle.",
        ).wait_for()
        self.assertFalse(
            self.page.locator(".annotation-table--notes").is_visible()
        )
        action_icons = self.page.locator(
            ".annotation-card",
            has_text="Une note personnelle.",
        ).locator(".annotation-action__icon")
        self.assertEqual(action_icons.count(), 3)

        self.page.get_by_role("button", name="Tableau").click()
        self.page.locator(
            ".annotation-table__body",
            has_text="Une note personnelle.",
        ).wait_for()
        notes_table = self.page.locator(".annotation-table--notes")
        self.assertEqual(
            notes_table.evaluate("table => getComputedStyle(table).display"),
            "block",
        )
        self.assertEqual(
            notes_table.locator(".annotation-table__row").evaluate(
                "row => getComputedStyle(row).display"
            ),
            "grid",
        )
        action_icons = self.page.locator(
            ".annotation-table__row",
            has_text="Une note personnelle.",
        ).locator(".annotation-action__icon")
        self.assertEqual(action_icons.count(), 3)
        icon_styles = action_icons.evaluate_all(
            """
            icons => icons.map(icon => {
              const style = getComputedStyle(icon);
              return {
                color: style.color,
                background: style.backgroundColor,
              };
            })
            """
        )
        self.assertEqual(
            len({style["color"] for style in icon_styles}),
            3,
        )
        self.assertTrue(
            all(
                style["background"] != "rgba(0, 0, 0, 0)"
                for style in icon_styles
            )
        )
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

        today_section = self.page.locator(
            '[aria-labelledby="highlights-today-heading"]'
        )
        today_section.get_by_text(
            "Passage retenu dans une réponse."
        ).wait_for()
        today_section.get_by_text(
            "Passage retenu dans une expression."
        ).wait_for()

        response_card = self.page.locator(
            ".annotation-table__row",
            has_text="Passage retenu dans une réponse.",
        )
        expression_card = self.page.locator(
            ".annotation-table__row",
            has_text="Passage retenu dans une expression.",
        )
        self.assertEqual(
            response_card.locator(".annotation-card__origin")
            .text_content()
            .strip(),
            "Réponse",
        )
        self.assertEqual(
            expression_card.locator(".annotation-card__origin")
            .text_content()
            .strip(),
            "Expression",
        )
        self.assert_no_horizontal_overflow()

        self.page.set_viewport_size({"width": 1200, "height": 800})
        self.page.reload()
        highlights_table = self.page.locator(
            ".annotation-table--highlights"
        )
        highlights_table.get_by_role(
            "columnheader",
            name="Passage",
        ).wait_for()
        self.assertEqual(
            highlights_table.evaluate(
                "table => getComputedStyle(table).display"
            ),
            "table",
        )
        self.assertEqual(
            highlights_table.locator(".annotation-table__row")
            .first.evaluate("row => getComputedStyle(row).display"),
            "table-row",
        )
        self.page.get_by_role("button", name="Cartes").click()
        self.page.locator(
            ".annotation-card",
            has_text="Passage retenu dans une réponse.",
        ).wait_for()
        self.assertFalse(highlights_table.is_visible())
        self.assert_no_horizontal_overflow()

    def test_collection_view_choice_persists_across_catalogs(self):
        note = Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.NOTE,
            body="Préférence globale d’affichage.",
        )
        self.page.set_viewport_size({"width": 1200, "height": 800})
        task_url = self.live_server_url + reverse(
            "study:task_detail",
            args=[self.part.slug, self.task.slug],
        )
        self.page.goto(task_url)

        collection = self.page.locator(
            ".task-themes [data-collection-view='adaptive']"
        )
        collection.locator("[data-collection-item]").first.wait_for()
        self.assertEqual(
            collection.evaluate(
                "element => getComputedStyle(element).display"
            ),
            "grid",
        )
        self.assertEqual(
            self.page.get_by_role("button", name="Cartes").get_attribute(
                "aria-pressed"
            ),
            "true",
        )

        self.page.get_by_role("button", name="Tableau").click()
        header = collection.locator("[data-collection-table-header]")
        self.assertTrue(header.is_visible())
        self.assertEqual(
            header.locator("span").all_text_contents(),
            ["Thème", "Contenu", "Progression", "État"],
        )
        self.assertEqual(
            collection.evaluate(
                "element => getComputedStyle(element).display"
            ),
            "flex",
        )
        self.assertEqual(
            collection.locator("[data-collection-item]").first.evaluate(
                "item => getComputedStyle(item).borderRadius"
            ),
            "0px",
        )
        self.assertEqual(
            header.evaluate("element => getComputedStyle(element).gridTemplateColumns"),
            collection.locator(".deck__body").first.evaluate(
                "element => getComputedStyle(element).gridTemplateColumns"
            ),
        )
        self.assertLess(
            collection.locator("[data-collection-item]")
            .first.bounding_box()["height"],
            80,
        )
        self.assertEqual(
            self.page.evaluate(
                "localStorage.getItem('collectionViewMode')"
            ),
            "table",
        )
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.assertFalse(header.is_visible())
        self.assert_no_horizontal_overflow()

        self.page.set_viewport_size({"width": 1200, "height": 800})
        self.page.goto(
            self.live_server_url
            + theme_detail_url(self.theme)
        )
        response_list = self.page.locator(
            ".qlist[data-collection-view='adaptive']"
        )
        response_list.locator("[data-collection-item]").first.wait_for()
        self.assertEqual(
            response_list.evaluate(
                "element => getComputedStyle(element).display"
            ),
            "flex",
        )
        self.assertEqual(
            response_list.locator("[data-collection-item]").first.evaluate(
                "item => getComputedStyle(item).borderRadius"
            ),
            "0px",
        )
        response_header = response_list.locator(
            "[data-collection-table-header]"
        )
        self.assertEqual(
            response_header.evaluate(
                "element => getComputedStyle(element).gridTemplateColumns"
            ),
            response_list.locator("[data-collection-item]").first.evaluate(
                "element => getComputedStyle(element).gridTemplateColumns"
            ),
        )

        self.page.goto(
            self.live_server_url
            + reverse(
                "study:task_notes",
                args=[self.part.slug, self.task.slug],
            )
        )
        note_table = self.page.locator(".annotation-table--notes")
        note_table.locator(
            ".annotation-table__body",
            has_text="Préférence globale d’affichage.",
        ).wait_for()
        self.assertTrue(note_table.is_visible())
        self.assertEqual(
            self.page.get_by_role("button", name="Tableau").get_attribute(
                "aria-pressed"
            ),
            "true",
        )

        self.page.get_by_role("button", name="Cartes").click()
        self.page.locator(
            ".annotation-card",
            has_text="Préférence globale d’affichage.",
        ).wait_for()
        self.assertFalse(note_table.is_visible())
        self.page.goto(
            self.page.url.split("#", 1)[0] + f"#note-{note.id}"
        )
        note_card = self.page.locator(f"#note-{note.id}-card")
        self.assertTrue(
            note_card.evaluate(
                "card => card.classList.contains('is-annotation-anchor')"
            )
        )
        self.page.get_by_role("button", name="Tableau").click()
        self.assertTrue(
            self.page.locator(f"#note-{note.id}").evaluate(
                "row => row.classList.contains('is-annotation-anchor')"
            )
        )
        self.page.get_by_role("button", name="Cartes").click()
        self.page.goto(task_url)
        self.assertEqual(
            collection.evaluate(
                "element => getComputedStyle(element).display"
            ),
            "grid",
        )
        self.assert_no_horizontal_overflow()

    def test_comprehension_table_uses_real_compact_columns(self):
        first = factories.make_comprehension_test(
            number=1,
            question_count=31,
            mode=ComprehensionMode.ORALE,
        )
        factories.make_comprehension_test(
            number=4,
            question_count=24,
            mode=ComprehensionMode.ORALE,
        )
        factories.make_comprehension_test(
            number=5,
            question_count=28,
            mode=ComprehensionMode.ORALE,
        )
        factories.make_comprehension_attempt(
            user=self.user,
            test=first,
            answered_questions=3,
        )

        self.page.set_viewport_size({"width": 1440, "height": 900})
        self.page.goto(
            self.live_server_url
            + reverse("study:comprehension_oral_group", args=[1])
        )
        self.page.get_by_role("button", name="Tableau").click()
        table = self.page.locator(".collection-table--tests")
        header = table.locator("[data-collection-table-header]")
        first_row = table.locator("[data-collection-item]").first

        self.assertEqual(
            header.locator("span").all_text_contents(),
            ["Test", "Détails", "Questions", "Progression", "Action"],
        )
        self.assertEqual(
            header.evaluate(
                "element => getComputedStyle(element).gridTemplateColumns"
            ),
            first_row.evaluate(
                "element => getComputedStyle(element).gridTemplateColumns"
            ),
        )
        self.assertLessEqual(first_row.bounding_box()["height"], 84)
        aligned_edges = self.page.evaluate(
            """
            () => {
              const headerCells = [
                ...document.querySelectorAll(
                  '.collection-table--tests [data-collection-table-header] > span'
                )
              ];
              const rowCells = [
                ...document.querySelector(
                  '.collection-table--tests [data-collection-item]'
                ).children
              ];
              return headerCells.map((cell, index) => {
                const headerRect = cell.getBoundingClientRect();
                const rowRect = rowCells[index].getBoundingClientRect();
                return Math.abs(headerRect.left - rowRect.left);
              });
            }
            """
        )
        self.assertTrue(all(offset <= 1 for offset in aligned_edges))
        self.assert_no_horizontal_overflow()

        self.page.set_viewport_size({"width": 390, "height": 844})
        self.assertFalse(header.is_visible())
        mobile_rows = table.locator("[data-collection-item]")
        mobile_heights = mobile_rows.evaluate_all(
            "rows => rows.map(row => row.getBoundingClientRect().height)"
        )
        self.assertLessEqual(max(mobile_heights), 210)
        self.assertLessEqual(mobile_heights[1], 120)
        self.assertNotEqual(
            mobile_rows.first.evaluate(
                "row => getComputedStyle(row).borderRadius"
            ),
            "0px",
        )
        self.assert_no_horizontal_overflow()

    def test_annotation_search_rows_keep_identical_columns(self):
        Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.NOTE,
            title="Alignement principal",
            body="Contenu pour vérifier les colonnes.",
            source_path=response_detail_url(self.first.response),
        )
        Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.NOTE,
            title="Alignement secondaire",
            body="Une ligne sans lien de source.",
        )
        self.page.set_viewport_size({"width": 1200, "height": 800})
        self.page.goto(
            self.live_server_url
            + reverse("study:annotation_search")
            + "?q=alignement"
        )
        self.page.get_by_role("button", name="Tableau").click()

        table = self.page.locator(".collection-table--annotation-search")
        header = table.locator("[data-collection-table-header]")
        rows = table.locator("[data-collection-item]")
        self.assertEqual(rows.count(), 2)
        header_tracks = header.evaluate(
            "element => getComputedStyle(element).gridTemplateColumns"
        )
        self.assertEqual(
            rows.evaluate_all(
                "elements => elements.map("
                "element => getComputedStyle(element).gridTemplateColumns)"
            ),
            [header_tracks, header_tracks],
        )
        row_edge_offsets = self.page.evaluate(
            """
            () => {
              const headerCells = [
                ...document.querySelectorAll(
                  '.collection-table--annotation-search '
                  + '[data-collection-table-header] > span'
                )
              ];
              return [
                ...document.querySelectorAll(
                  '.collection-table--annotation-search '
                  + '[data-collection-item]'
                )
              ].map(row => [...row.children].map((cell, index) => {
                return Math.abs(
                  cell.getBoundingClientRect().left
                  - headerCells[index].getBoundingClientRect().left
                );
              }));
            }
            """
        )
        self.assertTrue(
            all(
                offset <= 1
                for row_offsets in row_edge_offsets
                for offset in row_offsets
            )
        )
        self.assert_no_horizontal_overflow()

    def test_mobile_note_dialogs_create_and_edit_cleanly(self):
        notes_url = (
            self.live_server_url
            + reverse(
                "study:task_notes",
                args=[self.part.slug, self.task.slug],
            )
        )
        self.page.goto(notes_url)
        self.page.get_by_role("button", name="Nouvelle note").click()

        create_dialog = self.page.locator("#note-create-dialog")
        create_dialog.wait_for(state="visible")
        dialog_box = create_dialog.bounding_box()
        viewport = self.page.viewport_size
        self.assertIsNotNone(dialog_box)
        self.assertLess(
            abs(
                dialog_box["x"]
                + dialog_box["width"] / 2
                - viewport["width"] / 2
            ),
            4,
        )
        self.assertLess(
            abs(
                dialog_box["y"]
                + dialog_box["height"] / 2
                - viewport["height"] / 2
            ),
            24,
        )

        create_dialog.get_by_label("Titre (facultatif)").fill(
            "Note créée dans la fenêtre"
        )
        create_dialog.get_by_label("Votre note").fill(
            "Première version de la note."
        )
        create_dialog.get_by_role("button", name="Enregistrer").click()

        note_card = self.page.locator(
            ".annotation-card",
            has_text="Note créée dans la fenêtre",
        )
        note_card.wait_for()
        self.assertTrue(self.page.url.split("#")[-1].startswith("note-"))

        note_card.get_by_role("button", name="Modifier la note").click()
        edit_dialog = self.page.locator("#note-edit-dialog")
        edit_dialog.wait_for(state="visible")
        self.assertEqual(
            edit_dialog.get_by_label("Titre (facultatif)").input_value(),
            "Note créée dans la fenêtre",
        )
        edit_dialog.get_by_label("Votre note").fill("")
        edit_dialog.get_by_role("button", name="Enregistrer").click()
        edit_dialog.get_by_text(
            "Corrigez la note avant de l'enregistrer."
        ).wait_for()
        self.assertTrue(edit_dialog.is_visible())
        edit_dialog.get_by_label("Votre note").fill(
            "Version corrigée depuis la fenêtre."
        )
        edit_dialog.get_by_role("button", name="Enregistrer").click()
        self.page.locator(
            ".annotation-card__body",
            has_text="Version corrigée depuis la fenêtre.",
        ).wait_for()

        self.page.get_by_role("button", name="Tableau").click()
        note_row = self.page.locator(
            ".annotation-table__row",
            has_text="Version corrigée depuis la fenêtre.",
        )
        note_row.wait_for()
        action_buttons = note_row.locator(".annotation-action")
        self.assertEqual(action_buttons.count(), 3)
        self.assertTrue(
            all(
                size["width"] <= 38 and size["height"] <= 38
                for size in action_buttons.evaluate_all(
                    """
                    buttons => buttons.map(button => {
                      const rect = button.getBoundingClientRect();
                      return {width: rect.width, height: rect.height};
                    })
                    """
                )
            )
        )
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
            source_path=response_detail_url(self.first.response),
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
        response_url = response_detail_url(self.first.response)
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

        category_url = reverse(
            "study:vocabulary_category",
            args=[category.slug],
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
        views_metric = self.page.locator(".home-hero__metrics dd").nth(1)
        views_metric.evaluate(
            """
            element => {
              element.firstChild.textContent = "188";
              element.querySelector(".hero-metric__total").textContent = "/ 9900";
            }
            """
        )
        self.assertLessEqual(
            views_metric.evaluate("element => element.scrollWidth"),
            views_metric.evaluate("element => element.clientWidth"),
        )
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

        for width in (1024, 900, 861):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": 700})
                self.assertLessEqual(
                    views_metric.evaluate("element => element.scrollWidth"),
                    views_metric.evaluate("element => element.clientWidth"),
                )
                self.assert_no_horizontal_overflow()

        self.page.set_viewport_size({"width": 320, "height": 568})
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

    def test_written_expression_sections_and_notes_tabs_are_centered(self):
        Command()._import_sections(load_sections())
        self.page.set_viewport_size({"width": 1200, "height": 800})

        self.page.goto(self.live_server_url + reverse("study:expression"))
        self.page.locator(
            ".expression-path--ee",
            has_text="Expression écrite",
        ).click()
        self.page.locator("h1", has_text="Expression écrite").wait_for()
        self.assertEqual(self.page.locator(".deck--soon").count(), 3)
        self.assert_no_horizontal_overflow()

        self.page.goto(
            self.live_server_url + reverse("study:notes_overview")
        )
        tabs_box = self.page.locator(".notes-tabs").bounding_box()
        main_box = self.page.locator("#main").bounding_box()
        self.assertAlmostEqual(
            tabs_box["x"] + tabs_box["width"] / 2,
            main_box["x"] + main_box["width"] / 2,
            delta=1,
        )
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
        question_buttons = self.page.locator(".ce-question-map__grid > *")
        question_box = question_buttons.first.bounding_box()
        self.assertLessEqual(question_box["width"], 34)
        self.assertAlmostEqual(
            question_box["width"],
            question_box["height"],
            delta=1,
        )
        self.page.locator(".ce-choice", has_text="Choix B français 1").click()
        self.page.get_by_role(
            "heading",
            name="A · Choix A français 1",
        ).wait_for()
        explanation = self.page.locator(".ce-rationales--explanation")
        self.assertTrue(explanation.evaluate("element => element.open"))
        header_box = self.page.locator(".ce-correction__head").bounding_box()
        explanation_box = explanation.bounding_box()
        self.assertGreaterEqual(
            explanation_box["y"] - header_box["y"] - header_box["height"],
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
