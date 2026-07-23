from __future__ import annotations

import ipaddress
import secrets
from datetime import timedelta

from django.conf import settings as django_settings
from django.contrib.auth import authenticate, get_user_model
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import (
    AccountRecoveryCode,
    Card,
    CardType,
    LoginThrottle,
    Phrase,
    PhraseTier,
    Response,
    ReviewLog,
    ReviewSession,
    Settings,
)

LOGIN_WINDOW = timedelta(minutes=15)
LOGIN_LOCK = timedelta(minutes=15)
LOGIN_FAILURE_LIMIT = 5
IP_ATTEMPT_LIMIT = 30
ACCOUNT_FAILURE_LIMIT = 8
ACCOUNT_WINDOW = timedelta(hours=24)
ACCOUNT_LOCK_MAX = timedelta(hours=4)
THROTTLE_RETENTION = timedelta(days=2)
RECOVERY_CODE_COUNT = 8
RECOVERY_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
STUDY_DATA_LOCK_ID = 20341866963359064
STUDY_DATA_BATCH_SIZE = 500


def acquire_study_data_lock() -> None:
    """Serialize content imports with user deck provisioning on PostgreSQL."""
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                [STUDY_DATA_LOCK_ID],
            )


def provision_user_study_data(user) -> None:
    """Create a private deck, claiming legacy progress for the first account."""
    with transaction.atomic():
        acquire_study_data_lock()
        is_first_user = not get_user_model().objects.exclude(pk=user.pk).exists()
        has_owned_cards = Card.objects.filter(user__isnull=False).exists()
        if is_first_user or not has_owned_cards:
            Card.objects.filter(user__isnull=True).update(user=user)
            ReviewLog.objects.filter(
                user__isnull=True,
                card__user=user,
            ).update(user=user)

            existing_settings = (
                Settings.objects.select_for_update().filter(user=user).first()
            )
            legacy_settings = (
                Settings.objects.select_for_update()
                .filter(user__isnull=True)
                .order_by("pk")
                .first()
            )
            if legacy_settings:
                if existing_settings:
                    existing_settings.new_cards_per_day = (
                        legacy_settings.new_cards_per_day
                    )
                    existing_settings.max_reviews_per_day = (
                        legacy_settings.max_reviews_per_day
                    )
                    existing_settings.save(
                        update_fields=[
                            "new_cards_per_day",
                            "max_reviews_per_day",
                        ]
                    )
                    legacy_settings.delete()
                else:
                    legacy_settings.user = user
                    legacy_settings.save(update_fields=["user"])

            existing_session = (
                ReviewSession.objects.select_for_update().filter(user=user).first()
            )
            legacy_session = (
                ReviewSession.objects.select_for_update()
                .filter(user__isnull=True)
                .order_by("pk")
                .first()
            )
            if legacy_session:
                if existing_session:
                    existing_session.current_card = legacy_session.current_card
                    existing_session.previous_card = legacy_session.previous_card
                    existing_session.previous_review = (
                        legacy_session.previous_review
                    )
                    existing_session.scope = legacy_session.scope
                    existing_session.revisit_seen_card_ids = (
                        legacy_session.revisit_seen_card_ids
                    )
                    existing_session.presentation_token = (
                        legacy_session.presentation_token
                    )
                    existing_session.save(
                        update_fields=[
                            "current_card",
                            "previous_card",
                            "previous_review",
                            "scope",
                            "revisit_seen_card_ids",
                            "presentation_token",
                            "updated_at",
                        ]
                    )
                    legacy_session.delete()
                else:
                    legacy_session.user = user
                    legacy_session.save(update_fields=["user"])

        existing_responses = set(
            Card.objects.filter(
                user=user,
                card_type=CardType.SPINE,
            ).values_list("response_id", flat=True)
        )
        Card.objects.bulk_create(
            [
                Card(user=user, card_type=CardType.SPINE, response=response)
                for response in Response.objects.filter(is_active=True).exclude(
                    pk__in=existing_responses
                )
            ],
            ignore_conflicts=True,
            batch_size=STUDY_DATA_BATCH_SIZE,
        )

        phrase_card_types = (
            (
                CardType.PHRASE_PRODUCTION,
                Phrase.objects.filter(is_active=True),
            ),
            (
                CardType.PHRASE_RECOGNITION,
                Phrase.objects.filter(
                    is_active=True,
                    tier=PhraseTier.SHARED,
                ),
            ),
        )
        for card_type, phrases in phrase_card_types:
            existing_phrases = set(
                Card.objects.filter(
                    user=user,
                    card_type=card_type,
                ).values_list("phrase_id", flat=True)
            )
            Card.objects.bulk_create(
                [
                    Card(user=user, card_type=card_type, phrase=phrase)
                    for phrase in phrases.exclude(pk__in=existing_phrases)
                ],
                ignore_conflicts=True,
                batch_size=STUDY_DATA_BATCH_SIZE,
            )

        Settings.load(user)
        ReviewSession.load(user)


