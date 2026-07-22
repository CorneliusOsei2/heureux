from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

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
        self.assertEqual([memoire.number for memoire in banks], [1, 2])
        self.assertEqual(banks[0], bank)

    def test_memoire_two_groups_reusable_cross_prompt_patterns(self):
        banks = load_question_banks()
        self.assertEqual(len(banks), 2)
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

    def test_monthly_batches_are_question_only_and_memory_driven(self):
        months = load_tache_two_subject_months()

        self.assertEqual(len(months), 5)
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
        self.assertEqual(may.subject_count, 24)
        self.assertEqual(may.question_count, 357)
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
                [20, 21, 22, 23, 24],
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
                [15, 15, 15, 15, 15],
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
            [72, 74, 60, 72, 73],
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

        self.assertEqual(len(responses), 94)
        self.assertEqual(
            sum(len(response.arguments) for response in responses),
            1379,
        )
        self.assertEqual(len(vocabulary), 2820)
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
            vocabulary_by_id["T2M5S24V30"].order,
            first_vocabulary_order + 2819,
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
        self.assertEqual(response.context["memory_count"], 2)
        self.assertEqual(response.context["subject_count"], 94)
        self.assertEqual(response.context["category_count"], 26)
        self.assertEqual(response.context["question_count"], 90)
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
        self.assertContains(response, "0/90 questions apprises")
        self.assertContains(response, "0/94 sujets terminés")
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
        self.assertEqual(response.context["memory_count"], 2)
        self.assertEqual(response.context["category_count"], 26)
        self.assertEqual(response.context["question_count"], 90)
        self.assertContains(response, "<span>Mémoires</span>", html=True)
        self.assertContains(response, "data-collection-view-toggle")
        self.assertContains(response, 'data-collection-view="adaptive"')
        self.assertContains(response, "collection-table-header--memories")
        self.assertContains(response, "data-collection-item", count=2)
        self.assertContains(response, "Mémoire 1")
        self.assertContains(response, "Mémoire 2")
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
                24,
            ],
        )

        index = self.client.get(index_url)
        self.assertEqual(index.status_code, 200)
        self.assertTemplateUsed(index, "study/tache_two_subjects.html")
        self.assertEqual(index.context["month_count"], 5)
        self.assertEqual(index.context["batch_count"], 19)
        self.assertEqual(index.context["subject_count"], 94)
        self.assertEqual(index.context["question_count"], 1379)
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
        self.assertContains(index, "data-tache-two-subject-batch", count=19)
        self.assertContains(index, "subject-batch-card--new", count=19)
        self.assertContains(index, "0/15 sujets terminés")
        self.assertContains(index, "0/30 sujets terminés")
        self.assertContains(index, "0/5 sujets terminés")
        self.assertContains(index, "0/10 sujets terminés")
        self.assertContains(index, "0/24 sujets terminés")
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
            count=5,
        )
        self.assertContains(may_batch, may_subject_url)

        may_subject = self.client.get(may_subject_url)
        self.assertEqual(may_subject.status_code, 200)
        self.assertContains(
            may_subject,
            "Nouveau travail d&#x27;un ancien collègue",
        )
        self.assertContains(
            may_subject,
            "data-tache-two-question",
            count=15,
        )
        self.assertContains(may_subject, "30 vocabs")

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

        self.assertEqual(responses.count(), 94)
        self.assertEqual(vocabulary.count(), 2820)
        self.assertEqual(
            Card.objects.filter(
                user=self.user,
                card_type=CardType.SPINE,
                response_id__in=response_ids,
            ).count(),
            94,
        )
        self.assertEqual(
            Card.objects.filter(
                user=self.user,
                card_type=CardType.PHRASE_PRODUCTION,
                phrase__in=vocabulary,
            ).count(),
            2820,
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
            count=94,
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
        self.assertContains(task_list, "0/94 sujets terminés")

    def test_unknown_subject_month_batch_and_number_are_not_found(self):
        route_args = [self.task.part.slug, self.task.slug]
        missing_month = self.client.get(
            reverse(
                "study:task_subject_batch",
                args=[*route_args, "juin", 1],
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
                args=[self.task.part.slug, self.task.slug, 3],
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
            "94 sujets · 2 mémoires · 26 catégories · 90 questions",
        )
        self.assertContains(response, "0/90 apprises")
        self.assertContains(response, "0/94 sujets terminés")
        self.assertContains(response, "À commencer")
        task_card = next(
            row
            for row in response.context["tasks"]
            if row["task"].pk == self.task.pk
        )
        self.assertEqual(task_card["question_bank"]["progress"].total, 184)
        self.assertEqual(
            task_card["question_bank"]["subject_progress"].total,
            94,
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
        self.assertContains(task_list, "1/90 apprises")
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
        self.assertContains(overview, "90/90 questions apprises")
        self.assertContains(task_list, "90/90 apprises")
        self.assertContains(task_list, "0/94 sujets terminés")
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
            184,
        )
        self.assertContains(completed_task_list, "94/94 sujets terminés")

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
