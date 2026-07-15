from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from study.models import Annotation, AnnotationKind

from . import factories


class AnnotationTests(TestCase):
    def setUp(self):
        self.user = factories.make_user("notes-owner")
        self.other = factories.make_user("notes-other")
        self.client.force_login(self.user)
        self.part = factories.make_part(slug="orale")
        self.task = factories.make_task(part=self.part, slug="tache-3")
        self.source_path = reverse(
            "study:task_detail",
            args=[self.part.slug, self.task.slug],
        )
        self.selection = {
            "quote": "Il faut nuancer cette affirmation.",
            "start_offset": "24",
            "end_offset": "58",
            "prefix": "Préambule ",
            "suffix": " Conclusion",
            "source_path": self.source_path,
            "source_title": "Tâche 3 · Heureux",
            "task_id": str(self.task.id),
        }

    def test_notes_hierarchy_and_subsections_render(self):
        Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.NOTE,
            body="Réutiliser cette structure.",
        )
        Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.HIGHLIGHT,
            quote="Passage important",
            source_path=self.source_path,
            start_offset=2,
            end_offset=19,
        )

        overview = self.client.get(reverse("study:notes_overview"))
        self.assertContains(overview, self.part.name)
        self.assertContains(overview, self.task.name)
        self.assertContains(overview, "1 note")
        self.assertContains(overview, "1 surlignage")

        detail = self.client.get(
            reverse(
                "study:task_notes",
                args=[self.part.slug, self.task.slug],
            )
        )
        self.assertContains(detail, "Notes")
        self.assertContains(detail, "Surlignages")
        self.assertContains(detail, "Réutiliser cette structure.")
        self.assertContains(detail, "Passage important")

    def test_freeform_notes_are_categorized_by_task_or_general(self):
        task_url = reverse(
            "study:task_notes",
            args=[self.part.slug, self.task.slug],
        )
        response = self.client.post(
            task_url,
            {"title": "Connecteurs", "body": "Employer cependant et pourtant."},
        )
        task_note = Annotation.objects.get(title="Connecteurs")
        self.assertRedirects(response, task_url + f"#note-{task_note.id}")
        self.assertEqual(task_note.user, self.user)
        self.assertEqual(task_note.task, self.task)
        self.assertEqual(task_note.kind, AnnotationKind.NOTE)

        general_url = reverse("study:general_notes")
        response = self.client.post(
            general_url,
            {"title": "", "body": "Objectif de la semaine."},
        )
        general_note = Annotation.objects.get(body="Objectif de la semaine.")
        self.assertRedirects(response, general_url + f"#note-{general_note.id}")
        self.assertIsNone(general_note.task)

    def test_selected_note_is_private_and_source_linked(self):
        response = self.client.post(
            reverse("study:annotation_create"),
            {**self.selection, "kind": AnnotationKind.NOTE, "body": "À mémoriser."},
        )
        self.assertEqual(response.status_code, 201)
        note = Annotation.objects.get()
        self.assertEqual(note.user, self.user)
        self.assertEqual(note.task, self.task)
        self.assertEqual(note.quote, self.selection["quote"])
        self.assertEqual(note.body, "À mémoriser.")
        self.assertEqual(note.source_path, self.source_path)

        self.client.force_login(self.other)
        other_page = self.client.get(
            reverse(
                "study:task_notes",
                args=[self.part.slug, self.task.slug],
            )
        )
        self.assertNotContains(other_page, "À mémoriser.")
        self.assertEqual(
            self.client.post(
                reverse("study:annotation_delete", args=[note.id])
            ).status_code,
            404,
        )

    def test_highlight_creation_is_idempotent_and_restorable(self):
        payload = {**self.selection, "kind": AnnotationKind.HIGHLIGHT}
        first = self.client.post(reverse("study:annotation_create"), payload)
        second = self.client.post(reverse("study:annotation_create"), payload)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(Annotation.objects.count(), 1)

        response = self.client.get(
            reverse("study:annotations_for_source"),
            {"source_path": self.source_path},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["highlights"]), 1)
        self.assertEqual(
            response.json()["highlights"][0]["quote"],
            self.selection["quote"],
        )

        self.client.force_login(self.other)
        response = self.client.get(
            reverse("study:annotations_for_source"),
            {"source_path": self.source_path},
        )
        self.assertEqual(response.json()["highlights"], [])

    def test_annotation_validation_rejects_empty_or_external_selection(self):
        empty = self.client.post(
            reverse("study:annotation_create"),
            {**self.selection, "kind": AnnotationKind.NOTE, "quote": "   "},
        )
        self.assertEqual(empty.status_code, 400)

        external = self.client.post(
            reverse("study:annotation_create"),
            {
                **self.selection,
                "kind": AnnotationKind.HIGHLIGHT,
                "source_path": "https://example.com/stolen",
            },
        )
        self.assertEqual(external.status_code, 400)
        self.assertFalse(Annotation.objects.exists())

    def test_note_can_be_updated_and_highlight_deleted(self):
        note = Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.NOTE,
            body="Première version",
        )
        highlight = Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.HIGHLIGHT,
            quote="Texte surligné",
            source_path=self.source_path,
            start_offset=1,
            end_offset=15,
        )
        detail_url = reverse(
            "study:task_notes",
            args=[self.part.slug, self.task.slug],
        )

        response = self.client.post(
            reverse("study:annotation_update", args=[note.id]),
            {
                "title": "Version finale",
                "body": "Note corrigée",
                "next": detail_url,
            },
        )
        self.assertRedirects(response, detail_url + f"#note-{note.id}")
        note.refresh_from_db()
        self.assertEqual(note.title, "Version finale")
        self.assertEqual(note.body, "Note corrigée")

        response = self.client.post(
            reverse("study:annotation_delete", args=[highlight.id]),
            {"next": detail_url},
        )
        self.assertRedirects(response, detail_url)
        self.assertFalse(Annotation.objects.filter(pk=highlight.id).exists())

    def test_page_annotation_context_does_not_misclassify_dashboard(self):
        dashboard = self.client.get(reverse("study:dashboard"))
        self.assertContains(dashboard, 'data-annotation-task-id=""')

        task_page = self.client.get(self.source_path)
        self.assertContains(
            task_page,
            f'data-annotation-task-id="{self.task.id}"',
        )
