from __future__ import annotations

import os

from django.contrib.sessions.models import Session
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from playwright.sync_api import sync_playwright

from django.utils import timezone

from study import content
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
    Task,
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
        mobile_active_style = navigation.get_by_role(
            "link",
            name="Accueil",
            exact=True,
        ).evaluate(
            """
            element => {
              const style = getComputedStyle(element);
              return {
                background: style.backgroundColor,
                borderLeftWidth: style.borderLeftWidth,
                borderRadius: style.borderRadius,
              };
            }
            """
        )
        self.assertEqual(mobile_active_style["background"], "rgba(0, 0, 0, 0)")
        self.assertEqual(mobile_active_style["borderLeftWidth"], "3px")
        self.assertEqual(mobile_active_style["borderRadius"], "0px")
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
        desktop_active_style = navigation.get_by_role(
            "link",
            name="Accueil",
            exact=True,
        ).evaluate(
            """
            element => {
              const style = getComputedStyle(element);
              return {
                background: style.backgroundColor,
                borderBottomWidth: style.borderBottomWidth,
                borderRadius: style.borderRadius,
              };
            }
            """
        )
        self.assertEqual(desktop_active_style["background"], "rgba(0, 0, 0, 0)")
        self.assertEqual(desktop_active_style["borderBottomWidth"], "2px")
        self.assertEqual(desktop_active_style["borderRadius"], "0px")

    @override_settings(DEBUG=False)
    def test_unknown_url_uses_custom_not_found_page(self):
        self.page.set_viewport_size({"width": 320, "height": 568})

        response = self.page.goto(
            self.live_server_url + "/chemin-introuvable/"
        )

        self.assertEqual(response.status, 404)
        self.page.get_by_role(
            "heading",
            name="Cette page n’existe pas",
        ).wait_for()
        self.page.locator(
            '.not-found-page__icon .ui-icon[data-icon="compass"]'
        ).wait_for()
        self.page.get_by_role(
            "link",
            name="Retour à l’accueil",
        ).wait_for()
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
        self.assertEqual(icon.get_attribute("data-icon"), self.task.icon)
        self.assertNotEqual(
            icon.evaluate("element => getComputedStyle(element).color"),
            self.page.locator(
                ".title-with-icon > span:last-child"
            ).evaluate("element => getComputedStyle(element).color"),
        )
        self.assertEqual(
            self.page.locator(".task-hero").evaluate(
                "element => getComputedStyle(element).backgroundColor"
            ),
            "rgba(0, 0, 0, 0)",
        )
        self.assertEqual(
            self.page.locator(".task-nav a.is-active").evaluate(
                "element => getComputedStyle(element).backgroundColor"
            ),
            "rgba(0, 0, 0, 0)",
        )
        self.assertEqual(
            self.page.locator(".task-nav a.is-active").evaluate(
                "element => getComputedStyle(element).borderBottomWidth"
            ),
            "3px",
        )

    def test_tache_two_memories_are_structured_on_desktop_and_mobile(self):
        Command()._import_sections(load_sections())
        memory_path = reverse(
            "study:task_memory_detail",
            args=["eo", "tache-2", 1],
        )
        highlight = Annotation.objects.create(
            user=self.user,
            task=Task.objects.get(part__slug="eo", slug="tache-2"),
            kind=AnnotationKind.HIGHLIGHT,
            quote="il",
            source_path=memory_path,
            source_key="question-bank:part-01",
            start_offset=0,
            end_offset=2,
            prefix="Ancienne interface · Y a-t-",
            suffix=(
                "des tarifs réduits pour les étudiants "
                "ancienne interface"
            ),
        )
        spanning_highlight = Annotation.objects.create(
            user=self.user,
            task=highlight.task,
            kind=AnnotationKind.HIGHLIGHT,
            quote=(
                "familles ?   03   "
                "Est-ce qu'on peut essayer"
            ),
            source_path=memory_path,
            source_key="question-bank:part-01",
            start_offset=0,
            end_offset=42,
            prefix="Ancienne interface · les seniors ou les ",
            suffix=" avant de s'engager ancienne interface",
        )
        part_url = (
            self.live_server_url
            + reverse("study:part_detail", args=["eo"])
        )
        overview_url = (
            self.live_server_url
            + reverse("study:task_detail", args=["eo", "tache-2"])
        )
        memories_url = (
            self.live_server_url
            + reverse("study:task_memories", args=["eo", "tache-2"])
        )
        memory_url = (
            self.live_server_url
            + memory_path
        )
        self.page.set_viewport_size({"width": 1280, "height": 850})
        self.page.goto(part_url)
        self.page.get_by_role("button", name="Tableau").click()
        guide_progress = self.page.locator(".deck__progress-cell--guide")
        guide_row = self.page.locator(
            ".deck:has(.deck__progress-cell--guide)"
        )
        self.assertEqual(guide_progress.count(), 1)
        self.assertNotEqual(
            guide_row.evaluate(
                "element => getComputedStyle(element).backgroundColor"
            ),
            self.page.locator(".collection-table--decks").evaluate(
                "element => getComputedStyle(element).backgroundColor"
            ),
        )
        guide_style = guide_progress.evaluate(
            """
            element => ({
              background: getComputedStyle(element).backgroundColor,
              radius: getComputedStyle(element).borderRadius,
              barHeight: element.querySelector(
                '.progress'
              ).getBoundingClientRect().height,
            })
            """
        )
        self.assertEqual(guide_style["background"], "rgba(0, 0, 0, 0)")
        self.assertEqual(guide_style["radius"], "0px")
        self.assertLessEqual(guide_style["barHeight"], 10)
        self.assertEqual(
            guide_progress.locator(".deck__progress-copy").inner_text(),
            "0/190 apprises · 0/348 sujets terminés",
        )

        self.page.goto(overview_url)
        self.page.get_by_role(
            "heading",
            name="Tâche 2",
            exact=True,
        ).wait_for()
        overview_panels = self.page.locator(
            "[data-tache-two-overview-panel]"
        )
        self.assertEqual(overview_panels.count(), 2)
        self.assertEqual(
            len(
                self.page.locator(
                    ".tache-two-overview-grid"
                ).evaluate(
                    "element => getComputedStyle(element)"
                    ".gridTemplateColumns.split(' ')"
                )
            ),
            2,
        )
        self.assertEqual(
            overview_panels.nth(0).get_by_role(
                "heading",
                name="Mémoires",
                exact=True,
            ).count(),
            1,
        )
        self.assertEqual(
            overview_panels.nth(1).get_by_role(
                "heading",
                name="Sujets",
                exact=True,
            ).count(),
            1,
        )
        self.assertEqual(
            overview_panels.nth(0).evaluate(
                "element => element.tagName"
            ),
            "A",
        )
        self.assertEqual(
            overview_panels.nth(0).get_attribute("href"),
            memories_url.removeprefix(self.live_server_url),
        )
        self.assertEqual(
            overview_panels.nth(1).get_attribute("href"),
            reverse("study:task_browse", args=["eo", "tache-2"]),
        )
        panel_footer = overview_panels.nth(0).locator(
            ".tache-two-overview-panel__footer"
        )
        progress_box = panel_footer.locator(
            ".tache-two-progress-summary"
        ).bounding_box()
        action_box = panel_footer.locator(
            ".tache-two-overview-panel__action"
        ).bounding_box()
        self.assertLess(
            abs(
                (progress_box["y"] + progress_box["height"] / 2)
                - (action_box["y"] + action_box["height"] / 2)
            ),
            12,
        )
        overview_nav = self.page.locator(".task-nav--memories")
        self.assertEqual(overview_nav.locator("a").count(), 3)
        self.assertEqual(
            overview_nav.locator("a.is-active").inner_text(),
            "Vue d'ensemble",
        )
        self.assertEqual(
            self.page.locator("[data-collection-view-toggle]").count(),
            0,
        )

        self.page.set_viewport_size({"width": 320, "height": 700})
        self.assertEqual(
            len(
                self.page.locator(
                    ".tache-two-overview-grid"
                ).evaluate(
                    "element => getComputedStyle(element)"
                    ".gridTemplateColumns.split(' ')"
                )
            ),
            1,
        )
        self.assert_no_horizontal_overflow()

        self.page.set_viewport_size({"width": 1280, "height": 850})
        overview_panels.nth(0).click()
        self.page.wait_for_url(memories_url)
        self.page.get_by_role(
            "heading",
            name="Mémoires",
            exact=True,
        ).wait_for()
        table_toggle = self.page.get_by_role("button", name="Tableau")
        cards_toggle = self.page.get_by_role("button", name="Cartes")
        table_header = self.page.locator(
            ".collection-table-header--memories"
        )
        self.assertEqual(table_toggle.get_attribute("aria-pressed"), "true")
        self.assertTrue(table_header.is_visible())
        memory_entry = self.page.locator(".memory-entry")
        self.assertEqual(memory_entry.count(), 6)
        memory_entry = memory_entry.first
        self.assertEqual(
            len(
                memory_entry.evaluate(
                    "element => getComputedStyle(element)"
                    ".gridTemplateColumns.split(' ')"
                )
            ),
            3,
        )
        cards_toggle.click()
        self.assertEqual(cards_toggle.get_attribute("aria-pressed"), "true")
        self.assertFalse(table_header.is_visible())
        self.assertLess(
            memory_entry.bounding_box()["width"],
            self.page.locator("main").bounding_box()["width"] * 0.65,
        )
        table_toggle.click()
        self.assertEqual(table_toggle.get_attribute("aria-pressed"), "true")
        self.assertTrue(table_header.is_visible())
        self.assertLessEqual(memory_entry.bounding_box()["height"], 155)
        self.assertEqual(
            memory_entry.get_attribute("href"),
            memory_url.removeprefix(self.live_server_url),
        )
        entry_borders = self.page.locator(".memory-entry").last.evaluate(
            """
            element => {
              const style = getComputedStyle(element);
              return [
                style.borderTopColor,
                style.borderRightColor,
                style.borderBottomColor,
                style.borderLeftColor,
              ];
            }
            """
        )
        self.assertEqual(len(set(entry_borders)), 1)
        memory_nav = self.page.locator(".task-nav--memories")
        self.assertEqual(memory_nav.locator("a").count(), 3)
        self.assertEqual(
            memory_nav.locator("a.is-active").inner_text(),
            "Mémoires",
        )
        self.page.set_viewport_size({"width": 320, "height": 700})
        self.assertEqual(
            len(
                memory_entry.evaluate(
                    "element => getComputedStyle(element)"
                    ".gridTemplateColumns.split(' ')"
                )
            ),
            2,
        )
        self.assertEqual(
            len(
                memory_nav.evaluate(
                    "element => getComputedStyle(element)"
                    ".gridTemplateColumns.split(' ')"
                )
            ),
            3,
        )
        self.assert_no_horizontal_overflow()

        self.page.set_viewport_size({"width": 1280, "height": 850})
        memory_entry.click()
        self.page.wait_for_url(memory_url)
        self.page.get_by_role(
            "heading",
            name="Mémoire 1",
            exact=True,
        ).wait_for()
        task_nav = self.page.locator(".task-nav--memories")
        self.assertEqual(task_nav.locator("a").count(), 3)
        self.assertEqual(
            task_nav.locator("a.is-active").inner_text(),
            "Mémoires",
        )

        sections = self.page.locator("[data-question-bank-section]")
        questions = self.page.locator("[data-question-bank-question]")
        self.assertEqual(sections.count(), 21)
        self.assertEqual(questions.count(), 65)
        saved_mark = self.page.locator(
            f'[data-highlight-id="{highlight.pk}"]'
        )
        saved_mark.wait_for()
        self.assertEqual(saved_mark.inner_text(), "il")
        self.assertIn(
            "Y a-t-il des tarifs réduits",
            saved_mark.locator(
                "xpath=ancestor::*[@data-question-bank-question]"
            ).inner_text(),
        )
        spanning_marks = self.page.locator(
            f'[data-highlight-id="{spanning_highlight.pk}"]'
        )
        spanning_marks.first.wait_for()
        self.assertGreater(spanning_marks.count(), 1)
        spanning_text = " ".join(spanning_marks.all_inner_texts())
        self.assertIn(
            "familles ? 03 Est-ce qu'on peut essayer",
            " ".join(spanning_text.split()),
        )
        structural_marks = self.page.locator(
            ".question-bank-questions > mark.user-highlight, "
            "[data-question-bank-question] > mark.user-highlight"
        )
        self.assertEqual(structural_marks.count(), 0)
        self.assertEqual(
            questions.nth(1).locator(":scope > *").count(),
            3,
        )
        self.assertEqual(
            questions.nth(2).locator(":scope > *").count(),
            3,
        )
        desktop = self.page.locator("[data-question-bank]").evaluate(
            """
            root => {
              const index = root.querySelector('.question-bank-index');
              const content = root.querySelector('.question-bank-sections');
              const first = root.querySelector('.question-bank-section');
              const indexRect = index.getBoundingClientRect();
              const contentRect = content.getBoundingClientRect();
              const style = getComputedStyle(first);
              return {
                columns: getComputedStyle(root).gridTemplateColumns,
                indexRight: indexRect.right,
                contentLeft: contentRect.left,
                borderColors: [
                  style.borderTopColor,
                  style.borderRightColor,
                  style.borderBottomColor,
                  style.borderLeftColor,
                ],
              };
            }
            """
        )
        self.assertGreaterEqual(desktop["contentLeft"], desktop["indexRight"])
        self.assertEqual(len(set(desktop["borderColors"])), 1)
        self.assert_no_horizontal_overflow()

        first_question = questions.first
        first_checkbox = first_question.get_by_role("checkbox")
        self.assertEqual(first_checkbox.get_attribute("aria-checked"), "false")
        self.assertIn(
            "Comment fonctionnent les tarifs",
            first_checkbox.get_attribute("aria-label"),
        )
        self.assertNotEqual(
            first_checkbox.locator(".ui-icon").evaluate(
                "element => getComputedStyle(element).color"
            ),
            "rgba(0, 0, 0, 0)",
        )
        first_checkbox.click()
        self.page.get_by_role(
            "heading",
            name="1 sur 65 questions apprises",
            exact=True,
        ).wait_for()
        self.assertTrue(
            first_checkbox.evaluate(
                "element => document.activeElement === element"
            )
        )
        self.assertEqual(first_checkbox.get_attribute("aria-checked"), "true")
        self.assertEqual(
            first_checkbox.locator(".ui-icon").evaluate(
                "element => getComputedStyle(element).color"
            ),
            "rgb(255, 255, 255)",
        )
        self.assertTrue(
            "is-complete"
            in (first_question.get_attribute("class") or "")
        )
        self.page.reload()
        first_checkbox = self.page.get_by_role("checkbox").first
        self.assertEqual(first_checkbox.get_attribute("aria-checked"), "true")
        self.page.get_by_role(
            "heading",
            name="1 sur 65 questions apprises",
            exact=True,
        ).wait_for()

        self.page.set_viewport_size({"width": 320, "height": 700})
        mobile = self.page.locator("[data-question-bank]").evaluate(
            """
            root => {
              const nav = root.querySelector('.question-bank-index nav');
              const taskNav = document.querySelector('.task-nav--memories');
              return {
                columns: getComputedStyle(root).gridTemplateColumns,
                navScrolls: nav.scrollWidth > nav.clientWidth,
                taskNavColumns: getComputedStyle(taskNav).gridTemplateColumns,
              };
            }
            """
        )
        self.assertEqual(len(mobile["columns"].split()), 1)
        self.assertTrue(mobile["navScrolls"])
        self.assertEqual(len(mobile["taskNavColumns"].split()), 3)
        checkbox_shape = self.page.get_by_role("checkbox").first.evaluate(
            """
            element => {
              const rect = element.getBoundingClientRect();
              const visual = getComputedStyle(element, '::before');
              return {
                targetWidth: rect.width,
                targetHeight: rect.height,
                visualWidth: parseFloat(visual.width),
                visualHeight: parseFloat(visual.height),
                visualBorderRadius: visual.borderRadius,
              };
            }
            """
        )
        self.assertGreaterEqual(checkbox_shape["targetWidth"], 30)
        self.assertEqual(
            checkbox_shape["targetWidth"],
            checkbox_shape["targetHeight"],
        )
        self.assertLessEqual(checkbox_shape["visualWidth"], 14)
        self.assertEqual(
            checkbox_shape["visualWidth"],
            checkbox_shape["visualHeight"],
        )
        self.assertEqual(checkbox_shape["visualBorderRadius"], "50%")
        question_layout = self.page.locator(
            "[data-question-bank-question]"
        ).first.evaluate(
            """
            row => {
              const number = row.querySelector(
                '.question-bank-question__number'
              ).getBoundingClientRect();
              const text = row.querySelector(':scope > div')
                .getBoundingClientRect();
              const checkbox = row.querySelector(
                '.memory-question-check'
              ).getBoundingClientRect();
              return {
                numberLeft: number.left,
                textLeft: text.left,
                textRight: text.right,
                checkboxLeft: checkbox.left,
              };
            }
            """
        )
        self.assertLess(question_layout["numberLeft"], question_layout["textLeft"])
        self.assertLessEqual(
            question_layout["textRight"],
            question_layout["checkboxLeft"],
        )
        self.assert_no_horizontal_overflow()

        self.page.get_by_role(
            "link",
            name="Vue d'ensemble",
            exact=True,
        ).click()
        self.page.wait_for_url(overview_url)
        memory_panel = self.page.locator(
            ".tache-two-overview-panel--memories"
        )
        self.assertEqual(
            memory_panel.locator(
                ".tache-two-progress-summary__copy > span:last-child"
            ).inner_text(),
            "1/190 questions apprises",
        )
        self.assertTrue(
            "progress-status--active"
            in (
                memory_panel.locator(
                    ".progress-status"
                ).get_attribute("class")
                or ""
            )
        )
        self.assert_no_horizontal_overflow()

    def test_tache_two_subjects_have_practice_and_vocabulary_flow(self):
        command = Command()
        task_map = command._import_sections(load_sections())
        months = content.load_tache_two_subject_months()
        theme_map = command._import_themes(
            content.tache_two_themes(months),
            task_map,
        )
        family_map = command._import_families(
            content.tache_two_families(months)
        )
        responses = content.parse_tache_two_responses(months)
        response_map = command._import_responses(
            responses,
            theme_map,
            family_map,
        )
        prompt_index = command._import_prompts(
            responses,
            response_map,
            theme_map,
            family_map,
        )
        command._import_phrases(
            content.parse_tache_two_subject_vocabulary(responses),
            prompt_index,
        )
        command._sync_cards(response_map, user=self.user)
        overview_path = reverse(
            "study:task_detail",
            args=["eo", "tache-2"],
        )
        index_path = reverse(
            "study:task_browse",
            args=["eo", "tache-2"],
        )
        batch_path = reverse(
            "study:task_subject_batch",
            args=["eo", "tache-2", "janvier", 1],
        )
        subject_path = reverse(
            "study:task_subject_detail",
            args=["eo", "tache-2", "janvier", 1, 1],
        )

        self.page.set_viewport_size({"width": 1280, "height": 850})
        self.page.goto(self.live_server_url + overview_path)
        memory_heading = self.page.get_by_role(
            "heading",
            name="Mémoires",
            exact=True,
        )
        subject_heading = self.page.get_by_role(
            "heading",
            name="Sujets",
            exact=True,
        )
        memory_heading.wait_for()
        subject_heading.wait_for()
        self.assertLess(
            abs(
                memory_heading.bounding_box()["y"]
                - subject_heading.bounding_box()["y"]
            ),
            2,
        )
        self.assertEqual(
            self.page.locator("[data-tache-two-subject-batch]").count(),
            0,
        )
        self.assertEqual(
            self.page.locator(
                ".tache-two-overview-panel "
                ".tache-two-progress-summary"
            ).count(),
            2,
        )

        self.page.goto(self.live_server_url + index_path)
        self.page.get_by_role(
            "heading",
            name="Sujets par mois",
            exact=True,
        ).wait_for()
        directory_metrics = [
            int(value)
            for value in self.page.locator(
                ".memory-overview-hero__metrics dd"
            ).all_text_contents()
        ]
        month_count, batch_count, _ = directory_metrics
        self.assertEqual(
            self.page.locator("[data-tache-two-subject-batch]").count(),
            batch_count,
        )
        card_month_toggles = self.page.locator(
            ".tache-two-month__toggle"
        )
        self.assertTrue(
            all(
                card_month_toggles.nth(index).get_attribute(
                    "aria-expanded"
                )
                == "false"
                for index in range(card_month_toggles.count())
            )
        )
        card_month_grids = self.page.locator(".subject-batch-grid")
        self.assertTrue(
            all(
                card_month_grids.nth(index).is_hidden()
                for index in range(card_month_grids.count())
            )
        )
        self.assertEqual(self.page.get_by_role("note").count(), 0)
        self.assertEqual(self.page.get_by_text("Réflexe Mémoire").count(), 0)

        table_toggle = self.page.get_by_role("button", name="Tableau")
        table_toggle.click()
        self.assertEqual(table_toggle.get_attribute("aria-pressed"), "true")
        table_header = self.page.locator(
            ".tache-two-batch-table thead"
        )
        self.assertEqual(table_header.count(), 1)
        self.assertTrue(table_header.is_visible())
        month_groups = self.page.locator(
            "[data-tache-two-month-group]"
        )
        self.assertEqual(
            month_groups.count(),
            month_count,
        )
        table_rows = self.page.locator("[data-tache-two-month-row]")
        self.assertEqual(table_rows.count(), batch_count)
        self.assertTrue(
            all(
                table_rows.nth(index).is_hidden()
                for index in range(table_rows.count())
            )
        )

        first_month_toggle = self.page.locator(
            ".tache-two-batch-table__month-toggle"
        ).first
        first_month_rows = month_groups.first.locator(
            "[data-tache-two-month-row]"
        )
        second_month_rows = month_groups.nth(1).locator(
            "[data-tache-two-month-row]"
        )
        first_month_toggle.click()
        self.assertEqual(
            first_month_toggle.get_attribute("aria-expanded"),
            "true",
        )
        self.assertTrue(
            all(
                first_month_rows.nth(index).is_visible()
                for index in range(first_month_rows.count())
            )
        )
        self.assertTrue(
            all(
                second_month_rows.nth(index).is_hidden()
                for index in range(second_month_rows.count())
            )
        )

        table_shell = self.page.locator(".tache-two-batch-table-shell")
        self.page.set_viewport_size({"width": 320, "height": 700})
        self.assertTrue(
            table_shell.evaluate(
                "element => element.scrollWidth > element.clientWidth"
            )
        )
        self.assert_no_horizontal_overflow()
        self.page.set_viewport_size({"width": 1280, "height": 850})
        self.page.locator("[data-tache-two-subject-table-link]").first.click()
        self.page.wait_for_url(self.live_server_url + batch_path)
        self.page.get_by_role(
            "heading",
            name="Janvier · Batch 1",
            exact=True,
        ).wait_for()
        self.assertEqual(
            self.page.locator("[data-tache-two-subject]").count(),
            5,
        )
        self.assertEqual(
            self.page.locator(
                ".tache-two-subject-card .progress-status--new"
            ).count(),
            5,
        )
        self.assertTrue(
            self.page.locator(
                ".collection-table-header--tache-two-subjects"
            ).is_visible()
        )

        self.page.set_viewport_size({"width": 320, "height": 700})
        self.assert_no_horizontal_overflow()
        self.page.locator("[data-tache-two-subject]").first.click()
        self.page.wait_for_url(self.live_server_url + subject_path)
        self.page.get_by_role(
            "heading",
            name="Achat d'objets avant un déménagement",
            exact=True,
        ).wait_for()
        self.assertEqual(
            self.page.locator("[data-tache-two-question]").count(),
            14,
        )
        self.assertEqual(
            self.page.locator(".tache-two-question__memory").count(),
            0,
        )
        self.page.get_by_text("Progression du sujet", exact=True).wait_for()
        self.page.get_by_role(
            "link",
            name="Pratiquer ce sujet",
            exact=True,
        ).wait_for()
        self.page.get_by_role(
            "link",
            name="Pratiquer les vocabs",
            exact=True,
        ).wait_for()
        self.assertEqual(
            self.page.locator("#subject-vocabulary .response-batch").count(),
            3,
        )
        self.assert_no_horizontal_overflow()

        self.page.get_by_role(
            "link",
            name="Pratiquer ce sujet",
            exact=True,
        ).click()
        self.page.get_by_text(
            "Questions d'interaction",
            exact=True,
        ).wait_for()
        self.assertNotIn("3 arguments", self.page.locator("main").inner_text())

        self.page.goto(self.live_server_url + subject_path)
        self.page.get_by_role(
            "link",
            name="Pratiquer les vocabs",
            exact=True,
        ).click()
        self.page.get_by_text(
            "Vocabulaire du sujet",
            exact=True,
        ).wait_for()
        self.assert_no_horizontal_overflow()

    def test_subject_vocabulary_directory_searches_rich_decks(self):
        self.page.set_viewport_size({"width": 1120, "height": 760})
        first_prompt = self.first.response.prompts.get(is_canonical=True)
        second_prompt = self.second.response.prompts.get(is_canonical=True)
        first_prompt.text = "Faut-il voyager pour découvrir le monde ?"
        first_prompt.save(update_fields=["text"])
        second_prompt.text = "Les réseaux sociaux rapprochent-ils les jeunes ?"
        second_prompt.save(update_fields=["text"])
        first_vocabulary = factories.make_phrase(tier="subject")
        first_vocabulary.source_prompts.add(first_prompt)
        factories.make_phrase_card(
            phrase=first_vocabulary,
            user=self.user,
        )
        second_vocabulary = factories.make_phrase(tier="subject")
        second_vocabulary.source_prompts.add(second_prompt)
        factories.make_phrase_card(
            phrase=second_vocabulary,
            user=self.user,
        )

        self.page.goto(
            self.live_server_url + reverse("study:vocabulary")
        )
        self.page.locator(
            "a[data-vocabulary-path].expression-path--eo"
        ).click()
        self.page.wait_for_url(
            self.live_server_url
            + reverse("study:part_vocabulary", args=[self.part.slug])
        )
        self.page.get_by_role(
            "heading",
            name=f"Vocabulaire · {self.part.name}",
            exact=True,
        ).wait_for()
        self.assert_no_horizontal_overflow()
        self.page.locator(
            ".deck",
            has_text=self.task.name,
        ).click()
        self.page.wait_for_url(
            self.live_server_url
            + reverse(
                "study:task_phrases",
                args=[self.part.slug, self.task.slug],
            )
            + "#vocabulaire-par-sujet"
        )
        summary_layout = self.page.evaluate(
            """() => {
              const hero = document.querySelector(
                '.vocabulary-hub > .vocabulary-hero'
              ).getBoundingClientRect();
              const statuses = document.querySelector(
                '.vocabulary-summary-row > .vocabulary-status-grid'
              ).getBoundingClientRect();
              const toolbar = document.querySelector(
                '.vocabulary-summary-row > .collection-view-toolbar'
              ).getBoundingClientRect();
              return {
                heroGap: statuses.top - hero.bottom,
                sharesRow: toolbar.top < statuses.bottom &&
                  toolbar.bottom > statuses.top,
              };
            }"""
        )
        self.assertLessEqual(summary_layout["heroGap"], 16)
        self.assertTrue(summary_layout["sharesRow"])

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

    def test_question_bank_index_uses_readable_small_type(self):
        Command()._import_sections(load_sections())
        self.page.set_viewport_size({"width": 1280, "height": 800})
        self.page.goto(
            self.live_server_url
            + reverse(
                "study:task_memory_detail",
                args=["eo", "tache-2", 1],
            )
        )
        first_link = self.page.locator(".question-bank-index nav a").first
        first_link.get_by_text("Tarifs", exact=True).wait_for()

        label_size = first_link.locator("strong").evaluate(
            "element => parseFloat(getComputedStyle(element).fontSize)"
        )
        number_size = first_link.locator("span").evaluate(
            "element => parseFloat(getComputedStyle(element).fontSize)"
        )

        self.assertGreaterEqual(label_size, 13)
        self.assertGreaterEqual(number_size, 11.5)
        self.assert_no_horizontal_overflow()

    def test_subject_completion_checkbox_is_explicit_on_mobile(self):
        page_errors = []
        self.page.on("pageerror", lambda error: page_errors.append(str(error)))
        response_id = self.first.response_id
        completion_path = reverse(
            "study:subject_completion",
            args=[self.part.slug, self.task.slug, response_id],
        )
        self.page.goto(
            self.live_server_url + response_detail_url(self.first.response)
        )
        checkbox = self.page.locator(
            "[data-subject-completion-form] button"
        )
        status = self.page.locator(
            f'[data-subject-progress-status="{response_id}"]'
        )

        self.assertEqual(checkbox.get_attribute("aria-checked"), "false")
        self.assertEqual(
            checkbox.evaluate(
                "element => getComputedStyle(element).borderRadius"
            ),
            "50%",
        )
        self.page.get_by_text("J’ai terminé ce sujet", exact=True).wait_for()

        with self.page.expect_response(
            lambda response: completion_path in response.url
        ):
            checkbox.click()
        self.assertFalse(
            page_errors,
            f"Subject completion JavaScript failed: {page_errors}",
        )
        self.page.locator(
            f'[data-subject-progress-status="{response_id}"]',
            has_text="Terminé",
        ).wait_for()
        self.assertEqual(checkbox.get_attribute("aria-checked"), "true")
        self.first.refresh_from_db()
        self.assertIsNotNone(self.first.subject_completed_at)

        self.first.response_practice_started_at = timezone.now()
        self.first.save(update_fields=["response_practice_started_at"])
        with self.page.expect_response(
            lambda response: completion_path in response.url
        ):
            checkbox.click()
        self.page.locator(
            f'[data-subject-progress-status="{response_id}"]',
            has_text="En cours",
        ).wait_for()
        self.assertEqual(checkbox.get_attribute("aria-checked"), "false")
        self.first.refresh_from_db()
        self.assertIsNone(self.first.subject_completed_at)
        self.assertEqual(status.inner_text(), "En cours")
        self.assert_no_horizontal_overflow()

        theme_url = self.live_server_url + theme_detail_url(self.theme)
        self.page.goto(theme_url)
        row = self.page.locator(
            f'[data-subject-progress-row="{response_id}"]'
        )
        row_checkbox = row.locator(
            "[data-subject-completion-form] button"
        )
        with self.page.expect_navigation(wait_until="networkidle"):
            row_checkbox.click()
        self.assertEqual(self.page.url, theme_url)
        self.assertEqual(row_checkbox.get_attribute("aria-checked"), "true")
        self.page.get_by_text("1 terminé.", exact=False).wait_for()
        self.assert_no_horizontal_overflow()

        with self.page.expect_navigation():
            row.locator(".subject-row-hit-area").click()
        self.assertEqual(
            self.page.url,
            self.live_server_url + response_detail_url(self.first.response),
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
        self.assertIn('"Book Antiqua"', layout["fontFamily"])
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
        for selector in (
            ".grade__icon",
            ".grade__key",
            ".kbd-hint kbd",
        ):
            self.assertEqual(
                self.page.locator(selector).first.evaluate(
                    "element => getComputedStyle(element).borderRadius"
                ),
                "50%",
            )
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

    def test_selection_toolbar_keyboard_shortcuts(self):
        self.context.add_init_script(
            """
            (() => {
              const synthesis = {
                getVoices: () => [],
                addEventListener: () => {},
                cancel: () => {},
                resume: () => {},
                speak: utterance => {
                  window.__shortcutSpokenText = utterance.text;
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
              Object.defineProperty(navigator, "clipboard", {
                configurable: true,
                value: {
                  writeText: text => {
                    window.__shortcutCopiedText = text;
                    return Promise.resolve();
                  },
                },
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
        prompt_text = prompt.text_content()
        self.page.locator("#reveal").click()

        toolbar = self.page.locator("[data-selection-translate]")
        shortcuts = {
            "[data-copy-selection]": "C",
            "[data-read-selection]": "R",
            "[data-translate-selection]": "T",
            "[data-note-selection]": "N",
            "[data-highlight-selection]": "H",
        }
        for selector, key in shortcuts.items():
            self.assertEqual(
                toolbar.locator(selector).get_attribute("aria-keyshortcuts"),
                key,
            )

        self.select_prompt(start=0, end=12)
        selected = self.page.evaluate(
            "window.getSelection().toString().trim()"
        )
        self.page.keyboard.press("c")
        self.page.wait_for_function(
            "expected => window.__shortcutCopiedText === expected",
            arg=selected,
        )
        self.assertEqual(
            self.page.locator("[data-copy-selection-label]").inner_text(),
            "Copied",
        )
        self.assertEqual(
            ReviewLog.objects.filter(user=self.user).count(),
            0,
        )

        self.select_prompt(start=0, end=12)
        self.page.keyboard.press("r")
        self.page.wait_for_function(
            "expected => window.__shortcutSpokenText === expected",
            arg=selected,
        )
        self.page.wait_for_timeout(150)
        self.assertEqual(prompt.text_content(), prompt_text)
        self.assertEqual(
            ReviewLog.objects.filter(user=self.user).count(),
            0,
        )
        self.page.keyboard.press("Escape")

        self.select_prompt(start=0, end=12)
        with self.page.expect_response(
            lambda response: reverse("study:annotation_create") in response.url
        ):
            self.page.keyboard.press("h")
        prompt.locator("mark.user-highlight").wait_for()

        self.select_prompt(start=0, end=12)
        self.page.keyboard.press("n")
        note_panel = self.page.locator("[data-note-panel]")
        note_panel.wait_for()
        note_body = note_panel.locator("[data-note-body]")
        self.assertTrue(
            note_body.evaluate(
                "element => element === document.activeElement"
            )
        )
        self.page.keyboard.press("h")
        self.assertEqual(note_body.input_value(), "h")
        self.assertEqual(
            Annotation.objects.filter(
                user=self.user,
                kind=AnnotationKind.HIGHLIGHT,
            ).count(),
            1,
        )
        self.page.keyboard.press("Escape")
        self.page.evaluate(
            "document.activeElement && document.activeElement.blur()"
        )

        self.select_prompt(start=0, end=12)
        self.page.keyboard.press("t")
        self.page.locator("[data-translation-panel]").wait_for()

    def test_selection_note_paste_button_inserts_clipboard_at_cursor(self):
        self.context.add_init_script(
            """
            Object.defineProperty(navigator, "clipboard", {
              configurable: true,
              value: {
                readText: () => Promise.resolve("texte collé"),
              },
            });
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
        self.page.locator("[data-note-selection]").click()

        panel = self.page.locator("[data-note-panel]")
        panel.wait_for()
        note_body = panel.locator("[data-note-body]")
        note_body.fill("Avant  après")
        note_body.evaluate("element => element.setSelectionRange(6, 6)")
        paste_button = panel.get_by_role(
            "button",
            name="Coller depuis le presse-papiers",
        )

        paste_button.click()

        panel.get_by_text("Texte collé.", exact=True).wait_for()
        self.assertEqual(note_body.input_value(), "Avant texte collé après")
        self.assertIn(
            "ui-icons.svg?v=3#icon-clipboard-paste",
            paste_button.locator("use").get_attribute("href"),
        )
        self.assert_no_horizontal_overflow()

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
        self.assertLessEqual(first_row.bounding_box()["height"], 88)
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
        self.assertLessEqual(max(mobile_heights), 214)
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

    def test_selected_note_study_card_switches_between_distinct_faces(self):
        Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.NOTE,
            quote="séance",
            body="showing",
            study_later=True,
        )
        self.page.set_viewport_size({"width": 320, "height": 568})
        self.page.goto(
            self.live_server_url + reverse("study:annotation_study")
        )

        front = self.page.locator("[data-study-front]")
        back = self.page.locator("[data-study-back]")
        front.get_by_text("séance", exact=True).wait_for()
        self.assertFalse(back.get_by_text("showing", exact=True).is_visible())

        self.page.locator("[data-study-reveal]").click()

        back.get_by_text("showing", exact=True).wait_for()
        self.assertFalse(front.is_visible())
        self.assertFalse(
            front.get_by_text("séance", exact=True).is_visible()
        )
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
        card_backgrounds = self.page.locator(".daily-card").evaluate_all(
            "cards => cards.map(card => getComputedStyle(card).backgroundColor)"
        )
        surface_color = self.page.evaluate(
            """
            () => {
              const probe = document.createElement("div");
              probe.style.background = "var(--surface)";
              document.body.append(probe);
              const color = getComputedStyle(probe).backgroundColor;
              probe.remove();
              return color;
            }
            """
        )
        self.assertEqual(set(card_backgrounds), {surface_color})
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
        mobile_hero_layout = self.page.locator(".home-hero").evaluate(
            """hero => {
              const heroBox = hero.getBoundingClientRect();
              const copyBox = hero.querySelector(
                '.home-hero__copy'
              ).getBoundingClientRect();
              const metrics = hero.querySelector('.home-hero__metrics');
              const metricsBox = metrics.getBoundingClientRect();
              const metricsStyle = getComputedStyle(metrics);
              const heroStyle = getComputedStyle(hero);
              return {
                height: heroBox.height,
                metricsWidth: metricsBox.width,
                heroWidth: heroBox.width,
                heroContentWidth: hero.clientWidth -
                  parseFloat(heroStyle.paddingLeft) -
                  parseFloat(heroStyle.paddingRight),
                verticalGap: metricsBox.top - copyBox.bottom,
                columns: metricsStyle.gridTemplateColumns.split(' ').length,
                backgroundImage: heroStyle.backgroundImage,
                radius: parseFloat(heroStyle.borderTopLeftRadius),
              };
            }"""
        )
        self.assertLessEqual(
            mobile_hero_layout["height"], 190, mobile_hero_layout
        )
        self.assertAlmostEqual(
            mobile_hero_layout["metricsWidth"],
            mobile_hero_layout["heroContentWidth"],
            delta=1,
        )
        self.assertLessEqual(mobile_hero_layout["verticalGap"], 12)
        self.assertEqual(mobile_hero_layout["columns"], 3)
        self.assertNotEqual(
            mobile_hero_layout["backgroundImage"],
            "none",
            mobile_hero_layout,
        )
        self.assertGreaterEqual(mobile_hero_layout["radius"], 12)
        mobile_heights = self.page.locator(".daily-card").evaluate_all(
            "cards => cards.map(card => card.getBoundingClientRect().height)"
        )
        self.assertLessEqual(max(mobile_heights), 320)
        self.assert_no_horizontal_overflow()

    def test_mobile_active_notes_scope_scrolls_into_view(self):
        for order, slug in enumerate(
            ("tache-0", "tache-1", "tache-2"),
            start=1,
        ):
            task = factories.make_task(part=self.part, slug=slug)
            task.order = order
            task.save(update_fields=["order"])
        self.task.order = 99
        self.task.save(update_fields=["order"])

        self.page.set_viewport_size({"width": 320, "height": 568})
        self.page.goto(
            self.live_server_url
            + reverse(
                "study:task_notes",
                args=[self.part.slug, self.task.slug],
            )
        )
        self.page.wait_for_function(
            """() => {
              const nav = document.querySelector('.notes-scope-nav');
              const active = nav && nav.querySelector('.is-active');
              if (!active) return false;
              const navBox = nav.getBoundingClientRect();
              const activeBox = active.getBoundingClientRect();
              return nav.scrollLeft > 0 &&
                activeBox.left >= navBox.left - 1 &&
                activeBox.right <= navBox.right + 1;
            }"""
        )
        scope_layout = self.page.locator(".notes-scope-nav").evaluate(
            """nav => {
              const navBox = nav.getBoundingClientRect();
              const activeBox = nav.querySelector(
                '.is-active'
              ).getBoundingClientRect();
              return {
                scrollLeft: nav.scrollLeft,
                navLeft: navBox.left,
                navRight: navBox.right,
                activeLeft: activeBox.left,
                activeRight: activeBox.right,
              };
            }"""
        )
        self.assertGreater(scope_layout["scrollLeft"], 0)
        self.assertGreaterEqual(
            scope_layout["activeLeft"],
            scope_layout["navLeft"] - 1,
        )
        self.assertLessEqual(
            scope_layout["activeRight"],
            scope_layout["navRight"] + 1,
        )
        self.assert_no_horizontal_overflow()

    def test_mobile_oral_audio_controls_are_circular_and_operable(self):
        oral_test = factories.make_comprehension_test(
            question_count=1,
            mode=ComprehensionMode.ORALE,
        )
        self.page.add_init_script(
            """
            class FakeSpeechSynthesisUtterance {
              constructor(text) {
                this.text = text;
              }
            }
            const fakeSpeechSynthesis = {
              speaking: false,
              pending: false,
              paused: false,
              getVoices() {
                return [{
                  name: 'Amélie',
                  voiceURI: 'test-fr',
                  lang: 'fr-FR',
                  localService: true,
                  default: true,
                }];
              },
              addEventListener() {},
              speak(utterance) {
                this.lastUtterance = utterance;
                this.speaking = true;
              },
              cancel() {
                this.speaking = false;
                this.pending = false;
                this.paused = false;
              },
              resume() {
                this.paused = false;
              },
            };
            Object.defineProperty(window, 'speechSynthesis', {
              configurable: true,
              value: fakeSpeechSynthesis,
            });
            Object.defineProperty(window, 'SpeechSynthesisUtterance', {
              configurable: true,
              value: FakeSpeechSynthesisUtterance,
            });
            """
        )
        self.page.set_viewport_size({"width": 320, "height": 568})
        self.page.goto(
            self.live_server_url
            + reverse(
                "study:comprehension_oral_question_study",
                args=[oral_test.slug, 1],
            )
        )

        dialogue = self.page.locator(
            '[data-co-audio-play][data-co-audio-target="dialogue"]'
        )
        dialogue.wait_for()
        self.assertEqual(
            dialogue.get_attribute("aria-label"),
            "Écouter le dialogue en français",
        )
        stop = self.page.get_by_role("button", name="Arrêter la lecture")
        stop_metrics = stop.evaluate(
            """element => {
              const style = getComputedStyle(element);
              return {
                width: parseFloat(style.width),
                height: parseFloat(style.height),
                radius: style.borderTopLeftRadius,
              };
            }"""
        )
        self.assertGreaterEqual(stop_metrics["width"], 42)
        self.assertAlmostEqual(
            stop_metrics["width"],
            stop_metrics["height"],
            delta=0.5,
        )
        self.assertEqual(stop_metrics["radius"], "50%")

        self.assertTrue(dialogue.is_enabled())
        self.assertFalse(stop.is_enabled())
        dialogue.click()
        self.assertEqual(dialogue.get_attribute("aria-pressed"), "true")
        self.assertTrue(stop.is_enabled())
        stop.click()
        self.assertEqual(dialogue.get_attribute("aria-pressed"), "false")
        self.assertFalse(stop.is_enabled())
        self.assert_no_horizontal_overflow()

    def test_written_expression_sections_and_notes_tabs_are_centered(self):
        Command()._import_sections(load_sections())
        self.page.set_viewport_size({"width": 1200, "height": 800})

        self.page.goto(self.live_server_url + reverse("study:expression"))
        self.page.locator(
            ".expression-path--ee",
            has_text="Écrite",
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

    def test_text_and_icon_controls_have_distinct_shapes(self):
        Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.NOTE,
            body="Note utilisée pour vérifier les contrôles mobiles.",
        )
        self.page.set_viewport_size({"width": 1200, "height": 800})
        self.page.goto(
            self.live_server_url + reverse("study:notes_overview")
        )

        compose_metrics = self.page.get_by_role(
            "button", name="Nouvelle note"
        ).evaluate(
            """element => {
              const style = getComputedStyle(element);
              return {
                height: parseFloat(style.height),
                radius: parseFloat(style.borderTopLeftRadius),
              };
            }"""
        )
        self.assertGreaterEqual(
            compose_metrics["radius"], compose_metrics["height"] / 2 - 1
        )

        search_metrics = self.page.locator(".search-form .btn--icon").evaluate(
            """element => {
              const style = getComputedStyle(element);
              return {
                width: parseFloat(style.width),
                height: parseFloat(style.height),
                radius: style.borderTopLeftRadius,
                color: style.color,
                iconColor: getComputedStyle(
                  element.querySelector('.btn__icon')
                ).color,
              };
            }"""
        )
        self.assertAlmostEqual(
            search_metrics["width"], search_metrics["height"], delta=0.5
        )
        self.assertEqual(search_metrics["radius"], "50%")
        self.assertNotEqual(
            search_metrics["iconColor"], search_metrics["color"]
        )

        tab_metrics = self.page.locator(".notes-tab").first.evaluate(
            """element => {
              const style = getComputedStyle(element);
              return {
                height: parseFloat(style.height),
                radius: parseFloat(style.borderTopLeftRadius),
              };
            }"""
        )
        self.assertGreaterEqual(
            tab_metrics["radius"], tab_metrics["height"] / 2 - 1
        )

        self.page.set_viewport_size({"width": 320, "height": 568})
        search_input_box = self.page.locator(
            ".notes-toolbar .search-form__input"
        ).bounding_box()
        search_button = self.page.locator(
            ".notes-toolbar .search-form .btn--icon"
        )
        search_button_box = search_button.bounding_box()
        self.assertLess(search_input_box["x"], search_button_box["x"])
        self.assertAlmostEqual(
            search_input_box["y"] + search_input_box["height"] / 2,
            search_button_box["y"] + search_button_box["height"] / 2,
            delta=1,
        )
        self.assertAlmostEqual(
            search_button_box["width"], search_button_box["height"], delta=0.5
        )
        self.assertEqual(
            search_button.evaluate(
                "element => getComputedStyle(element).borderTopLeftRadius"
            ),
            "50%",
        )

        action_boxes = self.page.locator(
            ".notes-toolbar__actions .btn"
        ).evaluate_all(
            """elements => elements.map(element => {
              const rect = element.getBoundingClientRect();
              return {
                y: rect.y,
                width: rect.width,
                height: rect.height,
              };
            })"""
        )
        self.assertEqual(len(action_boxes), 2)
        self.assertAlmostEqual(action_boxes[0]["y"], action_boxes[1]["y"], delta=1)
        self.assertAlmostEqual(
            action_boxes[0]["width"], action_boxes[1]["width"], delta=1
        )

        tabs_box = self.page.locator(".notes-tabs").bounding_box()
        view_toolbar_box = self.page.locator(
            ".notes-view-controls > .collection-view-toolbar"
        ).bounding_box()
        self.assertAlmostEqual(
            tabs_box["y"] + tabs_box["height"] / 2,
            view_toolbar_box["y"] + view_toolbar_box["height"] / 2,
            delta=1,
        )
        view_button_metrics = self.page.locator(
            ".notes-view-controls .collection-view-toggle button"
        ).evaluate_all(
            """elements => elements.map(element => {
              const style = getComputedStyle(element);
              return {
                width: parseFloat(style.width),
                height: parseFloat(style.height),
                radius: style.borderTopLeftRadius,
              };
            })"""
        )
        self.assertEqual(len(view_button_metrics), 2)
        self.assertTrue(
            all(
                abs(item["width"] - item["height"]) <= 0.5
                and item["radius"] == "50%"
                for item in view_button_metrics
            )
        )
        self.assert_no_horizontal_overflow()

        self.page.set_viewport_size({"width": 1200, "height": 800})
        self.page.goto(
            self.live_server_url
            + reverse("study:review")
            + "?kind=spine&reset=1"
        )
        reveal = self.page.locator("#reveal")
        reveal_metrics = reveal.evaluate(
            """element => {
              const style = getComputedStyle(element);
              return {
                height: parseFloat(style.height),
                radius: parseFloat(style.borderTopLeftRadius),
              };
            }"""
        )
        self.assertGreaterEqual(
            reveal_metrics["radius"], reveal_metrics["height"] / 2 - 1
        )
        reveal.click()
        grade_metrics = self.page.locator(".grade").first.evaluate(
            """element => {
              const style = getComputedStyle(element);
              return {
                height: parseFloat(style.height),
                radius: parseFloat(style.borderTopLeftRadius),
              };
            }"""
        )
        self.assertGreaterEqual(
            grade_metrics["radius"], grade_metrics["height"] / 2 - 1
        )

        self.page.set_viewport_size({"width": 320, "height": 568})
        toggle = self.page.get_by_role("button", name="Ouvrir le menu")
        toggle_metrics = toggle.evaluate(
            """element => {
              const style = getComputedStyle(element);
              return {
                width: parseFloat(style.width),
                height: parseFloat(style.height),
                radius: style.borderTopLeftRadius,
              };
            }"""
        )
        self.assertAlmostEqual(
            toggle_metrics["width"], toggle_metrics["height"], delta=0.5
        )
        self.assertEqual(toggle_metrics["radius"], "50%")
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

        self.page.get_by_role("link", name="Batch 1").click()
        self.page.get_by_role(
            "heading",
            name="Batch 01",
        ).wait_for()
        self.assertEqual(self.page.locator(".ce-group-test-row").count(), 5)
        row_checkbox = self.page.locator(
            "[data-comprehension-completion-form] button"
        ).first
        self.assertEqual(row_checkbox.get_attribute("aria-checked"), "false")
        checkbox_metrics = row_checkbox.evaluate(
            """element => {
              const style = getComputedStyle(element);
              return {
                width: parseFloat(style.width),
                height: parseFloat(style.height),
                radius: style.borderTopLeftRadius,
              };
            }"""
        )
        self.assertAlmostEqual(
            checkbox_metrics["width"],
            checkbox_metrics["height"],
            delta=0.5,
        )
        self.assertEqual(checkbox_metrics["radius"], "50%")
        row_checkbox.click()
        self.page.wait_for_load_state("networkidle")
        row_checkbox = self.page.locator(
            "[data-comprehension-completion-form] button"
        ).first
        self.assertEqual(row_checkbox.get_attribute("aria-checked"), "true")
        self.assert_no_horizontal_overflow()

        self.page.get_by_role("link", name="Découvrir le test").click()
        self.page.get_by_role("heading", name=test.title).wait_for()
        detail_checkbox = self.page.locator(
            "[data-comprehension-completion-form] button"
        )
        self.assertEqual(detail_checkbox.get_attribute("aria-checked"), "true")
        detail_checkbox.click()
        self.page.get_by_text("À commencer", exact=True).wait_for()
        self.assertEqual(detail_checkbox.get_attribute("aria-checked"), "false")
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
        self.page.get_by_role("heading", name="Correction détaillée").wait_for()
        self.page.get_by_text("Tentative terminée").wait_for()
        results_checkbox = self.page.locator(
            "[data-comprehension-completion-form] button"
        )
        self.assertEqual(results_checkbox.get_attribute("aria-checked"), "false")
        results_checkbox.click()
        self.page.get_by_text("Terminé", exact=True).wait_for()
        self.assertEqual(results_checkbox.get_attribute("aria-checked"), "true")
        self.assertEqual(self.page.locator(".ce-review-item").count(), 2)
        self.assert_no_horizontal_overflow()

    def test_mobile_oral_completion_control_uses_the_shared_flow(self):
        test = factories.make_comprehension_test(
            question_count=2,
            mode=ComprehensionMode.ORALE,
        )
        self.page.set_viewport_size({"width": 320, "height": 568})
        self.page.goto(
            self.live_server_url
            + reverse("study:comprehension_oral_group", args=[1])
        )

        checkbox = self.page.locator(
            "[data-comprehension-completion-form] button"
        ).first
        self.assertEqual(checkbox.get_attribute("aria-checked"), "false")
        checkbox.click()
        self.page.wait_for_load_state("networkidle")
        checkbox = self.page.locator(
            "[data-comprehension-completion-form] button"
        ).first
        self.assertEqual(checkbox.get_attribute("aria-checked"), "true")
        self.assert_no_horizontal_overflow()

        self.page.get_by_role("link", name="Découvrir le test").click()
        self.page.get_by_role("heading", name=test.title).wait_for()
        self.assertEqual(
            self.page.locator(
                "[data-comprehension-completion-form] button"
            ).get_attribute("aria-checked"),
            "true",
        )
        self.assert_no_horizontal_overflow()
