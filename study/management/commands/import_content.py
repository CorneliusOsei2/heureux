"""Import the bundled answer bank into the database.

Idempotent and non-destructive: content is upserted by immutable keys, learner
cards keep their spaced-repetition state, and removed source items are archived
instead of cascading into private progress or review history.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from study import content
from study.accounts import (
    acquire_study_data_lock,
    provision_user_study_data,
    users_with_study_state,
)
from study.models import (
    Argument,
    Card,
    CardState,
    CardType,
    ContentImportState,
    ComprehensionChoice,
    ComprehensionQuestion,
    ComprehensionTest,
    ExamPart,
    Family,
    Phrase,
    PhraseCategory,
    Prompt,
    Response,
    Settings,
    Task,
    Theme,
)

PHRASE_ID_MERGES = {
    "N83": "I15",
    "W25": "ED15",
}
IMPORT_BATCH_SIZE = 500


def _apply_values(instance, values):
    changed = False
    for attribute, value in values.items():
        if getattr(instance, attribute) == value:
            continue
        setattr(instance, attribute, value)
        changed = True
    return changed


class Command(BaseCommand):
    help = "Import themes, families, responses, prompts and phrases."

    def add_arguments(self, parser):
        parser.add_argument(
            "--if-changed",
            action="store_true",
            help="Skip the import when this bundled content is already loaded.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write("Waiting for the database content-import lock...")
        self.stdout.flush()
        self._acquire_import_lock()
        fingerprint = self._source_fingerprint()
        if (
            options["if_changed"]
            and ContentImportState.objects.filter(
                pk="bundled",
                fingerprint=fingerprint,
            ).exists()
        ):
            self.stdout.write("Bundled content unchanged; import skipped.")
            return

        self.stdout.write("Bundled content changed; parsing source files...")
        self.stdout.flush()
        subject_months = content.load_tache_two_subject_months()
        ee_tache3_months = content.load_ee_tache3_months()
        themes = [
            *content.load_themes(),
            *content.tache_two_themes(subject_months),
            *content.ee_tache3_themes(ee_tache3_months),
        ]
        sections = content.load_sections()
        question_banks = content.load_question_banks()
        family_map, families = content.parse_families()
        families = [
            *families,
            *content.tache_two_families(subject_months),
            *content.ee_tache3_families(ee_tache3_months),
        ]
        standard_responses = content.parse_responses()
        tache_two_responses = content.parse_tache_two_responses(
            subject_months
        )
        ee_tache3_responses = content.parse_ee_tache3_responses(
            ee_tache3_months
        )
        responses = [
            *standard_responses,
            *tache_two_responses,
            *ee_tache3_responses,
        ]
        phrases = content.parse_phrases(standard_responses)
        subject_vocabulary = content.parse_subject_vocabulary(
            standard_responses
        )
        tache_two_vocabulary = (
            content.parse_tache_two_subject_vocabulary(
                tache_two_responses
            )
        )
        ee_tache3_vocabulary = content.parse_ee_tache3_subject_vocabulary(
            ee_tache3_responses
        )
        comprehension_tests = content.load_comprehension_tests()
        comprehension_vocabulary = content.parse_comprehension_vocabulary(
            comprehension_tests
        )
        all_phrases = [
            *phrases,
            *subject_vocabulary,
            *tache_two_vocabulary,
            *ee_tache3_vocabulary,
            *(item.phrase for item in comprehension_vocabulary),
        ]
        phrase_id_locations = {}
        collisions = set()
        for phrase in all_phrases:
            key = phrase.phrase_id.casefold()
            if key in phrase_id_locations:
                collisions.add(phrase.phrase_id)
                collisions.add(phrase_id_locations[key])
            else:
                phrase_id_locations[key] = phrase.phrase_id
        if collisions:
            raise CommandError(
                "Duplicate phrase IDs across imported vocabularies: "
                + ", ".join(sorted(collisions))
            )
        phrases = all_phrases

        self.stdout.write("Synchronizing shared content rows...")
        self.stdout.flush()
        task_by_slug = self._import_sections(sections)
        theme_by_name = self._import_themes(themes, task_by_slug)
        family_by_name = self._import_families(families)
        response_by_key = self._import_responses(
            responses, theme_by_name, family_by_name
        )
        prompt_index = self._import_prompts(
            responses, response_by_key, theme_by_name, family_by_name
        )
        self._import_phrases(phrases, prompt_index)
        self._import_comprehension_tests(comprehension_tests)
        self._link_comprehension_vocabulary(comprehension_vocabulary)
        users = list(users_with_study_state())
        self.stdout.write(
            f"Synchronizing {len(users)} learner deck"
            f"{'' if len(users) == 1 else 's'}..."
        )
        self.stdout.flush()
        if users:
            for user in users:
                provision_user_study_data(user)
        else:
            self._sync_cards(response_by_key)
            Settings.load()
        self._reconcile_response_cards(response_by_key)
        self._reconcile_phrase_cards()
        self._reconcile_local_phrase_directions()
        ContentImportState.objects.update_or_create(
            pk="bundled",
            defaults={"fingerprint": fingerprint},
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Imported {t} themes, {f} families, {r} responses, "
                "{p} prompts, {qm} Tâche 2 {memory_label} with {qb} questions, "
                "{ph} phrases, "
                "{ct} comprehension tests, "
                "{cq} comprehension questions, {c} cards.".format(
                    t=Theme.objects.filter(is_active=True).count(),
                    f=Family.objects.filter(is_active=True).count(),
                    r=Response.objects.filter(is_active=True).count(),
                    p=Prompt.objects.filter(is_active=True).count(),
                    qm=len(question_banks),
                    memory_label=(
                        "memory"
                        if len(question_banks) == 1
                        else "memories"
                    ),
                    qb=sum(bank.question_count for bank in question_banks),
                    ph=Phrase.objects.filter(is_active=True).count(),
                    ct=ComprehensionTest.objects.filter(is_active=True).count(),
                    cq=ComprehensionQuestion.objects.filter(is_active=True).count(),
                    c=Card.objects.count(),
                )
            )
        )

    @staticmethod
    def _acquire_import_lock():
        acquire_study_data_lock()

    @staticmethod
    def _source_fingerprint():
        command_path = Path(__file__).resolve()
        parser_path = Path(content.__file__).resolve()
        files = [
            ("import_content.py", command_path),
            ("content.py", parser_path),
        ]
        study_dir = content.CONTENT_DIR.parent
        files.extend(
            (name, study_dir / name)
            for name in ("accounts.py", "models.py")
        )
        files.extend(
            (
                f"migrations/{path.name}",
                path,
            )
            for path in (study_dir / "migrations").glob("*.py")
        )
        files.extend(
            (
                f"content/{path.relative_to(content.CONTENT_DIR).as_posix()}",
                path,
            )
            for path in content.CONTENT_DIR.rglob("*")
            if path.is_file()
        )
        digest = hashlib.sha256()
        for label, path in sorted(files):
            digest.update(label.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

    def _import_sections(self, sections):
        seen_parts = set()
        seen_tasks = set()
        task_by_slug = {}
        for part in sections:
            part_obj, _ = ExamPart.objects.update_or_create(
                slug=part.slug,
                defaults={
                    "name": part.name,
                    "short_name": part.short_name,
                    "icon": part.icon,
                    "color": part.color,
                    "order": part.order,
                    "available": part.available,
                    "is_active": True,
                },
            )
            seen_parts.add(part_obj.pk)
            for task in part.tasks:
                task_obj, _ = Task.objects.update_or_create(
                    part=part_obj,
                    slug=task.slug,
                    defaults={
                        "name": task.name,
                        "subtitle": task.subtitle,
                        "icon": task.icon,
                        "color": task.color,
                        "order": task.order,
                        "available": task.available,
                        "is_active": True,
                    },
                )
                task_by_slug[f"{part.slug}/{task.slug}"] = task_obj
                task_by_slug.setdefault(task.slug, task_obj)
                seen_tasks.add(task_obj.pk)
        Task.objects.exclude(pk__in=seen_tasks).update(is_active=False)
        ExamPart.objects.exclude(pk__in=seen_parts).update(is_active=False)
        return task_by_slug

    def _import_themes(self, themes, task_by_slug):
        seen = set()
        mapping = {}
        for theme in themes:
            obj, _ = Theme.objects.update_or_create(
                slug=theme.slug,
                defaults={
                    "name": theme.name,
                    "display_name": theme.display,
                    "order": theme.order,
                    "color": theme.color,
                    "icon": theme.icon,
                    "task": task_by_slug.get(theme.task),
                    "is_active": True,
                },
            )
            mapping[theme.name] = obj
            seen.add(obj.pk)
        Theme.objects.exclude(pk__in=seen).update(is_active=False)
        return mapping

    def _import_families(self, families):
        seen = set()
        mapping = {}
        for name, order in families:
            obj, _ = Family.objects.update_or_create(
                content_key=content.family_content_key(order),
                defaults={
                    "name": name,
                    "slug": content._slugify(name),
                    "order": order,
                    "is_active": True,
                },
            )
            mapping[name] = obj
            seen.add(obj.pk)
        Family.objects.exclude(pk__in=seen).update(is_active=False)
        return mapping

    def _import_comprehension_tests(self, tests):
        test_fields = [
            "mode",
            "number",
            "title",
            "description",
            "expected_question_count",
            "order",
            "is_published",
            "is_active",
        ]
        existing_tests = ComprehensionTest.objects.in_bulk(
            [test_data.slug for test_data in tests],
            field_name="slug",
        )
        tests_by_slug = {}
        new_tests = []
        changed_tests = []
        for test_data in tests:
            values = {
                "mode": test_data.mode,
                "number": test_data.number,
                "title": test_data.title,
                "description": test_data.description,
                "expected_question_count": test_data.expected_question_count,
                "order": test_data.order,
                "is_published": test_data.is_published,
                "is_active": True,
            }
            test = existing_tests.get(test_data.slug)
            if test is None:
                test = ComprehensionTest(slug=test_data.slug, **values)
                new_tests.append(test)
            elif _apply_values(test, values):
                changed_tests.append(test)
            tests_by_slug[test_data.slug] = test

        ComprehensionTest.objects.bulk_create(
            new_tests,
            batch_size=IMPORT_BATCH_SIZE,
        )
        if changed_tests:
            ComprehensionTest.objects.bulk_update(
                changed_tests,
                test_fields,
                batch_size=IMPORT_BATCH_SIZE,
            )

        question_rows = [
            (tests_by_slug[test_data.slug], question_data)
            for test_data in tests
            for question_data in test_data.questions
        ]
        question_fields = [
            "test",
            "number",
            "passage_fr",
            "passage_en",
            "prompt_fr",
            "prompt_en",
            "correct_explanation",
            "is_active",
        ]
        existing_questions = ComprehensionQuestion.objects.in_bulk(
            [question_data.content_key for _test, question_data in question_rows],
            field_name="content_key",
        )
        questions_by_key = {}
        new_questions = []
        changed_questions = []
        for test, question_data in question_rows:
            values = {
                "test_id": test.pk,
                "number": question_data.number,
                "passage_fr": question_data.passage_fr,
                "passage_en": question_data.passage_en,
                "prompt_fr": question_data.prompt_fr,
                "prompt_en": question_data.prompt_en,
                "correct_explanation": question_data.correct_explanation,
                "is_active": True,
            }
            question = existing_questions.get(question_data.content_key)
            if question is None:
                question = ComprehensionQuestion(
                    content_key=question_data.content_key,
                    **values,
                )
                new_questions.append(question)
            elif _apply_values(question, values):
                changed_questions.append(question)
            questions_by_key[question_data.content_key] = question

        ComprehensionQuestion.objects.bulk_create(
            new_questions,
            batch_size=IMPORT_BATCH_SIZE,
        )
        if changed_questions:
            ComprehensionQuestion.objects.bulk_update(
                changed_questions,
                question_fields,
                batch_size=IMPORT_BATCH_SIZE,
            )

        choice_fields = [
            "text_fr",
            "text_en",
            "rationale",
            "is_correct",
            "is_active",
        ]
        question_ids = [question.pk for question in questions_by_key.values()]
        existing_choices = {
            (choice.question_id, choice.letter): choice
            for choice in ComprehensionChoice.objects.filter(
                question_id__in=question_ids
            )
        }
        seen_choice_ids = set()
        new_choices = []
        changed_choices = []
        for _test, question_data in question_rows:
            question = questions_by_key[question_data.content_key]
            for choice_data in question_data.choices:
                values = {
                    "text_fr": choice_data.text_fr,
                    "text_en": choice_data.text_en,
                    "rationale": choice_data.rationale,
                    "is_correct": choice_data.is_correct,
                    "is_active": True,
                }
                choice = existing_choices.get(
                    (question.pk, choice_data.letter)
                )
                if choice is None:
                    choice = ComprehensionChoice(
                        question_id=question.pk,
                        letter=choice_data.letter,
                        **values,
                    )
                    new_choices.append(choice)
                elif _apply_values(choice, values):
                    changed_choices.append(choice)
                if choice.pk is not None:
                    seen_choice_ids.add(choice.pk)

        ComprehensionChoice.objects.bulk_create(
            new_choices,
            batch_size=IMPORT_BATCH_SIZE,
        )
        seen_choice_ids.update(
            choice.pk for choice in new_choices if choice.pk is not None
        )
        if changed_choices:
            ComprehensionChoice.objects.bulk_update(
                changed_choices,
                choice_fields,
                batch_size=IMPORT_BATCH_SIZE,
            )

        if question_ids:
            ComprehensionChoice.objects.filter(
                question_id__in=question_ids
            ).exclude(pk__in=seen_choice_ids).update(is_active=False)

        seen_question_ids = set(question_ids)
        seen_test_ids = {test.pk for test in tests_by_slug.values()}
        if seen_test_ids:
            ComprehensionQuestion.objects.filter(
                test_id__in=seen_test_ids
            ).exclude(pk__in=seen_question_ids).update(is_active=False)
        ComprehensionTest.objects.exclude(pk__in=seen_test_ids).update(
            is_active=False,
            is_published=False,
        )

    @staticmethod
    def _link_comprehension_vocabulary(vocabulary):
        through_model = Phrase.source_questions.through
        through_model.objects.all().delete()
        if not vocabulary:
            return

        phrases = Phrase.objects.in_bulk(
            [item.phrase.phrase_id for item in vocabulary],
            field_name="phrase_id",
        )
        question_keys = {
            (item.test_slug, number)
            for item in vocabulary
            for number in item.question_numbers
        }
        questions = {
            (test_slug, number): question_id
            for test_slug, number, question_id in (
                ComprehensionQuestion.objects.filter(
                    test__slug__in={
                        test_slug for test_slug, _number in question_keys
                    }
                ).values_list("test__slug", "number", "pk")
            )
        }
        missing = sorted(question_keys - set(questions))
        if missing:
            labels = ", ".join(
                f"{test_slug} Q{number}"
                for test_slug, number in missing
            )
            raise CommandError(
                "Comprehension vocabulary references unknown questions: "
                + labels
            )
        links = [
            through_model(
                phrase_id=phrases[item.phrase.phrase_id].pk,
                comprehensionquestion_id=questions[
                    (item.test_slug, number)
                ],
            )
            for item in vocabulary
            for number in item.question_numbers
        ]
        through_model.objects.bulk_create(
            links,
            batch_size=IMPORT_BATCH_SIZE,
        )

    def _import_responses(self, responses, theme_by_name, family_by_name):
        mapping = {}
        claimed = set()
        incoming_keys = {data.content_key for data in responses}
        prompt_response_ids = dict(
            Prompt.objects.exclude(content_key="")
            .exclude(content_key__isnull=True)
            .values_list("content_key", "response_id")
        )
        existing_by_key = Response.objects.in_bulk(
            incoming_keys,
            field_name="content_key",
        )
        existing_by_pk = Response.objects.in_bulk(
            set(prompt_response_ids.values())
        )
        response_sources = {}
        new_responses = []
        changed_responses = []
        response_fields = [
            "content_key",
            "body_hash",
            "theme",
            "family",
            "prompt",
            "reformulation",
            "position",
            "position_claire",
            "nuance",
            "conclusion",
            "body",
            "body_html",
            "is_active",
        ]
        for data in responses:
            source_ids = {
                prompt_response_ids[prompt.content_key]
                for prompt in data.prompts
                if prompt.content_key in prompt_response_ids
            }
            obj = existing_by_key.get(data.content_key)
            if obj is None:
                candidates = (
                    existing_by_pk[response_id]
                    for response_id in source_ids
                    if response_id in existing_by_pk
                    and response_id not in claimed
                    and existing_by_pk[response_id].content_key
                    not in incoming_keys
                )
                obj = min(candidates, key=lambda item: item.pk, default=None)
            values = {
                "content_key": data.content_key,
                "body_hash": data.body_hash,
                "theme_id": theme_by_name[data.theme].pk,
                "family_id": family_by_name[data.family].pk,
                "prompt": data.prompt,
                "reformulation": data.reformulation,
                "position": data.position,
                "position_claire": data.position_claire,
                "nuance": data.nuance,
                "conclusion": data.conclusion,
                "body": data.body,
                "body_html": data.body_html,
                "is_active": True,
            }
            if obj is None:
                obj = Response(**values)
                new_responses.append(obj)
            else:
                source_ids.add(obj.pk)
                if _apply_values(obj, values):
                    changed_responses.append(obj)
            mapping[data.content_key] = obj
            response_sources[data.content_key] = source_ids
            if obj.pk is not None:
                claimed.add(obj.pk)

        if changed_responses:
            Response.objects.bulk_update(
                changed_responses,
                response_fields,
                batch_size=IMPORT_BATCH_SIZE,
            )
        Response.objects.bulk_create(
            new_responses,
            batch_size=IMPORT_BATCH_SIZE,
        )

        seen = {obj.pk for obj in mapping.values()}
        existing_arguments = {
            (argument.response_id, argument.order): argument
            for argument in Argument.objects.filter(response_id__in=seen)
        }
        desired_argument_keys = set()
        new_arguments = []
        changed_arguments = []
        argument_fields = [
            "idea",
            "developpement",
            "exemple",
            "consequence",
        ]
        for data in responses:
            response = mapping[data.content_key]
            for arg in data.arguments:
                key = (response.pk, arg.order)
                desired_argument_keys.add(key)
                values = {
                    "idea": arg.idea,
                    "developpement": arg.developpement,
                    "exemple": arg.exemple,
                    "consequence": arg.consequence,
                }
                argument = existing_arguments.get(key)
                if argument is None:
                    new_arguments.append(
                        Argument(
                            response_id=response.pk,
                            order=arg.order,
                            **values,
                        )
                    )
                elif _apply_values(argument, values):
                    changed_arguments.append(argument)

        removed_argument_ids = [
            argument.pk
            for key, argument in existing_arguments.items()
            if key not in desired_argument_keys
        ]
        if removed_argument_ids:
            Argument.objects.filter(pk__in=removed_argument_ids).delete()
        Argument.objects.bulk_create(
            new_arguments,
            batch_size=IMPORT_BATCH_SIZE,
        )
        if changed_arguments:
            Argument.objects.bulk_update(
                changed_arguments,
                argument_fields,
                batch_size=IMPORT_BATCH_SIZE,
            )

        Response.objects.exclude(pk__in=seen).update(is_active=False)
        self._response_sources = response_sources
        return mapping

    def _import_prompts(
        self, responses, response_by_key, theme_by_name, family_by_name
    ):
        index = {}
        prompt_rows = [
            (response_by_key[data.content_key], prompt)
            for data in responses
            for prompt in data.prompts
        ]
        existing_prompts = Prompt.objects.in_bulk(
            [prompt.content_key for _response, prompt in prompt_rows],
            field_name="content_key",
        )
        new_prompts = []
        changed_prompts = []
        prompt_fields = [
            "theme",
            "number",
            "response",
            "family",
            "text",
            "is_canonical",
            "is_active",
        ]
        for response, prompt in prompt_rows:
            values = {
                "theme_id": theme_by_name[prompt.theme].pk,
                "number": prompt.number,
                "response_id": response.pk,
                "family_id": family_by_name[prompt.family].pk,
                "text": prompt.text,
                "is_canonical": prompt.is_canonical,
                "is_active": True,
            }
            obj = existing_prompts.get(prompt.content_key)
            if obj is None:
                obj = Prompt(content_key=prompt.content_key, **values)
                new_prompts.append(obj)
            elif _apply_values(obj, values):
                changed_prompts.append(obj)
            index[(prompt.theme, prompt.number)] = obj

        Prompt.objects.bulk_create(
            new_prompts,
            batch_size=IMPORT_BATCH_SIZE,
        )
        if changed_prompts:
            Prompt.objects.bulk_update(
                changed_prompts,
                prompt_fields,
                batch_size=IMPORT_BATCH_SIZE,
            )
        seen = {prompt.pk for prompt in index.values()}
        Prompt.objects.exclude(pk__in=seen).update(is_active=False)
        return index

    def _import_phrases(self, phrases, prompt_index):
        for data in phrases:
            missing_sources = [
                key for key in data.sources if key not in prompt_index
            ]
            if missing_sources:
                labels = ", ".join(
                    f"{theme} P{number}" for theme, number in missing_sources
                )
                raise CommandError(
                    f"Phrase {data.phrase_id} references unknown prompts: {labels}"
                )

        seen_categories = {}
        order = 0
        lot_orders = dict(
            Phrase.objects.values_list("phrase_id", "lot_order")
        )
        next_lot_order = max(lot_orders.values(), default=0) + 1
        existing_by_id = Phrase.objects.in_bulk(field_name="phrase_id")
        new_phrases = []
        changed_phrases = []
        phrase_fields = [
            "tier",
            "category",
            "english_cue",
            "expression",
            "anchor",
            "example",
            "note",
            "sources_raw",
            "order",
            "lot_order",
            "is_active",
        ]
        for data in phrases:
            if data.category not in seen_categories:
                order += 1
                category, _ = PhraseCategory.objects.update_or_create(
                    content_key=content.phrase_category_content_key(
                        data.category
                    ),
                    defaults={
                        "name": data.category,
                        "slug": content._slugify(data.category),
                        "order": order,
                        "is_active": True,
                    },
                )
                seen_categories[data.category] = category
            category = seen_categories[data.category]

            existing_lot_order = lot_orders.get(data.phrase_id)
            if existing_lot_order is None:
                lot_order = next_lot_order
                next_lot_order += 1
                lot_orders[data.phrase_id] = lot_order
            else:
                lot_order = existing_lot_order
            phrase = existing_by_id.get(data.phrase_id)
            values = {
                "tier": data.tier,
                "category_id": category.pk,
                "english_cue": data.english_cue,
                "expression": data.expression,
                "anchor": data.anchor,
                "example": data.example,
                "note": data.note,
                "sources_raw": data.sources_raw,
                "order": data.order,
                "lot_order": lot_order,
                "is_active": True,
            }
            if phrase is None:
                phrase = Phrase(phrase_id=data.phrase_id, **values)
                new_phrases.append(phrase)
            elif _apply_values(phrase, values):
                changed_phrases.append(phrase)

        Phrase.objects.bulk_create(
            new_phrases,
            batch_size=IMPORT_BATCH_SIZE,
        )
        if changed_phrases:
            Phrase.objects.bulk_update(
                changed_phrases,
                phrase_fields,
                batch_size=IMPORT_BATCH_SIZE,
            )

        phrase_ids = [data.phrase_id for data in phrases]
        phrase_by_id = Phrase.objects.in_bulk(
            phrase_ids,
            field_name="phrase_id",
        )
        seen_phrases = {phrase.pk for phrase in phrase_by_id.values()}
        Phrase.objects.exclude(pk__in=seen_phrases).filter(
            is_active=True
        ).update(is_active=False)
        through_model = Phrase.source_prompts.through
        desired_links = {
            (
                phrase_by_id[data.phrase_id].pk,
                prompt_index[source].pk,
            )
            for data in phrases
            for source in data.sources
        }
        existing_links = {}
        seen_phrase_ids = list(seen_phrases)
        for start in range(0, len(seen_phrase_ids), IMPORT_BATCH_SIZE):
            rows = through_model.objects.filter(
                phrase_id__in=seen_phrase_ids[
                    start : start + IMPORT_BATCH_SIZE
                ]
            ).values_list("pk", "phrase_id", "prompt_id")
            existing_links.update(
                {
                    (phrase_id, prompt_id): link_id
                    for link_id, phrase_id, prompt_id in rows
                }
            )
        obsolete_link_ids = [
            link_id
            for pair, link_id in existing_links.items()
            if pair not in desired_links
        ]
        for start in range(0, len(obsolete_link_ids), IMPORT_BATCH_SIZE):
            through_model.objects.filter(
                pk__in=obsolete_link_ids[start : start + IMPORT_BATCH_SIZE]
            ).delete()
        source_links = [
            through_model(phrase_id=phrase_id, prompt_id=prompt_id)
            for phrase_id, prompt_id in desired_links
            if (phrase_id, prompt_id) not in existing_links
        ]
        through_model.objects.bulk_create(
            source_links,
            batch_size=IMPORT_BATCH_SIZE,
        )

        PhraseCategory.objects.exclude(
            pk__in=[c.pk for c in seen_categories.values()]
        ).update(is_active=False)

    @staticmethod
    def _card_progress_rank(card):
        reviewed_at = (
            card.last_reviewed.timestamp() if card.last_reviewed else 0
        )
        return (
            reviewed_at,
            card.reps,
            card.interval_days,
            card.state != CardState.NEW,
            card.needs_revisit,
            card.suspended,
            -card.pk,
        )

    def _reconcile_response_cards(self, response_by_key):
        source_plan = getattr(self, "_response_sources", {})
        response_ids = {
            response_id
            for source_ids in source_plan.values()
            for response_id in source_ids
        }
        response_ids.update(
            response.pk for response in response_by_key.values()
        )
        if not response_ids:
            return

        cards_by_response = defaultdict(dict)
        cards = Card.objects.filter(
            card_type=CardType.SPINE,
            response_id__in=response_ids,
            user_id__isnull=False,
        )
        for card in cards:
            cards_by_response[card.response_id][card.user_id] = card

        schedule_fields = (
            "state",
            "due",
            "interval_days",
            "ease",
            "reps",
            "lapses",
            "learning_step",
            "last_reviewed",
            "last_rating",
            "needs_revisit",
            "revisit_added_at",
            "started_at",
            "response_practice_started_at",
            "subject_completed_at",
            "suspended",
        )
        changed = []
        for content_key, target_response in response_by_key.items():
            source_ids = source_plan.get(content_key, set())
            target_cards = cards_by_response.get(target_response.pk, {})
            user_ids = {
                user_id
                for response_id in source_ids
                for user_id in cards_by_response.get(response_id, {})
            }
            for user_id in user_ids:
                target_card = target_cards.get(user_id)
                if target_card is None:
                    continue
                candidates = [
                    cards_by_response[response_id][user_id]
                    for response_id in source_ids
                    if user_id in cards_by_response.get(response_id, {})
                ]
                source_card = max(
                    candidates,
                    key=self._card_progress_rank,
                    default=target_card,
                )
                schedule_improves = self._card_progress_rank(
                    source_card
                ) > self._card_progress_rank(target_card)
                started_at = min(
                    (
                        card.started_at
                        for card in [target_card, *candidates]
                        if card.started_at is not None
                    ),
                    default=None,
                )
                response_practice_started_at = min(
                    (
                        card.response_practice_started_at
                        for card in [target_card, *candidates]
                        if card.response_practice_started_at is not None
                    ),
                    default=None,
                )
                subject_completed_at = min(
                    (
                        card.subject_completed_at
                        for card in [target_card, *candidates]
                        if card.subject_completed_at is not None
                    ),
                    default=None,
                )
                if (
                    not schedule_improves
                    and started_at == target_card.started_at
                    and response_practice_started_at
                    == target_card.response_practice_started_at
                    and subject_completed_at
                    == target_card.subject_completed_at
                ):
                    continue
                if schedule_improves:
                    for field in schedule_fields:
                        if field in {
                            "started_at",
                            "response_practice_started_at",
                            "subject_completed_at",
                        }:
                            continue
                        setattr(target_card, field, getattr(source_card, field))
                target_card.started_at = started_at
                target_card.response_practice_started_at = (
                    response_practice_started_at
                )
                target_card.subject_completed_at = subject_completed_at
                changed.append(target_card)

        if changed:
            Card.objects.bulk_update(changed, schedule_fields)

    def _reconcile_phrase_cards(self):
        phrase_ids = set(PHRASE_ID_MERGES)
        phrase_ids.update(PHRASE_ID_MERGES.values())
        phrases = {
            phrase.phrase_id: phrase
            for phrase in Phrase.objects.filter(phrase_id__in=phrase_ids)
        }
        cards = Card.objects.filter(
            phrase__phrase_id__in=phrase_ids,
            user_id__isnull=False,
        ).select_related("phrase")
        cards_by_key = {
            (card.phrase.phrase_id, card.user_id, card.card_type): card
            for card in cards
        }
        schedule_fields = (
            "state",
            "due",
            "interval_days",
            "ease",
            "reps",
            "lapses",
            "learning_step",
            "last_reviewed",
            "last_rating",
            "needs_revisit",
            "revisit_added_at",
            "started_at",
            "suspended",
        )
        changed = []
        for source_id, target_id in PHRASE_ID_MERGES.items():
            if source_id not in phrases or target_id not in phrases:
                continue
            source_cards = [
                card
                for (phrase_id, _user_id, _card_type), card in cards_by_key.items()
                if phrase_id == source_id
            ]
            for source_card in source_cards:
                target_card = cards_by_key.get(
                    (target_id, source_card.user_id, source_card.card_type)
                )
                if target_card is None:
                    continue
                schedule_improves = self._card_progress_rank(
                    source_card
                ) > self._card_progress_rank(target_card)
                started_at = min(
                    (
                        value
                        for value in (
                            target_card.started_at,
                            source_card.started_at,
                        )
                        if value is not None
                    ),
                    default=None,
                )
                if (
                    not schedule_improves
                    and started_at == target_card.started_at
                ):
                    continue
                if schedule_improves:
                    for field in schedule_fields:
                        if field == "started_at":
                            continue
                        setattr(target_card, field, getattr(source_card, field))
                target_card.started_at = started_at
                changed.append(target_card)
        if changed:
            Card.objects.bulk_update(changed, schedule_fields)

    def _reconcile_local_phrase_directions(self):
        cards = Card.objects.filter(
            phrase__tier="response",
            user_id__isnull=False,
            card_type__in=[
                CardType.PHRASE_PRODUCTION,
                CardType.PHRASE_RECOGNITION,
            ],
        )
        cards_by_key = {
            (card.phrase_id, card.user_id, card.card_type): card
            for card in cards
        }
        schedule_fields = (
            "state",
            "due",
            "interval_days",
            "ease",
            "reps",
            "lapses",
            "learning_step",
            "last_reviewed",
            "last_rating",
            "needs_revisit",
            "revisit_added_at",
            "started_at",
        )
        changed = []
        for (phrase_id, user_id, card_type), source_card in cards_by_key.items():
            if card_type != CardType.PHRASE_RECOGNITION:
                continue
            target_card = cards_by_key.get(
                (phrase_id, user_id, CardType.PHRASE_PRODUCTION)
            )
            if target_card is None:
                continue
            schedule_improves = self._card_progress_rank(
                source_card
            ) > self._card_progress_rank(target_card)
            started_at = min(
                (
                    value
                    for value in (
                        target_card.started_at,
                        source_card.started_at,
                    )
                    if value is not None
                ),
                default=None,
            )
            if (
                not schedule_improves
                and started_at == target_card.started_at
            ):
                continue
            if schedule_improves:
                for field in schedule_fields:
                    if field == "started_at":
                        continue
                    setattr(target_card, field, getattr(source_card, field))
            target_card.started_at = started_at
            changed.append(target_card)
        if changed:
            Card.objects.bulk_update(changed, schedule_fields)

    def _sync_cards(self, response_by_key, user=None):
        """Create one card per studyable item; never reset existing state."""
        responses = list(response_by_key.values())
        existing_response_ids = set(
            Card.objects.filter(
                user=user,
                card_type=CardType.SPINE,
            ).values_list("response_id", flat=True)
        )
        Card.objects.bulk_create(
            [
                Card(
                    user=user,
                    card_type=CardType.SPINE,
                    response=response,
                )
                for response in responses
                if response.pk not in existing_response_ids
            ],
            ignore_conflicts=True,
            batch_size=IMPORT_BATCH_SIZE,
        )

        phrases = list(Phrase.objects.filter(is_active=True))
        phrase_card_types = (
            (CardType.PHRASE_PRODUCTION, phrases),
            (
                CardType.PHRASE_RECOGNITION,
                [phrase for phrase in phrases if phrase.tier == "shared"],
            ),
        )
        for card_type, eligible_phrases in phrase_card_types:
            existing_phrase_ids = set(
                Card.objects.filter(
                    user=user,
                    card_type=card_type,
                ).values_list("phrase_id", flat=True)
            )
            Card.objects.bulk_create(
                [
                    Card(
                        user=user,
                        card_type=card_type,
                        phrase=phrase,
                    )
                    for phrase in eligible_phrases
                    if phrase.pk not in existing_phrase_ids
                ],
                ignore_conflicts=True,
                batch_size=IMPORT_BATCH_SIZE,
            )
