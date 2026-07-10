"""Data models for the EO T3 French oral-exam flashcards app.

Two layers:

* Content — imported from the markdown/TSV answer bank and treated as the
  source of truth: Theme, Family, Response (a unique argued answer), its
  Arguments, the Prompts that map onto it, and reusable Phrases.
* Study — a single reviewable ``Card`` per studyable item carrying its own
  SM-2 spaced-repetition state (personal single-user app), plus a ``ReviewLog``
  that records every grade for statistics.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import models
from django.db.models import Q
from django.utils import timezone


class ExamPart(models.Model):
    """A top-level exam component, e.g. Épreuve orale or Épreuve écrite."""

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=64, unique=True)
    short_name = models.CharField(max_length=32)
    emoji = models.CharField(max_length=8, default="📝")
    color = models.CharField(max_length=7, default="#6366f1")
    order = models.PositiveIntegerField(default=0)
    available = models.BooleanField(default=True)

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
    order = models.PositiveIntegerField(default=0)

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

    body_hash = models.CharField(max_length=32, unique=True)
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

    class Meta:
        ordering = ["theme__order", "id"]

    def __str__(self) -> str:
        return f"{self.theme.display_name}: {self.prompt[:60]}"

    @property
    def canonical_prompt(self) -> "Prompt | None":
        return self.prompts.filter(is_canonical=True).first()

    @property
    def alias_prompts(self):
        """Prompts other than the canonical one."""
        return self.prompts.filter(is_canonical=False)

    @property
    def has_aliases(self) -> bool:
        return self.prompts.count() > 1


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


class Prompt(models.Model):
    """A prompt as numbered inside a theme; maps onto exactly one Response."""

    response = models.ForeignKey(
        Response, on_delete=models.CASCADE, related_name="prompts"
    )
    theme = models.ForeignKey(
        Theme, on_delete=models.CASCADE, related_name="prompts"
    )
    family = models.ForeignKey(
        Family, on_delete=models.CASCADE, related_name="prompts"
    )
    number = models.PositiveIntegerField()
    text = models.TextField()
    is_canonical = models.BooleanField(default=False)

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
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "name"]
        verbose_name_plural = "phrase categories"

    def __str__(self) -> str:
        return self.name


class Phrase(models.Model):
    """A reusable French chunk with an English cue and a grounded example."""

    phrase_id = models.CharField(max_length=16, unique=True)
    category = models.ForeignKey(
        PhraseCategory, on_delete=models.CASCADE, related_name="phrases"
    )
    english_cue = models.CharField(max_length=200)
    expression = models.CharField(max_length=300)
    anchor = models.CharField(max_length=300)
    example = models.TextField()
    note = models.TextField(blank=True)
    sources_raw = models.CharField(max_length=200, blank=True)
    source_prompts = models.ManyToManyField(
        Prompt, related_name="phrases", blank=True
    )
    order = models.PositiveIntegerField(default=0)

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
    def active(self):
        return self.filter(suspended=False)

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
        ]
        indexes = [
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


class ReviewLog(models.Model):
    """One recorded grade, enabling retention and workload statistics."""

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

    class Meta:
        ordering = ["-reviewed_at"]

    def __str__(self) -> str:
        return f"{self.card_id} · {self.get_rating_display()}"


class Settings(models.Model):
    """Singleton study configuration (row id = 1)."""

    new_cards_per_day = models.PositiveIntegerField(default=15)
    max_reviews_per_day = models.PositiveIntegerField(default=200)

    class Meta:
        verbose_name_plural = "settings"

    def __str__(self) -> str:
        return "Study settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "Settings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
