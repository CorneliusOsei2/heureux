from datetime import timedelta
from importlib import import_module

from django.apps import apps
from django.test import TestCase
from django.utils import timezone

from study.models import Annotation, AnnotationKind, ReviewSession

from . import factories


skill_code_migration = import_module(
    "study.migrations.0024_standardize_skill_codes"
)
canonical_url_migration = import_module(
    "study.migrations.0027_canonical_public_urls"
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


class CanonicalPublicUrlMigrationTests(TestCase):
    def setUp(self):
        self.user = factories.make_user("canonical-url-migration")
        self.part = factories.make_part("eo")
        self.task = factories.make_task(self.part, "tache-3")
        self.theme = factories.make_theme("migration-theme", task=self.task)
        self.response = factories.make_response(theme=self.theme)
        self.prompt = self.response.prompts.get(is_canonical=True)

    def test_migration_rewrites_stored_paths_to_the_public_hierarchy(self):
        cases = {
            (
                f"/response/{self.response.pk}/"
                f"?prompt={self.prompt.pk}&saved=1#argument"
            ): f"/eo/tache-3/sujets/{self.prompt.pk}/#argument",
            (
                f"/theme/{self.theme.slug}/"
            ): f"/eo/tache-3/themes/{self.theme.slug}/",
            (
                f"/family/{self.response.family.slug}/"
            ): f"/eo/tache-3/familles/{self.response.family.slug}/",
            (
                "/expression/eo/tache-3/expressions/"
                "?part=eo&task=tache-3"
            ): "/eo/tache-3/vocabulaire/",
            (
                "/expression/eo/tache-3/expressions/"
                "?category=nuancer"
            ): (
                "/eo/tache-3/vocabulaire/categories/nuancer/"
            ),
            (
                "/browse/?part=eo&task=tache-3"
            ): "/eo/tache-3/sujets/",
            (
                "/comprehension/ce/test-1/question/2/?mode=ce"
            ): "/ce/tests/test-1/questions/2/",
            (
                "/comprehension/co/vocabulaire/?mode=co"
            ): "/co/vocabulaire/",
            (
                "/vocabulaire/?part=eo&task=tache-3"
                "&category=nuancer&q=utile"
            ): (
                "/eo/tache-3/vocabulaire/categories/nuancer/"
                "?q=utile"
            ),
            (
                "/notes/?part=eo&task=tache-3&tab=highlights"
            ): "/eo/tache-3/notes/?tab=highlights",
            (
                "/notes/etudier/?part=eo&task=tache-3"
            ): "/eo/tache-3/notes/etudier/",
            (
                "/review/?part=eo&task=tache-3&kind=spine"
            ): "/eo/tache-3/revision/cartes/?kind=spine",
            (
                "/reviser/?part=eo&task=tache-3&kind=spine"
            ): "/eo/tache-3/revision/cartes/?kind=spine",
            (
                "/revisit/?part=eo&task=tache-3"
            ): "/eo/tache-3/revision/a-revoir/",
            "/login/?next=%2Fstats%2F": (
                "/compte/connexion/?next=%2Fprogression%2F"
            ),
        }
        annotations = {
            source: Annotation.objects.create(
                user=self.user,
                task=self.task,
                kind=AnnotationKind.NOTE,
                body=f"Stored path {index}",
                source_path=source,
            )
            for index, source in enumerate(cases)
        }

        canonical_url_migration.migrate_public_urls(apps, None)

        for source, expected in cases.items():
            with self.subTest(source=source):
                annotations[source].refresh_from_db()
                self.assertEqual(
                    annotations[source].source_path,
                    expected,
                )

    def test_migration_merges_highlights_that_collapse_to_one_path(self):
        canonical_path = (
            f"/eo/tache-3/sujets/{self.prompt.pk}/"
        )
        canonical = Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.HIGHLIGHT,
            quote="Ancien passage",
            source_path=canonical_path,
            source_key="response:migration-theme:p1",
            start_offset=4,
            end_offset=19,
        )
        legacy = Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.HIGHLIGHT,
            quote="Passage personnalisé",
            source_path=f"/response/{self.response.pk}/",
            source_key=canonical.source_key,
            start_offset=4,
            end_offset=19,
            study_later=True,
        )
        Annotation.objects.filter(pk=canonical.pk).update(
            updated_at=timezone.now() - timedelta(minutes=1)
        )

        canonical_url_migration.migrate_public_urls(apps, None)

        canonical.refresh_from_db()
        self.assertFalse(
            Annotation.objects.filter(pk=legacy.pk).exists()
        )
        self.assertEqual(canonical.source_path, canonical_path)
        self.assertEqual(canonical.quote, "Passage personnalisé")
        self.assertTrue(canonical.study_later)
