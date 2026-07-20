from django.contrib import admin

from .models import (
    Annotation,
    Argument,
    Card,
    ComprehensionAnswer,
    ComprehensionAttempt,
    ComprehensionChoice,
    ComprehensionQuestion,
    ComprehensionTest,
    Family,
    LoginThrottle,
    Phrase,
    PhraseCategory,
    Prompt,
    Response,
    ReviewLog,
    ReviewSession,
    Settings,
    Theme,
)


class ArgumentInline(admin.TabularInline):
    model = Argument
    extra = 0


class PromptInline(admin.TabularInline):
    model = Prompt
    extra = 0
    fields = ("theme", "number", "is_canonical", "text")


class ComprehensionQuestionInline(admin.TabularInline):
    model = ComprehensionQuestion
    extra = 0
    fields = ("number", "prompt_fr", "is_active")
    show_change_link = True


class ComprehensionChoiceInline(admin.TabularInline):
    model = ComprehensionChoice
    extra = 0


@admin.register(ComprehensionTest)
class ComprehensionTestAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "mode",
        "number",
        "is_published",
        "is_active",
        "expected_question_count",
    )
    list_filter = ("mode", "is_published", "is_active")
    inlines = [ComprehensionQuestionInline]


@admin.register(ComprehensionQuestion)
class ComprehensionQuestionAdmin(admin.ModelAdmin):
    list_display = ("test", "number", "prompt_fr", "is_active")
    list_filter = ("test", "is_active")
    search_fields = ("passage_fr", "prompt_fr")
    inlines = [ComprehensionChoiceInline]


@admin.register(ComprehensionAttempt)
class ComprehensionAttemptAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "test",
        "status",
        "score",
        "total_questions",
        "started_at",
        "completed_at",
    )
    list_filter = ("test", "status")
    readonly_fields = ("started_at", "updated_at", "completed_at")


@admin.register(ComprehensionAnswer)
class ComprehensionAnswerAdmin(admin.ModelAdmin):
    list_display = (
        "attempt",
        "question",
        "selected_choice",
        "is_correct",
        "submitted_at",
    )
    list_filter = ("is_correct", "question__test")


@admin.register(Theme)
class ThemeAdmin(admin.ModelAdmin):
    list_display = ("display_name", "name", "order", "emoji", "color")
    ordering = ("order",)


@admin.register(Family)
class FamilyAdmin(admin.ModelAdmin):
    list_display = ("name", "order")
    ordering = ("order",)


@admin.register(Response)
class ResponseAdmin(admin.ModelAdmin):
    list_display = ("id", "theme", "family", "short_prompt")
    list_filter = ("theme", "family")
    search_fields = ("prompt", "body")
    inlines = [ArgumentInline, PromptInline]

    @admin.display(description="Prompt")
    def short_prompt(self, obj):
        return obj.prompt[:80]


@admin.register(Prompt)
class PromptAdmin(admin.ModelAdmin):
    list_display = ("label", "theme", "number", "is_canonical")
    list_filter = ("theme", "is_canonical")
    search_fields = ("text",)


@admin.register(PhraseCategory)
class PhraseCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "order")
    ordering = ("order",)


@admin.register(Phrase)
class PhraseAdmin(admin.ModelAdmin):
    list_display = ("phrase_id", "tier", "category", "expression")
    list_filter = ("tier", "category")
    search_fields = ("expression", "english_cue", "example")


@admin.register(Card)
class CardAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "card_type",
        "state",
        "started_at",
        "response_practice_started_at",
        "due",
        "interval_days",
        "ease",
        "reps",
        "lapses",
        "suspended",
    )
    list_filter = ("user", "card_type", "state", "suspended")


@admin.register(ReviewLog)
class ReviewLogAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "card",
        "reviewed_at",
        "rating",
        "state_before",
        "state_after",
    )
    list_filter = ("user", "rating", "state_after")
    date_hierarchy = "reviewed_at"


@admin.register(Settings)
class SettingsAdmin(admin.ModelAdmin):
    list_display = ("user", "new_cards_per_day", "max_reviews_per_day")


admin.site.register(ReviewSession)
admin.site.register(LoginThrottle)
admin.site.register(Annotation)
