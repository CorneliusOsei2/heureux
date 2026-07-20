from __future__ import annotations

import json
import tempfile
from dataclasses import replace
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from study import content
from study.management.commands.import_content import Command
from study.models import (
    Card,
    CardType,
    ComprehensionAnswer,
    ComprehensionAttempt,
    ComprehensionAttemptStatus,
    ComprehensionChoice,
    ComprehensionMode,
    ComprehensionQuestion,
    ComprehensionTest,
    Phrase,
    PhraseTier,
)

from . import factories


class ComprehensionContentTests(SimpleTestCase):
    def test_bundled_tests_are_complete_or_explicitly_unpublished(self):
        tests = content.load_comprehension_tests()

        self.assertEqual(
            [test.slug for test in tests],
            [
                "test-1",
                "test-2",
                "test-3",
                "test-4",
                "test-5",
                "test-6",
                "test-7",
                "test-9",
                "test-10",
                "oral-test-1",
                "oral-test-4",
                "oral-test-5",
                "oral-test-6",
                "oral-test-8",
                "oral-test-9",
                "oral-test-10",
            ],
        )
        self.assertEqual(
            [len(test.questions) for test in tests],
            [
                39,
                39,
                39,
                39,
                39,
                39,
                39,
                39,
                39,
                31,
                24,
                28,
                37,
                35,
                39,
                39,
            ],
        )
        self.assertEqual(
            [test.is_published for test in tests],
            [True] * 16,
        )
        self.assertEqual(
            {
                test.slug: [question.number for question in test.questions]
                for test in tests
                if test.mode == "orale"
            },
            {
                "oral-test-1": list(range(9, 40)),
                "oral-test-4": list(range(5, 29)),
                "oral-test-5": list(range(1, 29)),
                "oral-test-6": list(range(1, 38)),
                "oral-test-8": list(range(1, 36)),
                "oral-test-9": list(range(1, 40)),
                "oral-test-10": list(range(1, 40)),
            },
        )
        self.assertTrue(
            all(
                question.content_key.startswith("co:")
                for test in tests
                if test.mode == "orale"
                for question in test.questions
            )
        )
        self.assertTrue(
            all(not question.passage_en for question in tests[2].questions)
        )
        self.assertTrue(
            all(
                len(question.choices) == 4
                and sum(choice.is_correct for choice in question.choices) == 1
                for test in tests
                for question in test.questions
            )
        )

    def test_missing_passage_translations_require_an_explicit_manifest_opt_in(self):
        with self.assertRaisesRegex(
            ValueError,
            "Q1 has no passage translation",
        ):
            content._parse_comprehension_source(
                content.COMPREHENSION_DIR / "test_03.md",
                slug="test-3",
            )

    def test_complete_test_one_includes_its_final_question(self):
        test = content.load_comprehension_tests()[0]
        final_question = test.questions[-1]

        self.assertEqual(final_question.number, 39)
        self.assertEqual(
            next(
                choice.letter
                for choice in final_question.choices
                if choice.is_correct
            ),
            "D",
        )
        self.assertTrue(final_question.correct_explanation)

    def test_parser_rejects_duplicate_choice_letters(self):
        source = (content.COMPREHENSION_DIR / "test_02.md").read_text(
            encoding="utf-8"
        )
        malformed = source.replace(
            "| D | Des voyages | Trips |",
            "| C | Des voyages | Trips |",
            1,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate-choice.md"
            path.write_text(malformed, encoding="utf-8")

            with self.assertRaisesRegex(
                ValueError,
                "A, B, C and D exactly once",
            ):
                content._parse_comprehension_source(path, slug="test")

    def test_oral_audio_text_has_no_english_placeholders(self):
        english_markers = (
            "choose the reply",
            "choose the appropriate reply",
            "match the image",
            "response to the",
            "identify the appropriate statement",
            "describe the weather shown",
            "choose what follows logically",
            "image matching --",
        )

        for test in content.load_comprehension_tests():
            if test.mode != "orale":
                continue
            for question in test.questions:
                spoken_text = (
                    f"{question.passage_fr} {question.prompt_fr}".casefold()
                )
                with self.subTest(test=test.slug, question=question.number):
                    self.assertFalse(
                        any(marker in spoken_text for marker in english_markers)
                    )


class ComprehensionImportTests(TestCase):
    def test_import_publishes_the_complete_group_one_content(self):
        Command()._import_comprehension_tests(
            content.load_comprehension_tests()
        )

        self.assertEqual(
            list(
                ComprehensionTest.objects.order_by(
                    "mode",
                    "number",
                ).values_list(
                    "mode",
                    "number",
                    "is_published",
                )
            ),
            [
                ("ecrite", 1, True),
                ("ecrite", 2, True),
                ("ecrite", 3, True),
                ("ecrite", 4, True),
                ("ecrite", 5, True),
                ("ecrite", 6, True),
                ("ecrite", 7, True),
                ("ecrite", 9, True),
                ("ecrite", 10, True),
                ("orale", 1, True),
                ("orale", 4, True),
                ("orale", 5, True),
                ("orale", 6, True),
                ("orale", 8, True),
                ("orale", 9, True),
                ("orale", 10, True),
            ],
        )
        self.assertEqual(
            {
                (test.mode, test.number): test.questions.filter(
                    is_active=True
                ).count()
                for test in ComprehensionTest.objects.order_by(
                    "mode",
                    "number",
                )
            },
            {
                ("ecrite", 1): 39,
                ("ecrite", 2): 39,
                ("ecrite", 3): 39,
                ("ecrite", 4): 39,
                ("ecrite", 5): 39,
                ("ecrite", 6): 39,
                ("ecrite", 7): 39,
                ("ecrite", 9): 39,
                ("ecrite", 10): 39,
                ("orale", 1): 31,
                ("orale", 4): 24,
                ("orale", 5): 28,
                ("orale", 6): 37,
                ("orale", 8): 35,
                ("orale", 9): 39,
                ("orale", 10): 39,
            },
        )

    def test_import_is_idempotent_and_preserves_learner_answers(self):
        tests = content.load_comprehension_tests()
        command = Command()
        command._import_comprehension_tests(tests)
        test = ComprehensionTest.objects.get(slug="test-1")
        question = test.questions.get(number=1)
        question_pk = question.pk
        user = factories.make_user("ce-import")
        attempt = factories.make_comprehension_attempt(user=user, test=test)
        choice = question.choices.get(is_correct=True)
        answer = ComprehensionAnswer.objects.create(
            attempt=attempt,
            question=question,
            selected_choice=choice,
            is_correct=True,
        )

        first = tests[0]
        updated_question = replace(
            first.questions[0],
            passage_fr="Passage corrigé.",
        )
        command._import_comprehension_tests(
            [
                replace(
                    first,
                    questions=(updated_question, *first.questions[1:]),
                ),
                *tests[1:],
            ]
        )

        question.refresh_from_db()
        answer.refresh_from_db()
        self.assertEqual(question.pk, question_pk)
        self.assertEqual(question.passage_fr, "Passage corrigé.")
        self.assertEqual(answer.question_id, question_pk)
        self.assertEqual(answer.selected_choice_id, choice.pk)

    def test_missing_shared_test_is_archived_not_deleted(self):
        test = factories.make_comprehension_test()
        user = factories.make_user("ce-archive")
        attempt = factories.make_comprehension_attempt(user=user, test=test)

        Command()._import_comprehension_tests([])

        test.refresh_from_db()
        self.assertFalse(test.is_active)
        self.assertFalse(test.is_published)
        self.assertTrue(
            ComprehensionAttempt.objects.filter(pk=attempt.pk).exists()
        )

    def test_vocabulary_import_links_sources_and_preserves_card_progress(self):
        tests = content.load_comprehension_tests()
        vocabulary = content.parse_comprehension_vocabulary(tests)
        command = Command()
        command._import_phrases(
            [item.phrase for item in vocabulary],
            {},
        )
        command._import_comprehension_tests(tests)
        command._link_comprehension_vocabulary(vocabulary)

        self.assertEqual(
            Phrase.objects.filter(
                tier=PhraseTier.COMPREHENSION,
                is_active=True,
            ).count(),
            450,
        )
        first_item = vocabulary[0]
        first_phrase = Phrase.objects.get(
            phrase_id=first_item.phrase.phrase_id
        )
        self.assertEqual(
            set(
                first_phrase.source_questions.values_list(
                    "test__slug",
                    "number",
                )
            ),
            {
                (first_item.test_slug, number)
                for number in first_item.question_numbers
            },
        )
        self.assertFalse(
            Phrase.objects.filter(
                tier=PhraseTier.COMPREHENSION,
                source_questions__isnull=True,
            ).exists()
        )

        user = factories.make_user("ce-vocabulary-import")
        command._sync_cards({}, user=user)
        cards = Card.objects.filter(
            user=user,
            phrase__tier=PhraseTier.COMPREHENSION,
        )
        self.assertEqual(cards.count(), 450)
        self.assertEqual(
            set(cards.values_list("card_type", flat=True)),
            {CardType.PHRASE_PRODUCTION},
        )
        card = cards.get(phrase=first_phrase)
        card.reps = 7
        card.interval_days = 18
        card.save(update_fields=["reps", "interval_days"])

        command._import_phrases(
            [item.phrase for item in vocabulary],
            {},
        )
        command._link_comprehension_vocabulary(vocabulary)
        command._sync_cards({}, user=user)

        card.refresh_from_db()
        self.assertEqual(card.reps, 7)
        self.assertEqual(card.interval_days, 18)


class ComprehensionModelTests(TestCase):
    def test_test_numbers_are_unique_within_each_mode(self):
        factories.make_comprehension_test(
            number=1,
            mode=ComprehensionMode.ECRITE,
        )
        oral = factories.make_comprehension_test(
            number=1,
            mode=ComprehensionMode.ORALE,
        )

        self.assertEqual(oral.number, 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ComprehensionTest.objects.create(
                slug="another-oral-test",
                mode=ComprehensionMode.ORALE,
                number=1,
                title="Another oral test",
            )

    def test_only_one_active_attempt_is_allowed_per_user_and_test(self):
        user = factories.make_user("ce-unique")
        test = factories.make_comprehension_test()
        factories.make_comprehension_attempt(user=user, test=test)

        with self.assertRaises(IntegrityError), transaction.atomic():
            factories.make_comprehension_attempt(user=user, test=test)

    def test_answer_rejects_a_choice_from_another_question(self):
        user = factories.make_user("ce-validation")
        test = factories.make_comprehension_test()
        attempt = factories.make_comprehension_attempt(user=user, test=test)
        questions = list(test.questions.all())
        answer = ComprehensionAnswer(
            attempt=attempt,
            question=questions[0],
            selected_choice=questions[1].choices.first(),
            is_correct=False,
        )

        with self.assertRaises(ValidationError):
            answer.full_clean()


class ComprehensionFlowTests(TestCase):
    def setUp(self):
        self.user = factories.make_user("ce-learner")
        self.other_user = factories.make_user("ce-other")
        self.client.force_login(self.user)
        self.test = factories.make_comprehension_test(question_count=3)
        self.draft = factories.make_comprehension_test(
            number=2,
            question_count=2,
            is_published=False,
        )

    def start(self):
        response = self.client.post(
            reverse("study:comprehension_start", args=[self.test.slug]),
            {"action": "continue"},
        )
        self.assertEqual(response.status_code, 302)
        return ComprehensionAttempt.objects.get(
            user=self.user,
            test=self.test,
            status=ComprehensionAttemptStatus.IN_PROGRESS,
        )

    def submit(self, attempt, question_number, letter):
        question = self.test.questions.get(number=question_number)
        choice = question.choices.get(letter=letter)
        return self.client.post(
            reverse(
                "study:comprehension_question",
                args=[self.test.slug, attempt.pk, question_number],
            ),
            {"choice": choice.pk},
        )

    def test_written_library_lots_tests_in_fixed_sets_of_five(self):
        attempt = factories.make_comprehension_attempt(
            user=self.user,
            test=self.test,
            answered_questions=1,
        )

        overview = self.client.get(reverse("study:comprehension_overview"))
        group = self.client.get(
            reverse("study:comprehension_group", args=[1])
        )

        self.assertContains(overview, "8 lots de 5 tests")
        self.assertContains(overview, "Lot 1")
        self.assertNotContains(overview, "Groupe 1")
        self.assertContains(
            overview,
            'class="deck card ce-group-card"',
            count=8,
        )
        self.assertContains(
            overview,
            reverse("study:comprehension_group", args=[1]),
        )
        self.assertEqual(len(group.context["group"]["slots"]), 5)
        self.assertEqual(group.context["group_label"], "Lot")
        self.assertContains(group, "Lot 01")
        self.assertContains(group, "1/3")
        self.assertContains(group, "Bientôt")
        self.assertContains(
            group,
            reverse("study:comprehension_test", args=[self.test.slug]),
        )
        self.assertNotContains(
            group,
            reverse("study:comprehension_test", args=[self.draft.slug]),
        )
        self.assertEqual(attempt.answers.count(), 1)

    def test_test_progress_bubbles_to_group_and_skill_path(self):
        untouched_overview = self.client.get(
            reverse("study:comprehension_overview"),
        )
        untouched_hub = self.client.get(reverse("study:comprehension_hub"))
        self.assertEqual(
            untouched_overview.context["groups"][0]["progress"].status,
            "new",
        )
        self.assertEqual(
            untouched_hub.context["comprehension"]["ecrite"][
                "progress"
            ].status,
            "new",
        )

        attempt = self.start()
        active_overview = self.client.get(
            reverse("study:comprehension_overview"),
        )
        active_hub = self.client.get(reverse("study:comprehension_hub"))
        self.assertEqual(
            active_overview.context["groups"][0]["progress"].status,
            "active",
        )
        self.assertEqual(
            active_hub.context["comprehension"]["ecrite"]["progress"].status,
            "active",
        )

        for question_number in range(1, 4):
            self.submit(attempt, question_number, "A")
        completed_overview = self.client.get(
            reverse("study:comprehension_overview"),
        )
        completed_hub = self.client.get(reverse("study:comprehension_hub"))
        self.assertEqual(
            completed_overview.context["groups"][0]["progress"].status,
            "done",
        )
        self.assertEqual(
            completed_hub.context["comprehension"]["ecrite"][
                "progress"
            ].status,
            "done",
        )

    def test_group_outside_the_eight_group_curriculum_is_not_found(self):
        self.assertEqual(
            self.client.get(
                reverse("study:comprehension_group", args=[9])
            ).status_code,
            404,
        )

    def test_oral_library_groups_tests_in_fixed_sets_of_five(self):
        oral = factories.make_comprehension_test(
            number=1,
            question_count=3,
            mode=ComprehensionMode.ORALE,
        )
        oral_draft = factories.make_comprehension_test(
            number=2,
            question_count=2,
            mode=ComprehensionMode.ORALE,
            is_published=False,
        )
        factories.make_comprehension_attempt(
            user=self.user,
            test=oral,
            answered_questions=1,
        )

        overview = self.client.get(
            reverse("study:comprehension_oral_overview")
        )
        group = self.client.get(
            reverse("study:comprehension_oral_group", args=[1])
        )

        self.assertContains(overview, "2 groupes de 5 tests")
        self.assertContains(
            overview,
            'class="deck card ce-group-card"',
            count=2,
        )
        self.assertContains(
            overview,
            reverse("study:comprehension_oral_group", args=[1]),
        )
        self.assertContains(overview, "Groupe 1")
        self.assertEqual(len(group.context["group"]["slots"]), 5)
        self.assertEqual(group.context["group_label"], "Groupe")
        self.assertContains(group, "Groupe 01")
        self.assertContains(
            group,
            reverse("study:comprehension_oral_test", args=[oral.slug]),
        )
        self.assertNotContains(
            group,
            reverse("study:comprehension_oral_test", args=[oral_draft.slug]),
        )

    def test_oral_group_outside_the_curriculum_is_not_found(self):
        self.assertEqual(
            self.client.get(
                reverse("study:comprehension_oral_group", args=[3])
            ).status_code,
            404,
        )

    def test_unpublished_test_cannot_be_opened_or_started(self):
        detail = self.client.get(
            reverse("study:comprehension_test", args=[self.draft.slug])
        )
        start = self.client.post(
            reverse("study:comprehension_start", args=[self.draft.slug]),
            {"action": "continue"},
        )

        self.assertEqual(detail.status_code, 404)
        self.assertEqual(start.status_code, 404)
        self.assertFalse(
            ComprehensionAttempt.objects.filter(test=self.draft).exists()
        )

    def test_test_page_lists_every_question_before_practice(self):
        response = self.client.get(
            reverse("study:comprehension_test", args=[self.test.slug])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Questions · 3")
        self.assertContains(response, "Pratiquer ce test")
        self.assertContains(
            response,
            reverse("study:comprehension_group", args=[1]),
        )
        self.assertContains(response, "Lot 1")
        self.assertNotContains(response, "Groupe 1")
        for question in self.test.questions.all():
            with self.subTest(question=question.number):
                self.assertContains(response, question.prompt_fr)
                self.assertContains(
                    response,
                    reverse(
                        "study:comprehension_question_study",
                        args=[self.test.slug, question.number],
                    ),
                )

    def test_study_question_reveals_learning_content_and_practice_action(self):
        question = self.test.questions.get(number=1)
        response = self.client.get(
            reverse(
                "study:comprehension_question_study",
                args=[self.test.slug, question.number],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, question.passage_fr)
        self.assertContains(response, question.passage_en)
        self.assertContains(response, question.prompt_en)
        self.assertContains(response, question.correct_explanation)
        self.assertContains(response, "Rationale for B on question 1.")
        self.assertContains(response, "Bonne réponse")
        self.assertContains(response, "Pratiquer ce test")
        self.assertContains(response, "Toutes les questions")
        self.assertNotContains(response, "data-co-audio-reader")

    def test_unpublished_question_cannot_be_studied(self):
        response = self.client.get(
            reverse(
                "study:comprehension_question_study",
                args=[self.draft.slug, 1],
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_quiz_choices_are_immediate_submit_buttons(self):
        attempt = self.start()
        response = self.client.get(
            reverse(
                "study:comprehension_question",
                args=[self.test.slug, attempt.pk, 1],
            )
        )

        self.assertContains(
            response,
            'class="ce-choice" type="submit" name="choice"',
            count=4,
        )
        self.assertContains(response, "validée immédiatement")
        self.assertNotContains(response, "Valider ma réponse")

    def test_start_is_resumable_and_does_not_duplicate_active_attempt(self):
        attempt = self.start()

        second = self.client.post(
            reverse("study:comprehension_start", args=[self.test.slug]),
            {"action": "continue"},
        )

        self.assertRedirects(
            second,
            reverse(
                "study:comprehension_question",
                args=[self.test.slug, attempt.pk, 1],
            ),
            fetch_redirect_response=False,
        )
        self.assertEqual(
            ComprehensionAttempt.objects.filter(
                user=self.user,
                test=self.test,
                status=ComprehensionAttemptStatus.IN_PROGRESS,
            ).count(),
            1,
        )

    def test_translation_is_hidden_until_answer_and_submission_is_immutable(self):
        attempt = self.start()
        question = self.test.questions.get(number=1)
        url = reverse(
            "study:comprehension_question",
            args=[self.test.slug, attempt.pk, 1],
        )

        before = self.client.get(url)
        self.assertNotContains(before, question.passage_en)
        self.assertNotContains(before, question.choices.get(letter="B").text_en)

        response = self.submit(attempt, 1, "B")
        self.assertRedirects(response, url, fetch_redirect_response=False)
        after = self.client.get(url)
        self.assertContains(after, question.passage_en)
        self.assertContains(after, "Pourquoi votre choix B ne convient pas")
        self.assertContains(
            after,
            '<details class="ce-rationales ce-rationales--explanation" open>',
        )
        correct_choice = question.choices.get(is_correct=True)
        self.assertContains(
            after,
            (
                '<h2 id="ce-correction-title">'
                f"{correct_choice.letter} · {correct_choice.text_fr}"
                "</h2>"
            ),
            html=True,
        )
        self.assertNotContains(after, "ce-correction__answer")
        self.assertNotContains(
            after,
            "Bien joué, vous avez repéré l’information essentielle.",
        )
        self.assertContains(after, "Question suivante")

        self.submit(attempt, 1, "A")
        answer = attempt.answers.get(question=question)
        self.assertEqual(answer.selected_choice.letter, "B")
        self.assertFalse(answer.is_correct)

    def test_invalid_or_foreign_choice_is_rejected(self):
        attempt = self.start()
        url = reverse(
            "study:comprehension_question",
            args=[self.test.slug, attempt.pk, 1],
        )
        foreign_choice = self.test.questions.get(number=2).choices.first()

        missing = self.client.post(url, {})
        foreign = self.client.post(url, {"choice": foreign_choice.pk})

        self.assertEqual(missing.status_code, 400)
        self.assertEqual(foreign.status_code, 400)
        self.assertContains(
            missing,
            "Choisissez une réponse",
            status_code=400,
        )
        self.assertEqual(attempt.answers.count(), 0)

    def test_legacy_attempt_does_not_restore_a_deactivated_choice(self):
        attempt = factories.make_comprehension_attempt(
            user=self.user,
            test=self.test,
        )
        question = self.test.questions.get(number=1)
        removed_choice = question.choices.get(letter="D")
        removed_choice.is_active = False
        removed_choice.save(update_fields=["is_active"])
        url = reverse(
            "study:comprehension_question",
            args=[self.test.slug, attempt.pk, 1],
        )

        response = self.client.get(url)
        rejected = self.client.post(
            url,
            {"choice": removed_choice.pk},
        )

        self.assertEqual(len(response.context["choices"]), 3)
        self.assertNotContains(response, removed_choice.text_fr)
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(attempt.answers.count(), 0)

    def test_free_navigation_allows_jumping_and_resume_advances(self):
        attempt = self.start()
        future_url = reverse(
            "study:comprehension_question",
            args=[self.test.slug, attempt.pk, 3],
        )

        future = self.client.get(future_url)
        self.assertEqual(future.status_code, 200)
        self.assertNotContains(future, "is-locked")
        self.assertEqual(len(future.context["navigator"]), 3)
        self.assertTrue(
            all(
                not item["is_answered"]
                for item in future.context["navigator"]
            )
        )

        middle = self.client.get(
            reverse(
                "study:comprehension_question",
                args=[self.test.slug, attempt.pk, 2],
            )
        )
        self.assertContains(middle, "Question suivante")
        self.assertContains(middle, "Question précédente")
        self.assertEqual(attempt.answers.count(), 0)

        self.submit(attempt, 1, "A")
        resume = self.client.post(
            reverse("study:comprehension_start", args=[self.test.slug]),
            {"action": "continue"},
        )
        self.assertRedirects(
            resume,
            reverse(
                "study:comprehension_question",
                args=[self.test.slug, attempt.pk, 2],
            ),
            fetch_redirect_response=False,
        )

    def test_completion_scores_results_and_allows_a_retake(self):
        attempt = self.start()
        self.submit(attempt, 1, "A")
        self.submit(attempt, 2, "B")
        final = self.submit(attempt, 3, "A")
        correction_url = (
            reverse(
                "study:comprehension_question",
                args=[self.test.slug, attempt.pk, 3],
            )
            + "?correction=1"
        )
        result_url = reverse(
            "study:comprehension_results",
            args=[self.test.slug, attempt.pk],
        )

        self.assertRedirects(
            final,
            correction_url,
            fetch_redirect_response=False,
        )
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, ComprehensionAttemptStatus.COMPLETED)
        self.assertEqual(attempt.score, 2)
        self.assertEqual(attempt.total_questions, 3)
        correction = self.client.get(correction_url)
        self.assertContains(correction, "Voir mes résultats")
        results = self.client.get(result_url)
        self.assertContains(results, "2")
        self.assertContains(results, "sur 3")
        self.assertContains(results, "67")
        self.assertContains(results, "Correction détaillée")

        retry = self.client.post(
            reverse("study:comprehension_start", args=[self.test.slug]),
            {"action": "restart"},
        )
        self.assertEqual(retry.status_code, 302)
        self.assertEqual(
            ComprehensionAttempt.objects.filter(
                user=self.user,
                test=self.test,
            ).count(),
            2,
        )

    def test_completed_attempt_can_retrain_only_its_wrong_answers(self):
        source = self.start()
        self.submit(source, 1, "B")
        self.submit(source, 2, "A")
        self.submit(source, 3, "B")
        source.refresh_from_db()

        response = self.client.post(
            reverse("study:comprehension_start", args=[self.test.slug]),
            {
                "action": "errors",
                "attempt_id": source.pk,
            },
        )

        focused = ComprehensionAttempt.objects.exclude(pk=source.pk).get(
            user=self.user,
            test=self.test,
        )
        self.assertRedirects(
            response,
            reverse(
                "study:comprehension_question",
                args=[self.test.slug, focused.pk, 1],
            ),
            fetch_redirect_response=False,
        )
        self.assertEqual(focused.total_questions, 2)
        self.assertEqual(
            [
                question["number"]
                for question in focused.content_snapshot["questions"]
            ],
            [1, 3],
        )
        self.assertEqual(
            focused.content_snapshot["source_attempt_id"],
            source.pk,
        )

        self.submit(focused, 1, "A")
        self.submit(focused, 3, "A")
        focused.refresh_from_db()
        self.assertEqual(focused.score, 2)
        self.assertEqual(focused.total_questions, 2)
        results = self.client.get(
            reverse(
                "study:comprehension_results",
                args=[self.test.slug, focused.pk],
            )
        )
        self.assertTrue(results.context["is_error_practice"])
        self.assertContains(results, "Entraînement ciblé terminé")

    def test_completed_results_keep_the_original_question_and_answer_key(self):
        attempt = self.start()
        self.submit(attempt, 1, "A")
        self.submit(attempt, 2, "A")
        self.submit(attempt, 3, "A")
        question = self.test.questions.get(number=1)
        original_passage = question.passage_fr
        question.passage_fr = "Contenu remplacé après la tentative."
        question.save(update_fields=["passage_fr"])
        question.choices.update(is_correct=False)
        question.choices.filter(letter="B").update(is_correct=True)

        response = self.client.get(
            reverse(
                "study:comprehension_results",
                args=[self.test.slug, attempt.pk],
            )
        )
        first_item = response.context["review_items"][0]

        self.assertEqual(first_item["question"]["passage_fr"], original_passage)
        self.assertEqual(first_item["correct_choice"]["letter"], "A")
        self.assertTrue(first_item["answer"].is_correct)
        self.assertNotContains(response, "Contenu remplacé")

    def test_attempt_keeps_a_question_archived_after_it_started(self):
        attempt = self.start()
        self.submit(attempt, 1, "A")
        self.test.questions.filter(number=2).update(is_active=False)
        stale_url = reverse(
            "study:comprehension_question",
            args=[self.test.slug, attempt.pk, 2],
        )

        response = self.client.get(stale_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["question"]["number"], 2)
        self.assertEqual(response.context["total_questions"], 3)
        attempt.refresh_from_db()
        self.assertEqual(attempt.current_question, 2)

    def test_archived_answer_remains_in_the_attempt_progress_total(self):
        attempt = self.start()
        self.submit(attempt, 1, "A")
        self.test.questions.filter(number=1).update(is_active=False)
        self.submit(attempt, 2, "A")

        response = self.client.get(
            reverse(
                "study:comprehension_question",
                args=[self.test.slug, attempt.pk, 2],
            )
        )

        self.assertEqual(response.context["answered_count"], 2)
        self.assertEqual(response.context["total_questions"], 3)
        self.assertContains(response, "2 réponses enregistrées")

    def test_attempt_does_not_shrink_when_remaining_questions_are_archived(self):
        attempt = self.start()
        self.submit(attempt, 1, "A")
        self.test.questions.filter(number__gte=2).update(is_active=False)

        response = self.client.get(
            reverse(
                "study:comprehension_question",
                args=[self.test.slug, attempt.pk, 2],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["question"]["number"], 2)
        self.assertEqual(response.context["total_questions"], 3)
        attempt.refresh_from_db()
        self.assertEqual(
            attempt.status,
            ComprehensionAttemptStatus.IN_PROGRESS,
        )
        self.assertEqual(attempt.total_questions, 3)

    def test_submission_uses_the_answer_key_pinned_when_attempt_started(self):
        attempt = self.start()
        question = self.test.questions.get(number=1)
        original_passage = question.passage_fr
        self.client.get(
            reverse(
                "study:comprehension_question",
                args=[self.test.slug, attempt.pk, 1],
            )
        )
        question.passage_fr = "Passage changed by a later import."
        question.save(update_fields=["passage_fr"])
        question.choices.filter(letter="A").update(is_correct=False)
        question.choices.filter(letter="B").update(is_correct=True)

        response = self.submit(attempt, 1, "A")
        answer = attempt.answers.get(question=question)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(answer.is_correct)
        self.assertEqual(
            answer.question_snapshot["passage_fr"],
            original_passage,
        )
        self.assertEqual(
            next(
                choice["letter"]
                for choice in answer.question_snapshot["choices"]
                if choice["is_correct"]
            ),
            "A",
        )

    def test_newly_imported_question_does_not_enter_an_existing_attempt(self):
        attempt = self.start()
        question = ComprehensionQuestion.objects.create(
            test=self.test,
            content_key="ce:test-1:q04",
            number=4,
            passage_fr="Nouveau passage.",
            passage_en="New passage.",
            prompt_fr="Nouvelle question ?",
            prompt_en="New question?",
        )
        for letter in "ABCD":
            ComprehensionChoice.objects.create(
                question=question,
                letter=letter,
                text_fr=f"Nouveau choix {letter}",
                text_en=f"New choice {letter}",
                is_correct=(letter == "A"),
            )
        self.submit(attempt, 1, "A")
        self.submit(attempt, 2, "A")
        final = self.submit(attempt, 3, "A")

        self.assertIn("?correction=1", final.url)
        attempt.refresh_from_db()
        self.assertEqual(attempt.total_questions, 3)
        correction = self.client.get(final.url)
        self.assertEqual(correction.context["total_questions"], 3)
        self.assertEqual(len(correction.context["navigator"]), 3)

    def test_archived_test_keeps_owned_history_without_dead_retake_action(self):
        attempt = self.start()
        self.submit(attempt, 1, "A")
        self.submit(attempt, 2, "A")
        self.submit(attempt, 3, "A")
        self.test.is_active = False
        self.test.is_published = False
        self.test.save(update_fields=["is_active", "is_published"])

        overview = self.client.get(reverse("study:comprehension_overview"))
        group = self.client.get(
            reverse("study:comprehension_group", args=[1])
        )
        detail = self.client.get(
            reverse("study:comprehension_test", args=[self.test.slug])
        )
        results = self.client.get(
            reverse(
                "study:comprehension_results",
                args=[self.test.slug, attempt.pk],
            )
        )

        self.assertContains(overview, "Historique disponible")
        self.assertContains(group, "Archivé")
        self.assertContains(group, "Voir le test archivé")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Vos résultats précédents")
        self.assertEqual(results.status_code, 200)
        self.assertNotContains(results, "Refaire ce test")
        self.assertContains(results, "ne peut pas être recommencé")

    def test_unpublished_test_keeps_owned_history_visible(self):
        attempt = self.start()
        self.submit(attempt, 1, "A")
        self.submit(attempt, 2, "A")
        self.submit(attempt, 3, "A")
        self.test.is_published = False
        self.test.save(update_fields=["is_published"])

        overview = self.client.get(reverse("study:comprehension_overview"))
        group = self.client.get(
            reverse("study:comprehension_group", args=[1])
        )

        self.assertContains(overview, "Historique disponible")
        self.assertContains(group, "Indisponible")
        self.assertContains(group, "Voir l’historique")
        self.assertContains(
            group,
            reverse("study:comprehension_test", args=[self.test.slug]),
        )
        self.assertTrue(
            ComprehensionAttempt.objects.filter(pk=attempt.pk).exists()
        )

    def test_attempts_are_private(self):
        attempt = self.start()
        question_url = reverse(
            "study:comprehension_question",
            args=[self.test.slug, attempt.pk, 1],
        )
        result_url = reverse(
            "study:comprehension_results",
            args=[self.test.slug, attempt.pk],
        )
        self.client.force_login(self.other_user)

        self.assertEqual(self.client.get(question_url).status_code, 404)
        self.assertEqual(self.client.get(result_url).status_code, 404)

    def test_account_export_contains_owned_comprehension_progress(self):
        attempt = self.start()
        self.submit(attempt, 1, "A")

        response = self.client.get(reverse("study:export_account"))
        payload = json.loads(response.content)

        self.assertEqual(payload["version"], 3)
        self.assertEqual(len(payload["comprehension_attempts"]), 1)
        exported = payload["comprehension_attempts"][0]
        self.assertEqual(exported["test"], self.test.slug)
        self.assertEqual(exported["answers"][0]["selected_choice"], "A")
        self.assertEqual(exported["content_snapshot"], {})
        self.assertNotIn("Correct explanation 2.", response.content.decode())

    def test_progress_reset_removes_only_the_current_users_attempts(self):
        self.start()
        other_attempt = factories.make_comprehension_attempt(
            user=self.other_user,
            test=self.test,
        )

        response = self.client.post(
            reverse("study:reset_progress"),
            {
                "current_pin": "123456",
                "confirmation": "REINITIALISER",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            ComprehensionAttempt.objects.filter(user=self.user).exists()
        )
        self.assertTrue(
            ComprehensionAttempt.objects.filter(pk=other_attempt.pk).exists()
        )


class OralComprehensionFlowTests(TestCase):
    def setUp(self):
        self.user = factories.make_user("co-learner")
        self.client.force_login(self.user)
        self.test = factories.make_comprehension_test(
            number=1,
            question_count=31,
            first_question_number=9,
            mode=ComprehensionMode.ORALE,
        )

    def test_oral_library_and_test_keep_original_question_numbers(self):
        overview = self.client.get(
            reverse("study:comprehension_oral_overview")
        )
        group = self.client.get(
            reverse("study:comprehension_oral_group", args=[1])
        )
        detail = self.client.get(
            reverse(
                "study:comprehension_oral_test",
                args=[self.test.slug],
            )
        )

        self.assertEqual(overview.status_code, 200)
        self.assertContains(
            overview,
            reverse("study:comprehension_oral_group", args=[1]),
        )
        self.assertContains(group, "31 questions")
        self.assertContains(
            group,
            reverse(
                "study:comprehension_oral_test",
                args=[self.test.slug],
            ),
        )
        self.assertContains(detail, "Compréhension orale")
        self.assertContains(detail, ">9</span>")
        self.assertContains(detail, ">39</span>")
        self.assertContains(detail, "Groupe 1")
        self.assertContains(
            detail,
            reverse("study:comprehension_oral_group", args=[1]),
        )
        self.assertEqual(
            self.client.get(
                reverse(
                    "study:comprehension_test",
                    args=[self.test.slug],
                )
            ).status_code,
            404,
        )

    def test_oral_attempt_runs_from_q9_through_q39(self):
        start = self.client.post(
            reverse(
                "study:comprehension_oral_start",
                args=[self.test.slug],
            ),
            {"action": "continue"},
        )
        attempt = ComprehensionAttempt.objects.get(
            user=self.user,
            test=self.test,
        )
        q9_url = reverse(
            "study:comprehension_oral_question",
            args=[self.test.slug, attempt.pk, 9],
        )

        self.assertRedirects(
            start,
            q9_url,
            fetch_redirect_response=False,
        )
        q9 = self.client.get(q9_url)
        self.assertEqual(q9.context["position"], 1)
        self.assertEqual(q9.context["question"]["number"], 9)
        self.assertContains(q9, "Dialogue")
        self.assertContains(q9, 'aria-label="Lecteur audio français"')
        self.assertContains(q9, 'data-co-audio-reader')
        self.assertContains(q9, 'data-co-audio-target="dialogue"')
        self.assertContains(q9, 'data-co-audio-target="question"')
        self.assertContains(q9, 'data-co-audio-rate')

        study = self.client.get(
            reverse(
                "study:comprehension_oral_question_study",
                args=[self.test.slug, 9],
            )
        )
        self.assertContains(study, 'data-co-audio-reader')
        self.assertContains(study, self.test.questions.get(number=9).passage_fr)

        for number in range(9, 40):
            question = self.test.questions.get(number=number)
            choice = question.choices.get(letter="A")
            response = self.client.post(
                reverse(
                    "study:comprehension_oral_question",
                    args=[self.test.slug, attempt.pk, number],
                ),
                {"choice": choice.pk},
            )
            if number == 9:
                attempt.refresh_from_db()
                self.assertEqual(attempt.current_question, 10)

        attempt.refresh_from_db()
        self.assertEqual(attempt.status, ComprehensionAttemptStatus.COMPLETED)
        self.assertEqual(attempt.current_question, 39)
        self.assertEqual(attempt.total_questions, 31)
        self.assertRedirects(
            response,
            reverse(
                "study:comprehension_oral_question",
                args=[self.test.slug, attempt.pk, 39],
            )
            + "?correction=1",
            fetch_redirect_response=False,
        )
        results = self.client.get(
            reverse(
                "study:comprehension_oral_results",
                args=[self.test.slug, attempt.pk],
            )
        )
        self.assertEqual(results.status_code, 200)
        self.assertContains(results, "sur 31")
        self.assertContains(results, 'data-co-audio-reader', count=31)
        self.assertNotContains(results, "Pratiquer le vocabulaire")

    def test_archived_oral_history_remains_reachable_from_the_hub(self):
        factories.make_comprehension_attempt(
            user=self.user,
            test=self.test,
            status=ComprehensionAttemptStatus.COMPLETED,
            answered_questions=31,
        )
        self.test.is_active = False
        self.test.is_published = False
        self.test.save(update_fields=["is_active", "is_published"])

        hub = self.client.get(reverse("study:comprehension_hub"))
        overview = self.client.get(
            reverse("study:comprehension_oral_overview")
        )
        group = self.client.get(
            reverse("study:comprehension_oral_group", args=[1])
        )

        self.assertContains(
            hub,
            reverse("study:comprehension_oral_overview"),
        )
        self.assertContains(hub, "1 terminé")
        self.assertEqual(overview.status_code, 200)
        self.assertContains(
            overview,
            reverse("study:comprehension_oral_group", args=[1]),
        )
        self.assertContains(group, "Archivé")
        self.assertContains(
            group,
            reverse(
                "study:comprehension_oral_test",
                args=[self.test.slug],
            ),
        )
