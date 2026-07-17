from datetime import timedelta
from importlib import import_module

from django.apps import apps
from django.test import TestCase

from study.models import Annotation, AnnotationKind, ReviewSession

from . import factories


skill_code_migration = import_module(
    "study.migrations.0024_standardize_skill_codes"
)


class SkillCodeMigrationTests(TestCase):
    def test_migration_updates_saved_urls_and_merges_duplicate_highlights(self):
        user = factories.make_user("skill-code-migration")
        part = factories.make_part("orale")
        task = factories.make_task(part, "tache-3")
        session = ReviewSession.load(user)
        session.scope = {
            "kind": "spine",
            "part": "orale",
            "task": "tache-3",
        }
        session.save(update_fields=["scope"])

        canonical_path = "/expression/eo/tache-3/?part=eo&task=tache-3"
        canonical = Annotation.objects.create(
            user=user,
            task=task,
            kind=AnnotationKind.HIGHLIGHT,
            quote="Passage initial",
            source_path=canonical_path,
            source_key="response:culture:p1",
            start_offset=4,
            end_offset=19,
        )
        duplicate = Annotation.objects.create(
            user=user,
            task=task,
            kind=AnnotationKind.HIGHLIGHT,
            quote="Passage personnalisé",
            source_path=(
                "/expression/orale/tache-3/"
                "?part=orale&task=tache-3"
            ),
            source_key="response:culture:p1",
            start_offset=4,
            end_offset=19,
            study_later=True,
        )
        Annotation.objects.filter(pk=duplicate.pk).update(
            updated_at=canonical.updated_at + timedelta(seconds=1)
        )
        note = Annotation.objects.create(
            user=user,
            kind=AnnotationKind.NOTE,
            body="À retenir",
            source_path=(
                "/comprehension-ecrite/test-1/"
                "?mode=ecrite"
            ),
        )

        skill_code_migration.migrate_skill_codes(apps, None)

        part.refresh_from_db()
        session.refresh_from_db()
        canonical.refresh_from_db()
        note.refresh_from_db()
        self.assertEqual(part.slug, "eo")
        self.assertEqual(part.short_name, "EO")
        self.assertEqual(session.scope["part"], "eo")
        self.assertEqual(
            note.source_path,
            "/comprehension/ce/test-1/?mode=ce",
        )
        self.assertEqual(
            Annotation.objects.filter(
                kind=AnnotationKind.HIGHLIGHT
            ).count(),
            1,
        )
        self.assertEqual(canonical.quote, "Passage personnalisé")
        self.assertTrue(canonical.study_later)
