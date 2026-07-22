from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from study.accounts import provision_user_study_data
from study.content import (
    load_question_bank,
    load_question_banks,
    load_comprehension_tests,
    load_tache_two_subject_months,
    parse_comprehension_vocabulary,
    parse_tache_two_responses,
    parse_tache_two_subject_vocabulary,
)
from study.models import (
    Annotation,
    AnnotationKind,
    Card,
    CardState,
    CardType,
    MemoryQuestionProgress,
    Phrase,
    PhraseTier,
    Response,
    Task,
)
from study.routing import response_detail_url

from . import factories


FINAL_EXACT_RESPONSE_REUSE = (
    ("octobre", 1, "janvier", 3, 11),
    ("octobre", 2, "janvier", 3, 12),
    ("octobre", 3, "janvier", 3, 13),
    ("octobre", 4, "janvier", 3, 14),
    ("octobre", 5, "mai", 4, 15),
    ("octobre", 6, "mars", 3, 11),
    ("octobre", 7, "mars", 3, 12),
    ("octobre", 8, "mars", 3, 13),
    ("octobre", 9, "mars", 3, 14),
    ("octobre", 10, "fevrier", 5, 25),
    ("octobre", 11, "juillet", 1, 1),
    ("octobre", 15, "juin", 1, 4),
    ("octobre", 21, "mai", 2, 8),
    ("octobre", 23, "juillet", 6, 26),
    ("octobre", 31, "aout", 2, 6),
    ("octobre", 32, "mai", 2, 7),
    ("octobre", 33, "mai", 2, 10),
    ("octobre", 34, "mai", 2, 9),
    ("octobre", 35, "aout", 2, 10),
    ("octobre", 46, "fevrier", 4, 16),
    ("octobre", 47, "fevrier", 4, 17),
    ("octobre", 48, "fevrier", 4, 18),
    ("octobre", 49, "fevrier", 4, 19),
    ("octobre", 50, "fevrier", 4, 20),
    ("novembre", 4, "janvier", 2, 8),
    ("novembre", 6, "mai", 5, 21),
    ("novembre", 7, "mai", 5, 22),
    ("novembre", 8, "mai", 5, 23),
    ("novembre", 9, "mai", 5, 24),
    ("novembre", 10, "mai", 5, 25),
    ("novembre", 11, "fevrier", 3, 12),
    ("novembre", 12, "septembre", 4, 17),
    ("novembre", 13, "septembre", 4, 18),
    ("novembre", 14, "septembre", 4, 19),
    ("novembre", 15, "septembre", 4, 20),
    ("decembre", 3, "juin", 3, 11),
    ("decembre", 6, "janvier", 2, 6),
    ("decembre", 7, "janvier", 2, 7),
    ("decembre", 8, "janvier", 2, 8),
    ("decembre", 9, "janvier", 2, 9),
    ("decembre", 10, "janvier", 2, 10),
    ("decembre", 11, "avril", 1, 1),
    ("decembre", 12, "avril", 1, 2),
    ("decembre", 13, "avril", 1, 3),
    ("decembre", 14, "avril", 1, 4),
    ("decembre", 15, "avril", 1, 5),
    ("decembre", 21, "juin", 6, 26),
    ("decembre", 22, "juin", 6, 27),
    ("decembre", 23, "juin", 6, 28),
    ("decembre", 24, "juin", 6, 29),
    ("decembre", 25, "juin", 6, 30),
    ("decembre", 28, "janvier", 3, 13),
    ("decembre", 29, "mars", 3, 12),
    ("decembre", 31, "fevrier", 1, 1),
    ("decembre", 32, "fevrier", 1, 2),
    ("decembre", 33, "fevrier", 1, 3),
    ("decembre", 34, "fevrier", 1, 4),
    ("decembre", 35, "fevrier", 1, 5),
    ("decembre", 44, "janvier", 3, 11),
)

