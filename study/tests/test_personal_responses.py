from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from study.models import Card, CardType, PersonalResponse

from . import factories


class PersonalResponseTests(TestCase):
    def setUp(self):
        self.owner = factories.make_user("response-owner")
        self.other = factories.make_user("response-other")
        self.part = factories.make_part("orale")
        self.task = factories.make_task(self.part, "tache-3")
        self.theme = factories.make_theme("culture", task=self.task)
        self.response = factories.make_response(theme=self.theme)
        self.owner_card = Card.objects.create(
            user=self.owner,
            card_type=CardType.SPINE,
            response=self.response,
        )
        Card.objects.create(
            user=self.other,
            card_type=CardType.SPINE,
            response=self.response,
        )
        self.edit_url = reverse(
            "study:edit_response",
            args=[self.response.pk],
        )
        argument = self.response.arguments.get()
        self.payload = {
            "reformulation": "Ma reformulation personnelle.",
            "position": "Ma position personnelle.",
            "position_claire": "Je suis clairement favorable.",
            f"argument_{argument.order}_idea": "Mon idée précise.",
            f"argument_{argument.order}_developpement": (
                "Mon développement détaillé."
            ),
            f"argument_{argument.order}_exemple": "Mon exemple concret.",
            f"argument_{argument.order}_consequence": (
                "Ma conséquence logique."
            ),
            "nuance": "Ma nuance personnelle.",
            "conclusion": "Ma conclusion personnelle.",
            "prompt": "Tentative de modifier le sujet.",
            "action": "save",
        }
        self.client.force_login(self.owner)

    def test_editor_shows_prompt_as_read_only(self):
        page = self.client.get(self.edit_url)

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, self.response.prompt)
        self.assertContains(page, "Sujet non modifiable")
        self.assertNotContains(page, 'name="prompt"')

    def test_introduction_and_position_are_labeled_independently(self):
        editor = self.client.get(self.edit_url)

        self.assertEqual(editor.context["form"].fields["position"].label, "Position")
        self.assertEqual(
            editor.context["form"].fields["position_claire"].label,
            "Introduction",
        )

        self.client.post(self.edit_url, self.payload)
        detail = self.client.get(
            reverse("study:response_detail", args=[self.response.pk])
        )

        self.assertContains(
            detail,
            """
            <section class="card section-card">
              <div class="spine-label">Position</div>
              <p class="spine-text">Ma position personnelle.</p>
            </section>
            """,
            html=True,
        )
        self.assertContains(
            detail,
            """
            <section class="card section-card">
              <div class="spine-label">Introduction</div>
              <p class="spine-text">Je suis clairement favorable.</p>
            </section>
            """,
            html=True,
        )

    def test_personal_edit_keeps_shared_prompt_and_response_unchanged(self):
        original_prompt = self.response.prompt
        original_position = self.response.position

        result = self.client.post(self.edit_url, self.payload)

        self.assertRedirects(
            result,
            reverse("study:response_detail", args=[self.response.pk])
            + "?saved=1",
            fetch_redirect_response=False,
        )
        personal = PersonalResponse.objects.get(
            user=self.owner,
            response=self.response,
        )
        self.assertEqual(personal.position, "Ma position personnelle.")
        self.response.refresh_from_db()
        self.assertEqual(self.response.prompt, original_prompt)
        self.assertEqual(self.response.position, original_position)

    def test_personal_version_is_private_and_used_in_learning_and_review(self):
        self.client.post(self.edit_url, self.payload)

        detail = self.client.get(
            reverse("study:response_detail", args=[self.response.pk])
        )
        review = self.client.get(
            reverse("study:review_next") + "?kind=spine"
        ).json()

        self.assertContains(detail, "Ma position personnelle.")
        self.assertContains(detail, "Mon développement détaillé.")
        self.assertContains(detail, "Version personnelle")
        self.assertIn("Mon idée précise.", review["back_html"])
        self.assertNotIn("Mon développement détaillé.", review["back_html"])

        self.client.force_login(self.other)
        other_detail = self.client.get(
            reverse("study:response_detail", args=[self.response.pk])
        )
        other_review = self.client.get(
            reverse("study:review_next") + "?kind=spine"
        ).json()
        self.assertNotContains(other_detail, "Ma position personnelle.")
        self.assertNotIn("Mon idée précise.", other_review["back_html"])

    def test_reset_restores_shared_version_without_touching_progress(self):
        self.client.post(self.edit_url, self.payload)
        self.owner_card.reps = 6
        self.owner_card.save(update_fields=["reps"])

        result = self.client.post(self.edit_url, {"action": "reset"})

        self.assertRedirects(
            result,
            reverse("study:response_detail", args=[self.response.pk])
            + "?reset=1",
            fetch_redirect_response=False,
        )
        self.assertFalse(
            PersonalResponse.objects.filter(
                user=self.owner,
                response=self.response,
            ).exists()
        )
        self.owner_card.refresh_from_db()
        self.assertEqual(self.owner_card.reps, 6)

    def test_editor_is_limited_to_expression_orale_tache_3(self):
        written_part = factories.make_part("ecrite")
        written_task = factories.make_task(written_part, "tache-3")
        written_theme = factories.make_theme(
            "written-theme",
            task=written_task,
        )
        written_response = factories.make_response(theme=written_theme)

        response = self.client.get(
            reverse("study:edit_response", args=[written_response.pk])
        )

        self.assertEqual(response.status_code, 404)
