"""Data models for the EO T3 French oral-exam flashcards app.

Two layers:

* Content — imported from the markdown/TSV answer bank and treated as the
  source of truth: Theme, Family, Response (a unique argued answer), its
  Arguments, the Prompts that map onto it, and reusable Phrases.
* Study — one reviewable ``Card`` per user and studyable item, carrying isolated
  SM-2 spaced-repetition state, plus a ``ReviewLog`` for every grade.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings as django_settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class ExamPart(models.Model):
    """A top-level expression component, such as oral or written work."""

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=64, unique=True)
    short_name = models.CharField(max_length=32)
    emoji = models.CharField(max_length=8, default="📝")
    color = models.CharField(max_length=7, default="#6366f1")
    order = models.PositiveIntegerField(default=0)
    available = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self) -> str:
        return self.name


class Task(models.Model):
    """A task within an exam part, e.g. Tâche 2 or Tâche 3."""

    slug = models.SlugField(max_length=64)
    part = models.ForeignKey(
        ExamPart, on_delete=models.CASCADE, related_name="tasks"
    )
    name = models.CharField(max_length=64)
    subtitle = models.CharField(max_length=160, blank=True)
    emoji = models.CharField(max_length=8, default="🎯")
    color = models.CharField(max_length=7, default="#6366f1")
    order = models.PositiveIntegerField(default=0)
    available = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["part__order", "order", "name"]
        unique_together = ("part", "slug")

    def __str__(self) -> str:
        return f"{self.part.short_name} · {self.name}"


class Theme(models.Model):
    """A French exam theme, e.g. Culture or Santé."""

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=64, unique=True)
    display_name = models.CharField(max_length=64)
    order = models.PositiveIntegerField(default=0)
    color = models.CharField(max_length=7, default="#6366f1")
    emoji = models.CharField(max_length=8, default="📘")
    is_active = models.BooleanField(default=True, db_index=True)
    task = models.ForeignKey(
        Task,
        on_delete=models.SET_NULL,
        related_name="themes",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["order", "name"]

    def __str__(self) -> str:
        return self.display_name


class Family(models.Model):
    """A topic family grouping related prompts (17 total)."""

    slug = models.SlugField(unique=True, max_length=120)
    name = models.CharField(max_length=200, unique=True)
    content_key = models.CharField(max_length=120, unique=True)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["order", "name"]
        verbose_name_plural = "families"

    def __str__(self) -> str:
        return self.name


class Response(models.Model):
    """A single argued answer (the memorizable "spine").

    167 prompts collapse into 130 unique responses; equivalent prompts share
    one Response and appear as its aliases.
    """

    content_key = models.CharField(max_length=120, unique=True)
    body_hash = models.CharField(max_length=64, db_index=True)
    theme = models.ForeignKey(
        Theme, on_delete=models.CASCADE, related_name="responses"
    )
    family = models.ForeignKey(
        Family, on_delete=models.CASCADE, related_name="responses"
    )
    prompt = models.TextField(help_text="Canonical prompt text.")
    reformulation = models.TextField(blank=True)
    position = models.TextField(blank=True)
    position_claire = models.TextField(blank=True)
    nuance = models.TextField(blank=True)
    conclusion = models.TextField(blank=True)
    body = models.TextField()
    body_html = models.TextField()
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["theme__order", "id"]

    def __str__(self) -> str:
        return f"{self.theme.display_name}: {self.prompt[:60]}"

    @property
    def canonical_prompt(self) -> "Prompt | None":
        return self.prompts.filter(
            is_active=True,
            is_canonical=True,
        ).first()

    @property
    def alias_prompts(self):
        """Prompts other than the canonical one."""
        return self.prompts.filter(
            is_active=True,
            is_canonical=False,
        )

    @property
    def has_aliases(self) -> bool:
        return self.prompts.filter(is_active=True).count() > 1


class Argument(models.Model):
    """One of the three developed arguments of a Response."""

    response = models.ForeignKey(
        Response, on_delete=models.CASCADE, related_name="arguments"
    )
    order = models.PositiveSmallIntegerField()
    idea = models.TextField()
    developpement = models.TextField(blank=True)
    exemple = models.TextField(blank=True)
    consequence = models.TextField(blank=True)

    class Meta:
        ordering = ["order"]
        unique_together = ("response", "order")

    def __str__(self) -> str:
        return f"Arg {self.order}: {self.idea[:50]}"


class PersonalResponse(models.Model):
    """A learner-owned response version; the shared exam prompt never changes."""

    user = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="personal_responses",
    )
    response = models.ForeignKey(
        Response,
        on_delete=models.CASCADE,
        related_name="personal_versions",
    )
    reformulation = models.TextField(blank=True)
    position = models.TextField(blank=True)
    position_claire = models.TextField(blank=True)
    arguments = models.JSONField(default=list)
    nuance = models.TextField(blank=True)
    conclusion = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "response"],
                name="unique_user_personal_response",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "updated_at"]),
        ]


class Prompt(models.Model):
    """A prompt as numbered inside a theme; maps onto exactly one Response."""

    response = models.ForeignKey(
        Response, on_delete=models.CASCADE, related_name="prompts"
    )
    content_key = models.CharField(max_length=120, unique=True)
    theme = models.ForeignKey(
        Theme, on_delete=models.CASCADE, related_name="prompts"
    )
    family = models.ForeignKey(
        Family, on_delete=models.CASCADE, related_name="prompts"
    )
    number = models.PositiveIntegerField()
    text = models.TextField()
    is_canonical = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["theme__order", "number"]
        unique_together = ("theme", "number")

    def __str__(self) -> str:
        return f"{self.theme.display_name} P{self.number}"

    @property
    def label(self) -> str:
        return f"{self.theme.display_name} P{self.number}"


class PhraseCategory(models.Model):
    """A grouping of reusable expressions, e.g. « Nuancer et comparer »."""

    slug = models.SlugField(unique=True, max_length=120)
    name = models.CharField(max_length=120, unique=True)
    content_key = models.CharField(max_length=120, unique=True)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["order", "name"]
        verbose_name_plural = "phrase categories"

    def __str__(self) -> str:
        return self.name


class PhraseTier(models.TextChoices):
    SHARED = "shared", "Shared catalog"
    RESPONSE = "response", "Response vocabulary"


class Phrase(models.Model):
    """A reusable French chunk with an English cue and a grounded example."""

    phrase_id = models.CharField(max_length=16, unique=True)
    tier = models.CharField(
        max_length=8,
        choices=PhraseTier.choices,
        default=PhraseTier.RESPONSE,
        db_index=True,
    )
    category = models.ForeignKey(
        PhraseCategory, on_delete=models.CASCADE, related_name="phrases"
    )
    english_cue = models.CharField(max_length=200)
    expression = models.CharField(max_length=300)
    anchor = models.CharField(max_length=300)
    example = models.TextField()
    note = models.TextField(blank=True)
    sources_raw = models.TextField(blank=True)
    source_prompts = models.ManyToManyField(
        Prompt, related_name="phrases", blank=True
    )
    order = models.PositiveIntegerField(default=0)
    lot_order = models.PositiveIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["order", "phrase_id"]

    def __str__(self) -> str:
        return f"{self.phrase_id}: {self.expression[:50]}"

    @property
    def cloze_example(self) -> str:
        """The example with the anchor blanked out for production drills."""
        lowered = self.example.lower()
        needle = self.anchor.lower()
        index = lowered.find(needle)
        if index < 0:
            return self.example
        return (
            self.example[:index]
            + "……"
            + self.example[index + len(self.anchor):]
        )

    @property
    def example_html(self) -> str:
        """The example with the anchor wrapped in <mark> for display."""
        import html
        import re

        escaped = html.escape(self.example)
        if not self.anchor:
            return escaped
        match = re.search(
            re.escape(html.escape(self.anchor)), escaped, flags=re.IGNORECASE
        )
        if not match:
            return escaped
        start, end = match.span()
        return f"{escaped[:start]}<mark>{escaped[start:end]}</mark>{escaped[end:]}"


class ContentImportState(models.Model):
    """Fingerprint of the bundled content most recently imported."""

    key = models.CharField(max_length=32, primary_key=True)
    fingerprint = models.CharField(max_length=64)
    imported_at = models.DateTimeField(auto_now=True)


class AnnotationKind(models.TextChoices):
    NOTE = "note", "Note"
    HIGHLIGHT = "highlight", "Highlight"


class Annotation(models.Model):
    """A private note or persistent page highlight owned by one learner."""

    user = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="study_annotations",
    )
    task = models.ForeignKey(
        Task,
        on_delete=models.SET_NULL,
        related_name="annotations",
        null=True,
        blank=True,
    )
    kind = models.CharField(max_length=12, choices=AnnotationKind.choices)
    title = models.CharField(max_length=160, blank=True)
    body = models.TextField(blank=True)
    quote = models.TextField(blank=True)
    source_path = models.CharField(max_length=500, blank=True)
    source_key = models.CharField(max_length=200, blank=True)
    source_title = models.CharField(max_length=300, blank=True)
    start_offset = models.PositiveIntegerField(null=True, blank=True)
    end_offset = models.PositiveIntegerField(null=True, blank=True)
    prefix = models.CharField(max_length=160, blank=True)
    suffix = models.CharField(max_length=160, blank=True)
    study_later = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "user",
                    "source_path",
                    "source_key",
                    "start_offset",
                    "end_offset",
                ],
                condition=Q(kind=AnnotationKind.HIGHLIGHT),
                name="unique_user_source_highlight",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "task", "kind", "updated_at"]),
            models.Index(fields=["user", "source_path", "kind"]),
        ]

    def clean(self):
        super().clean()
        if self.kind == AnnotationKind.NOTE:
            if not self.body.strip() and not self.quote.strip():
                raise ValidationError("A note must contain text or a selected excerpt.")
            return
        if self.kind == AnnotationKind.HIGHLIGHT:
            if (
                not self.quote
                or self.start_offset is None
                or self.end_offset is None
                or self.end_offset <= self.start_offset
            ):
                raise ValidationError(
                    "A highlight requires selected text and valid page offsets."
                )

    def __str__(self) -> str:
        text = self.title or self.body or self.quote
        return f"{self.get_kind_display()}: {text[:60]}"


# --------------------------------------------------------------------------
# Study layer: spaced repetition
# --------------------------------------------------------------------------


class CardType(models.TextChoices):
    SPINE = "spine", "Response spine"
    PHRASE_PRODUCTION = "phrase_prod", "Phrase — production"
    PHRASE_RECOGNITION = "phrase_recog", "Phrase — recognition"


class CardState(models.TextChoices):
    NEW = "new", "New"
    LEARNING = "learning", "Learning"
    REVIEW = "review", "Review"
    RELEARNING = "relearning", "Relearning"


class Rating(models.IntegerChoices):
    AGAIN = 1, "Again"
    HARD = 2, "Hard"
    GOOD = 3, "Good"
    EASY = 4, "Easy"


class CardQuerySet(models.QuerySet):
    def current_content(self):
        return self.filter(
            Q(response__is_active=True) | Q(phrase__is_active=True)
        )

    def active(self):
        return self.current_content().filter(suspended=False)

    def due_reviews(self, now=None):
        now = now or timezone.now()
        return self.active().filter(
            state__in=[
                CardState.LEARNING,
                CardState.REVIEW,
                CardState.RELEARNING,
            ],
            due__lte=now,
        )

    def new_cards(self):
        return self.active().filter(state=CardState.NEW)


class Card(models.Model):
    """A reviewable item with its own SM-2 scheduling state."""

    user = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="study_cards",
        null=True,
        blank=True,
    )
    card_type = models.CharField(max_length=16, choices=CardType.choices)
    response = models.ForeignKey(
        Response,
        on_delete=models.CASCADE,
        related_name="cards",
        null=True,
        blank=True,
    )
    phrase = models.ForeignKey(
        Phrase,
        on_delete=models.CASCADE,
        related_name="cards",
        null=True,
        blank=True,
    )

    state = models.CharField(
        max_length=12, choices=CardState.choices, default=CardState.NEW
    )
    due = models.DateTimeField(null=True, blank=True, db_index=True)
    interval_days = models.FloatField(default=0.0)
    ease = models.FloatField(default=2.5)
    reps = models.PositiveIntegerField(default=0)
    lapses = models.PositiveIntegerField(default=0)
    learning_step = models.PositiveSmallIntegerField(default=0)
    last_reviewed = models.DateTimeField(null=True, blank=True)
    last_rating = models.PositiveSmallIntegerField(null=True, blank=True)
    needs_revisit = models.BooleanField(default=False, db_index=True)
    revisit_added_at = models.DateTimeField(null=True, blank=True)
    suspended = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    objects = CardQuerySet.as_manager()

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                name="card_has_exactly_one_target",
                check=(
                    Q(response__isnull=False, phrase__isnull=True)
                    | Q(response__isnull=True, phrase__isnull=False)
                ),
            ),
            models.UniqueConstraint(
                fields=["user", "card_type", "response"],
                name="unique_user_response_card",
            ),
            models.UniqueConstraint(
                fields=["user", "card_type", "phrase"],
                name="unique_user_phrase_card",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "state", "due"]),
            models.Index(fields=["state", "due"]),
            models.Index(fields=["card_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_card_type_display()} #{self.pk}"

    @property
    def theme(self) -> Theme | None:
        if self.response_id:
            return self.response.theme
        return None

    @property
    def is_new(self) -> bool:
        return self.state == CardState.NEW

    @property
    def is_due(self) -> bool:
        if self.state == CardState.NEW:
            return False
        return self.due is not None and self.due <= timezone.now()


class ReviewSession(models.Model):
    """Per-user pointer to the unfinished card and its deck scope."""

    user = models.OneToOneField(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="review_session",
        null=True,
        blank=True,
    )
    current_card = models.ForeignKey(
        Card,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    previous_card = models.ForeignKey(
        Card,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    previous_review = models.ForeignKey(
        "ReviewLog",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    scope = models.JSONField(default=dict, blank=True)
    revisit_seen_card_ids = models.JSONField(default=list, blank=True)
    presentation_token = models.CharField(max_length=64, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def load(cls, user=None) -> "ReviewSession":
        if user is not None and getattr(user, "is_authenticated", False):
            obj, _ = cls.objects.get_or_create(user=user)
            return obj
        obj = cls.objects.filter(user__isnull=True).order_by("pk").first()
        return obj or cls.objects.create()


class ReviewLog(models.Model):
    """One recorded grade, enabling retention and workload statistics."""

    user = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="study_review_logs",
        null=True,
        blank=True,
    )
    card = models.ForeignKey(
        Card, on_delete=models.CASCADE, related_name="reviews"
    )
    reviewed_at = models.DateTimeField(default=timezone.now, db_index=True)
    rating = models.PositiveSmallIntegerField(choices=Rating.choices)
    state_before = models.CharField(max_length=12, choices=CardState.choices)
    state_after = models.CharField(max_length=12, choices=CardState.choices)
    interval_before = models.FloatField(default=0.0)
    interval_after = models.FloatField(default=0.0)
    ease_before = models.FloatField(default=2.5)
    ease_after = models.FloatField(default=2.5)
    elapsed_ms = models.PositiveIntegerField(default=0)
    card_before = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-reviewed_at"]

    def __str__(self) -> str:
        return f"{self.card_id} · {self.get_rating_display()}"


class Settings(models.Model):
    """Legacy per-user study record retained for learner ownership migration."""

    user = models.OneToOneField(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="study_settings",
        null=True,
        blank=True,
    )
    new_cards_per_day = models.PositiveIntegerField(default=15)
    max_reviews_per_day = models.PositiveIntegerField(default=200)

    class Meta:
        verbose_name_plural = "settings"

    def __str__(self) -> str:
        return "Study settings"

    @classmethod
    def load(cls, user=None) -> "Settings":
        if user is not None and getattr(user, "is_authenticated", False):
            obj, _ = cls.objects.get_or_create(user=user)
            return obj
        obj = cls.objects.filter(user__isnull=True).order_by("pk").first()
        return obj or cls.objects.create()


class LoginThrottle(models.Model):
    """Database-backed rate-limit counter without storing raw identifiers."""

    key_hash = models.CharField(max_length=64, primary_key=True)
    failures = models.PositiveSmallIntegerField(default=0)
    window_started_at = models.DateTimeField(default=timezone.now)
    locked_until = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)


class AccountRecoveryCode(models.Model):
    """A one-time, high-entropy account recovery code stored as a keyed digest."""

    user = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recovery_codes",
    )
    token_digest = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(default=timezone.now)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["user", "used_at"]),
        ]