VOCABULARY_MONTH_PREFIXES = {
    "janvier": "J1",
    "fevrier": "F2",
    "mars": "M3",
    "avril": "A4",
    "mai": "M5",
    "juin": "J6",
    "juillet": "J7",
    "aout": "A8",
    "septembre": "S9",
    "octobre": "O10",
    "novembre": "N11",
    "decembre": "D12",
}


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
        banks = load_question_banks()
        self.assertEqual(
            [memoire.number for memoire in banks],
            [1, 2, 3, 4, 5, 6],
        )
        self.assertEqual(banks[0], bank)

    def test_memoire_two_groups_reusable_cross_prompt_patterns(self):
        banks = load_question_banks()
        self.assertEqual(len(banks), 6)
        memoire = banks[1]

        self.assertEqual(memoire.number, 2)
        self.assertEqual(memoire.title, "Mémoire 2")
        self.assertEqual(memoire.label, "Questions transversales")
        self.assertEqual(memoire.icon, "compass")
        self.assertEqual(memoire.category_count, 5)
        self.assertEqual(memoire.question_count, 25)
        self.assertEqual(
            [section.number for section in memoire.sections],
            [1, 2, 3, 4, 5],
        )
        self.assertEqual(
            [section.title for section in memoire.sections],
            [
                "Choix / recommandation",
                "Retour d'expérience",
                "Déroulement / accompagnement",
                "Avantages / comparaison",
                "Souplesse / imprévus",
            ],
        )
        self.assertTrue(
            all(section.question_count == 5 for section in memoire.sections)
        )
        self.assertEqual(len(memoire.question_keys), 25)
        self.assertEqual(len(set(memoire.question_keys)), 25)
        self.assertTrue(
            all(
                key.startswith("memory:2:question:")
                for key in memoire.question_keys
            )
        )
        memoire_one = banks[0]
        questions = [
            question.text.casefold()
            for section in memoire.sections
            for group in section.groups
            for question in group.questions
        ]
        memoire_one_questions = {
            question.text.casefold()
            for section in memoire_one.sections
            for group in section.groups
            for question in group.questions
        }
        self.assertTrue(set(questions).isdisjoint(memoire_one_questions))
        for prompt_bound_term in [
            "canada",
            "enfant",
            "film",
            "livre",
            "quartier",
            "travail",
            "véhicule",
        ]:
            self.assertTrue(
                all(prompt_bound_term not in question for question in questions)
            )

        section_months = {
            section_number: set() for section_number in range(1, 6)
        }
        section_subjects = {
            section_number: set() for section_number in range(1, 6)
        }
        months = {
            month.slug: month for month in load_tache_two_subject_months()
        }
        for month_slug in ["mars", "avril"]:
            for batch in months[month_slug].batches:
                for subject in batch.subjects:
                    for question in subject.questions:
                        if question.memory_number != 2:
                            continue
                        section_months[question.memory_section].add(month_slug)
                        section_subjects[question.memory_section].add(
                            (month_slug, subject.number)
                        )
        for section_number in range(1, 6):
            self.assertEqual(
                section_months[section_number],
                {"mars", "avril"},
            )
            self.assertGreaterEqual(
                len(section_subjects[section_number]),
                3,
            )

    def test_memoire_three_deepens_may_and_june_patterns(self):
        banks = load_question_banks()
        memoire = banks[2]

        self.assertEqual(memoire.number, 3)
        self.assertEqual(memoire.title, "Mémoire 3")
        self.assertEqual(memoire.label, "Questions d'approfondissement")
        self.assertEqual(memoire.icon, "search")
        self.assertEqual(memoire.category_count, 5)
        self.assertEqual(memoire.question_count, 25)
        self.assertEqual(
            [section.title for section in memoire.sections],
            [
                "Dossier, garanties et conditions d'engagement",
                "Profil et besoins (personne ou animal)",
                "État et historique d'un bien",
                "Parcours, évolution et projets",
                "Encadrement, entourage et responsabilités",
            ],
        )
        self.assertTrue(
            all(section.question_count == 5 for section in memoire.sections)
        )
        self.assertEqual(len(set(memoire.question_keys)), 25)
        self.assertTrue(
            all(
                key.startswith("memory:3:question:")
                for key in memoire.question_keys
            )
        )

        previous_questions = {
            question.text.casefold()
            for bank in banks[:2]
            for section in bank.sections
            for group in section.groups
            for question in group.questions
        }
        current_questions = {
            question.text.casefold()
            for section in memoire.sections
            for group in section.groups
            for question in group.questions
        }
        self.assertTrue(current_questions.isdisjoint(previous_questions))

        section_months = {
            section_number: set() for section_number in range(1, 6)
        }
        section_counts = {
            section_number: 0 for section_number in range(1, 6)
        }
        for month in load_tache_two_subject_months()[4:6]:
            for batch in month.batches:
                for subject in batch.subjects:
                    for question in subject.questions:
                        if question.memory_number != 3:
                            continue
                        section_months[question.memory_section].add(month.slug)
                        section_counts[question.memory_section] += 1
        self.assertEqual(
            section_months,
            {
                1: {"mai", "juin"},
                2: {"mai", "juin"},
                3: {"mai", "juin"},
                4: {"mai", "juin"},
                5: {"mai", "juin"},
            },
        )
        self.assertEqual(section_counts, {1: 14, 2: 11, 3: 6, 4: 10, 5: 14})

    def test_memoire_four_groups_new_july_and_august_patterns(self):
        banks = load_question_banks()
        memoire = banks[3]

        self.assertEqual(memoire.number, 4)
        self.assertEqual(memoire.title, "Mémoire 4")
        self.assertEqual(memoire.label, "Questions situationnelles")
        self.assertEqual(memoire.icon, "target")
        self.assertEqual(memoire.category_count, 5)
        self.assertEqual(memoire.question_count, 25)
        self.assertEqual(
            [section.title for section in memoire.sections],
            [
                "Ouverture — reprise et arrivée",
                "Économies et bons plans",
                "Se lancer — débuter une activité",
                "Adapter selon la saison et le moment",
                "Avis personnel et recommandation ciblée",
            ],
        )
        self.assertTrue(
            all(section.question_count == 5 for section in memoire.sections)
        )
        self.assertEqual(len(set(memoire.question_keys)), 25)
        self.assertTrue(
            all(
                key.startswith("memory:4:question:")
                for key in memoire.question_keys
            )
        )

        previous_questions = {
            question.text.casefold()
            for bank in banks[:3]
            for section in bank.sections
            for group in section.groups
            for question in group.questions
        }
        current_questions = {
            question.text.casefold()
            for section in memoire.sections
            for group in section.groups
            for question in group.questions
        }
        self.assertTrue(current_questions.isdisjoint(previous_questions))

        section_months = {
            section_number: set() for section_number in range(1, 6)
        }
        section_counts = {
            section_number: 0 for section_number in range(1, 6)
        }
        months = load_tache_two_subject_months()
        self.assertFalse(
            any(
                question.memory_number == 4
                for month in months[:6]
                for batch in month.batches
                for subject in batch.subjects
                for question in subject.questions
            )
        )
        for month in months[6:8]:
            for batch in month.batches:
                for subject in batch.subjects:
                    for question in subject.questions:
                        if question.memory_number != 4:
                            continue
                        section_months[question.memory_section].add(month.slug)
                        section_counts[question.memory_section] += 1
        self.assertEqual(
            section_months,
            {
                1: {"juillet", "aout"},
                2: {"juillet", "aout"},
                3: {"juillet"},
                4: {"juillet", "aout"},
                5: {"juillet", "aout"},
            },
        )
        self.assertEqual(section_counts, {1: 6, 2: 5, 3: 6, 4: 5, 5: 8})

    def test_memoire_five_groups_relational_patterns(self):
        banks = load_question_banks()
        memoire = banks[4]

        self.assertEqual(memoire.number, 5)
        self.assertEqual(memoire.title, "Mémoire 5")
        self.assertEqual(memoire.label, "Questions relationnelles")
        self.assertEqual(memoire.icon, "users")
        self.assertEqual(memoire.category_count, 5)
        self.assertEqual(memoire.question_count, 25)
        self.assertEqual(
            [section.number for section in memoire.sections],
            [1, 2, 3, 4, 5],
        )
        self.assertEqual(
            [section.title for section in memoire.sections],
            [
                "Prendre des nouvelles après un coup dur",
                "S'installer, s'adapter, tirer les leçons d'un changement",
                "Évaluer une proposition avant de s'engager",
                "Cadrer un événement ou une célébration",
                "Faire confiance à une personne recommandée",
            ],
        )
        self.assertTrue(
            all(section.question_count == 5 for section in memoire.sections)
        )
        self.assertEqual(len(memoire.question_keys), 25)
        self.assertEqual(len(set(memoire.question_keys)), 25)
        self.assertTrue(
            all(
                key.startswith("memory:5:question:")
                for key in memoire.question_keys
            )
        )
        previous_questions = {
            question.text.casefold()
            for bank in banks[:4]
            for section in bank.sections
            for group in section.groups
            for question in group.questions
        }
        current_questions = {
            question.text.casefold()
            for section in memoire.sections
            for group in section.groups
            for question in group.questions
        }
        self.assertTrue(current_questions.isdisjoint(previous_questions))

        months = load_tache_two_subject_months()
        self.assertFalse(
            any(
                question.memory_number == 5
                for month in months[:8]
                for batch in month.batches
                for subject in batch.subjects
                for question in subject.questions
            )
        )
        section_months = {
            section_number: set() for section_number in range(1, 6)
        }
        section_counts = {
            section_number: 0 for section_number in range(1, 6)
        }
        linked_questions = set()
        for month in months[8:]:
            for batch in month.batches:
                for subject in batch.subjects:
                    for question in subject.questions:
                        if question.memory_number != 5:
                            continue
                        section_months[question.memory_section].add(month.slug)
                        section_counts[question.memory_section] += 1
                        linked_questions.add(question.text.casefold())
        self.assertEqual(
            section_months,
            {
                1: {"octobre"},
                2: {"septembre", "octobre"},
                3: {"septembre", "octobre"},
                4: {"septembre", "octobre", "decembre"},
                5: {"septembre", "octobre", "novembre"},
            },
        )
        self.assertEqual(section_counts, {1: 5, 2: 5, 3: 6, 4: 8, 5: 7})
        self.assertEqual(linked_questions, current_questions)

    def test_memoire_six_groups_social_life_patterns(self):
        banks = load_question_banks()
        memoire = banks[5]

        self.assertEqual(memoire.number, 6)
        self.assertEqual(memoire.title, "Mémoire 6")
        self.assertEqual(memoire.label, "Questions de vie sociale")
        self.assertEqual(memoire.icon, "hand-wave")
        self.assertEqual(memoire.category_count, 5)
        self.assertEqual(memoire.question_count, 25)
        self.assertEqual(
            [section.number for section in memoire.sections],
            [1, 2, 3, 4, 5],
        )
        self.assertEqual(
            [section.title for section in memoire.sections],
            [
                "Prendre des nouvelles — vie, projets et liens sociaux",
                "Vie associative — missions, engagement et impact",
                "Découvrir une destination — lieux, quartiers et expériences",
                "S'intégrer — rencontres, adaptation et culture locale",
                "Confirmer un séjour ou une sortie — règles, annulation et organisation",
            ],
        )
        self.assertTrue(
            all(section.question_count == 5 for section in memoire.sections)
        )
        self.assertEqual(len(memoire.question_keys), 25)
        self.assertEqual(len(set(memoire.question_keys)), 25)
        self.assertTrue(
            all(
                key.startswith("memory:6:question:")
                for key in memoire.question_keys
            )
        )
        previous_questions = {
            question.text.casefold()
            for bank in banks[:5]
            for section in bank.sections
            for group in section.groups
            for question in group.questions
        }
        current_questions = {
            question.text.casefold()
            for section in memoire.sections
            for group in section.groups
            for question in group.questions
        }
        self.assertTrue(current_questions.isdisjoint(previous_questions))

        months = load_tache_two_subject_months()
        self.assertFalse(
            any(
                question.memory_number == 6
                for month in months[:10]
                for batch in month.batches
                for subject in batch.subjects
                for question in subject.questions
            )
        )
        section_months = {
            section_number: set() for section_number in range(1, 6)
        }
        section_counts = {
            section_number: 0 for section_number in range(1, 6)
        }
        linked_questions = set()
        for month in months[10:]:
            for batch in month.batches:
                for subject in batch.subjects:
                    for question in subject.questions:
                        if question.memory_number != 6:
                            continue
                        section_months[question.memory_section].add(month.slug)
                        section_counts[question.memory_section] += 1
                        linked_questions.add(question.text.casefold())
        self.assertEqual(
            section_months,
            {
                1: {"decembre"},
                2: {"decembre"},
                3: {"decembre"},
                4: {"decembre"},
                5: {"novembre"},
            },
        )
        self.assertEqual(section_counts, {1: 5, 2: 5, 3: 5, 4: 5, 5: 5})
        self.assertEqual(linked_questions, current_questions)

    def test_monthly_batches_are_question_only_and_memory_driven(self):
        months = load_tache_two_subject_months()

        self.assertEqual(len(months), 12)
        january = months[0]
        self.assertEqual(january.name, "Janvier")
        self.assertEqual(january.batch_count, 3)
        self.assertEqual(january.subject_count, 15)
        self.assertEqual(january.question_count, 219)
        first_batch, second_batch, third_batch = january.batches
        self.assertEqual(first_batch.number, 1)
        self.assertEqual(
            [subject.number for subject in first_batch.subjects],
            [1, 2, 3, 4, 5],
        )
        self.assertEqual(
            [subject.question_count for subject in first_batch.subjects],
            [14, 12, 14, 15, 15],
        )
        self.assertEqual(
            sum(
                subject.memory_question_count
                for subject in first_batch.subjects
            ),
            67,
        )
        self.assertEqual(second_batch.number, 2)
        self.assertEqual(
            [subject.number for subject in second_batch.subjects],
            [6, 7, 8, 9, 10],
        )
        self.assertEqual(
            [subject.question_count for subject in second_batch.subjects],
            [14, 16, 15, 14, 15],
        )
        self.assertEqual(
            sum(
                subject.memory_question_count
                for subject in second_batch.subjects
            ),
            71,
        )
        self.assertEqual(third_batch.number, 3)
        self.assertEqual(
            [subject.number for subject in third_batch.subjects],
            [11, 12, 13, 14, 15],
        )
        self.assertEqual(
            [subject.question_count for subject in third_batch.subjects],
            [15, 15, 15, 15, 15],
        )
        self.assertEqual(
            sum(
                subject.memory_question_count
                for subject in third_batch.subjects
            ),
            75,
        )
        february = months[1]
        self.assertEqual(february.name, "Février")
        self.assertEqual(february.batch_count, 6)
        self.assertEqual(february.subject_count, 30)
        self.assertEqual(february.question_count, 430)
        self.assertEqual(
            [
                [subject.number for subject in batch.subjects]
                for batch in february.batches
            ],
            [
                [1, 2, 3, 4, 5],
                [6, 7, 8, 9, 10],
                [11, 12, 13, 14, 15],
                [16, 17, 18, 19, 20],
                [21, 22, 23, 24, 25],
                [26, 27, 28, 29, 30],
            ],
        )
        self.assertEqual(
            [
                [subject.question_count for subject in batch.subjects]
                for batch in february.batches
            ],
            [
                [15, 15, 15, 14, 14],
                [14, 14, 13, 14, 15],
                [14, 13, 14, 8, 15],
                [15, 13, 15, 15, 15],
                [15, 15, 15, 15, 15],
                [15, 15, 15, 15, 15],
            ],
        )
        self.assertEqual(
            [
                sum(
                    subject.memory_question_count
                    for subject in batch.subjects
                )
                for batch in february.batches
            ],
            [73, 38, 51, 72, 75, 73],
        )
        march = months[2]
        self.assertEqual(march.name, "Mars")
        self.assertEqual(march.batch_count, 3)
        self.assertEqual(march.subject_count, 15)
        self.assertEqual(march.question_count, 223)
        self.assertEqual(
            [
                [subject.number for subject in batch.subjects]
                for batch in march.batches
            ],
            [[1, 2, 3, 4, 5], [6, 7, 8, 9, 10], [11, 12, 13, 14, 15]],
        )
        self.assertEqual(
            [
                [subject.question_count for subject in batch.subjects]
                for batch in march.batches
            ],
            [
                [15, 15, 15, 15, 15],
                [15, 15, 15, 14, 14],
                [15, 15, 15, 15, 15],
            ],
        )
        self.assertEqual(
            [
                sum(
                    subject.memory_question_count
                    for subject in batch.subjects
                )
                for batch in march.batches
            ],
            [75, 73, 75],
        )
        april = months[3]
        self.assertEqual(april.name, "Avril")
        self.assertEqual(april.batch_count, 2)
        self.assertEqual(april.subject_count, 10)
        self.assertEqual(april.question_count, 150)
        self.assertEqual(
            [
                [subject.number for subject in batch.subjects]
                for batch in april.batches
            ],
            [[1, 2, 3, 4, 5], [6, 7, 8, 9, 10]],
        )
        self.assertEqual(
            [
                [subject.question_count for subject in batch.subjects]
                for batch in april.batches
            ],
            [[15, 15, 15, 15, 15], [15, 15, 15, 15, 15]],
        )
        self.assertEqual(
            [
                sum(
                    subject.memory_question_count
                    for subject in batch.subjects
                )
                for batch in april.batches
            ],
            [75, 75],
        )
        may = months[4]
        self.assertEqual(may.name, "Mai")
        self.assertEqual(may.batch_count, 5)
        self.assertEqual(may.subject_count, 25)
        self.assertEqual(may.question_count, 372)
        self.assertEqual(
            [
                [subject.number for subject in batch.subjects]
                for batch in may.batches
            ],
            [
                [1, 2, 3, 4, 5],
                [6, 7, 8, 9, 10],
                [11, 12, 13, 14],
                [15, 16, 17, 18, 19],
                [20, 21, 22, 23, 24, 25],
            ],
        )
        self.assertEqual(
            [
                [subject.question_count for subject in batch.subjects]
                for batch in may.batches
            ],
            [
                [15, 13, 15, 15, 15],
                [15, 15, 15, 15, 15],
                [15, 15, 15, 15],
                [15, 14, 15, 15, 15],
                [15, 15, 15, 15, 15, 15],
            ],
        )
        self.assertEqual(
            [
                sum(
                    subject.memory_question_count
                    for subject in batch.subjects
                )
                for batch in may.batches
            ],
            [72, 74, 60, 72, 88],
        )
        june = months[5]
        self.assertEqual(june.name, "Juin")
        self.assertEqual(june.batch_count, 9)
        self.assertEqual(june.subject_count, 45)
        self.assertEqual(june.question_count, 673)
        self.assertEqual(
            [
                [subject.number for subject in batch.subjects]
                for batch in june.batches
            ],
            [
                [1, 2, 3, 4, 5],
                [6, 7, 8, 9, 10],
                [11, 12, 13, 14, 15],
                [16, 17, 18, 19, 20],
                [21, 22, 23, 24, 25],
                [26, 27, 28, 29, 30],
                [31, 32, 33, 34, 35],
                [36, 37, 38, 39, 40],
                [41, 42, 43, 44, 45],
            ],
        )
        self.assertEqual(
            [
                [subject.question_count for subject in batch.subjects]
                for batch in june.batches
            ],
            [
                [15, 15, 15, 15, 15],
                [15, 15, 14, 15, 15],
                [15, 15, 15, 15, 15],
                [15, 15, 15, 15, 15],
                [14, 16, 15, 14, 15],
                [15, 15, 15, 15, 15],
                [15, 15, 15, 15, 15],
                [15, 15, 15, 15, 15],
                [15, 15, 15, 15, 15],
            ],
        )
        self.assertEqual(
            [
                sum(
                    subject.memory_question_count
                    for subject in batch.subjects
                )
                for batch in june.batches
            ],
            [75, 74, 75, 75, 71, 75, 73, 75, 75],
        )
        july = months[6]
        self.assertEqual(july.name, "Juillet")
        self.assertEqual(july.batch_count, 8)
        self.assertEqual(july.subject_count, 38)
        self.assertEqual(july.question_count, 567)
        self.assertEqual(
            [
                [subject.number for subject in batch.subjects]
                for batch in july.batches
            ],
            [
                [1, 2, 3, 4, 5],
                [6, 7, 8, 9, 10],
                [11, 12, 13, 14, 15],
                [16, 17, 18, 19, 20],
                [21, 22, 23, 24, 25],
                [26, 27, 28],
                [29, 30, 31, 32, 33],
                [34, 35, 36, 37, 38],
            ],
        )
        self.assertEqual(
            [
                [subject.question_count for subject in batch.subjects]
                for batch in july.batches
            ],
            [
                [15, 15, 15, 15, 15],
                [15, 15, 15, 15, 15],
                [15, 13, 15, 15, 15],
                [15, 15, 15, 15, 15],
                [15, 15, 15, 15, 15],
                [15, 15, 15],
                [15, 15, 15, 15, 15],
                [15, 15, 14, 15, 15],
            ],
        )
        self.assertEqual(
            [
                sum(
                    subject.memory_question_count
                    for subject in batch.subjects
                )
                for batch in july.batches
            ],
            [34, 41, 72, 28, 28, 45, 21, 47],
        )
        august = months[7]
        self.assertEqual(august.name, "Août")
        self.assertEqual(august.batch_count, 4)
        self.assertEqual(august.subject_count, 20)
        self.assertEqual(august.question_count, 300)
        self.assertEqual(
            [
                [subject.number for subject in batch.subjects]
                for batch in august.batches
            ],
            [
                [1, 2, 3, 4, 5],
                [6, 7, 8, 9, 10],
                [11, 12, 13, 14, 15],
                [16, 17, 18, 19, 20],
            ],
        )
        self.assertEqual(
            [
                [subject.question_count for subject in batch.subjects]
                for batch in august.batches
            ],
            [[15, 15, 15, 15, 15]] * 4,
        )
        self.assertEqual(
            [
                sum(
                    subject.memory_question_count
                    for subject in batch.subjects
                )
                for batch in august.batches
            ],
            [75, 64, 74, 75],
        )
        september = months[8]
        self.assertEqual(september.name, "Septembre")
        self.assertEqual(september.batch_count, 7)
        self.assertEqual(september.subject_count, 35)
        self.assertEqual(september.question_count, 523)
        self.assertEqual(
            [
                [subject.number for subject in batch.subjects]
                for batch in september.batches
            ],
            [
                [1, 2, 3, 4, 5],
                [6, 7, 8, 9, 10],
                [11, 12, 13, 14, 15],
                [16, 17, 18, 19, 20],
                [21, 22, 23, 24, 25],
                [26, 27, 28, 29, 30],
                [31, 32, 33, 34, 35],
            ],
        )
        self.assertEqual(
            [
                [subject.question_count for subject in batch.subjects]
                for batch in september.batches
            ],
            [
                [15, 15, 15, 15, 15],
                [15, 15, 15, 15, 15],
                [15, 15, 15, 15, 15],
                [13, 15, 15, 15, 15],
                [15, 15, 15, 15, 15],
                [15, 15, 15, 15, 15],
                [15, 15, 15, 15, 15],
            ],
        )
        self.assertEqual(
            [
                sum(
                    subject.memory_question_count
                    for subject in batch.subjects
                )
                for batch in september.batches
            ],
            [10, 73, 75, 20, 34, 34, 2],
        )
        october = months[9]
        self.assertEqual(october.name, "Octobre")
        self.assertEqual(october.batch_count, 10)
        self.assertEqual(october.subject_count, 50)
        self.assertEqual(october.question_count, 748)
        self.assertEqual(
            [
                [subject.question_count for subject in batch.subjects]
                for batch in october.batches
            ],
            [[15, 15, 15, 15, 15]] * 9
            + [[15, 13, 15, 15, 15]],
        )
        self.assertEqual(
            [
                sum(
                    subject.memory_question_count
                    for subject in batch.subjects
                )
                for batch in october.batches
            ],
            [75, 75, 54, 27, 37, 20, 64, 4, 12, 72],
        )
        november = months[10]
        self.assertEqual(november.name, "Novembre")
        self.assertEqual(november.batch_count, 3)
        self.assertEqual(november.subject_count, 15)
        self.assertEqual(november.question_count, 223)
        self.assertEqual(
            [
                [subject.question_count for subject in batch.subjects]
                for batch in november.batches
            ],
            [
                [15, 15, 15, 15, 15],
                [15, 15, 15, 15, 15],
                [13, 15, 15, 15, 15],
            ],
        )
        self.assertEqual(
            [
                sum(
                    subject.memory_question_count
                    for subject in batch.subjects
                )
                for batch in november.batches
            ],
            [35, 73, 20],
        )
        december = months[11]
        self.assertEqual(december.name, "Décembre")
        self.assertEqual(december.batch_count, 10)
        self.assertEqual(december.subject_count, 50)
        self.assertEqual(december.question_count, 747)
        self.assertEqual(
            [
                [subject.question_count for subject in batch.subjects]
                for batch in december.batches
            ],
            [
                [15, 15, 15, 15, 15],
                [14, 16, 15, 14, 15],
                [15, 15, 15, 15, 15],
                [15, 15, 15, 15, 15],
                [15, 15, 15, 15, 15],
                [15, 15, 15, 15, 15],
                [15, 15, 15, 14, 14],
                [15, 15, 15, 15, 15],
                [15, 15, 15, 15, 15],
                [15, 15, 15, 15, 15],
            ],
        )
        self.assertEqual(
            [
                sum(
                    subject.memory_question_count
                    for subject in batch.subjects
                )
                for batch in december.batches
            ],
            [26, 71, 75, 15, 75, 47, 73, 16, 24, 45],
        )

        def question_signatures(subject):
            return [
                (
                    question.text,
                    question.memory_number,
                    question.memory_section,
                )
                for question in subject.questions
            ]

        january_gym = january.batches[2].subjects[2]
        march_gym = march.batches[0].subjects[0]
        self.assertEqual(
            question_signatures(march_gym),
            question_signatures(january_gym),
        )
        for march_subject, february_subject in zip(
            march.batches[1].subjects,
            february.batches[0].subjects,
            strict=True,
        ):
            self.assertEqual(
                question_signatures(march_subject),
                question_signatures(february_subject),
            )
        for march_subject, february_subject in zip(
            march.batches[2].subjects,
            february.batches[4].subjects,
            strict=True,
        ):
            self.assertEqual(
                question_signatures(march_subject),
                question_signatures(february_subject),
            )
        april_travel = april.batches[1].subjects[0]
        february_travel = february.batches[5].subjects[3]
        self.assertEqual(
            question_signatures(april_travel),
            question_signatures(february_travel),
        )
        subjects_by_key = {
            (month.slug, batch.number, subject.number): subject
            for month in months
            for batch in month.batches
            for subject in batch.subjects
        }
        for may_key, source_key in (
            (("mai", 1, 1), ("fevrier", 4, 16)),
            (("mai", 1, 2), ("fevrier", 4, 17)),
            (("mai", 1, 3), ("fevrier", 4, 18)),
            (("mai", 1, 4), ("fevrier", 4, 19)),
            (("mai", 1, 5), ("fevrier", 4, 20)),
            (("mai", 3, 11), ("janvier", 3, 11)),
            (("mai", 3, 12), ("janvier", 3, 12)),
            (("mai", 3, 13), ("janvier", 3, 13)),
            (("mai", 3, 14), ("janvier", 3, 14)),
            (("mai", 4, 16), ("fevrier", 3, 11)),
        ):
            self.assertEqual(
                question_signatures(subjects_by_key[may_key]),
                question_signatures(subjects_by_key[source_key]),
            )
        for june_key, source_key in (
            (("juin", 2, 7), ("mars", 3, 12)),
            (("juin", 2, 8), ("mars", 2, 10)),
            (("juin", 4, 19), ("janvier", 3, 13)),
            (("juin", 5, 21), ("janvier", 2, 6)),
            (("juin", 5, 22), ("janvier", 2, 7)),
            (("juin", 5, 23), ("janvier", 2, 8)),
            (("juin", 5, 24), ("janvier", 2, 9)),
            (("juin", 5, 25), ("janvier", 2, 10)),
            (("juin", 7, 31), ("fevrier", 6, 26)),
            (("juin", 7, 32), ("fevrier", 6, 27)),
            (("juin", 7, 33), ("fevrier", 6, 28)),
            (("juin", 7, 34), ("fevrier", 6, 29)),
            (("juin", 7, 35), ("fevrier", 6, 30)),
            (("juin", 8, 36), ("juin", 1, 1)),
            (("juin", 8, 37), ("juin", 1, 2)),
            (("juin", 8, 38), ("juin", 1, 3)),
            (("juin", 8, 39), ("juin", 1, 4)),
            (("juin", 8, 40), ("juin", 1, 5)),
            (("juin", 9, 41), ("avril", 1, 1)),
            (("juin", 9, 42), ("avril", 1, 2)),
            (("juin", 9, 43), ("avril", 1, 3)),
            (("juin", 9, 44), ("avril", 1, 4)),
            (("juin", 9, 45), ("avril", 1, 5)),
        ):
            self.assertEqual(
                question_signatures(subjects_by_key[june_key]),
                question_signatures(subjects_by_key[source_key]),
            )
        for july_key, source_key in (
            (("juillet", 1, 1), ("juin", 1, 2)),
            (("juillet", 1, 5), ("avril", 1, 3)),
            (("juillet", 3, 11), ("mai", 1, 1)),
            (("juillet", 3, 12), ("mai", 1, 2)),
            (("juillet", 3, 13), ("fevrier", 4, 18)),
            (("juillet", 3, 14), ("fevrier", 4, 19)),
            (("juillet", 3, 15), ("fevrier", 4, 20)),
            (("juillet", 5, 21), ("juillet", 4, 16)),
            (("juillet", 5, 22), ("juillet", 4, 17)),
            (("juillet", 5, 23), ("juillet", 4, 18)),
            (("juillet", 5, 24), ("juillet", 4, 19)),
            (("juillet", 5, 25), ("juillet", 4, 20)),
            (("juillet", 6, 26), ("juin", 4, 18)),
            (("juillet", 6, 27), ("avril", 2, 7)),
            (("juillet", 6, 28), ("janvier", 3, 15)),
            (("juillet", 8, 35), ("mars", 3, 12)),
            (("juillet", 8, 36), ("mars", 2, 10)),
            (("juillet", 8, 37), ("juin", 2, 9)),
        ):
            self.assertEqual(
                question_signatures(subjects_by_key[july_key]),
                question_signatures(subjects_by_key[source_key]),
            )
        for august_key, source_key in (
            (("aout", 1, 1), ("juin", 1, 1)),
            (("aout", 1, 2), ("juin", 1, 2)),
            (("aout", 1, 3), ("juin", 1, 3)),
            (("aout", 1, 4), ("juin", 1, 4)),
            (("aout", 1, 5), ("juin", 1, 5)),
            (("aout", 2, 7), ("mai", 2, 7)),
            (("aout", 2, 8), ("mai", 2, 10)),
            (("aout", 2, 9), ("mai", 2, 9)),
            (("aout", 3, 11), ("mai", 2, 6)),
            (("aout", 3, 12), ("mai", 2, 7)),
            (("aout", 3, 13), ("mai", 2, 8)),
            (("aout", 3, 14), ("mai", 2, 9)),
            (("aout", 3, 15), ("mai", 2, 10)),
            (("aout", 4, 16), ("janvier", 3, 11)),
            (("aout", 4, 17), ("janvier", 3, 12)),
            (("aout", 4, 18), ("janvier", 3, 13)),
            (("aout", 4, 19), ("janvier", 3, 14)),
            (("aout", 4, 20), ("mai", 4, 15)),
        ):
            self.assertEqual(
                question_signatures(subjects_by_key[august_key]),
                question_signatures(subjects_by_key[source_key]),
            )
        for september_key, source_key in (
            (("septembre", 1, 1), ("juillet", 1, 4)),
            (("septembre", 2, 6), ("fevrier", 6, 26)),
            (("septembre", 2, 7), ("fevrier", 6, 27)),
            (("septembre", 2, 8), ("fevrier", 6, 28)),
            (("septembre", 2, 9), ("fevrier", 6, 29)),
            (("septembre", 2, 10), ("fevrier", 6, 30)),
            (("septembre", 3, 11), ("mars", 3, 11)),
            (("septembre", 3, 12), ("mars", 3, 12)),
            (("septembre", 3, 13), ("mars", 3, 13)),
            (("septembre", 3, 14), ("mars", 3, 14)),
            (("septembre", 3, 15), ("fevrier", 5, 25)),
            (("septembre", 4, 16), ("fevrier", 3, 12)),
            (("septembre", 5, 21), ("juillet", 8, 34)),
            (("septembre", 5, 24), ("janvier", 3, 13)),
            (("septembre", 5, 25), ("juillet", 1, 4)),
            (("septembre", 6, 26), ("juillet", 8, 34)),
            (("septembre", 6, 29), ("janvier", 3, 13)),
            (("septembre", 6, 30), ("juillet", 1, 4)),
            (("septembre", 7, 31), ("septembre", 1, 3)),
            (("septembre", 7, 33), ("septembre", 1, 4)),
            (("septembre", 6, 27), ("septembre", 5, 22)),
            (("septembre", 6, 28), ("septembre", 5, 23)),
        ):
            self.assertEqual(
                question_signatures(subjects_by_key[september_key]),
                question_signatures(subjects_by_key[source_key]),
            )
        for (
            target_month,
            target_subject,
            source_month,
            source_batch,
            source_subject,
        ) in FINAL_EXACT_RESPONSE_REUSE:
            target_batch = (target_subject - 1) // 5 + 1
            self.assertEqual(
                question_signatures(
                    subjects_by_key[
                        (target_month, target_batch, target_subject)
                    ]
                ),
                question_signatures(
                    subjects_by_key[
                        (source_month, source_batch, source_subject)
                    ]
                ),
            )
        self.assertTrue(
            all(
                question.text.endswith("?")
                for month in months
                for batch in month.batches
                for subject in batch.subjects
                for question in subject.questions
            )
        )
        corpus = " ".join(
            (
                subject.prompt
                + " "
                + " ".join(
                    question.text for question in subject.questions
                )
            )
            for month in months
            for batch in month.batches
            for subject in batch.subjects
        )
        self.assertNotIn("Dog sitting", corpus)
        self.assertNotIn("I live in your neighborhood", corpus)
        self.assertNotIn("**»**", corpus)
        self.assertNotIn("Vous partez en vacances où", corpus)
        self.assertNotIn("Quelle est la durée d'une séance.", corpus)
        self.assertNotIn("C'est facilement se déplacer", corpus)
        self.assertIn(
            "Pour finir — si tu ne devais me recommander "
            "qu'une seule chose",
            corpus,
        )

    def test_subjects_generate_srs_responses_and_vocabulary(self):
        responses = parse_tache_two_responses()
        vocabulary = parse_tache_two_subject_vocabulary(responses)

        self.assertEqual(len(responses), 348)
        self.assertEqual(
            sum(len(response.arguments) for response in responses),
            5175,
        )
        self.assertEqual(len(vocabulary), 10440)
        self.assertEqual(
            {phrase.tier for phrase in vocabulary},
            {PhraseTier.SUBJECT},
        )
        self.assertEqual(
            {
                source
                for phrase in vocabulary
                for source in phrase.sources
            },
            {
                (prompt.theme, prompt.number)
                for response in responses
                for prompt in response.prompts
            },
        )
        questions_by_source = {
            (prompt.theme, prompt.number): {
                argument.idea for argument in response.arguments
            }
            for response in responses
            for prompt in response.prompts
        }
        for phrase in vocabulary:
            self.assertEqual(len(phrase.sources), 1)
            self.assertIn(
                phrase.example,
                questions_by_source[phrase.sources[0]],
            )
            self.assertEqual(
                phrase.example.casefold().count(
                    phrase.expression.casefold()
                ),
                1,
            )
        for source in questions_by_source:
            source_phrases = [
                phrase
                for phrase in vocabulary
                if phrase.sources == (source,)
            ]
            category_counts = {}
            for phrase in source_phrases:
                category_counts[phrase.category] = (
                    category_counts.get(phrase.category, 0) + 1
                )
            self.assertEqual(len(source_phrases), 30)
            self.assertEqual(
                category_counts,
                {
                    "Mots clés du sujet": 10,
                    "Collocations du sujet": 10,
                    "Tournures pour l'oral": 10,
                },
            )
        comprehension_orders = {
            item.phrase.order
            for item in parse_comprehension_vocabulary(
                load_comprehension_tests()
            )
        }
        vocabulary_orders = [phrase.order for phrase in vocabulary]
        vocabulary_by_id = {
            phrase.phrase_id: phrase for phrase in vocabulary
        }

        def phrase_for_slot(prefix, index):
            base_id = f"{prefix}V{index:02d}"
            phrase = vocabulary_by_id.get(f"{base_id}R")
            if phrase is None:
                phrase = vocabulary_by_id[base_id]
            return phrase

        for index in range(1, 31):
            january_phrase = phrase_for_slot("T2J1S13", index)
            march_phrase = phrase_for_slot("T2M3S1", index)
            self.assertEqual(
                (
                    march_phrase.category,
                    march_phrase.english_cue,
                    march_phrase.expression,
                    march_phrase.example,
                    march_phrase.note,
                ),
                (
                    january_phrase.category,
                    january_phrase.english_cue,
                    january_phrase.expression,
                    january_phrase.example,
                    january_phrase.note,
                ),
            )
        for february_subject, march_subject in zip(
            range(1, 6),
            range(6, 11),
            strict=True,
        ):
            for index in range(1, 31):
                february_phrase = phrase_for_slot(
                    f"T2F2S{february_subject}",
                    index,
                )
                march_phrase = phrase_for_slot(
                    f"T2M3S{march_subject}",
                    index,
                )
                self.assertEqual(
                    (
                        march_phrase.category,
                        march_phrase.english_cue,
                        march_phrase.expression,
                        march_phrase.example,
                        march_phrase.note,
                    ),
                    (
                        february_phrase.category,
                        february_phrase.english_cue,
                        february_phrase.expression,
                        february_phrase.example,
                        february_phrase.note,
                    ),
                )
        for february_subject, march_subject in zip(
            range(21, 26),
            range(11, 16),
            strict=True,
        ):
            for index in range(1, 31):
                february_phrase = phrase_for_slot(
                    f"T2F2S{february_subject}",
                    index,
                )
                march_phrase = phrase_for_slot(
                    f"T2M3S{march_subject}",
                    index,
                )
                self.assertEqual(
                    (
                        march_phrase.category,
                        march_phrase.english_cue,
                        march_phrase.expression,
                        march_phrase.example,
                        march_phrase.note,
                    ),
                    (
                        february_phrase.category,
                        february_phrase.english_cue,
                        february_phrase.expression,
                        february_phrase.example,
                        february_phrase.note,
                    ),
                )
        for index in range(1, 31):
            february_phrase = phrase_for_slot("T2F2S29", index)
            april_phrase = phrase_for_slot("T2A4S6", index)
            self.assertEqual(
                (
                    april_phrase.category,
                    april_phrase.english_cue,
                    april_phrase.expression,
                    april_phrase.example,
                    april_phrase.note,
                ),
                (
                    february_phrase.category,
                    february_phrase.english_cue,
                    february_phrase.expression,
                    february_phrase.example,
                    february_phrase.note,
                ),
            )
        for source_prefix, may_prefix in (
            ("T2F2S16", "T2M5S1"),
            ("T2F2S17", "T2M5S2"),
            ("T2F2S18", "T2M5S3"),
            ("T2F2S19", "T2M5S4"),
            ("T2F2S20", "T2M5S5"),
            ("T2J1S11", "T2M5S11"),
            ("T2J1S12", "T2M5S12"),
            ("T2J1S13", "T2M5S13"),
            ("T2J1S14", "T2M5S14"),
            ("T2F2S11", "T2M5S16"),
        ):
            for index in range(1, 31):
                source_phrase = phrase_for_slot(source_prefix, index)
                may_phrase = phrase_for_slot(may_prefix, index)
                self.assertEqual(
                    (
                        may_phrase.category,
                        may_phrase.english_cue,
                        may_phrase.expression,
                        may_phrase.example,
                        may_phrase.note,
                    ),
                    (
                        source_phrase.category,
                        source_phrase.english_cue,
                        source_phrase.expression,
                        source_phrase.example,
                        source_phrase.note,
                    ),
                )
        for source_id, may_id in (
            ("T2A4S8V09", "T2M5S25V05"),
            ("T2A4S8V19", "T2M5S25V13"),
            ("T2A4S8V28", "T2M5S25V23"),
            ("T2J1S5V17", "T2M5S25V19"),
            ("T2J1S5V28", "T2M5S25V28"),
        ):
            source_phrase = vocabulary_by_id[source_id]
            may_phrase = vocabulary_by_id[may_id]
            self.assertEqual(
                (
                    may_phrase.category,
                    may_phrase.english_cue,
                    may_phrase.expression,
                    may_phrase.example,
                    may_phrase.note,
                ),
                (
                    source_phrase.category,
                    source_phrase.english_cue,
                    source_phrase.expression,
                    source_phrase.example,
                    source_phrase.note,
                ),
            )
        for source_prefix, june_prefix in (
            ("T2M3S12", "T2J6S7"),
            ("T2M3S10", "T2J6S8"),
            ("T2J1S13", "T2J6S19"),
            ("T2J1S6", "T2J6S21"),
            ("T2J1S7", "T2J6S22"),
            ("T2J1S8", "T2J6S23"),
            ("T2J1S9", "T2J6S24"),
            ("T2J1S10", "T2J6S25"),
            ("T2F2S26", "T2J6S31"),
            ("T2F2S27", "T2J6S32"),
            ("T2F2S28", "T2J6S33"),
            ("T2F2S29", "T2J6S34"),
            ("T2F2S30", "T2J6S35"),
            ("T2J6S1", "T2J6S36"),
            ("T2J6S2", "T2J6S37"),
            ("T2J6S3", "T2J6S38"),
            ("T2J6S4", "T2J6S39"),
            ("T2J6S5", "T2J6S40"),
            ("T2A4S1", "T2J6S41"),
            ("T2A4S2", "T2J6S42"),
            ("T2A4S3", "T2J6S43"),
            ("T2A4S4", "T2J6S44"),
            ("T2A4S5", "T2J6S45"),
        ):
            for index in range(1, 31):
                source_phrase = phrase_for_slot(source_prefix, index)
                june_phrase = phrase_for_slot(june_prefix, index)
                self.assertEqual(
                    (
                        june_phrase.category,
                        june_phrase.english_cue,
                        june_phrase.expression,
                        june_phrase.example,
                        june_phrase.note,
                    ),
                    (
                        source_phrase.category,
                        source_phrase.english_cue,
                        source_phrase.expression,
                        source_phrase.example,
                        source_phrase.note,
                    ),
                )
        for source_prefix, july_prefix in (
            ("T2J6S2", "T2J7S1"),
            ("T2A4S3", "T2J7S5"),
            ("T2M5S1", "T2J7S11"),
            ("T2M5S2", "T2J7S12"),
            ("T2F2S18", "T2J7S13"),
            ("T2F2S19", "T2J7S14"),
            ("T2F2S20", "T2J7S15"),
            ("T2J7S16", "T2J7S21"),
            ("T2J7S17", "T2J7S22"),
            ("T2J7S18", "T2J7S23"),
            ("T2J7S19", "T2J7S24"),
            ("T2J7S20", "T2J7S25"),
            ("T2J6S18", "T2J7S26"),
            ("T2A4S7", "T2J7S27"),
            ("T2J1S15", "T2J7S28"),
            ("T2M3S12", "T2J7S35"),
            ("T2M3S10", "T2J7S36"),
            ("T2J6S9", "T2J7S37"),
        ):
            for index in range(1, 31):
                source_phrase = phrase_for_slot(source_prefix, index)
                july_phrase = phrase_for_slot(july_prefix, index)
                self.assertEqual(
                    (
                        july_phrase.category,
                        july_phrase.english_cue,
                        july_phrase.expression,
                        july_phrase.example,
                        july_phrase.note,
                    ),
                    (
                        source_phrase.category,
                        source_phrase.english_cue,
                        source_phrase.expression,
                        source_phrase.example,
                        source_phrase.note,
                    ),
                )
        for source_prefix, august_prefix in (
            ("T2J6S1", "T2A8S1"),
            ("T2J6S2", "T2A8S2"),
            ("T2J6S3", "T2A8S3"),
            ("T2J6S4", "T2A8S4"),
            ("T2J6S5", "T2A8S5"),
            ("T2M5S7", "T2A8S7"),
            ("T2M5S10", "T2A8S8"),
            ("T2M5S9", "T2A8S9"),
            ("T2M5S6", "T2A8S11"),
            ("T2M5S7", "T2A8S12"),
            ("T2M5S8", "T2A8S13"),
            ("T2M5S9", "T2A8S14"),
            ("T2M5S10", "T2A8S15"),
            ("T2J1S11", "T2A8S16"),
            ("T2J1S12", "T2A8S17"),
            ("T2J1S13", "T2A8S18"),
            ("T2J1S14", "T2A8S19"),
            ("T2M5S15", "T2A8S20"),
        ):
            for index in range(1, 31):
                source_phrase = phrase_for_slot(source_prefix, index)
                august_phrase = phrase_for_slot(august_prefix, index)
                self.assertEqual(
                    (
                        august_phrase.category,
                        august_phrase.english_cue,
                        august_phrase.expression,
                        august_phrase.example,
                        august_phrase.note,
                    ),
                    (
                        source_phrase.category,
                        source_phrase.english_cue,
                        source_phrase.expression,
                        source_phrase.example,
                        source_phrase.note,
                    ),
                )
        for source_prefix, september_prefix in (
            ("T2J7S4", "T2S9S1"),
            ("T2F2S26", "T2S9S6"),
            ("T2F2S27", "T2S9S7"),
            ("T2F2S28", "T2S9S8"),
            ("T2F2S29", "T2S9S9"),
            ("T2F2S30", "T2S9S10"),
            ("T2M3S11", "T2S9S11"),
            ("T2M3S12", "T2S9S12"),
            ("T2M3S13", "T2S9S13"),
            ("T2M3S14", "T2S9S14"),
            ("T2F2S25", "T2S9S15"),
            ("T2F2S12", "T2S9S16"),
            ("T2J7S34", "T2S9S21"),
            ("T2J1S13", "T2S9S24"),
            ("T2J7S4", "T2S9S25"),
            ("T2J7S34", "T2S9S26"),
            ("T2J1S13", "T2S9S29"),
            ("T2J7S4", "T2S9S30"),
            ("T2S9S3", "T2S9S31"),
            ("T2S9S4", "T2S9S33"),
            ("T2S9S22", "T2S9S27"),
            ("T2S9S23", "T2S9S28"),
        ):
            for index in range(1, 31):
                source_phrase = phrase_for_slot(source_prefix, index)
                september_phrase = phrase_for_slot(
                    september_prefix,
                    index,
                )
                self.assertEqual(
                    (
                        september_phrase.category,
                        september_phrase.english_cue,
                        september_phrase.expression,
                        september_phrase.example,
                        september_phrase.note,
                    ),
                    (
                        source_phrase.category,
                        source_phrase.english_cue,
                        source_phrase.expression,
                        source_phrase.example,
                        source_phrase.note,
                    ),
                )
        for (
            target_month,
            target_subject,
            source_month,
            _source_batch,
            source_subject,
        ) in FINAL_EXACT_RESPONSE_REUSE:
            source_prefix = (
                f"T2{VOCABULARY_MONTH_PREFIXES[source_month]}"
                f"S{source_subject}"
            )
            target_prefix = (
                f"T2{VOCABULARY_MONTH_PREFIXES[target_month]}"
                f"S{target_subject}"
            )
            for index in range(1, 31):
                source_phrase = phrase_for_slot(source_prefix, index)
                target_phrase = phrase_for_slot(target_prefix, index)
                self.assertEqual(
                    (
                        target_phrase.category,
                        target_phrase.english_cue,
                        target_phrase.expression,
                        target_phrase.example,
                        target_phrase.note,
                    ),
                    (
                        source_phrase.category,
                        source_phrase.english_cue,
                        source_phrase.expression,
                        source_phrase.example,
                        source_phrase.note,
                    ),
                )
        first_vocabulary_order = max(comprehension_orders) + 1
        self.assertTrue(
            comprehension_orders.isdisjoint(vocabulary_orders)
        )
        self.assertEqual(
            vocabulary_orders,
            list(
                range(
                    max(comprehension_orders) + 1,
                    max(comprehension_orders) + len(vocabulary) + 1,
                )
            ),
        )
        self.assertEqual(
            vocabulary_by_id["T2J1S1V01"].order,
            first_vocabulary_order,
        )
        self.assertEqual(
            vocabulary_by_id["T2J1S15V30"].order,
            first_vocabulary_order + 449,
        )
        self.assertEqual(
            vocabulary_by_id["T2F2S1V01"].order,
            first_vocabulary_order + 450,
        )
        self.assertEqual(
            vocabulary_by_id["T2F2S30V30"].order,
            first_vocabulary_order + 1349,
        )
        self.assertEqual(
            vocabulary_by_id["T2M3S1V01"].order,
            first_vocabulary_order + 1350,
        )
        self.assertEqual(
            vocabulary_by_id["T2M3S10V30"].order,
            first_vocabulary_order + 1649,
        )
        self.assertEqual(
            vocabulary_by_id["T2M3S15V30"].order,
            first_vocabulary_order + 1799,
        )
        self.assertEqual(
            vocabulary_by_id["T2A4S1V01"].order,
            first_vocabulary_order + 1800,
        )
        self.assertEqual(
            vocabulary_by_id["T2A4S10V30"].order,
            first_vocabulary_order + 2099,
        )
        self.assertEqual(
            vocabulary_by_id["T2M5S1V01"].order,
            first_vocabulary_order + 2100,
        )
        self.assertEqual(
            vocabulary_by_id["T2M5S25V30"].order,
            first_vocabulary_order + 2849,
        )
        self.assertEqual(
            vocabulary_by_id["T2J6S1V01"].order,
            first_vocabulary_order + 2850,
        )
        self.assertEqual(
            vocabulary_by_id["T2J6S45V30"].order,
            first_vocabulary_order + 4199,
        )
        self.assertEqual(
            vocabulary_by_id["T2J7S1V01"].order,
            first_vocabulary_order + 4200,
        )
        self.assertEqual(
            vocabulary_by_id["T2J7S38V30"].order,
            first_vocabulary_order + 5339,
        )
        self.assertEqual(
            vocabulary_by_id["T2A8S1V01"].order,
            first_vocabulary_order + 5340,
        )
        self.assertEqual(
            vocabulary_by_id["T2A8S20V30"].order,
            first_vocabulary_order + 5939,
        )
        self.assertEqual(
            vocabulary_by_id["T2S9S1V01"].order,
            first_vocabulary_order + 5940,
        )
        self.assertEqual(
            vocabulary_by_id["T2S9S35V30"].order,
            first_vocabulary_order + 6989,
        )
        self.assertEqual(
            vocabulary_by_id["T2O10S1V01"].order,
            first_vocabulary_order + 6990,
        )
        self.assertEqual(
            vocabulary_by_id["T2O10S50V30"].order,
            first_vocabulary_order + 8489,
        )
        self.assertEqual(
            vocabulary_by_id["T2N11S1V01"].order,
            first_vocabulary_order + 8490,
        )
        self.assertEqual(
            vocabulary_by_id["T2N11S15V30"].order,
            first_vocabulary_order + 8939,
        )
        self.assertEqual(
            vocabulary_by_id["T2D12S1V01"].order,
            first_vocabulary_order + 8940,
        )
        self.assertEqual(
            vocabulary_by_id["T2D12S50V30"].order,
            first_vocabulary_order + 10439,
        )


class QuestionBankViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("import_content", stdout=StringIO())
        cls.user = factories.make_user("question-bank")
        provision_user_study_data(cls.user)
        cls.task = Task.objects.select_related("part").get(
            part__slug="eo",
            slug="tache-2",
        )

    def setUp(self):
        self.client.force_login(self.user)

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
        self.assertEqual(response.context["memory_count"], 6)
        self.assertEqual(response.context["subject_count"], 348)
        self.assertEqual(response.context["category_count"], 46)
        self.assertEqual(response.context["question_count"], 190)
        self.assertContains(
            response,
            "data-tache-two-overview-panel",
            count=2,
        )
        self.assertContains(
            response,
            'id="memory-overview-panel-title">Mémoires</h2>',
        )
        self.assertContains(
            response,
            'id="subject-overview-panel-title">Sujets</h2>',
        )
        self.assertContains(response, "0/190 questions apprises")
        self.assertContains(response, "0/348 sujets terminés")
        self.assertContains(
            response,
            reverse(
                "study:task_memories",
                args=[self.task.part.slug, self.task.slug],
            ),
        )
        self.assertContains(
            response,
            reverse(
                "study:task_browse",
                args=[self.task.part.slug, self.task.slug],
            ),
        )
        self.assertNotContains(response, "data-collection-view-toggle")
        self.assertNotContains(response, "data-collection-item")
        self.assertNotContains(response, "data-tache-two-subject-month")
        self.assertNotContains(response, "Janvier · Batch 1")
        self.assertNotContains(response, "memory-entry")
        self.assertNotContains(response, "data-question-bank-question")
        self.assertNotContains(response, "Sujets &amp; réponses")
        self.assertNotContains(response, "Réflexe Mémoire")
        self.assertNotContains(response, ">Pratiquer</a>")

    def test_memories_have_a_dedicated_tab(self):
        url = reverse(
            "study:task_memories",
            args=[self.task.part.slug, self.task.slug],
        )
        response = self.client.get(url)

        self.assertEqual(url, "/expression/orale/tache-2/memoires/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "study/tache_two_memories.html")
        self.assertEqual(response.context["memory_count"], 6)
        self.assertEqual(response.context["category_count"], 46)
        self.assertEqual(response.context["question_count"], 190)
        self.assertContains(response, "<span>Mémoires</span>", html=True)
        self.assertContains(response, "data-collection-view-toggle")
        self.assertContains(response, 'data-collection-view="adaptive"')
        self.assertContains(response, "collection-table-header--memories")
        self.assertContains(response, "data-collection-item", count=6)
        self.assertContains(response, "Mémoire 1")
        self.assertContains(response, "Mémoire 2")
        self.assertContains(response, "Mémoire 3")
        self.assertContains(response, "Mémoire 4")
        self.assertContains(response, "Mémoire 5")
        self.assertContains(response, "Mémoire 6")
        self.assertContains(
            response,
            reverse(
                "study:task_memory_detail",
                args=[self.task.part.slug, self.task.slug, 1],
            ),
        )
        self.assertContains(
            response,
            reverse(
                "study:task_memory_detail",
                args=[self.task.part.slug, self.task.slug, 2],
            ),
        )
        self.assertContains(
            response,
            reverse(
                "study:task_memory_detail",
                args=[self.task.part.slug, self.task.slug, 3],
            ),
        )
        self.assertContains(
            response,
            reverse(
                "study:task_memory_detail",
                args=[self.task.part.slug, self.task.slug, 4],
            ),
        )
        self.assertContains(
            response,
            reverse(
                "study:task_memory_detail",
                args=[self.task.part.slug, self.task.slug, 5],
            ),
        )
        self.assertContains(
            response,
            reverse(
                "study:task_memory_detail",
                args=[self.task.part.slug, self.task.slug, 6],
            ),
        )
        self.assertContains(
            response,
            '<a class="is-active" href="'
            + reverse(
                "study:task_memories",
                args=[self.task.part.slug, self.task.slug],
            )
            + '">Mémoires</a>',
            html=True,
        )
        self.assertNotContains(response, "data-question-bank-question")
        self.assertNotContains(response, "data-tache-two-subject-month")

    def test_subjects_are_grouped_by_month_and_batch(self):
        index_url = reverse(
            "study:task_browse",
            args=[self.task.part.slug, self.task.slug],
        )
        batch_url = reverse(
            "study:task_subject_batch",
            args=[self.task.part.slug, self.task.slug, "janvier", 1],
        )
        second_batch_url = reverse(
            "study:task_subject_batch",
            args=[self.task.part.slug, self.task.slug, "janvier", 2],
        )
        third_batch_url = reverse(
            "study:task_subject_batch",
            args=[self.task.part.slug, self.task.slug, "janvier", 3],
        )
        february_batch_url = reverse(
            "study:task_subject_batch",
            args=[self.task.part.slug, self.task.slug, "fevrier", 6],
        )
        march_batch_url = reverse(
            "study:task_subject_batch",
            args=[self.task.part.slug, self.task.slug, "mars", 1],
        )
        march_second_batch_url = reverse(
            "study:task_subject_batch",
            args=[self.task.part.slug, self.task.slug, "mars", 2],
        )
        march_third_batch_url = reverse(
            "study:task_subject_batch",
            args=[self.task.part.slug, self.task.slug, "mars", 3],
        )
        april_batch_url = reverse(
            "study:task_subject_batch",
            args=[self.task.part.slug, self.task.slug, "avril", 1],
        )
        april_second_batch_url = reverse(
            "study:task_subject_batch",
            args=[self.task.part.slug, self.task.slug, "avril", 2],
        )
        may_batch_url = reverse(
            "study:task_subject_batch",
            args=[self.task.part.slug, self.task.slug, "mai", 5],
        )
        june_batch_url = reverse(
            "study:task_subject_batch",
            args=[self.task.part.slug, self.task.slug, "juin", 9],
        )
        july_batch_url = reverse(
            "study:task_subject_batch",
            args=[self.task.part.slug, self.task.slug, "juillet", 8],
        )
        august_batch_url = reverse(
            "study:task_subject_batch",
            args=[self.task.part.slug, self.task.slug, "aout", 4],
        )
        september_batch_url = reverse(
            "study:task_subject_batch",
            args=[self.task.part.slug, self.task.slug, "septembre", 7],
        )
        subject_url = reverse(
            "study:task_subject_detail",
            args=[
                self.task.part.slug,
                self.task.slug,
                "janvier",
                1,
                1,
            ],
        )
        second_batch_subject_url = reverse(
            "study:task_subject_detail",
            args=[
                self.task.part.slug,
                self.task.slug,
                "janvier",
                2,
                6,
            ],
        )
        third_batch_subject_url = reverse(
            "study:task_subject_detail",
            args=[
                self.task.part.slug,
                self.task.slug,
                "janvier",
                3,
                11,
            ],
        )
        february_subject_url = reverse(
            "study:task_subject_detail",
            args=[
                self.task.part.slug,
                self.task.slug,
                "fevrier",
                6,
                26,
            ],
        )
        march_subject_url = reverse(
            "study:task_subject_detail",
            args=[
                self.task.part.slug,
                self.task.slug,
                "mars",
                1,
                5,
            ],
        )
        march_second_subject_url = reverse(
            "study:task_subject_detail",
            args=[
                self.task.part.slug,
                self.task.slug,
                "mars",
                2,
                10,
            ],
        )
        march_third_subject_url = reverse(
            "study:task_subject_detail",
            args=[
                self.task.part.slug,
                self.task.slug,
                "mars",
                3,
                11,
            ],
        )
        april_subject_url = reverse(
            "study:task_subject_detail",
            args=[
                self.task.part.slug,
                self.task.slug,
                "avril",
                1,
                1,
            ],
        )
        april_second_subject_url = reverse(
            "study:task_subject_detail",
            args=[
                self.task.part.slug,
                self.task.slug,
                "avril",
                2,
                6,
            ],
        )
        may_subject_url = reverse(
            "study:task_subject_detail",
            args=[
                self.task.part.slug,
                self.task.slug,
                "mai",
                5,
                25,
            ],
        )
        june_subject_url = reverse(
            "study:task_subject_detail",
            args=[
                self.task.part.slug,
                self.task.slug,
                "juin",
                9,
                45,
            ],
        )
        july_subject_url = reverse(
            "study:task_subject_detail",
            args=[
                self.task.part.slug,
                self.task.slug,
                "juillet",
                8,
                38,
            ],
        )
        august_subject_url = reverse(
            "study:task_subject_detail",
            args=[
                self.task.part.slug,
                self.task.slug,
                "aout",
                4,
                20,
            ],
        )
        september_subject_url = reverse(
            "study:task_subject_detail",
            args=[
                self.task.part.slug,
                self.task.slug,
                "septembre",
                7,
                32,
            ],
        )
        final_route_data = (
            (
                "octobre",
                "Octobre",
                10,
                50,
                "Apéritif de bienvenue – Québec",
                15,
            ),
            (
                "novembre",
                "Novembre",
                3,
                15,
                "Louer son appartement pour les vacances",
                15,
            ),
            (
                "decembre",
                "Décembre",
                10,
                50,
                "Découvrir la ville avec des amis – Office de tourisme",
                15,
            ),
        )
        final_routes = [
            (
                month_name,
                batch_number,
                subject_number,
                title,
                question_count,
                reverse(
                    "study:task_subject_batch",
                    args=[
                        self.task.part.slug,
                        self.task.slug,
                        month_slug,
                        batch_number,
                    ],
                ),
                reverse(
                    "study:task_subject_detail",
                    args=[
                        self.task.part.slug,
                        self.task.slug,
                        month_slug,
                        batch_number,
                        subject_number,
                    ],
                ),
            )
            for (
                month_slug,
                month_name,
                batch_number,
                subject_number,
                title,
                question_count,
            ) in final_route_data
        ]

        index = self.client.get(index_url)
        self.assertEqual(index.status_code, 200)
        self.assertTemplateUsed(index, "study/tache_two_subjects.html")
        self.assertEqual(index.context["month_count"], 12)
        self.assertEqual(index.context["batch_count"], 70)
        self.assertEqual(index.context["subject_count"], 348)
        self.assertEqual(index.context["question_count"], 5175)
        self.assertNotContains(index, "Réflexe Mémoire")
        self.assertContains(index, "Janvier")
        self.assertContains(index, "Batch 01")
        self.assertContains(index, "Batch 02")
        self.assertContains(index, "Batch 03")
        self.assertContains(index, "Février")
        self.assertContains(index, "Batch 06")
        self.assertContains(index, "Mars")
        self.assertContains(index, "Avril")
        self.assertContains(index, "Mai")
        self.assertContains(index, "Juin")
        self.assertContains(index, "Juillet")
        self.assertContains(index, "Août")
        self.assertContains(index, "Septembre")
        self.assertContains(index, "Octobre")
        self.assertContains(index, "Novembre")
        self.assertContains(index, "Décembre")
        self.assertContains(index, "data-tache-two-subject-batch", count=70)
        self.assertContains(index, "subject-batch-card--new", count=70)
        self.assertContains(index, "0/15 sujets terminés")
        self.assertContains(index, "0/30 sujets terminés")
        self.assertContains(index, "0/5 sujets terminés")
        self.assertContains(index, "0/10 sujets terminés")
        self.assertContains(index, "0/25 sujets terminés")
        self.assertContains(index, "0/45 sujets terminés")
        self.assertContains(index, "0/38 sujets terminés")
        self.assertContains(index, "0/20 sujets terminés")
        self.assertContains(index, "0/35 sujets terminés")
        self.assertContains(index, "0/50 sujets terminés")
        self.assertContains(index, batch_url)
        self.assertContains(index, second_batch_url)
        self.assertContains(index, third_batch_url)
        self.assertContains(index, february_batch_url)
        self.assertContains(index, march_batch_url)
        self.assertContains(index, march_second_batch_url)
        self.assertContains(index, march_third_batch_url)
        self.assertContains(index, april_batch_url)
        self.assertContains(index, april_second_batch_url)
        self.assertContains(index, may_batch_url)
        self.assertContains(index, june_batch_url)
        self.assertContains(index, july_batch_url)
        self.assertContains(index, august_batch_url)
        self.assertContains(index, september_batch_url)
        for *_, batch_route, subject_route in final_routes:
            self.assertContains(index, batch_route)
            self.assertContains(self.client.get(batch_route), subject_route)

        batch = self.client.get(batch_url)
        self.assertEqual(batch.status_code, 200)
        self.assertTemplateUsed(
            batch,
            "study/tache_two_subject_batch.html",
        )
        self.assertContains(batch, "Janvier · Batch 1")
        self.assertContains(batch, "data-tache-two-subject", count=5)
        self.assertContains(batch, "tache-two-subject-card--new", count=5)
        self.assertEqual(batch.context["subject_batch"]["progress"].status, "new")
        self.assertContains(batch, subject_url)

        subject = self.client.get(subject_url)
        self.assertEqual(subject.status_code, 200)
        self.assertTemplateUsed(
            subject,
            "study/tache_two_subject_detail.html",
        )
        self.assertContains(
            subject,
            "Achat d&#x27;objets avant un déménagement",
        )
        self.assertContains(subject, "data-tache-two-question", count=14)
        self.assertNotContains(subject, "tache-two-question__memory")
        self.assertNotContains(subject, "Réflexe Mémoire")
        self.assertContains(subject, "Progression du sujet")
        self.assertContains(subject, "Pratiquer ce sujet")
        self.assertContains(subject, "Pratiquer les vocabs")
        self.assertContains(subject, "30 vocabs")
        self.assertEqual(len(subject.context["vocabulary_batches"]), 3)
        self.assertTrue(
            all(
                batch["phrase_count"] == 10
                for batch in subject.context["vocabulary_batches"]
            )
        )
        self.assertEqual(len(subject.context["subject_vocabulary"]), 10)
        self.assertEqual(
            response_detail_url(subject.context["response"]),
            subject_url,
        )
        generic_url = reverse(
            "study:response_detail",
            args=[
                self.task.part.slug,
                self.task.slug,
                subject.context["selected_prompt"].pk,
            ],
        )
        self.assertRedirects(
            self.client.get(generic_url),
            subject_url,
            fetch_redirect_response=False,
        )
        self.assertContains(
            subject,
            "Merci pour toutes ces infos",
        )
        self.assertContains(
            subject,
            "data-annotation-source-key="
            '"tache-two:janvier:batch-1:subject-1"',
        )

        second_batch = self.client.get(second_batch_url)
        self.assertEqual(second_batch.status_code, 200)
        self.assertContains(second_batch, "Janvier · Batch 2")
        self.assertContains(
            second_batch,
            "data-tache-two-subject",
            count=5,
        )
        self.assertContains(second_batch, second_batch_subject_url)

        second_batch_subject = self.client.get(second_batch_subject_url)
        self.assertEqual(second_batch_subject.status_code, 200)
        self.assertContains(
            second_batch_subject,
            "Séances de yoga pour les employés",
        )
        self.assertContains(
            second_batch_subject,
            "data-tache-two-question",
            count=14,
        )
        self.assertContains(second_batch_subject, "30 vocabs")
        self.assertEqual(
            len(second_batch_subject.context["vocabulary_batches"]),
            3,
        )

        third_batch = self.client.get(third_batch_url)
        self.assertEqual(third_batch.status_code, 200)
        self.assertContains(third_batch, "Janvier · Batch 3")
        self.assertContains(
            third_batch,
            "data-tache-two-subject",
            count=5,
        )
        self.assertContains(third_batch, third_batch_subject_url)

        third_batch_subject = self.client.get(third_batch_subject_url)
        self.assertEqual(third_batch_subject.status_code, 200)
        self.assertContains(
            third_batch_subject,
            "Nouveau dans l&#x27;entreprise",
        )
        self.assertContains(
            third_batch_subject,
            "data-tache-two-question",
            count=15,
        )
        self.assertContains(third_batch_subject, "30 vocabs")
        self.assertEqual(
            len(third_batch_subject.context["vocabulary_batches"]),
            3,
        )

        february_batch = self.client.get(february_batch_url)
        self.assertEqual(february_batch.status_code, 200)
        self.assertContains(february_batch, "Février · Batch 6")
        self.assertContains(
            february_batch,
            "data-tache-two-subject",
            count=5,
        )
        self.assertContains(february_batch, february_subject_url)

        february_subject = self.client.get(february_subject_url)
        self.assertEqual(february_subject.status_code, 200)
        self.assertContains(
            february_subject,
            "Garde d&#x27;enfant pendant un week-end",
        )
        self.assertContains(
            february_subject,
            "data-tache-two-question",
            count=15,
        )
        self.assertContains(february_subject, "30 vocabs")
        self.assertEqual(
            len(february_subject.context["vocabulary_batches"]),
            3,
        )

        march_batch = self.client.get(march_batch_url)
        self.assertEqual(march_batch.status_code, 200)
        self.assertContains(march_batch, "Mars · Batch 1")
        self.assertContains(
            march_batch,
            "data-tache-two-subject",
            count=5,
        )
        self.assertContains(march_batch, march_subject_url)

        march_subject = self.client.get(march_subject_url)
        self.assertEqual(march_subject.status_code, 200)
        self.assertContains(
            march_subject,
            "Achat d&#x27;une voiture d&#x27;occasion",
        )
        self.assertContains(
            march_subject,
            "data-tache-two-question",
            count=15,
        )
        self.assertContains(march_subject, "30 vocabs")
        self.assertEqual(
            len(march_subject.context["vocabulary_batches"]),
            3,
        )

        march_second_batch = self.client.get(march_second_batch_url)
        self.assertEqual(march_second_batch.status_code, 200)
        self.assertContains(march_second_batch, "Mars · Batch 2")
        self.assertContains(
            march_second_batch,
            "data-tache-two-subject",
            count=5,
        )
        self.assertContains(
            march_second_batch,
            march_second_subject_url,
        )

        march_second_subject = self.client.get(march_second_subject_url)
        self.assertEqual(march_second_subject.status_code, 200)
        self.assertContains(
            march_second_subject,
            "Transports en commun dans une ville",
        )
        self.assertContains(
            march_second_subject,
            "data-tache-two-question",
            count=14,
        )
        self.assertContains(march_second_subject, "30 vocabs")
        self.assertEqual(
            len(march_second_subject.context["vocabulary_batches"]),
            3,
        )

        march_third_batch = self.client.get(march_third_batch_url)
        self.assertEqual(march_third_batch.status_code, 200)
        self.assertContains(march_third_batch, "Mars · Batch 3")
        self.assertContains(
            march_third_batch,
            "data-tache-two-subject",
            count=5,
        )
        self.assertContains(march_third_batch, march_third_subject_url)

        march_third_subject = self.client.get(march_third_subject_url)
        self.assertEqual(march_third_subject.status_code, 200)
        self.assertContains(
            march_third_subject,
            "Présentation d&#x27;un film",
        )
        self.assertContains(
            march_third_subject,
            "data-tache-two-question",
            count=15,
        )
        self.assertContains(march_third_subject, "30 vocabs")

        april_batch = self.client.get(april_batch_url)
        self.assertEqual(april_batch.status_code, 200)
        self.assertContains(april_batch, "Avril · Batch 1")
        self.assertContains(
            april_batch,
            "data-tache-two-subject",
            count=5,
        )
        self.assertContains(april_batch, april_subject_url)

        april_subject = self.client.get(april_subject_url)
        self.assertEqual(april_subject.status_code, 200)
        self.assertContains(
            april_subject,
            "Nouveau centre sportif de la ville",
        )
        self.assertContains(
            april_subject,
            "data-tache-two-question",
            count=15,
        )
        self.assertContains(april_subject, "30 vocabs")
        self.assertEqual(
            len(april_subject.context["vocabulary_batches"]),
            3,
        )

        april_second_batch = self.client.get(april_second_batch_url)
        self.assertEqual(april_second_batch.status_code, 200)
        self.assertContains(april_second_batch, "Avril · Batch 2")
        self.assertContains(
            april_second_batch,
            "data-tache-two-subject",
            count=5,
        )
        self.assertContains(april_second_batch, april_second_subject_url)

        april_second_subject = self.client.get(april_second_subject_url)
        self.assertEqual(april_second_subject.status_code, 200)
        self.assertContains(
            april_second_subject,
            "Vacances au Canada",
        )
        self.assertContains(
            april_second_subject,
            "data-tache-two-question",
            count=15,
        )
        self.assertContains(april_second_subject, "30 vocabs")

        may_batch = self.client.get(may_batch_url)
        self.assertEqual(may_batch.status_code, 200)
        self.assertContains(may_batch, "Mai · Batch 5")
        self.assertContains(
            may_batch,
            "data-tache-two-subject",
            count=6,
        )
        self.assertContains(may_batch, may_subject_url)

        may_subject = self.client.get(may_subject_url)
        self.assertEqual(may_subject.status_code, 200)
        self.assertContains(
            may_subject,
            "Garde d&#x27;un enfant le week-end",
        )
        self.assertContains(
            may_subject,
            "data-tache-two-question",
            count=15,
        )
        self.assertContains(may_subject, "30 vocabs")

        june_batch = self.client.get(june_batch_url)
        self.assertEqual(june_batch.status_code, 200)
        self.assertContains(june_batch, "Juin · Batch 9")
        self.assertContains(
            june_batch,
            "data-tache-two-subject",
            count=5,
        )
        self.assertContains(june_batch, june_subject_url)

        june_subject = self.client.get(june_subject_url)
        self.assertEqual(june_subject.status_code, 200)
        self.assertContains(
            june_subject,
            "École de langues – Renseignements",
        )
        self.assertContains(
            june_subject,
            "data-tache-two-question",
            count=15,
        )
        self.assertContains(june_subject, "30 vocabs")

        july_batch = self.client.get(july_batch_url)
        self.assertEqual(july_batch.status_code, 200)
        self.assertContains(july_batch, "Juillet · Batch 8")
        self.assertContains(
            july_batch,
            "data-tache-two-subject",
            count=5,
        )
        self.assertContains(july_batch, july_subject_url)

        july_subject = self.client.get(july_subject_url)
        self.assertEqual(july_subject.status_code, 200)
        self.assertContains(july_subject, "Choisir une nouvelle série")
        self.assertContains(
            july_subject,
            "data-tache-two-question",
            count=15,
        )
        self.assertContains(july_subject, "30 vocabs")

        august_batch = self.client.get(august_batch_url)
        self.assertEqual(august_batch.status_code, 200)
        self.assertContains(august_batch, "Août · Batch 4")
        self.assertContains(
            august_batch,
            "data-tache-two-subject",
            count=5,
        )
        self.assertContains(august_batch, august_subject_url)

        august_subject = self.client.get(august_subject_url)
        self.assertEqual(august_subject.status_code, 200)
        self.assertContains(
            august_subject,
            "Ville à visiter le temps d&#x27;un week-end",
        )
        self.assertContains(
            august_subject,
            "data-tache-two-question",
            count=15,
        )
        self.assertContains(august_subject, "30 vocabs")

        september_batch = self.client.get(september_batch_url)
        self.assertEqual(september_batch.status_code, 200)
        self.assertContains(september_batch, "Septembre · Batch 7")
        self.assertContains(
            september_batch,
            "data-tache-two-subject",
            count=5,
        )
        self.assertContains(september_batch, september_subject_url)

        september_subject = self.client.get(september_subject_url)
        self.assertEqual(september_subject.status_code, 200)
        self.assertContains(september_subject, "Nouvel emploi à Toronto")
        self.assertContains(
            september_subject,
            "data-tache-two-question",
            count=15,
        )
        self.assertContains(september_subject, "30 vocabs")
        for (
            month_name,
            batch_number,
            _subject_number,
            title,
            question_count,
            batch_route,
            subject_route,
        ) in final_routes:
            final_batch = self.client.get(batch_route)
            self.assertEqual(final_batch.status_code, 200)
            self.assertContains(
                final_batch,
                f"{month_name} · Batch {batch_number}",
            )
            self.assertContains(
                final_batch,
                "data-tache-two-subject",
                count=5,
            )
            self.assertContains(final_batch, subject_route)

            final_subject = self.client.get(subject_route)
            self.assertEqual(final_subject.status_code, 200)
            self.assertContains(final_subject, title)
            self.assertContains(
                final_subject,
                "data-tache-two-question",
                count=question_count,
            )
            self.assertContains(final_subject, "30 vocabs")
            self.assertEqual(
                sum(
                    batch["phrase_count"]
                    for batch in final_subject.context["vocabulary_batches"]
                ),
                30,
            )

    def test_import_provisions_real_subject_and_vocabulary_cards(self):
        responses = Response.objects.filter(
            content_key__startswith="tache2:",
            is_active=True,
        )
        response_ids = set(responses.values_list("pk", flat=True))
        vocabulary = Phrase.objects.filter(
            tier=PhraseTier.SUBJECT,
            source_prompts__response_id__in=response_ids,
            is_active=True,
        ).distinct()

        self.assertEqual(responses.count(), 348)
        self.assertEqual(vocabulary.count(), 10440)
        self.assertEqual(
            Card.objects.filter(
                user=self.user,
                card_type=CardType.SPINE,
                response_id__in=response_ids,
            ).count(),
            348,
        )
        self.assertEqual(
            Card.objects.filter(
                user=self.user,
                card_type=CardType.PHRASE_PRODUCTION,
                phrase__in=vocabulary,
            ).count(),
            10440,
        )

        directory = self.client.get(
            reverse(
                "study:task_phrases",
                args=[self.task.part.slug, self.task.slug],
            )
        )
        self.assertEqual(directory.status_code, 200)
        self.assertContains(directory, "Vocabulaire par sujet")
        self.assertContains(
            directory,
            "data-subject-vocabulary-row",
            count=348,
        )

    def test_existing_subject_highlight_marks_imported_response_in_progress(self):
        subject_url = reverse(
            "study:task_subject_detail",
            args=[
                self.task.part.slug,
                self.task.slug,
                "janvier",
                1,
                1,
            ],
        )
        Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.HIGHLIGHT,
            source_path=subject_url,
            source_key="tache-two:janvier:batch-1:subject-1",
            quote="Quels types d'objets",
            start_offset=0,
            end_offset=20,
        )

        subject = self.client.get(subject_url)
        batch = self.client.get(
            reverse(
                "study:task_subject_batch",
                args=[
                    self.task.part.slug,
                    self.task.slug,
                    "janvier",
                    1,
                ],
            )
        )
        directory = self.client.get(
            reverse(
                "study:task_browse",
                args=[self.task.part.slug, self.task.slug],
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

        self.assertEqual(subject.status_code, 200)
        self.assertTrue(subject.context["subject_progress"].has_highlight)
        self.assertEqual(subject.context["subject_progress"].status, "active")
        self.assertEqual(
            batch.context["subject_batch"]["subjects"][0]["progress"].status,
            "active",
        )
        self.assertContains(batch, "tache-two-subject-card--active", count=1)
        january = directory.context["subject_months"][0]
        self.assertEqual(january["progress"].status, "active")
        self.assertEqual(january["progress"].started, 1)
        self.assertEqual(january["batches"][0]["progress"].status, "active")
        self.assertContains(
            directory,
            "subject-batch-card--active",
            count=1,
        )
        self.assertEqual(
            overview.context["subject_summary"]["progress"].status,
            "active",
        )
        self.assertEqual(
            overview.context["subject_months"][0]["batches"][0][
                "progress"
            ].status,
            "active",
        )
        task_card = next(
            row
            for row in task_list.context["tasks"]
            if row["task"].pk == self.task.pk
        )
        self.assertEqual(
            task_card["question_bank"]["subject_progress"].status,
            "active",
        )
        self.assertEqual(
            task_card["question_bank"]["progress"].status,
            "active",
        )
        self.assertContains(task_list, "0/348 sujets terminés")

    def test_explicit_subject_completion_rolls_up_through_tache_two(self):
        response = Response.objects.get(
            content_key="tache2:janvier:batch-01:subject-01"
        )
        completion_url = reverse(
            "study:subject_completion",
            args=[self.task.part.slug, self.task.slug, response.pk],
        )

        completed = self.client.post(
            completion_url,
            {"completed": "1"},
            HTTP_X_REQUESTED_WITH="fetch",
        )
        batch = self.client.get(
            reverse(
                "study:task_subject_batch",
                args=[
                    self.task.part.slug,
                    self.task.slug,
                    "janvier",
                    1,
                ],
            )
        )
        directory = self.client.get(
            reverse(
                "study:task_browse",
                args=[self.task.part.slug, self.task.slug],
            )
        )

        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["subject"]["status"], "done")
        first_subject = batch.context["subject_batch"]["subjects"][0]
        self.assertEqual(first_subject["progress"].status, "done")
        self.assertTrue(first_subject["progress"].explicitly_completed)
        self.assertEqual(batch.context["subject_batch"]["completed"], 1)
        self.assertEqual(directory.context["subject_summary"]["completed"], 1)
        self.assertContains(batch, 'aria-checked="true"', count=1)

        cleared = self.client.post(
            completion_url,
            {"completed": "0"},
            HTTP_X_REQUESTED_WITH="fetch",
        )

        self.assertEqual(cleared.json()["subject"]["status"], "new")

    def test_unknown_subject_month_batch_and_number_are_not_found(self):
        route_args = [self.task.part.slug, self.task.slug]
        missing_month = self.client.get(
            reverse(
                "study:task_subject_batch",
                args=[*route_args, "inconnu", 1],
            )
        )
        missing_batch = self.client.get(
            reverse(
                "study:task_subject_batch",
                args=[*route_args, "janvier", 4],
            )
        )
        missing_subject = self.client.get(
            reverse(
                "study:task_subject_detail",
                args=[*route_args, "janvier", 1, 6],
            )
        )

        self.assertEqual(missing_month.status_code, 404)
        self.assertEqual(missing_batch.status_code, 404)
        self.assertEqual(missing_subject.status_code, 404)

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
        self.assertNotContains(response, "Questions réutilisables")
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
                args=[self.task.part.slug, self.task.slug, 7],
            )
        )
        unrelated = self.client.get(
            reverse(
                "study:task_memory_detail",
                args=[self.task.part.slug, "tache-3", 1],
            )
        )
        unrelated_index = self.client.get(
            reverse(
                "study:task_memories",
                args=[self.task.part.slug, "tache-3"],
            )
        )

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(unrelated.status_code, 404)
        self.assertEqual(unrelated_index.status_code, 404)

    def test_memoire_two_detail_and_progress_are_tracked_separately(self):
        memoire = load_question_banks()[1]
        detail = self.client.get(
            reverse(
                "study:task_memory_detail",
                args=[self.task.part.slug, self.task.slug, 2],
            )
        )

        self.assertEqual(detail.status_code, 200)
        self.assertTemplateUsed(detail, "study/question_bank.html")
        self.assertEqual(detail.context["question_bank"].number, 2)
        self.assertEqual(detail.context["question_bank"].question_count, 25)
        self.assertContains(detail, "Mémoire 2")
        self.assertContains(detail, "data-question-bank-section", count=5)
        self.assertContains(detail, "data-question-bank-question", count=25)
        self.assertContains(
            detail,
            "<span data-memory-completed>0</span> sur 25 questions apprises",
            html=True,
        )
        self.assertContains(
            detail,
            'data-annotation-source-key="question-bank:memory-02:part-01"',
        )

        checked = self.client.post(
            reverse(
                "study:task_memory_progress",
                args=[self.task.part.slug, self.task.slug, 2],
            ),
            {"question_key": memoire.question_keys[0], "completed": "1"},
            HTTP_X_REQUESTED_WITH="fetch",
        )
        self.assertEqual(checked.status_code, 200)
        self.assertEqual(checked.json()["memory"]["total"], 25)
        self.assertEqual(checked.json()["memory"]["completed"], 1)
        self.assertTrue(
            MemoryQuestionProgress.objects.filter(
                user=self.user,
                memory_number=2,
                question_key=memoire.question_keys[0],
            ).exists()
        )
        # Mémoire 1 progress is untouched by a Mémoire 2 check.
        self.assertFalse(
            MemoryQuestionProgress.objects.filter(
                user=self.user,
                memory_number=1,
            ).exists()
        )
        self.assertFalse(
            MemoryQuestionProgress.objects.filter(
                user=self.user,
                memory_number=3,
            ).exists()
        )
        self.assertFalse(
            MemoryQuestionProgress.objects.filter(
                user=self.user,
                memory_number=4,
            ).exists()
        )
        self.assertFalse(
            MemoryQuestionProgress.objects.filter(
                user=self.user,
                memory_number=5,
            ).exists()
        )
        self.assertFalse(
            MemoryQuestionProgress.objects.filter(
                user=self.user,
                memory_number=6,
            ).exists()
        )

    def test_memoire_three_detail_is_available(self):
        memoire = load_question_banks()[2]
        detail = self.client.get(
            reverse(
                "study:task_memory_detail",
                args=[self.task.part.slug, self.task.slug, 3],
            )
        )

        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.context["question_bank"], memoire)
        self.assertContains(detail, "Mémoire 3")
        self.assertContains(detail, "data-question-bank-section", count=5)
        self.assertContains(detail, "data-question-bank-question", count=25)
        self.assertContains(
            detail,
            'data-annotation-source-key="question-bank:memory-03:part-01"',
        )

    def test_memoire_four_detail_is_available(self):
        memoire = load_question_banks()[3]
        detail = self.client.get(
            reverse(
                "study:task_memory_detail",
                args=[self.task.part.slug, self.task.slug, 4],
            )
        )

        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.context["question_bank"], memoire)
        self.assertContains(detail, "Mémoire 4")
        self.assertContains(detail, "data-question-bank-section", count=5)
        self.assertContains(detail, "data-question-bank-question", count=25)
        self.assertContains(
            detail,
            'data-annotation-source-key="question-bank:memory-04:part-01"',
        )

    def test_memoires_five_and_six_details_are_available(self):
        for memory_number in (5, 6):
            memoire = load_question_banks()[memory_number - 1]
            detail = self.client.get(
                reverse(
                    "study:task_memory_detail",
                    args=[
                        self.task.part.slug,
                        self.task.slug,
                        memory_number,
                    ],
                )
            )

            self.assertEqual(detail.status_code, 200)
            self.assertEqual(detail.context["question_bank"], memoire)
            self.assertContains(detail, f"Mémoire {memory_number}")
            self.assertContains(
                detail,
                "data-question-bank-section",
                count=5,
            )
            self.assertContains(
                detail,
                "data-question-bank-question",
                count=25,
            )
            self.assertContains(
                detail,
                "data-memory-progress-form",
                count=25,
            )
            self.assertContains(
                detail,
                (
                    '<span data-memory-completed>0</span> '
                    "sur 25 questions apprises"
                ),
                html=True,
            )
            self.assertContains(
                detail,
                (
                    'data-annotation-source-key="question-bank:memory-'
                    f'{memory_number:02d}:part-01"'
                ),
            )

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
        self.assertContains(
            response,
            "348 sujets · 6 mémoires · 46 catégories · 190 questions",
        )
        self.assertContains(response, "0/190 apprises")
        self.assertContains(response, "0/348 sujets terminés")
        self.assertContains(response, "À commencer")
        task_card = next(
            row
            for row in response.context["tasks"]
            if row["task"].pk == self.task.pk
        )
        self.assertEqual(task_card["question_bank"]["progress"].total, 538)
        self.assertEqual(
            task_card["question_bank"]["subject_progress"].total,
            348,
        )

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
        self.assertContains(task_list, "1/190 apprises")
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
                    memory_number=memoire.number,
                    question_key=key,
                )
                for memoire in load_question_banks()
                for key in memoire.question_keys
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
        self.assertContains(overview, "190/190 questions apprises")
        self.assertContains(task_list, "190/190 apprises")
        self.assertContains(task_list, "0/348 sujets terminés")
        task_card = next(
            row
            for row in task_list.context["tasks"]
            if row["task"].pk == self.task.pk
        )
        self.assertEqual(
            task_card["question_bank"]["memory_progress"].status,
            "done",
        )
        self.assertEqual(
            task_card["question_bank"]["subject_progress"].status,
            "new",
        )
        self.assertEqual(
            task_card["question_bank"]["progress"].status,
            "active",
        )

        subject_card_ids = (
            Card.objects.filter(
                user=self.user,
                phrase__tier=PhraseTier.SUBJECT,
                phrase__source_prompts__response__content_key__startswith=(
                    "tache2:"
                ),
            )
            .values_list("pk", flat=True)
            .distinct()
        )
        Card.objects.filter(pk__in=subject_card_ids).update(
            state=CardState.LEARNING
        )
        vocabulary_completed_task_list = self.client.get(
            reverse("study:part_detail", args=[self.task.part.slug])
        )
        vocabulary_completed_task_card = next(
            row
            for row in vocabulary_completed_task_list.context["tasks"]
            if row["task"].pk == self.task.pk
        )

        self.assertEqual(
            vocabulary_completed_task_card["question_bank"][
                "subject_progress"
            ].status,
            "active",
        )
        self.assertEqual(
            vocabulary_completed_task_card["question_bank"]["progress"].status,
            "active",
        )
        self.assertEqual(
            vocabulary_completed_task_card["question_bank"][
                "progress"
            ].completed,
            190,
        )
        self.assertContains(
            vocabulary_completed_task_list,
            "0/348 sujets terminés",
        )

        Card.objects.filter(
            user=self.user,
            card_type=CardType.SPINE,
            response__content_key__startswith="tache2:",
        ).update(subject_completed_at=timezone.now())
        completed_task_list = self.client.get(
            reverse("study:part_detail", args=[self.task.part.slug])
        )
        completed_task_card = next(
            row
            for row in completed_task_list.context["tasks"]
            if row["task"].pk == self.task.pk
        )

        self.assertEqual(
            completed_task_card["question_bank"]["subject_progress"].status,
            "done",
        )
        self.assertEqual(
            completed_task_card["question_bank"]["progress"].status,
            "done",
        )
        self.assertEqual(
            completed_task_card["question_bank"]["progress"].completed,
            538,
        )
        self.assertContains(completed_task_list, "348/348 sujets terminés")

    def test_account_export_and_reset_include_manual_progress(self):
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
        response = Response.objects.filter(
            content_key__startswith="tache2:"
        ).first()
        completed_at = timezone.now()
        own_card = Card.objects.get(
            user=self.user,
            card_type=CardType.SPINE,
            response=response,
        )
        own_card.subject_completed_at = completed_at
        own_card.save(update_fields=["subject_completed_at"])
        other_card = Card.objects.create(
            user=other_user,
            card_type=CardType.SPINE,
            response=response,
            subject_completed_at=completed_at,
        )

        exported = self.client.get(reverse("study:export_account")).json()

        self.assertEqual(exported["version"], 3)
        self.assertEqual(
            exported["memory_question_progress"][0]["question_key"],
            own_progress.question_key,
        )
        self.assertEqual(len(exported["memory_question_progress"]), 1)
        exported_card = next(
            card
            for card in exported["cards"]
            if card["response_key"] == response.content_key
        )
        self.assertIsNotNone(exported_card["subject_completed_at"])

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
        own_card.refresh_from_db()
        other_card.refresh_from_db()
        self.assertIsNone(own_card.subject_completed_at)
        self.assertEqual(other_card.subject_completed_at, completed_at)
