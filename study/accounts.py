from __future__ import annotations

import hashlib
import ipaddress
from datetime import timedelta

from django.conf import settings as django_settings
from django.contrib.auth import authenticate, get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    Card,
    CardType,
    LoginThrottle,
    Phrase,
    Response,
    ReviewLog,
    ReviewSession,
    Settings,
)

LOGIN_WINDOW = timedelta(minutes=15)
LOGIN_LOCK = timedelta(minutes=15)
LOGIN_FAILURE_LIMIT = 5
IP_ATTEMPT_LIMIT = 30
THROTTLE_RETENTION = timedelta(hours=1)


def provision_user_study_data(user) -> None:
    """Create a private deck, claiming legacy progress for the first account."""
    with transaction.atomic():
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
                for response in Response.objects.exclude(pk__in=existing_responses)
            ],
            ignore_conflicts=True,
        )

        for card_type in (
            CardType.PHRASE_PRODUCTION,
            CardType.PHRASE_RECOGNITION,
        ):
            existing_phrases = set(
                Card.objects.filter(
                    user=user,
                    card_type=card_type,
                ).values_list("phrase_id", flat=True)
            )
            Card.objects.bulk_create(
                [
                    Card(user=user, card_type=card_type, phrase=phrase)
                    for phrase in Phrase.objects.exclude(pk__in=existing_phrases)
                ],
                ignore_conflicts=True,
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
    candidate = remote_addr
    if django_settings.TRUST_X_FORWARDED_FOR:
        forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded_for:
            candidate = forwarded_for.rsplit(",", 1)[-1].strip()
    try:
        return ipaddress.ip_address(candidate).compressed
    except ValueError:
        try:
            return ipaddress.ip_address(remote_addr).compressed
        except ValueError:
            return "unknown"


def login_throttle_key(request, username: str, *, purpose: str = "login") -> str:
    value = f"{purpose}|{username.lower()}|{_client_address(request)}".encode()
    return hashlib.sha256(value).hexdigest()


def _prune_stale_throttles(now) -> None:
    LoginThrottle.objects.filter(
        updated_at__lt=now - THROTTLE_RETENTION
    ).delete()


def _locked_throttle(key_hash: str, now):
    throttle, _ = LoginThrottle.objects.select_for_update().get_or_create(
        pk=key_hash,
        defaults={"window_started_at": now},
    )
    if throttle.locked_until and throttle.locked_until > now:
        return throttle, True
    if throttle.window_started_at <= now - LOGIN_WINDOW:
        throttle.failures = 0
        throttle.window_started_at = now
        throttle.locked_until = None
    return throttle, False


def _record_throttled_attempt(
    throttle,
    now,
    *,
    limit=LOGIN_FAILURE_LIMIT,
) -> None:
    throttle.failures += 1
    if throttle.failures >= limit:
        throttle.locked_until = now + LOGIN_LOCK
    throttle.save(
        update_fields=[
            "failures",
            "window_started_at",
            "locked_until",
            "updated_at",
        ]
    )


def authenticate_with_throttle(request, username: str, pin: str):
    """Serialize attempts per username/address and authenticate below the cap."""
    now = timezone.now()
    _prune_stale_throttles(now)
    ip_key = login_throttle_key(request, "", purpose="login-ip")
    username_key = login_throttle_key(request, username)
    with transaction.atomic():
        ip_throttle, ip_locked = _locked_throttle(ip_key, now)
        if ip_locked:
            return None, True
        _record_throttled_attempt(
            ip_throttle,
            now,
            limit=IP_ATTEMPT_LIMIT,
        )

        username_throttle, username_locked = _locked_throttle(
            username_key,
            now,
        )
        if username_locked:
            return None, True
        user = authenticate(request, username=username, password=pin)
        if user is not None:
            username_throttle.delete()
            return user, False
        _record_throttled_attempt(username_throttle, now)
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
