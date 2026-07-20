from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from study.content import (
    load_question_bank,
    load_question_banks,
    load_sections,
)
from study.management.commands.import_content import Command
from study.models import MemoryQuestionProgress, Task

from . import factories


class QuestionBankContentTests(TestCase):
    def test_master_bank_is_complete_and_consolidated(self):
        bank = load_question_bank()

        self.assertEqual(bank.number, 1)
        self.assertEqual(bank.title, "Mémoire 1")
        self.assertEqual(bank.label, "Questions réutilisables")
        self.assertEqual(bank.icon, "book-open")
        self.assertEqual(bank.category_count, 21)
        self.assertEqual(bank.question_count, 65)
        self.assertEqual(
            [section.number for section in bank.sections],
            list(range(1, 22)),
        )
        self.assertEqual(bank.sections[0].question_count, 7)
        self.assertEqual(bank.sections[3].question_count, 6)
        self.assertEqual(bank.sections[-1].title, "Rythme / journée type")

        questions = [
            question.text
            for section in bank.sections
            for group in section.groups
            for question in group.questions
        ]
        self.assertEqual(len(questions), len(set(questions)))
        self.assertEqual(len(bank.question_keys), 65)
        self.assertEqual(len(set(bank.question_keys)), 65)
        self.assertTrue(
            all(
                key.startswith("memory:1:question:")
                for key in bank.question_keys
            )
        )
        self.assertIn(
            "Parlons du budget — combien est-ce que ça coûte "
            "approximativement au total ?",
            questions,
        )
        self.assertIn(
            "Pour finir — si tu ne devais me recommander qu'une seule "
            "chose, ce serait laquelle ?",
            questions,
        )
        self.assertEqual(load_question_banks(), (bank,))


