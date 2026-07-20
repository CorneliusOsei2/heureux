"""Canonical public URL builders for scoped study content."""

from __future__ import annotations

from urllib.parse import urlencode

from django.urls import reverse

from .models import ComprehensionMode, ComprehensionTest, Prompt, Response, Theme


def _expression_task_args(task) -> list[str]:
    return [task.part.slug, task.slug]


def prompt_detail_url(prompt: Prompt) -> str:
    task = prompt.theme.task
    if task is None:
        raise ValueError("A public prompt must belong to an expression task.")
    return reverse(
        "study:response_detail",
        args=[task.part.slug, task.slug, prompt.pk],
    )


def response_detail_url(response: Response) -> str:
    prompt = response.canonical_prompt
    if prompt is None:
        raise ValueError("A public response must have an active canonical prompt.")
    return prompt_detail_url(prompt)


def theme_detail_url(theme: Theme) -> str:
    if theme.task is None:
        raise ValueError("A public theme must belong to an expression task.")
    return reverse(
        "study:theme_detail",
        args=[theme.task.part.slug, theme.task.slug, theme.slug],
    )


def comprehension_skill(mode: str) -> str:
    if mode == ComprehensionMode.ECRITE:
        return "ce"
    if mode == ComprehensionMode.ORALE:
        return "co"
    raise ValueError(f"Unsupported comprehension mode: {mode}")


def comprehension_vocabulary_url(
    *,
    test: ComprehensionTest | None = None,
    mode: str | None = None,
) -> str:
    mode = test.mode if test is not None else mode
    if mode == ComprehensionMode.ECRITE:
        route = (
            "study:comprehension_test_vocabulary"
            if test is not None
            else "study:comprehension_vocabulary"
        )
    elif mode == ComprehensionMode.ORALE:
        route = (
            "study:comprehension_oral_test_vocabulary"
            if test is not None
            else "study:comprehension_oral_vocabulary"
        )
    else:
        raise ValueError(f"Unsupported comprehension mode: {mode}")
    return reverse(route, args=[test.slug] if test is not None else None)


def vocabulary_url(*, task=None, category=None) -> str:
    if task is not None and category is not None:
        return reverse(
            "study:task_vocabulary_category",
            args=[*_expression_task_args(task), category.slug],
        )
    if task is not None:
        return reverse("study:task_phrases", args=_expression_task_args(task))
    if category is not None:
        return reverse("study:vocabulary_category", args=[category.slug])
    return reverse("study:vocabulary")


def review_url(scope: dict) -> str:
    query = {key: value for key, value in scope.items() if value not in (None, "")}
    part_slug = query.pop("part", None)
    task_slug = query.pop("task", None)
    test_slug = query.pop("test", None)

    if task_slug:
        if not part_slug:
            raise ValueError("A task review URL requires its expression part.")
        base = reverse("study:task_review", args=[part_slug, task_slug])
        if test_slug:
            query["test"] = test_slug
    elif part_slug:
        base = reverse("study:part_review", args=[part_slug])
        if test_slug:
            query["test"] = test_slug
    elif test_slug:
        test = ComprehensionTest.objects.only("slug", "mode").get(slug=test_slug)
        route = (
            "study:comprehension_vocabulary_review"
            if test.mode == ComprehensionMode.ECRITE
            else "study:comprehension_oral_vocabulary_review"
        )
        base = reverse(route, args=[test.slug])
    else:
        base = reverse("study:review")

    return f"{base}?{urlencode(query)}" if query else base
