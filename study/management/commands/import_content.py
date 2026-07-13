"""Import the bundled answer bank into the database.

Idempotent: content is upserted by natural keys and cards keep their
spaced-repetition state across re-imports. Orphans no longer present in the
source are pruned.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from study import content
from study.accounts import provision_user_study_data, users_with_study_state
from study.models import (
    Argument,
    Card,
    CardType,
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


class Command(BaseCommand):
    help = "Import themes, families, responses, prompts and phrases."

    @transaction.atomic
    def handle(self, *args, **options):
        themes = content.load_themes()
        sections = content.load_sections()
        family_map, families = content.parse_families()
        responses = content.parse_responses()
        phrases = content.parse_phrases(responses)

        task_by_slug = self._import_sections(sections)
        theme_by_name = self._import_themes(themes, task_by_slug)
        family_by_name = self._import_families(families)
        response_by_hash = self._import_responses(
            responses, theme_by_name, family_by_name
        )
        prompt_index = self._import_prompts(
            responses, response_by_hash, theme_by_name, family_by_name
        )
        self._import_phrases(phrases, prompt_index)
        users = list(users_with_study_state())
        if users:
            for user in users:
                provision_user_study_data(user)
        else:
            self._sync_cards(response_by_hash)
            Settings.load()

        self.stdout.write(
            self.style.SUCCESS(
                "Imported {t} themes, {f} families, {r} responses, "
                "{p} prompts, {ph} phrases, {c} cards.".format(
                    t=Theme.objects.count(),
                    f=Family.objects.count(),
                    r=Response.objects.count(),
                    p=Prompt.objects.count(),
                    ph=Phrase.objects.count(),
                    c=Card.objects.count(),
                )
            )
        )

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
                    "emoji": part.emoji,
                    "color": part.color,
                    "order": part.order,
                    "available": part.available,
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
                        "emoji": task.emoji,
                        "color": task.color,
                        "order": task.order,
                        "available": task.available,
                    },
                )
                task_by_slug[task.slug] = task_obj
                seen_tasks.add(task_obj.pk)
        Task.objects.exclude(pk__in=seen_tasks).delete()
        ExamPart.objects.exclude(pk__in=seen_parts).delete()
        return task_by_slug

    def _import_themes(self, themes, task_by_slug):
        seen = set()
        mapping = {}
        for theme in themes:
            obj, _ = Theme.objects.update_or_create(
                name=theme.name,
                defaults={
                    "slug": theme.slug,
                    "display_name": theme.display,
                    "order": theme.order,
                    "color": theme.color,
                    "emoji": theme.emoji,
                    "task": task_by_slug.get(theme.task),
                },
            )
            mapping[theme.name] = obj
            seen.add(obj.pk)
        Theme.objects.exclude(pk__in=seen).delete()
        return mapping

    def _import_families(self, families):
        seen = set()
        mapping = {}
        for name, order in families:
            obj, _ = Family.objects.update_or_create(
                name=name,
                defaults={"slug": content._slugify(name), "order": order},
            )
            mapping[name] = obj
            seen.add(obj.pk)
        Family.objects.exclude(pk__in=seen).delete()
        return mapping

    def _import_responses(self, responses, theme_by_name, family_by_name):
        seen = set()
        mapping = {}
        for data in responses:
            obj, _ = Response.objects.update_or_create(
                body_hash=data.body_hash,
                defaults={
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
                },
            )
            mapping[data.body_hash] = obj
            seen.add(obj.pk)

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

        Response.objects.exclude(pk__in=seen).delete()
        return mapping

    def _import_prompts(
        self, responses, response_by_hash, theme_by_name, family_by_name
    ):
        seen = set()
        index = {}
        for data in responses:
            response = response_by_hash[data.body_hash]
            for prompt in data.prompts:
                obj, _ = Prompt.objects.update_or_create(
                    theme=theme_by_name[prompt.theme],
                    number=prompt.number,
                    defaults={
                        "response": response,
                        "family": family_by_name[prompt.family],
                        "text": prompt.text,
                        "is_canonical": prompt.is_canonical,
                    },
                )
                index[(prompt.theme, prompt.number)] = obj
                seen.add(obj.pk)
        Prompt.objects.exclude(pk__in=seen).delete()
        return index

    def _import_phrases(self, phrases, prompt_index):
        seen_categories = {}
        seen_phrases = set()
        order = 0
        for data in phrases:
            if data.category not in seen_categories:
                order += 1
                category, _ = PhraseCategory.objects.update_or_create(
                    name=data.category,
                    defaults={
                        "slug": content._slugify(data.category),
                        "order": order,
                    },
                )
                seen_categories[data.category] = category
            category = seen_categories[data.category]

            phrase, _ = Phrase.objects.update_or_create(
                phrase_id=data.phrase_id,
                defaults={
                    "category": category,
                    "english_cue": data.english_cue,
                    "expression": data.expression,
                    "anchor": data.anchor,
                    "example": data.example,
                    "note": data.note,
                    "sources_raw": data.sources_raw,
                    "order": data.order,
                },
            )
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
            source_objs = [prompt_index[key] for key in data.sources]
            phrase.source_prompts.set(source_objs)
            seen_phrases.add(phrase.pk)

        Phrase.objects.exclude(pk__in=seen_phrases).delete()
        PhraseCategory.objects.exclude(
            pk__in=[c.pk for c in seen_categories.values()]
        ).delete()

    def _sync_cards(self, response_by_hash, user=None):
        """Create one card per studyable item; never reset existing state."""
        for response in response_by_hash.values():
            Card.objects.get_or_create(
                user=user,
                card_type=CardType.SPINE,
                response=response,
            )
        for phrase in Phrase.objects.all():
            Card.objects.get_or_create(
                user=user,
                card_type=CardType.PHRASE_PRODUCTION,
                phrase=phrase,
            )
            Card.objects.get_or_create(
                user=user,
                card_type=CardType.PHRASE_RECOGNITION,
                phrase=phrase,
            )

        # Prune cards whose target no longer exists.
        Card.objects.filter(
            card_type=CardType.SPINE, response__isnull=True
        ).delete()
        Card.objects.filter(
            card_type__in=[
                CardType.PHRASE_PRODUCTION,
                CardType.PHRASE_RECOGNITION,
            ],
            phrase__isnull=True,
        ).delete()