def users_with_study_state():
    """Return interactive learners while leaving unrelated admin users alone."""
    learner_marker = Q(is_staff=False, is_superuser=False) & (
        Q(study_settings__isnull=False) | Q(review_session__isnull=False)
    )
    return (
        get_user_model()
        .objects.filter(
            Q(study_cards__isnull=False)
            | learner_marker
        )
        .distinct()
        .order_by("pk")
    )


def _client_address(request) -> str:
    remote_addr = request.META.get("REMOTE_ADDR", "unknown")
    candidate = None
    if django_settings.TRUST_X_FORWARDED_FOR:
        forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded_for:
            try:
                forwarded = [
                    ipaddress.ip_address(value.strip())
                    for value in forwarded_for.split(",")
                ]
                trusted_networks = [
                    ipaddress.ip_network(value)
                    for value in django_settings.TRUSTED_PROXY_CIDRS
                ]
            except ValueError:
                forwarded = []
                trusted_networks = []
            if forwarded:
                candidate = forwarded[-1]
                if trusted_networks:
                    candidate = next(
                        (
                            address
                            for address in reversed(forwarded)
                            if not any(
                                address in network
                                for network in trusted_networks
                            )
                        ),
                        None,
                    )
    if candidate is not None:
        return candidate.compressed
    try:
        return ipaddress.ip_address(remote_addr).compressed
    except ValueError:
        return "unknown"


def login_throttle_key(request, username: str, *, purpose: str = "login") -> str:
    value = f"{purpose}|{username.lower()}|{_client_address(request)}"
    return salted_hmac(
        "study.login-throttle",
        value,
        secret=django_settings.SECRET_KEY,
        algorithm="sha256",
    ).hexdigest()


def account_throttle_key(username: str, *, purpose: str = "login") -> str:
    return salted_hmac(
        "study.account-throttle",
        f"{purpose}|{username.lower()}",
        secret=django_settings.SECRET_KEY,
        algorithm="sha256",
    ).hexdigest()


def _prune_stale_throttles(now) -> None:
    LoginThrottle.objects.filter(
        updated_at__lt=now - THROTTLE_RETENTION
    ).delete()


def _locked_throttle(key_hash: str, now, *, window=LOGIN_WINDOW):
    throttle, _ = LoginThrottle.objects.select_for_update().get_or_create(
        pk=key_hash,
        defaults={"window_started_at": now},
    )
    if throttle.locked_until and throttle.locked_until > now:
        return throttle, True
    if throttle.window_started_at <= now - window:
        throttle.failures = 0
        throttle.window_started_at = now
        throttle.locked_until = None
    return throttle, False


def _record_throttled_attempt(
    throttle,
    now,
    *,
    limit=LOGIN_FAILURE_LIMIT,
    progressive=False,
) -> None:
    throttle.failures += 1
    if throttle.failures >= limit:
        lock = LOGIN_LOCK
        if progressive:
            multiplier = 2 ** (throttle.failures - limit)
            lock = min(LOGIN_LOCK * multiplier, ACCOUNT_LOCK_MAX)
        throttle.locked_until = now + lock
    throttle.save(
        update_fields=[
            "failures",
            "window_started_at",
            "locked_until",
            "updated_at",
        ]
    )


def _acquire_throttle_tiers(
    request,
    username: str,
    now,
    *,
    ip_purpose: str,
    pair_purpose: str,
    account_purpose: str,
):
    """Lock the IP, username-pair, and account throttle tiers.

    Returns ``(throttles, locked)`` where ``throttles`` is the
    ``(ip, pair, account)`` triple (or ``None`` when a tier is locked).
    """
    ip_key = login_throttle_key(request, "", purpose=ip_purpose)
    pair_key = login_throttle_key(request, username, purpose=pair_purpose)
    account_key = account_throttle_key(username, purpose=account_purpose)
    ip_throttle, ip_locked = _locked_throttle(ip_key, now)
    if ip_locked:
        return None, True
    pair_throttle, pair_locked = _locked_throttle(pair_key, now)
    if pair_locked:
        return None, True
    account_throttle, account_locked = _locked_throttle(
        account_key,
        now,
        window=ACCOUNT_WINDOW,
    )
    if account_locked:
        return None, True
    return (ip_throttle, pair_throttle, account_throttle), False


