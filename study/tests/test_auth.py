from __future__ import annotations

from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from study import queue, srs
from study.accounts import (
    ACCOUNT_FAILURE_LIMIT,
    IP_ATTEMPT_LIMIT,
    STUDY_DATA_LOCK_ID,
    acquire_study_data_lock,
    generate_recovery_codes,
    login_throttle_key,
    provision_user_study_data,
    reserve_throttled_action,
    users_with_study_state,
)
from study.models import (
    AccountRecoveryCode,
    Annotation,
    AnnotationKind,
    Card,
    CardState,
    LoginThrottle,
    Rating,
    ReviewLog,
    ReviewSession,
    Settings,
)

from . import factories


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class AuthenticationTests(TestCase):
    def test_postgres_study_data_lock_serializes_import_and_provisioning(self):
        cursor = MagicMock()
        mocked_connection = MagicMock(vendor="postgresql")
        mocked_connection.cursor.return_value.__enter__.return_value = cursor

        with patch("study.accounts.connection", mocked_connection):
            acquire_study_data_lock()

        cursor.execute.assert_called_once_with(
            "SELECT pg_advisory_xact_lock(%s)",
            [STUDY_DATA_LOCK_ID],
        )

    def test_private_pages_redirect_to_login(self):
        response = self.client.get(reverse("study:dashboard"))
        self.assertRedirects(
            response,
            f"{reverse('study:login')}?next=/",
            fetch_redirect_response=False,
        )
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(
            self.client.get(reverse("study:login")).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse("study:register")).status_code,
            200,
        )

    def test_registration_normalizes_username_and_hashes_six_digit_pin(self):
        legacy_card = factories.make_spine_card(
            state=CardState.REVIEW,
            reps=4,
            needs_revisit=True,
        )
        legacy_settings = Settings.load()
        legacy_session = ReviewSession.load()
        legacy_session.current_card = legacy_card
        legacy_session.save(update_fields=["current_card"])

        response = self.client.post(
            reverse("study:register"),
            {
                "username": "  Alice.Smith  ",
                "pin": "482731",
                "pin_confirm": "482731",
            },
        )

        self.assertRedirects(response, reverse("study:recovery_codes"))
        user = get_user_model().objects.get()
        self.assertEqual(user.username, "alice.smith")
        self.assertNotEqual(user.password, "482731")
        self.assertTrue(user.check_password("482731"))
        legacy_card.refresh_from_db()
        legacy_settings.refresh_from_db()
        legacy_session.refresh_from_db()
        self.assertEqual(legacy_card.user, user)
        self.assertEqual(legacy_card.reps, 4)
        self.assertEqual(legacy_settings.user, user)
        self.assertEqual(legacy_session.user, user)
        self.assertEqual(legacy_session.current_card, legacy_card)
        self.assertEqual(
            AccountRecoveryCode.objects.filter(user=user).count(),
            8,
        )

    def test_registration_rejects_duplicate_username_case_insensitively(self):
        factories.make_user("alice")
        response = self.client.post(
            reverse("study:register"),
            {
                "username": "ALICE",
                "pin": "123456",
                "pin_confirm": "123456",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "déjà utilisé")
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_registration_requires_matching_six_digit_numeric_pin(self):
        for pin, confirmation in (
            ("12345", "12345"),
            ("1234567", "1234567"),
            ("12ab56", "12ab56"),
            ("123456", "654321"),
        ):
            with self.subTest(pin=pin, confirmation=confirmation):
                response = self.client.post(
                    reverse("study:register"),
                    {
                        "username": f"user{pin[:2]}{len(pin)}",
                        "pin": pin,
                        "pin_confirm": confirmation,
                    },
                )
                self.assertEqual(response.status_code, 200)
        self.assertEqual(get_user_model().objects.count(), 0)

    def test_login_logout_and_safe_next_redirect(self):
        user = factories.make_user("alice", pin="482731")
        response = self.client.post(
            reverse("study:login") + "?next=/stats/",
            {
                "username": "ALICE",
                "pin": "482731",
                "next": "/stats/",
            },
        )
        self.assertRedirects(
            response,
            "/stats/",
            fetch_redirect_response=False,
        )
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

        response = self.client.post(reverse("study:logout"))
        self.assertRedirects(response, reverse("study:login"))
        self.assertNotIn("_auth_user_id", self.client.session)

        response = self.client.post(
            reverse("study:login"),
            {
                "username": "alice",
                "pin": "482731",
                "next": "https://attacker.example/",
            },
        )
        self.assertRedirects(response, reverse("study:dashboard"))

    def test_login_is_locked_after_five_failed_attempts(self):
        factories.make_user("alice", pin="482731")
        for _ in range(5):
            response = self.client.post(
                reverse("study:login"),
                {"username": "alice", "pin": "000000"},
            )
            self.assertEqual(response.status_code, 200)

        response = self.client.post(
            reverse("study:login"),
            {"username": "alice", "pin": "482731"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertContains(response, "réessayez plus tard")

    def test_registration_is_limited_to_five_accounts_per_window(self):
        for number in range(5):
            response = self.client.post(
                reverse("study:register"),
                {
                    "username": f"user{number}",
                    "pin": "482731",
                    "pin_confirm": "482731",
                },
            )
            self.assertRedirects(response, reverse("study:recovery_codes"))
            self.client.post(reverse("study:logout"))

        throttle = LoginThrottle.objects.get()
        locked_until = throttle.locked_until
        response = self.client.post(
            reverse("study:register"),
            {
                "username": "usersix",
                "pin": "482731",
                "pin_confirm": "482731",
            },
        )

        self.assertEqual(get_user_model().objects.count(), 5)
        self.assertContains(response, "temporairement indisponible")
        throttle.refresh_from_db()
        self.assertEqual(throttle.locked_until, locked_until)

    def test_login_admission_is_bounded_per_client_address(self):
        for number in range(IP_ATTEMPT_LIMIT):
            self.client.post(
                reverse("study:login"),
                {"username": f"missing{number}", "pin": "482731"},
            )
        row_count = LoginThrottle.objects.count()

        self.client.post(
            reverse("study:login"),
            {"username": "oneattempttoomany", "pin": "482731"},
        )

        self.assertEqual(LoginThrottle.objects.count(), row_count)

    def test_account_lock_cannot_be_bypassed_with_multiple_addresses(self):
        factories.make_user("global-lock", pin="482731")
        for number in range(ACCOUNT_FAILURE_LIMIT):
            self.client.post(
                reverse("study:login"),
                {"username": "global-lock", "pin": "000000"},
                REMOTE_ADDR=f"198.51.100.{number + 1}",
            )

        response = self.client.post(
            reverse("study:login"),
            {"username": "global-lock", "pin": "482731"},
            REMOTE_ADDR="203.0.113.99",
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertContains(response, "réessayez plus tard")

    def test_successful_logins_do_not_consume_shared_address_limit(self):
        factories.make_user("successful", pin="482731")
        for _ in range(IP_ATTEMPT_LIMIT + 1):
            response = self.client.post(
                reverse("study:login"),
                {"username": "successful", "pin": "482731"},
                REMOTE_ADDR="198.51.100.40",
            )
            self.assertEqual(response.status_code, 302)
            self.client.post(reverse("study:logout"))

        request = RequestFactory().get(
            "/login/",
            REMOTE_ADDR="198.51.100.40",
        )
        throttle = LoginThrottle.objects.get(
            pk=login_throttle_key(request, "", purpose="login-ip")
        )
        self.assertEqual(throttle.failures, 0)
        self.assertIsNone(throttle.locked_until)

    def test_registration_preserves_safe_next_through_recovery_codes(self):
        response = self.client.post(
            reverse("study:register"),
            {
                "username": "next-user",
                "pin": "482731",
                "pin_confirm": "482731",
                "next": "/stats/",
            },
        )
        self.assertRedirects(
            response,
            reverse("study:recovery_codes"),
            fetch_redirect_response=False,
        )

        codes_page = self.client.get(reverse("study:recovery_codes"))

        self.assertContains(codes_page, 'href="/stats/"')

    def test_recovery_codes_are_displayed_once(self):
        self.client.post(
            reverse("study:register"),
            {
                "username": "codes-user",
                "pin": "482731",
                "pin_confirm": "482731",
            },
        )
        codes = self.client.session["new_recovery_codes"]

        first = self.client.get(reverse("study:recovery_codes"))
        second = self.client.get(reverse("study:recovery_codes"))

        self.assertEqual(len(codes), 8)
        self.assertContains(first, codes[0])
        self.assertNotContains(second, codes[0])

    def test_recovery_code_rotates_pin_and_all_codes(self):
        user = factories.make_user("recover-me", pin="482731")
        original_codes = generate_recovery_codes(user)
        original_digests = set(
            AccountRecoveryCode.objects.filter(user=user).values_list(
                "token_digest",
                flat=True,
            )
        )

        response = self.client.post(
            reverse("study:recover_account"),
            {
                "username": "RECOVER-ME",
                "recovery_code": original_codes[0].lower(),
                "new_pin": "731284",
                "new_pin_confirm": "731284",
            },
        )

        self.assertRedirects(
            response,
            reverse("study:recovery_codes"),
            fetch_redirect_response=False,
        )
        user.refresh_from_db()
        self.assertTrue(user.check_password("731284"))
        self.assertFalse(user.check_password("482731"))
        replacement_digests = set(
            AccountRecoveryCode.objects.filter(user=user).values_list(
                "token_digest",
                flat=True,
            )
        )
        self.assertEqual(len(replacement_digests), 8)
        self.assertFalse(original_digests & replacement_digests)

        self.client.post(reverse("study:logout"))
        reused = self.client.post(
            reverse("study:recover_account"),
            {
                "username": "recover-me",
                "recovery_code": original_codes[0],
                "new_pin": "111222",
                "new_pin_confirm": "111222",
            },
        )
        self.assertContains(reused, "Récupération impossible")

    def test_pin_change_keeps_current_session_and_invalidates_another(self):
        user = factories.make_user("pin-change", pin="482731")
        other_client = Client()
        self.client.force_login(user)
        other_client.force_login(user)

        response = self.client.post(
            reverse("study:change_pin"),
            {
                "current_pin": "482731",
                "new_pin": "731284",
                "new_pin_confirm": "731284",
            },
        )

        self.assertRedirects(
            response,
            reverse("study:settings") + "?pin_changed=1",
            fetch_redirect_response=False,
        )
        self.assertEqual(
            self.client.get(reverse("study:dashboard")).status_code,
            200,
        )
        self.assertRedirects(
            other_client.get(reverse("study:dashboard")),
            reverse("study:login") + "?next=/",
            fetch_redirect_response=False,
        )

    def test_account_export_contains_private_data_but_no_credentials(self):
        user = factories.make_user("export-user", pin="482731")
        provision_user_study_data(user)
        task = factories.make_task()
        Annotation.objects.create(
            user=user,
            task=task,
            kind=AnnotationKind.NOTE,
            body="A private note",
            study_later=True,
        )
        self.client.force_login(user)

        response = self.client.get(reverse("study:export_account"))
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertEqual(payload["account"]["username"], "export-user")
        self.assertEqual(payload["annotations"][0]["body"], "A private note")
        self.assertTrue(payload["annotations"][0]["study_later"])
        body = response.content.decode()
        self.assertNotIn("password", body)
        self.assertNotIn("token_digest", body)
        self.assertNotIn(user.password, body)

    def test_account_deletion_requires_pin_and_typed_username(self):
        user = factories.make_user("delete-user", pin="482731")
        provision_user_study_data(user)
        self.client.force_login(user)

        rejected = self.client.post(
            reverse("study:delete_account"),
            {
                "current_pin": "000000",
                "username_confirmation": "delete-user",
            },
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertTrue(get_user_model().objects.filter(pk=user.pk).exists())

        deleted = self.client.post(
            reverse("study:delete_account"),
            {
                "current_pin": "482731",
                "username_confirmation": "delete-user",
            },
        )
        self.assertRedirects(
            deleted,
            reverse("study:login") + "?deleted=1",
            fetch_redirect_response=False,
        )
        self.assertFalse(get_user_model().objects.filter(pk=user.pk).exists())

    @override_settings(TRUST_X_FORWARDED_FOR=True)
    def test_throttle_uses_rightmost_address_from_trusted_proxy(self):
        factory = RequestFactory()
        first = factory.get(
            "/login/",
            REMOTE_ADDR="10.0.0.8",
            HTTP_X_FORWARDED_FOR="198.51.100.2, 203.0.113.7",
        )
        spoofed_left = factory.get(
            "/login/",
            REMOTE_ADDR="10.0.0.8",
            HTTP_X_FORWARDED_FOR="192.0.2.99, 203.0.113.7",
        )
        other_client = factory.get(
            "/login/",
            REMOTE_ADDR="10.0.0.8",
            HTTP_X_FORWARDED_FOR="198.51.100.2, 203.0.113.8",
        )

        self.assertEqual(
            login_throttle_key(first, "alice"),
            login_throttle_key(spoofed_left, "alice"),
        )
        self.assertNotEqual(
            login_throttle_key(first, "alice"),
            login_throttle_key(other_client, "alice"),
        )

    @override_settings(
        TRUST_X_FORWARDED_FOR=True,
        TRUSTED_PROXY_CIDRS=[
            "10.0.0.0/8",
            "173.245.48.0/20",
        ],
    )
    def test_throttle_skips_only_configured_proxy_hops(self):
        factory = RequestFactory()
        cloudflare_request = factory.get(
            "/login/",
            REMOTE_ADDR="10.0.0.8",
            HTTP_X_FORWARDED_FOR=(
                "192.0.2.99, 198.51.100.2, 173.245.48.4, 10.0.0.9"
            ),
        )
        direct_request = factory.get(
            "/login/",
            REMOTE_ADDR="10.0.0.8",
            HTTP_X_FORWARDED_FOR=(
                "192.0.2.99, 203.0.113.7, 10.0.0.9"
            ),
        )
        cloudflare_client = factory.get(
            "/login/",
            REMOTE_ADDR="198.51.100.2",
        )
        direct_client = factory.get(
            "/login/",
            REMOTE_ADDR="203.0.113.7",
        )

        self.assertEqual(
            login_throttle_key(cloudflare_request, "alice"),
            login_throttle_key(cloudflare_client, "alice"),
        )
        self.assertEqual(
            login_throttle_key(direct_request, "alice"),
            login_throttle_key(direct_client, "alice"),
        )

    @override_settings(TRUST_X_FORWARDED_FOR=False)
    def test_untrusted_forwarded_address_is_ignored(self):
        factory = RequestFactory()
        first = factory.get(
            "/login/",
            REMOTE_ADDR="203.0.113.7",
            HTTP_X_FORWARDED_FOR="192.0.2.1",
        )
        second = factory.get(
            "/login/",
            REMOTE_ADDR="203.0.113.7",
            HTTP_X_FORWARDED_FOR="192.0.2.2",
        )

        self.assertEqual(
            login_throttle_key(first, "alice"),
            login_throttle_key(second, "alice"),
        )

    def test_stale_throttle_rows_are_pruned(self):
        old = LoginThrottle.objects.create(key_hash="a" * 64)
        LoginThrottle.objects.filter(pk=old.pk).update(
            updated_at=timezone.now() - timedelta(days=3)
        )

        reserve_throttled_action("b" * 64)

        self.assertFalse(LoginThrottle.objects.filter(pk=old.pk).exists())

    def test_import_does_not_give_legacy_progress_to_an_admin(self):
        admin = get_user_model().objects.create_superuser(
            username="admin",
            password="482731",
        )
        self.client.force_login(admin)
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Settings.objects.filter(user=admin).exists())
        self.assertFalse(ReviewSession.objects.filter(user=admin).exists())

        Settings.objects.create(user=admin)

        call_command("import_content", stdout=StringIO())

        self.assertFalse(users_with_study_state().filter(pk=admin.pk).exists())
        self.assertEqual(Card.objects.filter(user=admin).count(), 0)
        self.assertEqual(Card.objects.filter(user__isnull=True).count(), 8716)

        learner = get_user_model().objects.create_user(
            username="learner",
            password="482731",
        )
        provision_user_study_data(learner)

        self.assertEqual(Card.objects.filter(user=learner).count(), 8716)
        self.assertFalse(
            Card.objects.filter(
                user=learner,
                card_type="phrase_recog",
                phrase__tier__in=["response", "subject", "comprehension"],
            ).exists()
        )
        self.assertFalse(Card.objects.filter(user__isnull=True).exists())

    def test_legacy_claim_merges_preexisting_user_rows(self):
        part = factories.make_part("eo")
        task = factories.make_task(part, "tache-3")
        theme = factories.make_theme(task=task)
        family = factories.make_family()
        response = factories.make_response(theme=theme, family=family)
        card = Card.objects.create(card_type="spine", response=response)
        learner = get_user_model().objects.create_user(
            username="learner",
            password="482731",
        )
        Settings.objects.create(
            user=learner,
            new_cards_per_day=1,
            max_reviews_per_day=2,
        )
        Settings.objects.create(
            new_cards_per_day=33,
            max_reviews_per_day=444,
        )
        ReviewSession.objects.create(user=learner)
        ReviewSession.objects.create(
            current_card=card,
            scope={"kind": "spine"},
            presentation_token="legacy-token",
        )

        provision_user_study_data(learner)

        settings = Settings.load(learner)
        session = ReviewSession.load(learner)
        self.assertEqual(settings.new_cards_per_day, 33)
        self.assertEqual(settings.max_reviews_per_day, 444)
        self.assertEqual(session.current_card_id, card.pk)
        self.assertEqual(session.scope, {"kind": "spine"})
        self.assertEqual(session.presentation_token, "legacy-token")
        self.assertFalse(Settings.objects.filter(user__isnull=True).exists())
        self.assertFalse(ReviewSession.objects.filter(user__isnull=True).exists())

    def test_expired_ajax_session_returns_json_401(self):
        requests = (
            ("get", "study:review_next"),
            ("post", "study:review_answer"),
        )
        for method, route in requests:
            with self.subTest(route=route):
                response = getattr(self.client, method)(
                    reverse(route),
                    HTTP_X_REQUESTED_WITH="fetch",
                )
                self.assertEqual(response.status_code, 401)
                self.assertIn("session", response.json()["error"].lower())
                self.assertIn("no-store", response["Cache-Control"])
                self.assertEqual(
                    response.json()["login_url"],
                    "/login/?next=/review/",
                )

    def test_offline_page_never_exposes_account_details(self):
        user = get_user_model().objects.create_user(
            username="privateuser",
            password="482731",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("offline"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Vous êtes hors ligne")
        self.assertContains(response, "Réessayer")
        self.assertNotContains(response, user.username)
        self.assertNotContains(response, "Se déconnecter")

    def test_authenticated_pages_are_not_browser_cacheable(self):
        user = get_user_model().objects.create_user(
            username="privateuser",
            password="482731",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("study:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class UserProgressIsolationTests(TestCase):
    def setUp(self):
        self.part = factories.make_part("eo")
        self.task = factories.make_task(self.part, "tache-3")
        self.theme = factories.make_theme("culture", task=self.task)
        self.response = factories.make_response(theme=self.theme)
        self.phrase = factories.make_phrase()
        self.phrase.source_prompts.add(self.response.prompts.first())
        self.first = factories.make_user("first")
        self.second = factories.make_user("second")
        provision_user_study_data(self.first)
        provision_user_study_data(self.second)

    def test_each_user_gets_distinct_cards_and_queue(self):
        first_ids = set(
            Card.objects.filter(user=self.first).values_list("pk", flat=True)
        )
        second_ids = set(
            Card.objects.filter(user=self.second).values_list("pk", flat=True)
        )
        self.assertEqual(len(first_ids), 3)
        self.assertEqual(len(second_ids), 3)
        self.assertFalse(first_ids & second_ids)
        self.assertEqual(queue.queue_counts(user=self.first)["new_total"], 3)
        self.assertEqual(queue.queue_counts(user=self.second)["new_total"], 3)

    def test_review_revisit_stats_settings_session_and_undo_are_isolated(self):
        first_card = Card.objects.get(
            user=self.first,
            card_type="spine",
        )
        second_card = Card.objects.get(
            user=self.second,
            card_type="spine",
        )
        srs.review(first_card, Rating.GOOD)
        first_card.needs_revisit = True
        first_card.revisit_added_at = timezone.now()
        first_card.save(update_fields=["needs_revisit", "revisit_added_at"])
        first_settings = Settings.load(self.first)
        second_settings = Settings.load(self.second)
        first_settings.new_cards_per_day = 2
        first_settings.save(update_fields=["new_cards_per_day"])
        first_session = ReviewSession.load(self.first)
        first_session.current_card = first_card
        first_session.save(update_fields=["current_card"])

        self.assertEqual(ReviewLog.objects.filter(user=self.first).count(), 1)
        self.assertEqual(ReviewLog.objects.filter(user=self.second).count(), 0)
        self.assertEqual(
            queue.queue_counts(
                {"kind": "revisit"},
                user=self.first,
            )["revisit_total"],
            1,
        )
        self.assertEqual(
            queue.queue_counts(
                {"kind": "revisit"},
                user=self.second,
            )["revisit_total"],
            0,
        )
        self.assertEqual(first_settings.new_cards_per_day, 2)
        self.assertEqual(second_settings.new_cards_per_day, 15)
        self.assertIsNone(ReviewSession.load(self.second).current_card)
        self.assertIsNone(srs.undo_last(self.second))
        self.assertEqual(srs.undo_last(self.first), first_card)
        second_card.refresh_from_db()
        self.assertEqual(second_card.state, CardState.NEW)

    def test_reset_only_clears_logged_in_users_progress(self):
        first_card = Card.objects.get(
            user=self.first,
            card_type="spine",
        )
        second_card = Card.objects.get(
            user=self.second,
            card_type="spine",
        )
        srs.review(first_card, Rating.GOOD)
        srs.review(second_card, Rating.GOOD)
        self.client.force_login(self.first)

        self.client.post(
            reverse("study:reset_progress"),
            {
                "current_pin": "123456",
                "confirmation": "REINITIALISER",
            },
        )

        first_card.refresh_from_db()
        second_card.refresh_from_db()
        self.assertEqual(first_card.state, CardState.NEW)
        self.assertNotEqual(second_card.state, CardState.NEW)
        self.assertFalse(ReviewLog.objects.filter(user=self.first).exists())
        self.assertTrue(ReviewLog.objects.filter(user=self.second).exists())

    def test_user_cannot_grade_another_users_card(self):
        other_card = Card.objects.get(
            user=self.first,
            card_type="spine",
        )
        self.client.force_login(self.second)
        session = ReviewSession.load(self.second)
        session.current_card = other_card
        session.scope = {}
        session.presentation_token = "known-token"
        session.save(
            update_fields=[
                "current_card",
                "scope",
                "presentation_token",
            ]
        )

        response = self.client.post(
            reverse("study:review_answer"),
            {
                "card_id": other_card.pk,
                "action": "correct",
                "presentation_token": "known-token",
            },
        )
        self.assertEqual(response.status_code, 404)
        other_card.refresh_from_db()
        self.assertEqual(other_card.state, CardState.NEW)
