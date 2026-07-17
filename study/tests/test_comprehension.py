from __future__ import annotations

import json
from dataclasses import replace

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from study import content
from study.management.commands.import_content import Command
from study.models import (
    ComprehensionAnswer,
    ComprehensionAttempt,
    ComprehensionAttemptStatus,
    ComprehensionChoice,
    ComprehensionQuestion,
    ComprehensionTest,
)

from . import factories


class ComprehensionContentTests(SimpleTestCase):
    def test_bundled_tests_are_complete_or_explicitly_unpublished(self):
        tests = content.load_comprehension_tests()

        self.assertEqual([test.slug for test in tests], ["test-1", "test-2"])
        self.assertEqual(len(tests[0].questions), 36)
        self.assertTrue(tests[0].is_published)
        self.assertEqual(len(tests[1].questions), 21)
        self.assertFalse(tests[1].is_published)
        self.assertTrue(
            all(
                len(question.choices) == 4
                and sum(choice.is_correct for choice in question.choices) == 1
                for test in tests
                for question in test.questions
            )
        )

    def test_parser_uses_bold_answer_when_final_rationale_is_missing(self):
        test = content.load_comprehension_tests()[0]
        final_question = test.questions[-1]

        self.assertEqual(final_question.number, 36)
        self.assertEqual(
            next(
                choice.letter
                for choice in final_question.choices
                if choice.is_correct
            ),
            "A",
        )
        self.assertEqual(final_question.correct_explanation, "")


class ComprehensionImportTests(TestCase):
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
                tests[1],
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


class ComprehensionModelTests(TestCase):
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

    def test_library_shows_progress_and_keeps_draft_unavailable(self):
        attempt = factories.make_comprehension_attempt(
            user=self.user,
            test=self.test,
            answered_questions=1,
        )

        response = self.client.get(reverse("study:comprehension_overview"))

        self.assertContains(response, "Compréhension écrite")
        self.assertContains(response, "1/3")
        self.assertContains(response, "Bientôt")
        self.assertContains(
            response,
            reverse("study:comprehension_test", args=[self.test.slug]),
        )
        self.assertNotContains(
            response,
            reverse("study:comprehension_test", args=[self.draft.slug]),
        )
        self.assertEqual(attempt.answers.count(), 1)

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

    def test_future_questions_stay_locked_and_resume_advances(self):
        attempt = self.start()
        future_url = reverse(
            "study:comprehension_question",
            args=[self.test.slug, attempt.pk, 3],
        )
        first = self.client.get(future_url)
        self.assertRedirects(
            first,
            reverse(
                "study:comprehension_question",
                args=[self.test.slug, attempt.pk, 1],
            ),
            fetch_redirect_response=False,
        )

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

    def test_resume_skips_a_question_archived_during_an_attempt(self):
        attempt = self.start()
        self.submit(attempt, 1, "A")
        self.test.questions.filter(number=2).update(is_active=False)
        stale_url = reverse(
            "study:comprehension_question",
            args=[self.test.slug, attempt.pk, 2],
        )

        response = self.client.get(stale_url)

        self.assertRedirects(
            response,
            reverse(
                "study:comprehension_question",
                args=[self.test.slug, attempt.pk, 3],
            ),
            fetch_redirect_response=False,
        )
        attempt.refresh_from_db()
        self.assertEqual(attempt.current_question, 3)

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

    def test_resume_completes_when_no_active_questions_remain(self):
        attempt = self.start()
        self.submit(attempt, 1, "A")
        self.test.questions.filter(number__gte=2).update(is_active=False)

        response = self.client.get(
            reverse(
                "study:comprehension_question",
                args=[self.test.slug, attempt.pk, 2],
            )
        )

        self.assertRedirects(
            response,
            reverse(
                "study:comprehension_results",
                args=[self.test.slug, attempt.pk],
            ),
            fetch_redirect_response=False,
        )
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, ComprehensionAttemptStatus.COMPLETED)
        self.assertEqual(attempt.score, 1)
        self.assertEqual(attempt.total_questions, 1)

    def test_archived_test_keeps_owned_history_without_dead_retake_action(self):
        attempt = self.start()
        self.submit(attempt, 1, "A")
        self.submit(attempt, 2, "A")
        self.submit(attempt, 3, "A")
        self.test.is_active = False
        self.test.is_published = False
        self.test.save(update_fields=["is_active", "is_published"])

        overview = self.client.get(reverse("study:comprehension_overview"))
        detail = self.client.get(
            reverse("study:comprehension_test", args=[self.test.slug])
        )
        results = self.client.get(
            reverse(
                "study:comprehension_results",
                args=[self.test.slug, attempt.pk],
            )
        )

        self.assertContains(overview, "Archivé")
        self.assertContains(overview, "Voir le test archivé")
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

        self.assertContains(overview, "Indisponible")
        self.assertContains(overview, "Voir l’historique")
        self.assertContains(
            overview,
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

        self.assertEqual(payload["version"], 2)
        self.assertEqual(len(payload["comprehension_attempts"]), 1)
        exported = payload["comprehension_attempts"][0]
        self.assertEqual(exported["test"], self.test.slug)
        self.assertEqual(exported["answers"][0]["selected_choice"], "A")

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