def _record_throttle_tier_failure(throttles, now) -> None:
    """Record one failed attempt across the IP/pair/account throttle tiers."""
    ip_throttle, pair_throttle, account_throttle = throttles
    _record_throttled_attempt(ip_throttle, now, limit=IP_ATTEMPT_LIMIT)
    _record_throttled_attempt(pair_throttle, now)
    _record_throttled_attempt(
        account_throttle,
        now,
        limit=ACCOUNT_FAILURE_LIMIT,
        progressive=True,
    )


def authenticate_with_throttle(request, username: str, pin: str):
    """Authenticate under per-address, pair, and account-wide failure caps."""
    now = timezone.now()
    _prune_stale_throttles(now)
    with transaction.atomic():
        throttles, locked = _acquire_throttle_tiers(
            request,
            username,
            now,
            ip_purpose="login-ip",
            pair_purpose="login",
            account_purpose="login",
        )
        if locked:
            return None, True

        user = authenticate(request, username=username, password=pin)
        if user is not None:
            _, pair_throttle, account_throttle = throttles
            pair_throttle.delete()
            account_throttle.delete()
            return user, False

        _record_throttle_tier_failure(throttles, now)
        return None, False


def reserve_throttled_action(key_hash: str, now=None) -> bool:
    """Atomically reserve one rate-limited action; return whether it was blocked."""
    now = now or timezone.now()
    _prune_stale_throttles(now)
    with transaction.atomic():
        throttle, locked = _locked_throttle(key_hash, now)
        if locked:
            return True
        _record_throttled_attempt(throttle, now)
        return False


def _normalize_recovery_code(code: str) -> str:
    return "".join(character for character in code.upper() if character.isalnum())


def _recovery_code_digest(code: str) -> str:
    return salted_hmac(
        "study.account-recovery",
        _normalize_recovery_code(code),
        secret=django_settings.SECRET_KEY,
        algorithm="sha256",
    ).hexdigest()


def _new_recovery_code() -> str:
    raw = "".join(
        secrets.choice(RECOVERY_CODE_ALPHABET) for _ in range(12)
    )
    return "-".join(raw[index : index + 4] for index in range(0, 12, 4))


def generate_recovery_codes(user) -> list[str]:
    """Replace a learner's codes and return the one-time plaintext values."""
    codes = []
    while len(codes) < RECOVERY_CODE_COUNT:
        code = _new_recovery_code()
        if code not in codes:
            codes.append(code)
    with transaction.atomic():
        AccountRecoveryCode.objects.filter(user=user).delete()
        AccountRecoveryCode.objects.bulk_create(
            [
                AccountRecoveryCode(
                    user=user,
                    token_digest=_recovery_code_digest(code),
                )
                for code in codes
            ]
        )
    return codes


def reset_pin_with_recovery(
    request,
    username: str,
    recovery_code: str,
    new_pin: str,
):
    """Consume a recovery code and rotate both the PIN and recovery codes."""
    now = timezone.now()
    _prune_stale_throttles(now)
    with transaction.atomic():
        throttles, locked = _acquire_throttle_tiers(
            request,
            username,
            now,
            ip_purpose="recovery-ip",
            pair_purpose="recovery",
            account_purpose="recovery",
        )
        if locked:
            return None, [], True

        user = (
            get_user_model()
            .objects.select_for_update()
            .filter(username__iexact=username)
            .order_by("pk")
            .first()
        )
        recovery = None
        if user is not None:
            recovery = (
                AccountRecoveryCode.objects.select_for_update()
                .filter(
                    user=user,
                    token_digest=_recovery_code_digest(recovery_code),
                    used_at__isnull=True,
                )
                .first()
            )
        if recovery is None:
            _record_throttle_tier_failure(throttles, now)
            return None, [], False

        recovery.used_at = now
        recovery.save(update_fields=["used_at"])
        user.set_password(new_pin)
        user.save(update_fields=["password"])
        _, pair_throttle, account_throttle = throttles
        pair_throttle.delete()
        account_throttle.delete()
        codes = generate_recovery_codes(user)
        return user, codes, False
