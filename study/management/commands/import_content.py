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

        themes = content.load_themes()
        sections = content.load_sections()
        family_map, families = content.parse_families()
        responses = content.parse_responses()
        phrases = content.parse_phrases(responses)
        subject_vocabulary = content.parse_subject_vocabulary(responses)
        comprehension_tests = content.load_comprehension_tests()
        comprehension_vocabulary = content.parse_comprehension_vocabulary(
            comprehension_tests
        )
        all_phrases = [
            *phrases,
            *subject_vocabulary,
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
                "{p} prompts, {ph} phrases, {ct} comprehension tests, "
                "{cq} comprehension questions, {c} cards.".format(
                    t=Theme.objects.filter(is_active=True).count(),
                    f=Family.objects.filter(is_active=True).count(),
                    r=Response.objects.filter(is_active=True).count(),
                    p=Prompt.objects.filter(is_active=True).count(),
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
        seen_tests = set()
        for test_data in tests:
            test, _ = ComprehensionTest.objects.update_or_create(
                slug=test_data.slug,
                defaults={
                    "mode": test_data.mode,
                    "number": test_data.number,
                    "title": test_data.title,
                    "description": test_data.description,
                    "expected_question_count": test_data.expected_question_count,
                    "order": test_data.order,
                    "is_published": test_data.is_published,
                    "is_active": True,
                },
            )
            seen_tests.add(test.pk)
            seen_questions = set()
            for question_data in test_data.questions:
                question, _ = ComprehensionQuestion.objects.update_or_create(
                    content_key=question_data.content_key,
                    defaults={
                        "test": test,
                        "number": question_data.number,
                        "passage_fr": question_data.passage_fr,
                        "passage_en": question_data.passage_en,
                        "prompt_fr": question_data.prompt_fr,
                        "prompt_en": question_data.prompt_en,
                        "correct_explanation": (
                            question_data.correct_explanation
                        ),
                        "is_active": True,
                    },
                )
                seen_questions.add(question.pk)
                seen_letters = set()
                for choice_data in question_data.choices:
                    ComprehensionChoice.objects.update_or_create(
                        question=question,
                        letter=choice_data.letter,
                        defaults={
                            "text_fr": choice_data.text_fr,
                            "text_en": choice_data.text_en,
                            "rationale": choice_data.rationale,
                            "is_correct": choice_data.is_correct,
                            "is_active": True,
                        },
                    )
                    seen_letters.add(choice_data.letter)
                ComprehensionChoice.objects.filter(question=question).exclude(
                    letter__in=seen_letters
                ).update(is_active=False)
            test.questions.exclude(pk__in=seen_questions).update(is_active=False)
        ComprehensionTest.objects.exclude(pk__in=seen_tests).update(
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
        seen = set()
        mapping = {}
        claimed = set()
        incoming_keys = {data.content_key for data in responses}
        prompt_response_ids = dict(
            Prompt.objects.exclude(content_key="")
            .exclude(content_key__isnull=True)
            .values_list("content_key", "response_id")
        )
        response_sources = {}
        for data in responses:
            source_ids = {
                prompt_response_ids[prompt.content_key]
                for prompt in data.prompts
                if prompt.content_key in prompt_response_ids
            }
            obj = Response.objects.filter(content_key=data.content_key).first()
            if obj is None:
                prompt_keys = [prompt.content_key for prompt in data.prompts]
                obj = (
                    Response.objects.filter(
                        prompts__content_key__in=prompt_keys,
                    )
                    .exclude(pk__in=claimed)
                    .exclude(content_key__in=incoming_keys)
                    .distinct()
                    .order_by("pk")
                    .first()
                )
            values = {
                "content_key": data.content_key,
                "body_hash": data.body_hash,
                "theme": theme_by_name[data.theme],
                "family": family_by_name[data.family],
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
                obj = Response.objects.create(**values)
            else:
                source_ids.add(obj.pk)
                for field, value in values.items():
                    setattr(obj, field, value)
                obj.save(update_fields=list(values))
            mapping[data.content_key] = obj
            response_sources[data.content_key] = source_ids
            seen.add(obj.pk)
            claimed.add(obj.pk)

            arg_orders = set()
            for arg in data.arguments:
                Argument.objects.update_or_create(
                    response=obj,
                    order=arg.order,
                    defaults={
                        "idea": arg.idea,
                        "developpement": arg.developpement,
                        "exemple": arg.exemple,
                        "consequence": arg.consequence,
                    },
                )
                arg_orders.add(arg.order)
            obj.arguments.exclude(order__in=arg_orders).delete()

        Response.objects.exclude(pk__in=seen).update(is_active=False)
        self._response_sources = response_sources
        return mapping

    def _import_prompts(
        self, responses, response_by_key, theme_by_name, family_by_name
    ):
        seen = set()
        index = {}
        for data in responses:
            response = response_by_key[data.content_key]
            for prompt in data.prompts:
                obj, _ = Prompt.objects.update_or_create(
                    content_key=prompt.content_key,
                    defaults={
                        "theme": theme_by_name[prompt.theme],
                        "number": prompt.number,
                        "response": response,
                        "family": family_by_name[prompt.family],
                        "text": prompt.text,
                        "is_canonical": prompt.is_canonical,
                        "is_active": True,
                    },
                )
                index[(prompt.theme, prompt.number)] = obj
                seen.add(obj.pk)
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

        Phrase.objects.update(is_active=False)
        seen_categories = {}
        order = 0
        lot_orders = dict(
            Phrase.objects.values_list("phrase_id", "lot_order")
        )
        next_lot_order = max(lot_orders.values(), default=0) + 1
        existing_by_id = Phrase.objects.in_bulk(field_name="phrase_id")
        new_phrases = []
        changed_phrases = []
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
            if phrase is None:
                phrase = Phrase(phrase_id=data.phrase_id)
                new_phrases.append(phrase)
            else:
                changed_phrases.append(phrase)
            phrase.tier = data.tier
            phrase.category = category
            phrase.english_cue = data.english_cue
            phrase.expression = data.expression
            phrase.anchor = data.anchor
            phrase.example = data.example
            phrase.note = data.note
            phrase.sources_raw = data.sources_raw
            phrase.order = data.order
            phrase.lot_order = lot_order
            phrase.is_active = True

        Phrase.objects.bulk_create(
            new_phrases,
            batch_size=IMPORT_BATCH_SIZE,
        )
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
        through_model = Phrase.source_prompts.through
        seen_phrase_ids = list(seen_phrases)
        for start in range(0, len(seen_phrase_ids), IMPORT_BATCH_SIZE):
            through_model.objects.filter(
                phrase_id__in=seen_phrase_ids[
                    start : start + IMPORT_BATCH_SIZE
                ]
            ).delete()
        source_links = [
            through_model(
                phrase_id=phrase_by_id[data.phrase_id].pk,
                prompt_id=prompt_index[source].pk,
            )
            for data in phrases
            for source in data.sources
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
                if (
                    not schedule_improves
                    and started_at == target_card.started_at
                    and response_practice_started_at
                    == target_card.response_practice_started_at
                ):
                    continue
                if schedule_improves:
                    for field in schedule_fields:
                        if field in {
                            "started_at",
                            "response_practice_started_at",
                        }:
                            continue
                        setattr(target_card, field, getattr(source_card, field))
                target_card.started_at = started_at
                target_card.response_practice_started_at = (
                    response_practice_started_at
                )
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