class QuestionBankViewTests(TestCase):
    def setUp(self):
        self.user = factories.make_user("question-bank")
        self.client.force_login(self.user)
        Command()._import_sections(load_sections())
        self.task = Task.objects.select_related("part").get(
            part__slug="eo",
            slug="tache-2",
        )

    def test_tache_two_opens_a_memory_overview(self):
        response = self.client.get(
            reverse(
                "study:task_detail",
                args=[self.task.part.slug, self.task.slug],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "study/tache_two_overview.html")
        self.assertTrue(self.task.available)
        self.assertEqual(response.context["memory_count"], 1)
        self.assertEqual(response.context["category_count"], 21)
        self.assertEqual(response.context["question_count"], 65)
        self.assertContains(response, 'id="memory-library-title">Mémoires</h2>')
        self.assertContains(response, "Mémoire 1")
        self.assertContains(response, "Questions réutilisables")
        self.assertContains(
            response,
            reverse(
                "study:task_memory_detail",
                args=[self.task.part.slug, self.task.slug, 1],
            ),
        )
        self.assertNotContains(response, "data-question-bank-question")
        self.assertNotContains(response, "Sujets &amp; réponses")
        self.assertNotContains(response, ">Pratiquer</a>")

    def test_memory_detail_opens_the_annotation_ready_master_bank(self):
        response = self.client.get(
            reverse(
                "study:task_memory_detail",
                args=[self.task.part.slug, self.task.slug, 1],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "study/question_bank.html")
        self.assertEqual(response.context["question_bank"].question_count, 65)
        self.assertContains(response, "Mémoire 1")
        self.assertContains(response, "Questions réutilisables")
        self.assertNotContains(response, "La règle d'or")
        self.assertNotContains(response, "question-bank-rules")
        self.assertNotContains(
            response,
            "Deux formulations maximum par sujet.",
        )
        self.assertContains(response, "data-question-bank-section", count=21)
        self.assertContains(response, "data-question-bank-question", count=65)
        self.assertContains(response, "data-memory-progress-form", count=65)
        self.assertContains(
            response,
            "<span data-memory-completed>0</span> sur 65 questions apprises",
            html=True,
        )
        self.assertContains(
            response,
            'data-annotation-source-key="question-bank:part-01"',
        )
        self.assertContains(
            response,
            f'data-annotation-task-id="{self.task.pk}"',
        )
        self.assertNotContains(response, "Sujets &amp; réponses")
        self.assertNotContains(response, ">Pratiquer</a>")

    def test_unknown_or_unrelated_memory_is_not_found(self):
        missing = self.client.get(
            reverse(
                "study:task_memory_detail",
                args=[self.task.part.slug, self.task.slug, 2],
            )
        )
        unrelated = self.client.get(
            reverse(
                "study:task_memory_detail",
                args=[self.task.part.slug, "tache-3", 1],
            )
        )

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(unrelated.status_code, 404)

    def test_task_card_describes_the_guide_instead_of_empty_responses(self):
        task_url = reverse(
            "study:task_detail",
            args=[self.task.part.slug, self.task.slug],
        )

        response = self.client.get(
            reverse("study:part_detail", args=[self.task.part.slug])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, task_url)
        self.assertContains(response, "1 mémoire · 21 catégories · 65 questions")
        self.assertContains(response, "0/65 apprises")
        self.assertContains(response, "À commencer")

    def test_question_progress_can_be_checked_and_unchecked(self):
        bank = load_question_bank()
        question_key = bank.question_keys[0]
        url = reverse(
            "study:task_memory_progress",
            args=[self.task.part.slug, self.task.slug, bank.number],
        )

        checked = self.client.post(
            url,
            {"question_key": question_key, "completed": "1"},
            HTTP_X_REQUESTED_WITH="fetch",
        )

        self.assertEqual(checked.status_code, 200)
        self.assertEqual(
            checked.json()["memory"],
            {
                "completed": 1,
                "total": 65,
                "percent": 2,
                "status": "active",
                "label": "En cours",
            },
        )
        self.assertEqual(checked.json()["section"]["completed"], 1)
        self.assertTrue(
            MemoryQuestionProgress.objects.filter(
                user=self.user,
                memory_number=bank.number,
                question_key=question_key,
            ).exists()
        )
        task_list = self.client.get(
            reverse("study:part_detail", args=[self.task.part.slug])
        )
        self.assertContains(task_list, "1/65 apprises")
        self.assertContains(task_list, "En cours")

        unchecked = self.client.post(
            url,
            {"question_key": question_key, "completed": "0"},
            HTTP_X_REQUESTED_WITH="fetch",
        )

        self.assertEqual(unchecked.status_code, 200)
        self.assertFalse(unchecked.json()["completed"])
        self.assertEqual(unchecked.json()["memory"]["completed"], 0)
        self.assertFalse(
            MemoryQuestionProgress.objects.filter(
                user=self.user,
                question_key=question_key,
            ).exists()
        )

    def test_question_progress_has_a_native_form_fallback(self):
        bank = load_question_bank()
        response = self.client.post(
            reverse(
                "study:task_memory_progress",
                args=[self.task.part.slug, self.task.slug, bank.number],
            ),
            {
                "question_key": bank.question_keys[0],
                "completed": "1",
            },
        )

        self.assertRedirects(
            response,
            reverse(
                "study:task_memory_detail",
                args=[self.task.part.slug, self.task.slug, bank.number],
            )
            + f"#{bank.sections[0].anchor}",
            fetch_redirect_response=False,
        )

    def test_question_progress_is_idempotent_and_private(self):
        bank = load_question_bank()
        question_key = bank.question_keys[0]
        url = reverse(
            "study:task_memory_progress",
            args=[self.task.part.slug, self.task.slug, bank.number],
        )
        other_user = factories.make_user("other-memory-learner")

        for _ in range(2):
            response = self.client.post(
                url,
                {"question_key": question_key, "completed": "1"},
                HTTP_X_REQUESTED_WITH="fetch",
            )
            self.assertEqual(response.status_code, 200)

        self.assertEqual(
            MemoryQuestionProgress.objects.filter(
                memory_number=bank.number,
                question_key=question_key,
            ).count(),
            1,
        )
        self.client.force_login(other_user)
        detail = self.client.get(
            reverse(
                "study:task_memory_detail",
                args=[self.task.part.slug, self.task.slug, bank.number],
            )
        )
        self.assertEqual(detail.context["memory_progress"].completed, 0)
        self.assertContains(detail, 'aria-checked="false"', count=65)

    def test_unknown_question_progress_is_rejected(self):
        response = self.client.post(
            reverse(
                "study:task_memory_progress",
                args=[self.task.part.slug, self.task.slug, 1],
            ),
            {"question_key": "memory:1:question:unknown", "completed": "1"},
            HTTP_X_REQUESTED_WITH="fetch",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["error"],
            "Cette question ne fait pas partie de la mémoire.",
        )
        self.assertFalse(MemoryQuestionProgress.objects.exists())

    def test_progress_rolls_up_to_memory_and_task_cards(self):
        bank = load_question_bank()
        MemoryQuestionProgress.objects.bulk_create(
            [
                MemoryQuestionProgress(
                    user=self.user,
                    memory_number=bank.number,
                    question_key=key,
                )
                for key in bank.question_keys
            ]
        )

        detail = self.client.get(
            reverse(
                "study:task_memory_detail",
                args=[self.task.part.slug, self.task.slug, bank.number],
            )
        )
        overview = self.client.get(
            reverse(
                "study:task_detail",
                args=[self.task.part.slug, self.task.slug],
            )
        )
        task_list = self.client.get(
            reverse("study:part_detail", args=[self.task.part.slug])
        )

        self.assertEqual(detail.context["memory_progress"].status, "done")
        self.assertContains(detail, 'aria-checked="true"', count=65)
        self.assertContains(
            detail,
            "<span data-memory-completed>65</span> sur 65 questions apprises",
            html=True,
        )
        self.assertEqual(
            overview.context["memories"][0]["progress"].status,
            "done",
        )
        self.assertContains(overview, "65/65 apprises")
        self.assertContains(task_list, "65/65 apprises")
        self.assertContains(task_list, "Terminé")

    def test_account_export_and_reset_include_memory_progress(self):
        bank = load_question_bank()
        own_progress = MemoryQuestionProgress.objects.create(
            user=self.user,
            memory_number=bank.number,
            question_key=bank.question_keys[0],
        )
        other_user = factories.make_user("retained-memory-learner")
        other_progress = MemoryQuestionProgress.objects.create(
            user=other_user,
            memory_number=bank.number,
            question_key=bank.question_keys[1],
        )

        exported = self.client.get(reverse("study:export_account")).json()

        self.assertEqual(exported["version"], 3)
        self.assertEqual(
            exported["memory_question_progress"][0]["question_key"],
            own_progress.question_key,
        )
        self.assertEqual(len(exported["memory_question_progress"]), 1)

        response = self.client.post(
            reverse("study:reset_progress"),
            {
                "current_pin": "123456",
                "confirmation": "REINITIALISER",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            MemoryQuestionProgress.objects.filter(pk=own_progress.pk).exists()
        )
        self.assertTrue(
            MemoryQuestionProgress.objects.filter(pk=other_progress.pk).exists()
        )
