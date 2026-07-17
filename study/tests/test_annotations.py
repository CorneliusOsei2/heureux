from __future__ import annotations

import json

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

        notes_tab = self.client.get(
            reverse(
                "study:task_notes",
                args=[self.part.slug, self.task.slug],
            )
        )
        self.assertContains(notes_tab, 'role="tablist"')
        self.assertContains(
            notes_tab,
            'id="notes-tab"',
        )
        self.assertContains(notes_tab, 'aria-selected="true"')
        self.assertContains(notes_tab, "Réutiliser cette structure.")
        self.assertNotContains(notes_tab, "Passage important")

        highlights_tab = self.client.get(
            reverse(
                "study:task_notes",
                args=[self.part.slug, self.task.slug],
            ),
            {"tab": "highlights"},
        )
        self.assertEqual(highlights_tab.context["active_tab"], "highlights")
        self.assertContains(highlights_tab, "Passage important")
        self.assertNotContains(highlights_tab, "Réutiliser cette structure.")

    def test_highlights_are_grouped_by_responses_and_expressions(self):
        response_highlight = Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.HIGHLIGHT,
            quote="Passage d'une réponse",
            source_path=self.source_path,
            source_key="response:culture:p1:back",
            start_offset=1,
            end_offset=21,
        )
        legacy_response_highlight = Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.HIGHLIGHT,
            quote="Ancien passage de réponse",
            source_path=self.source_path,
            start_offset=22,
            end_offset=47,
        )
        expression_highlight = Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.HIGHLIGHT,
            quote="Passage d'une expression",
            source_path=reverse("study:review") + "?kind=phrase",
            source_key="phrase:expr-1:phrase_production:back",
            start_offset=1,
            end_offset=25,
        )
        legacy_expression_highlight = Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.HIGHLIGHT,
            quote="Ancien passage d'expression",
            source_path=reverse(
                "study:task_phrases",
                args=[self.part.slug, self.task.slug],
            ),
            start_offset=1,
            end_offset=28,
        )

        response = self.client.get(
            reverse(
                "study:task_notes",
                args=[self.part.slug, self.task.slug],
            ),
            {"tab": "highlights"},
        )

        groups = {
            group["key"]: {item.id for item in group["items"]}
            for group in response.context["highlight_groups"]
        }
        self.assertEqual(
            groups["responses"],
            {response_highlight.id, legacy_response_highlight.id},
        )
        self.assertEqual(
            groups["expressions"],
            {expression_highlight.id, legacy_expression_highlight.id},
        )
        self.assertContains(response, "Sujets &amp; réponses")
        self.assertContains(response, "Expressions")

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
        self.assertRedirects(
            response,
            task_url + f"?tab=notes#note-{task_note.id}",
        )
        self.assertEqual(task_note.user, self.user)
        self.assertEqual(task_note.task, self.task)
        self.assertEqual(task_note.kind, AnnotationKind.NOTE)

        general_url = reverse("study:general_notes")
        response = self.client.post(
            general_url,
            {"title": "", "body": "Objectif de la semaine."},
        )
        general_note = Annotation.objects.get(body="Objectif de la semaine.")
        self.assertRedirects(
            response,
            general_url + f"?tab=notes#note-{general_note.id}",
        )
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
        self.assertEqual(
            response.json()["notes_url"],
            reverse(
                "study:task_notes",
                args=[self.part.slug, self.task.slug],
            )
            + f"?tab=notes#note-{note.id}",
        )

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
        payload = {
            **self.selection,
            "kind": AnnotationKind.HIGHLIGHT,
            "overlap_ids": "",
        }
        first = self.client.post(reverse("study:annotation_create"), payload)
        second = self.client.post(reverse("study:annotation_create"), payload)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(Annotation.objects.count(), 1)
        self.assertEqual(
            first.json()["notes_url"],
            reverse(
                "study:task_notes",
                args=[self.part.slug, self.task.slug],
            )
            + f"?tab=highlights#highlight-{first.json()['id']}",
        )

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

    def test_dynamic_card_source_keys_prevent_offset_collisions(self):
        first = self.client.post(
            reverse("study:annotation_create"),
            {
                **self.selection,
                "kind": AnnotationKind.HIGHLIGHT,
                "source_key": "response:culture:p1:front",
            },
        )
        second = self.client.post(
            reverse("study:annotation_create"),
            {
                **self.selection,
                "kind": AnnotationKind.HIGHLIGHT,
                "quote": "Un autre passage aux mêmes positions.",
                "source_key": "response:economie:p1:front",
            },
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(Annotation.objects.count(), 2)
        restored = self.client.get(
            reverse("study:annotations_for_source"),
            {"source_path": self.source_path},
        ).json()["highlights"]
        keys = {item["source_key"] for item in restored}
        self.assertEqual(
            keys,
            {
                "response:culture:p1:front",
                "response:economie:p1:front",
            },
        )

    def test_changed_text_updates_the_same_highlight_anchor(self):
        payload = {
            **self.selection,
            "kind": AnnotationKind.HIGHLIGHT,
            "source_key": "response:culture:p1:back",
        }
        first = self.client.post(reverse("study:annotation_create"), payload)
        second = self.client.post(
            reverse("study:annotation_create"),
            {
                **payload,
                "quote": "Le passage a été légèrement corrigé.",
                "prefix": "Nouveau contexte ",
            },
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["id"], second.json()["id"])
        annotation = Annotation.objects.get()
        self.assertEqual(
            annotation.quote,
            "Le passage a été légèrement corrigé.",
        )
        self.assertEqual(annotation.prefix, "Nouveau contexte ")

    def test_partial_overlap_expands_and_merges_the_highlight(self):
        partial = {
            **self.selection,
            "kind": AnnotationKind.HIGHLIGHT,
            "quote": "nuancer cette",
            "start_offset": "32",
            "end_offset": "45",
        }
        first = self.client.post(reverse("study:annotation_create"), partial)
        annotation = Annotation.objects.get()
        annotation.study_later = True
        annotation.save(update_fields=["study_later", "updated_at"])

        expanded = self.client.post(
            reverse("study:annotation_create"),
            {
                **self.selection,
                "kind": AnnotationKind.HIGHLIGHT,
                "overlap_ids": str(annotation.id),
                "overlap_revisions": json.dumps(
                    {str(annotation.id): annotation.updated_at.isoformat()}
                ),
            },
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(expanded.status_code, 200)
        self.assertEqual(expanded.json()["id"], first.json()["id"])
        self.assertEqual(expanded.json()["removed_ids"], [])
        self.assertEqual(
            expanded.json()["delete_url"],
            reverse("study:annotation_delete", args=[annotation.id]),
        )
        annotation.refresh_from_db()
        self.assertEqual(annotation.quote, self.selection["quote"])
        self.assertEqual(annotation.start_offset, 24)
        self.assertEqual(annotation.end_offset, 58)
        self.assertTrue(annotation.study_later)

        restored = self.client.get(
            reverse("study:annotations_for_source"),
            {"source_path": self.source_path},
        ).json()["highlights"]
        self.assertEqual(len(restored), 1)
        self.assertEqual(
            restored[0]["delete_url"],
            reverse("study:annotation_delete", args=[annotation.id]),
        )

    def test_expanding_across_highlights_merges_them(self):
        first = Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.HIGHLIGHT,
            quote="Il faut",
            source_path=self.source_path,
            start_offset=24,
            end_offset=31,
        )
        second = Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.HIGHLIGHT,
            quote="nuancer",
            source_path=self.source_path,
            start_offset=32,
            end_offset=39,
            study_later=True,
        )

        response = self.client.post(
            reverse("study:annotation_create"),
            {
                **self.selection,
                "kind": AnnotationKind.HIGHLIGHT,
                "overlap_ids": f"{first.id},{second.id}",
                "overlap_revisions": json.dumps(
                    {
                        str(first.id): first.updated_at.isoformat(),
                        str(second.id): second.updated_at.isoformat(),
                    }
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], second.id)
        self.assertEqual(response.json()["removed_ids"], [first.id])
        merged = Annotation.objects.get()
        self.assertEqual(merged.quote, self.selection["quote"])
        self.assertTrue(merged.study_later)

    def test_resolved_overlap_ids_override_stale_offsets(self):
        resolved = Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.HIGHLIGHT,
            quote="Passage déplacé",
            source_path=self.source_path,
            start_offset=100,
            end_offset=115,
        )
        stale_collision = Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.HIGHLIGHT,
            quote="Autre passage déplacé",
            source_path=self.source_path,
            start_offset=30,
            end_offset=42,
        )

        response = self.client.post(
            reverse("study:annotation_create"),
            {
                **self.selection,
                "kind": AnnotationKind.HIGHLIGHT,
                "overlap_ids": str(resolved.id),
                "overlap_revisions": json.dumps(
                    {str(resolved.id): resolved.updated_at.isoformat()}
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], resolved.id)
        self.assertEqual(response.json()["removed_ids"], [])
        resolved.refresh_from_db()
        self.assertEqual(resolved.start_offset, 24)
        self.assertEqual(resolved.end_offset, 58)
        self.assertTrue(
            Annotation.objects.filter(pk=stale_collision.id).exists()
        )

    def test_resolved_ids_do_not_overwrite_exact_stale_collision(self):
        resolved = Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.HIGHLIGHT,
            quote="Passage déplacé",
            source_path=self.source_path,
            start_offset=100,
            end_offset=115,
        )
        stale_collision = Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.HIGHLIGHT,
            quote="Autre passage déplacé",
            source_path=self.source_path,
            start_offset=24,
            end_offset=58,
        )

        response = self.client.post(
            reverse("study:annotation_create"),
            {
                **self.selection,
                "kind": AnnotationKind.HIGHLIGHT,
                "overlap_ids": str(resolved.id),
                "overlap_revisions": json.dumps(
                    {str(resolved.id): resolved.updated_at.isoformat()}
                ),
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("passage en conflit", response.json()["error"])
        resolved.refresh_from_db()
        stale_collision.refresh_from_db()
        self.assertEqual(resolved.start_offset, 100)
        self.assertEqual(resolved.end_offset, 115)
        self.assertEqual(stale_collision.quote, "Autre passage déplacé")

    def test_stale_expansion_cannot_overwrite_a_concurrent_expansion(self):
        highlight = Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.HIGHLIGHT,
            quote="nuancer cette",
            source_path=self.source_path,
            start_offset=32,
            end_offset=45,
        )
        saved = self.client.get(
            reverse("study:annotations_for_source"),
            {"source_path": self.source_path},
        ).json()["highlights"][0]
        revisions = json.dumps({str(highlight.id): saved["revision"]})

        first = self.client.post(
            reverse("study:annotation_create"),
            {
                **self.selection,
                "kind": AnnotationKind.HIGHLIGHT,
                "quote": "Il faut nuancer cette",
                "end_offset": "45",
                "overlap_ids": str(highlight.id),
                "overlap_revisions": revisions,
            },
        )
        stale = self.client.post(
            reverse("study:annotation_create"),
            {
                **self.selection,
                "kind": AnnotationKind.HIGHLIGHT,
                "quote": "nuancer cette affirmation.",
                "start_offset": "32",
                "overlap_ids": str(highlight.id),
                "overlap_revisions": revisions,
            },
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(stale.status_code, 409)
        self.assertIn("autre onglet", stale.json()["error"])
        highlight.refresh_from_db()
        self.assertEqual(highlight.start_offset, 24)
        self.assertEqual(highlight.end_offset, 45)

    def test_response_query_variants_share_saved_highlights(self):
        detail_path = reverse(
            "study:response_detail",
            args=[self.task.pk],
        )
        highlight = Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.HIGHLIGHT,
            quote="Texte inchangé",
            source_path=detail_path + "?prompt=7",
            start_offset=100,
            end_offset=115,
        )

        restored = self.client.get(
            reverse("study:annotations_for_source"),
            {"source_path": detail_path + "?saved=1"},
        ).json()["highlights"]

        self.assertEqual([item["id"] for item in restored], [highlight.id])

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

        invalid_source_key = self.client.post(
            reverse("study:annotation_create"),
            {
                **self.selection,
                "kind": AnnotationKind.HIGHLIGHT,
                "source_key": "<script>",
            },
        )
        self.assertEqual(invalid_source_key.status_code, 400)
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

    def test_private_annotation_search_filters_content_and_kind(self):
        matching_note = Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.NOTE,
            title="Connecteurs",
            body="Employer cependant pour nuancer.",
            study_later=True,
        )
        Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.HIGHLIGHT,
            quote="Cependant, cette difficulté n'est pas une fatalité.",
            source_path=self.source_path,
            start_offset=1,
            end_offset=53,
        )
        Annotation.objects.create(
            user=self.other,
            task=self.task,
            kind=AnnotationKind.NOTE,
            body="Cependant, ceci est privé.",
        )

        response = self.client.get(
            reverse("study:annotation_search"),
            {"q": "cependant", "kind": "note", "study": "1"},
        )

        self.assertEqual(response.context["result_count"], 1)
        self.assertContains(response, matching_note.body)
        self.assertNotContains(response, "cette difficulté")
        self.assertNotContains(response, "ceci est privé")

    def test_annotations_can_be_marked_and_studied_by_task(self):
        note = Annotation.objects.create(
            user=self.user,
            task=self.task,
            kind=AnnotationKind.NOTE,
            title="Nuance",
            body="Le mot toujours est trop fort.",
        )
        other_task = factories.make_task(
            part=factories.make_part(slug="ecrite"),
            slug="tache-1",
        )
        other_note = Annotation.objects.create(
            user=self.user,
            task=other_task,
            kind=AnnotationKind.NOTE,
            body="Une autre tâche.",
            study_later=True,
        )

        detail_url = reverse(
            "study:task_notes",
            args=[self.part.slug, self.task.slug],
        )
        response = self.client.post(
            reverse("study:annotation_study_toggle", args=[note.id]),
            {"study_later": "1", "next": detail_url},
        )
        self.assertRedirects(response, detail_url)
        note.refresh_from_db()
        self.assertTrue(note.study_later)

        study = self.client.get(
            reverse(
                "study:task_annotation_study",
                args=[self.part.slug, self.task.slug],
            )
        )
        self.assertContains(study, "Le mot toujours est trop fort.")
        self.assertNotContains(study, other_note.body)
        self.assertContains(study, "data-annotation-study")

        self.client.force_login(self.other)
        self.assertEqual(
            self.client.post(
                reverse("study:annotation_study_toggle", args=[note.id]),
                {"study_later": "0"},
            ).status_code,
            404,
        )
        self.assertNotContains(
            self.client.get(reverse("study:annotation_study")),
            note.body,
        )
