"""Home dashboard and expression overview."""

from __future__ import annotations

from django.db.models import Prefetch, Q
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from .. import queue as queue_module
from ..cards import scope_label
from ..models import (
    Annotation,
    Card,
    CardType,
    ExamPart,
    PhraseTier,
    ReviewSession,
    Task,
)
from ..progress import card_unit_progress, combine_progress

from .common import (
    FUNCTIONAL_PHRASE_CATEGORY_NAMES,
    _task_card,
    current_streak,
    deck_stats,
)
from .comprehension import _comprehension_summary

def _parts_with_task_cards(now, user):
    return [
        {
            "part": part,
            "tasks": [
                _task_card(task, now, user)
                for task in part.tasks.all()
            ],
        }
        for part in ExamPart.objects.filter(is_active=True).prefetch_related(
            Prefetch("tasks", queryset=Task.objects.filter(is_active=True))
        )
    ]


def _home_expression_paths(parts):
    paths = []
    for item in parts:
        all_tasks = item["tasks"]
        available_tasks = [
            task
            for task in all_tasks
            if task["task"].available
        ]
        path_progress = combine_progress(
            task["stats"]["progress"] for task in available_tasks
        )
        paths.append(
            {
                **item,
                "available": bool(item["part"].available and all_tasks),
                "task_count": len(all_tasks),
                "has_content": bool(available_tasks),
                "prompt_count": sum(
                    task["prompt_count"] for task in available_tasks
                ),
                "seen": sum(
                    task["stats"]["seen"] for task in available_tasks
                ),
                "total": sum(
                    task["stats"]["total"] for task in available_tasks
                ),
                "due": sum(
                    task["stats"]["due"] for task in available_tasks
                ),
                "progress": path_progress,
            }
        )
    paths.sort(
        key=lambda item: (
            {"ee": 0, "eo": 1}.get(
                item["part"].slug,
                2,
            ),
            item["part"].order,
        )
    )
    return paths


def _vocabulary_expression_paths(now, user):
    part_items = _parts_with_task_cards(now, user)
    path_specs = (
        {
            "title": "Écrite",
            "short_name": "EE",
            "slugs": {"ee"},
            "fallback_emoji": "✍️",
        },
        {
            "title": "Orale",
            "short_name": "EO",
            "slugs": {"eo"},
            "fallback_emoji": "🎙️",
        },
    )
    paths = []
    for spec in path_specs:
        item = next(
            (
                candidate
                for candidate in part_items
                if candidate["part"].slug in spec["slugs"]
            ),
            None,
        )
        vocabulary_tasks = []
        if item:
            vocabulary_tasks = [
                task_item
                for task_item in item["tasks"]
                if task_item["task"].available
                and (
                    task_item["functional_phrase_count"]
                    + task_item["subject_vocabulary_count"]
                )
            ]
        available = bool(
            item and item["part"].available and vocabulary_tasks
        )
        url = ""
        vocabulary_progress = None
        if available:
            task_ids = [
                task_item["task"].pk for task_item in vocabulary_tasks
            ]
            vocabulary_cards = (
                Card.objects.current_content()
                .filter(
                    user=user,
                    phrase__source_prompts__is_active=True,
                    phrase__source_prompts__theme__is_active=True,
                    phrase__source_prompts__theme__task_id__in=task_ids,
                    phrase__category__is_active=True,
                )
                .filter(
                    Q(
                        phrase__tier=PhraseTier.SUBJECT,
                        card_type=CardType.PHRASE_PRODUCTION,
                    )
                    | Q(
                        phrase__tier=PhraseTier.SHARED,
                        phrase__category__name__in=(
                            FUNCTIONAL_PHRASE_CATEGORY_NAMES
                        ),
                        card_type__in=[
                            CardType.PHRASE_PRODUCTION,
                            CardType.PHRASE_RECOGNITION,
                        ],
                    )
                )
                .distinct()
            )
            vocabulary_progress = card_unit_progress(vocabulary_cards)
            if len(vocabulary_tasks) == 1:
                task = vocabulary_tasks[0]["task"]
                url = reverse(
                    "study:task_phrases",
                    args=[task.part.slug, task.slug],
                )
            else:
                url = reverse(
                    "study:part_detail",
                    args=[item["part"].slug],
                )
        paths.append(
            {
                "title": spec["title"],
                "short_name": spec["short_name"],
                "emoji": (
                    item["part"].emoji
                    if item
                    else spec["fallback_emoji"]
                ),
                "available": available,
                "url": url,
                "task_count": len(vocabulary_tasks),
                "prompt_count": sum(
                    task_item["prompt_count"]
                    for task_item in vocabulary_tasks
                ),
                "vocabulary_count": (
                    vocabulary_progress.total if vocabulary_progress else 0
                ),
                "progress": vocabulary_progress,
            }
        )
    return paths


def dashboard(request):
    now = timezone.now()
    expression_counts = queue_module.queue_counts(
        {"content": "spine"},
        now=now,
        user=request.user,
    )
    vocabulary_counts = queue_module.queue_counts(
        {"content": "vocabulary"},
        now=now,
        user=request.user,
    )
    user_cards = Card.objects.current_content().filter(
        user=request.user,
        suspended=False,
    )
    overall = deck_stats(user_cards, now)
    parts = _parts_with_task_cards(now, request.user)
    session = ReviewSession.load(request.user)

    context = {
        "expression_counts": expression_counts,
        "vocabulary_counts": vocabulary_counts,
        "parts": parts,
        "expression_paths": _home_expression_paths(parts),
        "overall": overall,
        "streak": current_streak(now, user=request.user),
        "comprehension": _comprehension_summary(request.user),
        "notes_to_study": Annotation.objects.filter(
            user=request.user,
            study_later=True,
        ).count(),
        "can_resume_review": bool(session.current_card_id),
        "resume_scope_label": (
            scope_label(session.scope)
            if session.current_card_id and isinstance(session.scope, dict)
            else ""
        ),
    }
    return render(request, "study/dashboard.html", context)


def expression_hub(request):
    now = timezone.now()
    parts = _parts_with_task_cards(now, request.user)
    paths = _home_expression_paths(parts)
    available_paths = [path for path in paths if path["available"]]
    return render(
        request,
        "study/expression_hub.html",
        {
            "paths": paths,
            "prompt_count": sum(
                path["prompt_count"] for path in available_paths
            ),
            "card_total": sum(path["total"] for path in available_paths),
            "card_seen": sum(path["seen"] for path in available_paths),
            "response_due": sum(path["due"] for path in available_paths),
        },
    )
