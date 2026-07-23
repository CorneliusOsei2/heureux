"""Content browsing, phrase library, detail pages, and stats."""

from __future__ import annotations

from functools import lru_cache


from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .. import content as content_module
from .. import queue as queue_module
from ..cards import scope_label
from ..forms import (
    PersonalResponseForm,
)
from ..models import (
    Card,
    CardState,
    CardType,
    ComprehensionMode,
    ComprehensionQuestion,
    ComprehensionTest,
    ExamPart,
    Family,
    MemoryQuestionProgress,
    Phrase,
    PhraseCategory,
    PhraseTier,
    PersonalResponse,
    Prompt,
    Rating,
    Response,
    ReviewLog,
    Task,
    Theme,
)
from ..personalization import effective_response
from ..progress import (
    card_unit_progress,
    combine_progress,
    progress_summary,
    subject_progress_by_response,
    summarize_subject_progress,
)
from ..routing import (
    comprehension_skill,
    comprehension_vocabulary_url,
    prompt_detail_url,
    review_url,
    vocabulary_url,
)

from .common import (
    FUNCTIONAL_PHRASE_CATEGORY_NAMES,
    MATURE_DAYS,
    _memory_progress,
    _review_batches,
    _route_task,
    _task_card,
    _task_cards,
    _task_phrases,
    _task_scope,
    _tache_two_progress,
    current_streak,
    deck_stats,
    recent_review_sessions,
    summarize_review_batches,
)
from .dashboard import (
    _vocabulary_expression_paths,
    _vocabulary_task_items,
)

def _subject_stats_for_themes(themes, user, now=None):
    now = now or timezone.now()
    response_ids_by_theme = {theme.pk: set() for theme in themes}
    for theme_id, response_id in Prompt.objects.filter(
        theme_id__in=response_ids_by_theme,
        is_active=True,
        response__is_active=True,
    ).values_list("theme_id", "response_id"):
        response_ids_by_theme[theme_id].add(response_id)
    response_ids = {
        response_id
        for theme_response_ids in response_ids_by_theme.values()
        for response_id in theme_response_ids
    }
    progress = subject_progress_by_response(user, response_ids)
    due_response_ids = set(
        Card.objects.active()
        .filter(
            user=user,
            card_type=CardType.SPINE,
            response_id__in=response_ids,
            state__in={
                CardState.LEARNING,
                CardState.RELEARNING,
                CardState.REVIEW,
            },
            due__lte=now,
        )
        .values_list("response_id", flat=True)
    )
    stats = {}
    for theme_id, theme_response_ids in response_ids_by_theme.items():
        summary = summarize_subject_progress(
            progress[response_id] for response_id in theme_response_ids
        )
        summary["due"] = len(theme_response_ids & due_response_ids)
        stats[theme_id] = summary
    return stats, progress, due_response_ids


def _phrase_deck_stats(now, user=None, task=None):
    cards = (
        _task_cards(task, user, "phrase")
        if task
        else queue_module.scoped_cards({"kind": "phrase"}, user=user)
    )
    return deck_stats(cards, now)


def _question_bank_memory_context(user, memories):
    memory_states = _memory_progress(user, memories)
    memory_items = [
        {
            "memory": memory,
            **memory_states[memory.number],
        }
        for memory in memories
    ]
    progress = combine_progress(
        item["progress"] for item in memory_items
    )
    return {
        "memories": memory_items,
        "memory_count": len(memories),
        "category_count": sum(memory.category_count for memory in memories),
        "question_count": sum(memory.question_count for memory in memories),
        "completed_count": progress.completed,
        "memory_summary": {
            "progress": progress,
            "completed": progress.completed,
            "total": progress.total,
            "started_new": max(progress.started - progress.completed, 0),
        },
    }


def part_detail(request, part_slug):
    part = get_object_or_404(
        ExamPart.objects.filter(is_active=True).prefetch_related(
            Prefetch("tasks", queryset=Task.objects.filter(is_active=True))
        ),
        slug=part_slug,
    )
    now = timezone.now()
    tasks = [
        _task_card(task, now, request.user)
        for task in part.tasks.all()
    ]
    if not part.available or not tasks:
        return render(
            request,
            "study/coming_soon.html",
            {"part": part, "task": None},
        )
    return render(
        request,
        "study/part_detail.html",
        {
            "part": part,
            "tasks": tasks,
            "available_task_count": sum(
                task["task"].available for task in tasks
            ),
        },
    )


def part_vocabulary(request, part_slug):
    part = get_object_or_404(
        ExamPart.objects.filter(is_active=True).prefetch_related(
            Prefetch("tasks", queryset=Task.objects.filter(is_active=True))
        ),
        slug=part_slug,
    )
    task_items = [
        _task_card(task, timezone.now(), request.user)
        for task in part.tasks.all()
    ]
    vocabulary_tasks = _vocabulary_task_items(
        {"part": part, "tasks": task_items},
        request.user,
    )
    if not part.available or not vocabulary_tasks:
        return render(
            request,
            "study/coming_soon.html",
            {"part": part, "task": None},
        )
    return render(
        request,
        "study/vocabulary_part.html",
        {
            "part": part,
            "tasks": vocabulary_tasks,
            "subject_count": sum(
                item["vocabulary_prompt_count"]
                for item in vocabulary_tasks
            ),
            "entry_count": sum(
                item["vocabulary_count"]
                for item in vocabulary_tasks
            ),
        },
    )


def _has_ee_tache_three_content(task):
    return Prompt.objects.filter(
        content_key__startswith=content_module.EE_TACHE3_CONTENT_PREFIX,
        theme__task=task,
        is_active=True,
        response__is_active=True,
    ).exists()


@lru_cache(maxsize=1)
def _ee_tache_three_sources_by_key():
    return {
        combinaison.content_key: combinaison
        for month in content_module.load_ee_tache3_months()
        for combinaison in month.combinaisons
    }


def _ee_tache_three_subject_context(user, task):
    source_months = content_module.load_ee_tache3_months()
    source_keys = [
        combinaison.content_key
        for month in source_months
        for combinaison in month.combinaisons
    ]
    theme_names = {
        content_module.ee_tache3_theme_name(month)
        for month in source_months
    }
    themes_by_name = {
        theme.name: theme
        for theme in Theme.objects.filter(
            task=task,
            is_active=True,
            name__in=theme_names,
        )
    }
    prompts_by_key = {
        prompt.content_key: prompt
        for prompt in Prompt.objects.filter(
            content_key__in=source_keys,
            theme__task=task,
            is_active=True,
            response__is_active=True,
        ).select_related("response", "theme", "family")
    }
    if (
        set(source_keys) - prompts_by_key.keys()
        or theme_names - themes_by_name.keys()
    ):
        raise RuntimeError("EE Tâche 3 content is not synchronized")

    progress_by_response = subject_progress_by_response(
        user,
        {
            prompt.response_id
            for prompt in prompts_by_key.values()
        },
    )
    all_progress = []
    months = []
    for source_month in source_months:
        theme = themes_by_name[
            content_module.ee_tache3_theme_name(source_month)
        ]
        subjects = []
        month_progress = []
        for combinaison in source_month.combinaisons:
            prompt = prompts_by_key[combinaison.content_key]
            progress = progress_by_response[prompt.response_id]
            month_progress.append(progress)
            all_progress.append(progress)
            subjects.append(
                {
                    "position": combinaison.position,
                    "combination_label": combinaison.combinaison,
                    "combination_number": (
                        combinaison.combinaison.removeprefix(
                            "Combinaison "
                        ).strip()
                    ),
                    "prompt": prompt,
                    "progress": progress,
                    "document_count": sum(
                        bool(document.strip())
                        for document in (
                            combinaison.document1,
                            combinaison.document2,
                        )
                    ),
                    "vocabulary_count": (
                        content_module.EE_TACHE3_VOCABULARY_PER_RESPONSE
                    ),
                }
            )

        summary = summarize_subject_progress(month_progress)
        months.append(
            {
                "number": source_month.number,
                "number_label": f"{source_month.number:02d}",
                "slug": source_month.slug,
                "name": source_month.name,
                "theme": theme,
                "subjects": subjects,
                "subject_count": len(subjects),
                "vocabulary_count": (
                    len(subjects)
                    * content_module.EE_TACHE3_VOCABULARY_PER_RESPONSE
                ),
                "review_url": review_url(
                    {
                        "kind": "spine",
                        "part": task.part.slug,
                        "task": task.slug,
                        "theme": theme.slug,
                    }
                ),
                **summary,
            }
        )

    summary = summarize_subject_progress(all_progress)
    return {
        "months": months,
        "month_count": len(months),
        "subject_count": len(all_progress),
        "vocabulary_count": (
            len(all_progress)
            * content_module.EE_TACHE3_VOCABULARY_PER_RESPONSE
        ),
        "subject_summary": summary,
    }


def task_detail(request, part_slug, task_slug):
    task = get_object_or_404(
        Task.objects.select_related("part"),
        slug=task_slug,
        part__slug=part_slug,
        is_active=True,
        part__is_active=True,
    )
    now = timezone.now()
    if not task.available:
        return render(
            request,
            "study/coming_soon.html",
            {"part": task.part, "task": task},
        )
    if (task.part.slug, task.slug) == content_module.QUESTION_BANK_TASK:
        memory_context = _question_bank_memory_context(
            request.user,
            content_module.load_question_banks(),
        )
        subject_state = _tache_two_progress(
            request.user,
            content_module.load_tache_two_subject_months(),
        )
        subject_months = subject_state["months"]
        return render(
            request,
            "study/tache_two_overview.html",
            {
                "part": task.part,
                "task": task,
                "memory_task": True,
                "subject_months": subject_months,
                "subject_summary": subject_state,
                "subject_count": subject_state["total"],
                "subject_month_count": len(subject_months),
                "subject_batch_count": sum(
                    month["batch_count"] for month in subject_months
                ),
                **memory_context,
            },
        )
    if (
        (task.part.slug, task.slug) == content_module.EE_TACHE3_TASK
        and _has_ee_tache_three_content(task)
    ):
        memory_context = _question_bank_memory_context(
            request.user,
            _load_task_memoires(task),
        )
        return render(
            request,
            "study/ee_tache_three_overview.html",
            {
                "part": task.part,
                "task": task,
                **_ee_tache_three_subject_context(request.user, task),
                **memory_context,
            },
        )

    active_themes = list(Theme.objects.filter(task=task, is_active=True))
    theme_stats, response_progress, due_response_ids = _subject_stats_for_themes(
        active_themes,
        request.user,
        now,
    )
    themes = []
    for theme in active_themes:
        themes.append(
            {
                "theme": theme,
                "stats": theme_stats[theme.pk],
                "prompt_count": Prompt.objects.filter(
                    theme=theme,
                    is_active=True,
                ).count(),
            }
        )
    scope = _task_scope(task)
    response_stats = summarize_subject_progress(response_progress.values())
    response_stats["due"] = len(due_response_ids)
    phrase_stats = _phrase_deck_stats(now, request.user, task)
    context = {
        "part": task.part,
        "task": task,
        "themes": themes,
        "stats": response_stats,
        "response_stats": response_stats,
        "phrase_stats": phrase_stats,
        "counts": queue_module.queue_counts(
            {**scope, "kind": "spine"},
            now,
            user=request.user,
        ),
        "prompt_count": Prompt.objects.filter(
            theme__task=task,
            is_active=True,
        ).count(),
        "phrase_count": _task_phrases(task).count(),
        "phrase_category_count": _task_phrases(task)
        .values("category_id")
        .distinct()
        .count(),
    }
    return render(request, "study/task_detail.html", context)


def _scope_filters(request, forced_task=None, forced_part_slug=None):
    """Build canonical path-based part/task filters for progression pages."""
    if "part" in request.GET or "task" in request.GET:
        raise Http404

    selected_task = forced_task
    selected_part = None
    part_slug = ""
    task_slug = ""
    if selected_task:
        part_slug = forced_task.part.slug
        task_slug = forced_task.slug
        selected_part = forced_task.part
    elif forced_part_slug:
        selected_part = get_object_or_404(
            ExamPart,
            slug=forced_part_slug,
            is_active=True,
        )
        part_slug = selected_part.slug

    active = Card.objects.active().filter(
        user=request.user,
        card_type=CardType.SPINE,
    )
    filter_parts = []
    active_part_tasks = []
    for part in ExamPart.objects.filter(
        is_active=True,
        slug__in={"eo", "ee"},
    ).prefetch_related(
        Prefetch("tasks", queryset=Task.objects.filter(is_active=True))
    ):
        filter_parts.append(
            {
                "slug": part.slug,
                "short_name": part.short_name,
                "count": active.filter(response__theme__task__part=part).count(),
                "active": part_slug == part.slug,
                "url": reverse("study:part_stats", args=[part.slug]),
            }
        )
        if part_slug == part.slug:
            for task in part.tasks.all():
                active_part_tasks.append(
                    {
                        "slug": task.slug,
                        "name": task.name,
                        "count": active.filter(response__theme__task=task).count(),
                        "active": task_slug == task.slug,
                        "url": reverse(
                            "study:task_stats",
                            args=[part.slug, task.slug],
                        ),
                    }
                )

    if task_slug:
        scope = {"part": part_slug, "task": task_slug}
    elif part_slug:
        scope = {"part": part_slug}
    else:
        scope = {}

    return {
        "filter_base": reverse("study:stats"),
        "filter_parts": filter_parts,
        "active_part": part_slug,
        "active_task": task_slug,
        "active_part_tasks": active_part_tasks,
        "active_part_url": (
            reverse("study:part_stats", args=[part_slug])
            if part_slug
            else ""
        ),
        "scope_review_url": (
            review_url({**scope, "kind": "spine"}) if scope else ""
        ),
        "scope_label": scope_label(scope),
        "scope": scope,
        "task": selected_task,
        "part": selected_part,
        "task_locked": forced_task is not None,
    }


def browse(request, part_slug=None, task_slug=None):
    forced_task = _route_task(part_slug, task_slug)
    if forced_task and not forced_task.available:
        return render(
            request,
            "study/coming_soon.html",
            {"part": forced_task.part, "task": forced_task},
        )
    if forced_task and (
        forced_task.part.slug,
        forced_task.slug,
    ) == content_module.QUESTION_BANK_TASK:
        subject_state = _tache_two_progress(
            request.user,
            content_module.load_tache_two_subject_months(),
        )
        months = subject_state["months"]
        return render(
            request,
            "study/tache_two_subjects.html",
            {
                "part": forced_task.part,
                "task": forced_task,
                "memory_task": True,
                "subject_months": months,
                "subject_summary": subject_state,
                "month_count": len(months),
                "batch_count": sum(
                    month["batch_count"] for month in months
                ),
                "subject_count": subject_state["total"],
                "question_count": sum(
                    month["question_count"] for month in months
                ),
            },
        )
    if (
        forced_task
        and (
            forced_task.part.slug,
            forced_task.slug,
        )
        == content_module.EE_TACHE3_TASK
        and _has_ee_tache_three_content(forced_task)
    ):
        return render(
            request,
            "study/ee_tache_three_subjects.html",
            {
                "part": forced_task.part,
                "task": forced_task,
                **_ee_tache_three_subject_context(
                    request.user,
                    forced_task,
                ),
            },
        )
    filters = _scope_filters(request, forced_task)
    scope = filters["scope"]

    theme_qs = Theme.objects.select_related("task__part").filter(is_active=True)
    if scope.get("task"):
        theme_qs = theme_qs.filter(
            task__slug=scope["task"],
            task__part__slug=scope["part"],
        )
    elif scope.get("part"):
        theme_qs = theme_qs.filter(task__part__slug=scope["part"])

    theme_rows = list(theme_qs)
    theme_stats, response_progress, _due_response_ids = _subject_stats_for_themes(
        theme_rows,
        request.user,
    )
    themes = []
    for theme in theme_rows:
        themes.append(
            {
                "theme": theme,
                "stats": theme_stats[theme.pk],
                "prompt_count": Prompt.objects.filter(
                    theme=theme,
                    is_active=True,
                ).count(),
            }
        )
    family_qs = Family.objects.filter(is_active=True)
    if scope.get("task"):
        family_qs = family_qs.filter(
            prompts__is_active=True,
            prompts__theme__task__slug=scope["task"],
            prompts__theme__task__part__slug=scope["part"],
        )
    elif scope.get("part"):
        family_qs = family_qs.filter(
            prompts__is_active=True,
            prompts__theme__task__part__slug=scope["part"]
        )
    families = list(
        family_qs.annotate(
            n=Count(
                "prompts",
                filter=Q(prompts__is_active=True),
                distinct=True,
            )
        ).order_by("order")
    )
    response_ids_by_family = {family.pk: set() for family in families}
    family_prompts = Prompt.objects.filter(
        family_id__in=response_ids_by_family,
        is_active=True,
        response__is_active=True,
        theme__is_active=True,
    )
    if scope.get("task"):
        family_prompts = family_prompts.filter(
            theme__task__slug=scope["task"],
            theme__task__part__slug=scope["part"],
        )
    elif scope.get("part"):
        family_prompts = family_prompts.filter(
            theme__task__part__slug=scope["part"],
        )
    for family_id, response_id in family_prompts.values_list(
        "family_id",
        "response_id",
    ):
        response_ids_by_family[family_id].add(response_id)
    for family in families:
        family.progress = summarize_subject_progress(
            response_progress[response_id]
            for response_id in response_ids_by_family[family.pk]
        )["progress"]
    prompt_qs = Prompt.objects.filter(is_active=True)
    response_qs = Response.objects.filter(is_active=True)
    phrase_qs = Phrase.objects.filter(is_active=True)
    if scope.get("task"):
        prompt_qs = prompt_qs.filter(
            theme__task__slug=scope["task"],
            theme__task__part__slug=scope["part"],
        )
        response_qs = response_qs.filter(
            theme__task__slug=scope["task"],
            theme__task__part__slug=scope["part"],
        )
        phrase_qs = phrase_qs.filter(
            source_prompts__theme__task__slug=scope["task"],
            source_prompts__theme__task__part__slug=scope["part"],
        ).distinct()
    elif scope.get("part"):
        prompt_qs = prompt_qs.filter(theme__task__part__slug=scope["part"])
        response_qs = response_qs.filter(
            theme__task__part__slug=scope["part"]
        )
        phrase_qs = phrase_qs.filter(
            source_prompts__theme__task__part__slug=scope["part"]
        ).distinct()
    context = {
        "themes": themes,
        "families": families,
        "theme_count": len(themes),
        "prompt_count": prompt_qs.count(),
        "response_count": response_qs.count(),
        "phrase_count": phrase_qs.count(),
        **filters,
    }
    return render(request, "study/browse.html", context)


def _canonical_numbers_by_response(response_ids) -> dict:
    """Map response id -> canonical prompt number in a single query.

    Avoids an N+1 from calling ``Response.canonical_prompt`` per row.
    """
    ids = list(response_ids)
    if not ids:
        return {}
    return dict(
        Prompt.objects.filter(
            response_id__in=ids,
            is_active=True,
            is_canonical=True,
        ).values_list("response_id", "number")
    )


def theme_detail(request, part_slug, task_slug, slug):
    task = _route_task(part_slug, task_slug)
    theme = get_object_or_404(
        Theme.objects.select_related("task__part"),
        slug=slug,
        task=task,
        is_active=True,
    )
    prompts = list(
        Prompt.objects.filter(theme=theme, is_active=True)
        .select_related("response", "response__theme", "family")
        .order_by("number")
    )
    canonical_numbers = _canonical_numbers_by_response(
        prompt.response_id for prompt in prompts
    )
    subject_progress = subject_progress_by_response(
        request.user,
        {prompt.response_id for prompt in prompts},
    )
    rows = [
        {
            "prompt": prompt,
            "progress": subject_progress[prompt.response_id],
            "is_alias": not prompt.is_canonical,
            "canonical_number": canonical_numbers.get(prompt.response_id),
        }
        for prompt in prompts
    ]
    stats = summarize_subject_progress(subject_progress.values())
    review_scope = {
        "kind": "spine",
        "part": task.part.slug,
        "task": task.slug,
        "theme": theme.slug,
    }
    if (task.part.slug, task.slug) == content_module.EE_TACHE3_TASK:
        source_month = next(
            (
                month
                for month in content_module.load_ee_tache3_months()
                if content_module.ee_tache3_theme_name(month) == theme.name
            ),
            None,
        )
        if source_month is not None:
            source_by_key = {
                combinaison.content_key: combinaison
                for combinaison in source_month.combinaisons
            }
            for row in rows:
                source = source_by_key.get(row["prompt"].content_key)
                if source is None:
                    raise RuntimeError(
                        "EE Tâche 3 content is not synchronized"
                    )
                row.update(
                    {
                        "position": source.position,
                        "combination_label": source.combinaison,
                        "combination_number": (
                            source.combinaison.removeprefix(
                                "Combinaison "
                            ).strip()
                        ),
                        "document_count": sum(
                            bool(document.strip())
                            for document in (
                                source.document1,
                                source.document2,
                            )
                        ),
                        "vocabulary_count": (
                            content_module.EE_TACHE3_VOCABULARY_PER_RESPONSE
                        ),
                    }
                )
            return render(
                request,
                "study/ee_tache_three_month.html",
                {
                    "theme": theme,
                    "task": task,
                    "part": task.part,
                    "subjects": rows,
                    "month": {
                        "number": source_month.number,
                        "number_label": f"{source_month.number:02d}",
                        "slug": source_month.slug,
                        "name": source_month.name,
                        "subject_count": len(rows),
                        "vocabulary_count": (
                            len(rows)
                            * content_module.EE_TACHE3_VOCABULARY_PER_RESPONSE
                        ),
                        **stats,
                    },
                    "review_url": review_url(review_scope),
                },
            )
    return render(
        request,
        "study/theme_detail.html",
        {
            "theme": theme,
            "task": task,
            "part": task.part,
            "rows": rows,
            "stats": stats,
            "review_batches": _review_batches(review_scope, request.user),
            "review_url": review_url(review_scope),
        },
    )


def _memory_task(part_slug, task_slug):
    task = get_object_or_404(
        Task.objects.select_related("part"),
        slug=task_slug,
        part__slug=part_slug,
        is_active=True,
        part__is_active=True,
        available=True,
    )
    if (task.part.slug, task.slug) != content_module.QUESTION_BANK_TASK:
        raise Http404
    return task


def _memoire_task(part_slug, task_slug):
    """Gate for tasks that expose a Mémoires section (EO T2 and EE T3)."""
    task = get_object_or_404(
        Task.objects.select_related("part"),
        slug=task_slug,
        part__slug=part_slug,
        is_active=True,
        part__is_active=True,
        available=True,
    )
    if (task.part.slug, task.slug) not in content_module.MEMOIRE_TASKS:
        raise Http404
    return task


def _load_task_memoires(task):
    directory, namespace = content_module.MEMOIRE_TASKS[
        (task.part.slug, task.slug)
    ]
    return content_module.load_question_banks(
        directory,
        key_namespace=namespace,
    )


def _memory_by_number(memories, memory_number):
    memory = next(
        (
            memory
            for memory in memories
            if memory.number == memory_number
        ),
        None,
    )
    if memory is None:
        raise Http404
    return memory


def task_memories(request, part_slug, task_slug):
    task = _memoire_task(part_slug, task_slug)
    memories = _load_task_memoires(task)
    return render(
        request,
        "study/tache_two_memories.html",
        {
            "part": task.part,
            "task": task,
            "memory_task": (
                (task.part.slug, task.slug)
                == content_module.QUESTION_BANK_TASK
            ),
            **_question_bank_memory_context(request.user, memories),
        },
    )


def _tache_two_subject_month(month_slug):
    month = next(
        (
            month
            for month in content_module.load_tache_two_subject_months()
            if month.slug == month_slug
        ),
        None,
    )
    if month is None:
        raise Http404
    return month


def _tache_two_subject_batch(month, batch_number):
    batch = next(
        (
            batch
            for batch in month.batches
            if batch.number == batch_number
        ),
        None,
    )
    if batch is None:
        raise Http404
    return batch


def task_subject_batch(request, part_slug, task_slug, month_slug, batch_number):
    task = _memory_task(part_slug, task_slug)
    month = _tache_two_subject_month(month_slug)
    _tache_two_subject_batch(month, batch_number)
    month = _tache_two_progress(request.user, (month,))["months"][0]
    batch = next(
        batch
        for batch in month["batches"]
        if batch["number"] == batch_number
    )
    return render(
        request,
        "study/tache_two_subject_batch.html",
        {
            "part": task.part,
            "task": task,
            "memory_task": True,
            "subject_month": month,
            "subject_batch": batch,
        },
    )


def task_subject_detail(
    request,
    part_slug,
    task_slug,
    month_slug,
    batch_number,
    subject_number,
):
    task = _memory_task(part_slug, task_slug)
    month = _tache_two_subject_month(month_slug)
    batch = _tache_two_subject_batch(month, batch_number)
    subject = next(
        (
            subject
            for subject in batch.subjects
            if subject.number == subject_number
        ),
        None,
    )
    if subject is None:
        raise Http404

    response = get_object_or_404(
        Response.objects.select_related(
            "theme__task__part",
            "family",
        ),
        content_key=content_module.tache_two_subject_content_key(
            month.slug,
            batch.number,
            subject.number,
        ),
        theme__task=task,
        is_active=True,
    )
    selected_prompt = get_object_or_404(
        Prompt.objects.select_related(
            "theme__task__part",
            "family",
            "response",
        ),
        response=response,
        content_key=response.content_key,
        is_active=True,
        is_canonical=True,
    )
    subject_progress = subject_progress_by_response(
        request.user,
        {response.pk},
    )[response.pk]
    card = Card.objects.filter(
        user=request.user,
        card_type=CardType.SPINE,
        response=response,
    ).first()
    task_scope = {"part": task.part.slug, "task": task.slug}
    vocabulary_context = _subject_vocabulary_context(
        response,
        task_scope,
        request.user,
    )
    questions = [
        {
            "number": index,
            "text": question.text,
        }
        for index, question in enumerate(subject.questions, start=1)
    ]
    subject_index = next(
        index
        for index, batch_subject in enumerate(batch.subjects)
        if batch_subject.number == subject.number
    )
    return render(
        request,
        "study/tache_two_subject_detail.html",
        {
            "part": task.part,
            "task": task,
            "memory_task": True,
            "subject_month": month,
            "subject_batch": batch,
            "subject": subject,
            "subject_questions": questions,
            "previous_subject": (
                batch.subjects[subject_index - 1]
                if subject_index > 0
                else None
            ),
            "next_subject": (
                batch.subjects[subject_index + 1]
                if subject_index + 1 < len(batch.subjects)
                else None
            ),
            "subject_position": subject_index + 1,
            "subject_total": len(batch.subjects),
            "selected_prompt": selected_prompt,
            "response": response,
            "card": card,
            "subject_progress": subject_progress,
            "response_review_url": review_url(
                {
                    **task_scope,
                    "kind": "spine",
                    "response": str(response.pk),
                }
            ),
            "theme_review_url": review_url(
                {
                    **task_scope,
                    "kind": "spine",
                    "theme": response.theme.slug,
                }
            ),
            **vocabulary_context,
        },
    )


def _memory_sections(memory, completed_keys):
    sections = []
    for section in memory.sections:
        completed_count = len(set(section.question_keys) & completed_keys)
        sections.append(
            {
                "number": section.number,
                "number_label": section.number_label,
                "title": section.title,
                "anchor": section.anchor,
                "question_count": section.question_count,
                "progress": progress_summary(
                    total=section.question_count,
                    started=completed_count,
                    completed=completed_count,
                ),
                "groups": [
                    {
                        "title": group.title,
                        "guidance": group.guidance,
                        "questions": [
                            {
                                "content_key": question.content_key,
                                "text": question.text,
                                "note": question.note,
                                "completed": (
                                    question.content_key in completed_keys
                                ),
                            }
                            for question in group.questions
                        ],
                    }
                    for group in section.groups
                ],
            }
        )
    return sections


def _memory_progress_error(request, message):
    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse({"error": message}, status=400)
    return HttpResponseBadRequest(message)


def task_memory_detail(request, part_slug, task_slug, memory_number):
    task = _memoire_task(part_slug, task_slug)
    memories = _load_task_memoires(task)
    question_bank = _memory_by_number(memories, memory_number)
    memory_state = _memory_progress(
        request.user,
        (question_bank,),
    )[question_bank.number]
    return render(
        request,
        "study/question_bank.html",
        {
            "part": task.part,
            "task": task,
            "memory_task": (
                (task.part.slug, task.slug)
                == content_module.QUESTION_BANK_TASK
            ),
            "question_bank": question_bank,
            "memory_progress": memory_state["progress"],
            "memory_sections": _memory_sections(
                question_bank,
                memory_state["completed_keys"],
            ),
        },
    )


@require_POST
def task_memory_progress(request, part_slug, task_slug, memory_number):
    task = _memoire_task(part_slug, task_slug)
    memories = _load_task_memoires(task)
    memory = _memory_by_number(memories, memory_number)
    question_key = request.POST.get("question_key", "").strip()
    completed = request.POST.get("completed")
    if completed not in {"0", "1"}:
        return _memory_progress_error(
            request,
            "État de progression invalide.",
        )

    section = next(
        (
            section
            for section in memory.sections
            if question_key in section.question_keys
        ),
        None,
    )
    if section is None:
        return _memory_progress_error(
            request,
            "Cette question ne fait pas partie de la mémoire.",
        )

    if completed == "1":
        MemoryQuestionProgress.objects.get_or_create(
            user=request.user,
            memory_number=memory.number,
            question_key=question_key,
        )
    else:
        MemoryQuestionProgress.objects.filter(
            user=request.user,
            memory_number=memory.number,
            question_key=question_key,
        ).delete()

    memory_state = _memory_progress(
        request.user,
        (memory,),
    )[memory.number]
    memory_summary = memory_state["progress"]
    section_completed = len(
        set(section.question_keys) & memory_state["completed_keys"]
    )
    section_summary = progress_summary(
        total=section.question_count,
        started=section_completed,
        completed=section_completed,
    )
    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse(
            {
                "completed": completed == "1",
                "question_key": question_key,
                "memory": {
                    "completed": memory_summary.completed,
                    "total": memory_summary.total,
                    "percent": memory_summary.percent,
                    "status": memory_summary.status,
                    "label": memory_summary.label,
                },
                "section": {
                    "number": section.number,
                    "completed": section_summary.completed,
                    "total": section_summary.total,
                    "percent": section_summary.percent,
                    "status": section_summary.status,
                    "label": section_summary.label,
                },
            }
        )
    return redirect(
        reverse(
            "study:task_memory_detail",
            args=[task.part.slug, task.slug, memory.number],
        )
        + f"#{section.anchor}"
    )


@require_POST
def subject_completion(request, part_slug, task_slug, response_id):
    task = _route_task(part_slug, task_slug)
    route_prompt = (
        Prompt.objects.select_related("response")
        .filter(
            response_id=response_id,
            response__is_active=True,
            is_active=True,
            theme__is_active=True,
            theme__task=task,
        )
        .order_by("-is_canonical", "number", "pk")
        .first()
    )
    if route_prompt is None:
        raise Http404
    response = route_prompt.response
    completed = request.POST.get("completed")
    if completed not in {"0", "1"}:
        if request.headers.get("X-Requested-With") == "fetch":
            return JsonResponse(
                {"error": "État de progression invalide."},
                status=400,
            )
        return HttpResponseBadRequest("État de progression invalide.")

    card, _created = Card.objects.get_or_create(
        user=request.user,
        card_type=CardType.SPINE,
        response=response,
    )
    completed_at = timezone.now() if completed == "1" else None
    Card.objects.filter(pk=card.pk).update(
        subject_completed_at=completed_at,
    )
    progress = subject_progress_by_response(
        request.user,
        {response.pk},
    )[response.pk]

    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse(
            {
                "response_id": response.pk,
                "completed": progress.explicitly_completed,
                "subject": {
                    "status": progress.status,
                    "label": progress.label,
                },
            }
        )

    return redirect(prompt_detail_url(route_prompt))


def family_detail(request, part_slug, task_slug, slug):
    task = _route_task(part_slug, task_slug)
    family = get_object_or_404(
        Family.objects.filter(
            prompts__is_active=True,
            prompts__theme__task=task,
        ).distinct(),
        slug=slug,
        is_active=True,
    )
    prompts = list(
        Prompt.objects.filter(
            family=family,
            theme__task=task,
            is_active=True,
        )
        .select_related("response", "theme", "family")
        .order_by("theme__order", "number")
    )
    if (
        (task.part.slug, task.slug) == content_module.EE_TACHE3_TASK
        and prompts
        and all(
            prompt.content_key.startswith(
                content_module.EE_TACHE3_CONTENT_PREFIX
            )
            for prompt in prompts
        )
    ):
        return redirect(
            "study:theme_detail",
            task.part.slug,
            task.slug,
            prompts[0].theme.slug,
        )
    response_ids = [prompt.response_id for prompt in prompts]
    canonical_numbers = _canonical_numbers_by_response(response_ids)
    subject_progress = subject_progress_by_response(
        request.user,
        response_ids,
    )
    rows = [
        {
            "prompt": prompt,
            "progress": subject_progress[prompt.response_id],
            "is_alias": not prompt.is_canonical,
            "canonical_number": canonical_numbers.get(prompt.response_id),
        }
        for prompt in prompts
    ]
    return render(
        request,
        "study/family_detail.html",
        {
            "family": family,
            "task": task,
            "part": task.part,
            "rows": rows,
            "family_progress": summarize_subject_progress(
                subject_progress.values()
            )["progress"],
            "review_url": review_url(
                {
                    "kind": "spine",
                    "part": task.part.slug,
                    "task": task.slug,
                    "family": family.slug,
                }
            ),
        },
    )


def _subject_vocabulary_context(response, task_scope, user):
    subject_vocabulary = list(
        Phrase.objects.filter(
            source_prompts__response=response,
            is_active=True,
            tier=PhraseTier.SUBJECT,
        )
        .distinct()
        .select_related("category")
        .order_by("lot_order", "phrase_id")
    )
    vocabulary_count = len(subject_vocabulary)
    vocabulary_batches = _review_batches(
        {
            **task_scope,
            "kind": "vocab",
            "response": str(response.pk),
        },
        user,
    )
    vocabulary_batch_progress = summarize_review_batches(
        vocabulary_batches
    )
    vocabulary_lot_labels = {
        "Mots clés du sujet": "Mots clés",
        "Collocations du sujet": "Collocations",
        "Expressions du sujet": "Expressions et idiomes",
        "Tournures pour l'oral": "Tournures pour l'oral",
        "Phrases modèles": "Phrases modèles",
    }
    for batch in vocabulary_batches:
        start = (batch["number"] - 1) * queue_module.PHRASE_BATCH_SIZE
        if start < vocabulary_count:
            category_name = subject_vocabulary[start].category.name
            batch["label"] = vocabulary_lot_labels.get(
                category_name,
                category_name,
            )
    first_vocabulary_batch = next(
        (batch for batch in vocabulary_batches if batch["can_review"]),
        vocabulary_batches[0] if vocabulary_batches else None,
    )
    return {
        "subject_vocabulary": subject_vocabulary[:10],
        "vocabulary_count": vocabulary_count,
        "vocabulary_batches": vocabulary_batches,
        "vocabulary_batch_progress": vocabulary_batch_progress,
        "vocabulary_review_url": (
            first_vocabulary_batch["review_url"]
            if first_vocabulary_batch
            else None
        ),
    }


def response_detail(request, part_slug, task_slug, prompt_id):
    task = _route_task(part_slug, task_slug)
    selected_prompt = get_object_or_404(
        Prompt.objects.select_related(
            "response__theme__task__part",
            "response__family",
            "theme__task__part",
            "family",
        ),
        pk=prompt_id,
        is_active=True,
        response__is_active=True,
        theme__is_active=True,
        theme__task=task,
    )
    if (task.part.slug, task.slug) == content_module.QUESTION_BANK_TASK:
        return redirect(prompt_detail_url(selected_prompt))
    response = selected_prompt.response
    response_content = effective_response(response, request.user)
    subject_progress = subject_progress_by_response(
        request.user,
        {response.pk},
    )[response.pk]
    prompts = list(
        response.prompts.filter(
            is_active=True,
            theme__is_active=True,
        ).select_related(
            "theme__task__part",
            "family",
        )
    )

    navigation_prompts = Prompt.objects.filter(
        is_active=True,
        theme__is_active=True,
        theme_id=selected_prompt.theme_id,
    ).select_related("theme")
    navigation_prompts = list(
        navigation_prompts.order_by("number", "pk")
    )
    prompt_index = next(
        index
        for index, prompt in enumerate(navigation_prompts)
        if prompt.pk == selected_prompt.pk
    )
    previous_prompt = (
        navigation_prompts[prompt_index - 1] if prompt_index > 0 else None
    )
    next_prompt = (
        navigation_prompts[prompt_index + 1]
        if prompt_index + 1 < len(navigation_prompts)
        else None
    )

    card = Card.objects.filter(
        user=request.user,
        card_type=CardType.SPINE,
        response=response,
    ).first()
    related_phrases = (
        Phrase.objects.filter(
            source_prompts__response=response,
            is_active=True,
        )
        .exclude(tier=PhraseTier.SUBJECT)
        .distinct()
        .select_related("category")
    )
    task_scope = {"part": task.part.slug, "task": task.slug}
    phrase_batches = _review_batches(
        {
            **task_scope,
            "kind": "phrase",
            "response": str(response.pk),
        },
        request.user,
    )
    phrase_batch_progress = summarize_review_batches(phrase_batches)
    vocabulary_context = _subject_vocabulary_context(
        response,
        task_scope,
        request.user,
    )
    ee_response = (
        (task.part.slug, task.slug)
        == content_module.EE_TACHE3_TASK
        and response.content_key.startswith(
            content_module.EE_TACHE3_CONTENT_PREFIX
        )
    )
    ee_combination_label = ""
    if ee_response:
        source = _ee_tache_three_sources_by_key().get(
            response.content_key
        )
        if source is None:
            raise RuntimeError("EE Tâche 3 content is not synchronized")
        ee_combination_label = source.combinaison
    return render(
        request,
        "study/response_detail.html",
        {
            "response": response,
            "selected_prompt": selected_prompt,
            "previous_prompt": previous_prompt,
            "next_prompt": next_prompt,
            "prompt_position": prompt_index + 1,
            "prompt_total": len(navigation_prompts),
            "task": task,
            "part": task.part,
            "response_content": response_content,
            "arguments": response_content.arguments,
            "ee_response": ee_response,
            "ee_combination_label": ee_combination_label,
            "source_documents_html": response.body_html,
            "prompts": prompts,
            "card": card,
            "subject_progress": subject_progress,
            "related_phrases": related_phrases,
            "phrase_batches": phrase_batches,
            "phrase_batch_progress": phrase_batch_progress,
            **vocabulary_context,
            "can_edit_response": response.prompts.filter(
                is_active=True,
                theme__task__slug="tache-3",
                theme__task__part__slug="eo",
            ).exists(),
            "response_review_url": review_url(
                {
                    **task_scope,
                    "kind": "spine",
                    "response": str(response.pk),
                }
            ),
            "theme_review_url": review_url(
                {
                    **task_scope,
                    "kind": "spine",
                    "theme": selected_prompt.theme.slug,
                }
            ),
            "personal_saved": request.GET.get("saved") == "1",
            "personal_reset": request.GET.get("reset") == "1",
        },
    )


def edit_response(request, part_slug, task_slug, prompt_id):
    task = _route_task(part_slug, task_slug)
    selected_prompt = get_object_or_404(
        Prompt.objects.filter(
            pk=prompt_id,
            is_active=True,
            response__is_active=True,
            theme__is_active=True,
            theme__task=task,
            theme__task__slug="tache-3",
            theme__task__part__slug="eo",
        )
        .select_related(
            "response__theme__task__part",
            "response__family",
            "theme__task__part",
            "family",
        )
    )
    response = selected_prompt.response
    personal = PersonalResponse.objects.filter(
        user=request.user,
        response=response,
    ).first()
    if request.method == "POST" and request.POST.get("action") == "reset":
        if personal is not None:
            personal.delete()
        return redirect(f"{prompt_detail_url(selected_prompt)}?reset=1")

    form = PersonalResponseForm(
        response,
        request.user,
        request.POST or None,
    )
    if request.method == "POST" and form.is_valid():
        PersonalResponse.objects.update_or_create(
            user=request.user,
            response=response,
            defaults=form.personal_defaults(),
        )
        return redirect(f"{prompt_detail_url(selected_prompt)}?saved=1")

    argument_fields = []
    for order in form.argument_orders:
        argument_fields.append(
            {
                "order": order,
                "fields": [
                    form[f"argument_{order}_{key}"]
                    for key, _label, _rows in form.argument_parts
                ],
            }
        )
    return render(
        request,
        "study/response_edit.html",
        {
            "response": response,
            "selected_prompt": selected_prompt,
            "task": task,
            "part": task.part,
            "form": form,
            "argument_fields": argument_fields,
            "has_personal_response": personal is not None,
        },
    )


def phrases(
    request,
    part_slug=None,
    task_slug=None,
    category_slug=None,
    comprehension_mode=None,
    test_slug=None,
):
    legacy_scope_keys = {"part", "task", "domain", "mode", "category", "test"}
    if legacy_scope_keys.intersection(request.GET):
        raise Http404
    if bool(part_slug) != bool(task_slug):
        raise Http404
    if comprehension_mode not in {
        None,
        ComprehensionMode.ECRITE,
        ComprehensionMode.ORALE,
    }:
        raise Http404
    if comprehension_mode and (task_slug or category_slug):
        raise Http404
    if test_slug and not comprehension_mode:
        raise Http404
    task = (
        _route_task(part_slug, task_slug)
        if part_slug and task_slug
        else None
    )
    if task and not task.available:
        return render(
            request,
            "study/coming_soon.html",
            {"part": task.part, "task": task},
        )
    functional_names = FUNCTIONAL_PHRASE_CATEGORY_NAMES
    category_descriptions = {
        "Structurer et prendre position": (
            "Reformuler le sujet, annoncer ton avis et guider clairement "
            "l'examinateur."
        ),
        "Nuancer et comparer": (
            "Éviter les réponses trop absolues et confronter plusieurs "
            "points de vue."
        ),
        "Cause, conséquence et évaluation": (
            "Expliquer pourquoi, montrer les effets et porter un jugement "
            "précis."
        ),
        "Schémas d'argumentation": (
            "Construire des arguments complets avec des tournures "
            "réutilisables."
        ),
    }
    if category_slug and test_slug:
        raise Http404
    selected = None
    selected_test = None
    all_phrases = (
        Phrase.objects.filter(
            is_active=True,
            tier=PhraseTier.SHARED,
        )
        .select_related("category")
        .prefetch_related("source_prompts__theme__task__part")
    )
    if task:
        all_phrases = all_phrases.filter(
            source_prompts__theme__task=task
        ).distinct()
    categories = list(
        PhraseCategory.objects.filter(
            is_active=True,
            phrases__in=all_phrases
        ).distinct().order_by("order")
    )
    phrase_scope = {"kind": "phrase"}
    if task:
        phrase_scope.update({"part": task.part.slug, "task": task.slug})
    category_card_counts = dict(
        queue_module.scoped_cards(
            phrase_scope,
            user=request.user,
            include_suspended=True,
        )
        .order_by()
        .values("phrase__category_id")
        .annotate(total=Count("id", distinct=True))
        .values_list("phrase__category_id", "total")
    )
    for category in categories:
        category.phrase_count = all_phrases.filter(
            category=category
        ).count()
        category.card_count = category_card_counts.get(category.id, 0)
        category.batch_count = (
            category.phrase_count + queue_module.PHRASE_BATCH_SIZE - 1
        ) // queue_module.PHRASE_BATCH_SIZE
        category.is_functional = category.name in functional_names
        category.learning_description = category_descriptions.get(
            category.name,
            "Expressions réutilisables dans plusieurs réponses.",
        )
        category.url = vocabulary_url(task=task, category=category)
        if category.is_functional:
            category.review_batches = _review_batches(
                {**phrase_scope, "category": category.slug},
                request.user,
            )
            category.progress = summarize_review_batches(
                category.review_batches
            )
            category.completed_batch_count = category.progress.completed

    phrase_qs = all_phrases.none()
    if category_slug:
        selected = next(
            (
                category
                for category in categories
                if category.slug == category_slug
            ),
            None,
        )
        if selected is None:
            raise Http404
        phrase_qs = all_phrases.filter(category=selected)
    elif test_slug:
        selected_test = get_object_or_404(
            ComprehensionTest,
            slug=test_slug,
            mode=comprehension_mode,
            is_active=True,
            is_published=True,
        )
        phrase_qs = (
            Phrase.objects.filter(
                is_active=True,
                tier=PhraseTier.COMPREHENSION,
                source_questions__test=selected_test,
                source_questions__is_active=True,
            )
            .select_related("category")
            .prefetch_related("source_questions__test")
            .distinct()
            .order_by("category__order", "lot_order", "phrase_id")
        )

    grouped = []
    review_batches = []
    first_review_batch = None
    if selected:
        grouped.append(
            {
                "category": selected,
                "phrases": list(phrase_qs),
            }
        )
        review_batches = getattr(selected, "review_batches", None)
        if review_batches is None:
            review_batches = _review_batches(
                {**phrase_scope, "category": selected.slug},
                request.user,
            )
        first_review_batch = next(
            (batch for batch in review_batches if batch["can_review"]),
            None,
        )
    elif selected_test:
        for category in PhraseCategory.objects.filter(
            phrases__in=phrase_qs,
            is_active=True,
        ).distinct().order_by("order"):
            grouped.append(
                {
                    "category": category,
                    "phrases": list(phrase_qs.filter(category=category)),
                }
            )
        review_batches = _review_batches(
            {"kind": "vocab", "test": selected_test.slug},
            request.user,
        )
        first_review_batch = next(
            (batch for batch in review_batches if batch["can_review"]),
            review_batches[0] if review_batches else None,
        )

    functional_categories = [
        category
        for category in categories
        if category.is_functional
    ]
    collection_progress = (
        summarize_review_batches(review_batches)
        if review_batches
        else None
    )
    comprehension_directory = comprehension_mode is not None
    vocabulary_landing = not (
        selected
        or selected_test
        or task
        or comprehension_directory
    )
    subject_theme_groups = []
    subject_prompt_count = 0
    subject_response_count = 0
    subject_vocabulary_count = 0
    comprehension_decks = []
    comprehension_vocabulary_paths = []
    comprehension_vocabulary_count = 0
    if not selected and not selected_test and not comprehension_directory:
        subject_prompts = (
            Prompt.objects.filter(
                is_active=True,
                response__is_active=True,
                theme__is_active=True,
            )
            .select_related("theme__task__part", "family", "response")
            .annotate(
                vocabulary_count=Count(
                    "phrases",
                    filter=Q(
                        phrases__is_active=True,
                        phrases__tier=PhraseTier.SUBJECT,
                    ),
                    distinct=True,
                )
            )
            .filter(vocabulary_count__gt=0)
        )
        subject_vocabulary = Phrase.objects.filter(
            is_active=True,
            tier=PhraseTier.SUBJECT,
            source_prompts__is_active=True,
            source_prompts__theme__is_active=True,
        )
        if task:
            subject_prompts = subject_prompts.filter(theme__task=task)
            subject_vocabulary = subject_vocabulary.filter(
                source_prompts__theme__task=task
            )
        subject_prompts = list(
            subject_prompts.order_by("theme__order", "number", "pk")
        )
        subject_prompt_count = len(subject_prompts)
        subject_response_ids = {
            prompt.response_id for prompt in subject_prompts
        }
        subject_progress = subject_progress_by_response(
            request.user,
            subject_response_ids,
        )
        current_group = None
        for prompt in subject_prompts:
            prompt.detail_url = prompt_detail_url(prompt)
            prompt.review_url = review_url(
                {
                    "part": prompt.theme.task.part.slug,
                    "task": prompt.theme.task.slug,
                    "kind": "vocab",
                    "response": str(prompt.response_id),
                    "batch": "1",
                }
            )
            prompt.vocabulary_batch_count = (
                prompt.vocabulary_count
                + queue_module.PHRASE_BATCH_SIZE
                - 1
            ) // queue_module.PHRASE_BATCH_SIZE
            prompt.subject_progress = subject_progress[prompt.response_id]
            prompt.vocabulary_progress = (
                prompt.subject_progress.vocabulary_progress
            )
            if (
                current_group is None
                or current_group["theme"].pk != prompt.theme_id
            ):
                current_group = {
                    "theme": prompt.theme,
                    "prompts": [],
                    "response_ids": set(),
                    "response_progress": {},
                }
                subject_theme_groups.append(current_group)
            current_group["prompts"].append(prompt)
            current_group["response_ids"].add(prompt.response_id)
            current_group["response_progress"][prompt.response_id] = (
                prompt.vocabulary_progress
            )
        for group in subject_theme_groups:
            group["deck_count"] = len(group.pop("response_ids"))
            response_progress = list(group.pop("response_progress").values())
            group["progress"] = progress_summary(
                total=len(response_progress),
                started=sum(
                    item.status != "new" for item in response_progress
                ),
                completed=sum(
                    item.status == "done" for item in response_progress
                ),
            )
        subject_response_count = len(subject_response_ids)
        subject_vocabulary_count = subject_vocabulary.distinct().count()

    if vocabulary_landing or comprehension_directory:
        tests = (
            ComprehensionTest.objects.filter(
                is_active=True,
                is_published=True,
            )
            .annotate(
                vocabulary_count=Count(
                    "questions__vocabulary",
                    filter=Q(
                        questions__vocabulary__is_active=True,
                        questions__vocabulary__tier=PhraseTier.COMPREHENSION,
                    ),
                    distinct=True,
                )
            )
            .filter(vocabulary_count__gt=0)
            .order_by("mode", "number")
        )
        if comprehension_mode:
            tests = tests.filter(mode=comprehension_mode)
        for test in tests:
            deck_scope = {"kind": "vocab", "test": test.slug}
            cards = queue_module.scoped_cards(
                deck_scope,
                user=request.user,
            )
            batches = _review_batches(deck_scope, request.user)
            deck_progress = card_unit_progress(cards)
            comprehension_decks.append(
                {
                    "test": test,
                    "vocabulary_count": test.vocabulary_count,
                    "batch_count": len(batches),
                    "completed_batch_count": sum(
                        batch["status"] == "complete"
                        for batch in batches
                    ),
                    "progress": deck_progress,
                    "stats": deck_stats(cards, timezone.now()),
                    "counts": queue_module.queue_counts(
                        deck_scope,
                        user=request.user,
                    ),
                    "skill_code": comprehension_skill(test.mode),
                    "detail_url": comprehension_vocabulary_url(test=test),
                    "review_url": review_url(
                        {**deck_scope, "batch": "1"}
                    ),
                }
            )
            comprehension_vocabulary_count += test.vocabulary_count
        if vocabulary_landing:
            for mode, code, title in (
                (ComprehensionMode.ECRITE, "ce", "Écrite"),
                (ComprehensionMode.ORALE, "co", "Orale"),
            ):
                mode_decks = [
                    deck
                    for deck in comprehension_decks
                    if deck["test"].mode == mode
                ]
                mode_progress = combine_progress(
                    deck["progress"] for deck in mode_decks
                )
                comprehension_vocabulary_paths.append(
                    {
                        "code": code,
                        "title": title,
                        "available": bool(mode_decks),
                        "url": comprehension_vocabulary_url(mode=mode),
                        "test_count": len(mode_decks),
                        "entry_count": sum(
                            deck["vocabulary_count"] for deck in mode_decks
                        ),
                        "progress": mode_progress,
                    }
                )

    expression_vocabulary_paths = (
        _vocabulary_expression_paths(timezone.now(), request.user)
        if vocabulary_landing
        else []
    )

    vocabulary_scope = {"content": "vocabulary"}
    vocabulary_cards = queue_module.scoped_cards(
        vocabulary_scope,
        user=request.user,
    )
    vocabulary_counts = queue_module.queue_counts(
        vocabulary_scope,
        user=request.user,
    )
    vocabulary_revisit_count = queue_module.scoped_cards(
        {"kind": "revisit", "content": "vocabulary"},
        user=request.user,
    ).count()
    vocabulary_weak_count = queue_module.queue_counts(
        {"kind": "weak", "content": "vocabulary"},
        user=request.user,
    )["weak_total"]
    selected_review_url = ""
    if selected_test:
        selected_review_url = review_url(
            {"kind": "vocab", "test": selected_test.slug}
        )
    elif selected:
        selected_review_url = review_url(
            {**phrase_scope, "category": selected.slug}
        )
    vocabulary_root_url = (
        comprehension_vocabulary_url(mode=selected_test.mode)
        if selected_test
        else vocabulary_url(task=task)
    )

    return render(
        request,
        "study/phrases.html",
        {
            "part": task.part if task else None,
            "task": task,
            "categories": categories,
            "functional_categories": functional_categories,
            "vocabulary_landing": vocabulary_landing,
            "comprehension_directory": comprehension_directory,
            "comprehension_mode": comprehension_mode,
            "comprehension_skill_code": (
                comprehension_skill(comprehension_mode)
                if comprehension_mode
                else ""
            ),
            "expression_vocabulary_paths": expression_vocabulary_paths,
            "comprehension_vocabulary_paths": comprehension_vocabulary_paths,
            "functional_phrase_count": sum(
                category.phrase_count
                for category in functional_categories
            ),
            "first_category": (
                functional_categories[0]
                if functional_categories
                else None
            ),
            "subject_theme_groups": subject_theme_groups,
            "subject_prompt_count": subject_prompt_count,
            "subject_response_count": subject_response_count,
            "subject_vocabulary_count": subject_vocabulary_count,
            "comprehension_decks": comprehension_decks,
            "comprehension_vocabulary_count": (
                comprehension_vocabulary_count
            ),
            "vocabulary_stats": deck_stats(
                vocabulary_cards,
                timezone.now(),
            ),
            "vocabulary_counts": vocabulary_counts,
            "vocabulary_revisit_count": vocabulary_revisit_count,
            "vocabulary_weak_count": vocabulary_weak_count,
            "grouped": grouped,
            "review_batches": review_batches,
            "collection_progress": collection_progress,
            "first_review_batch": first_review_batch,
            "batch_size": queue_module.PHRASE_BATCH_SIZE,
            "selected": selected,
            "selected_test": selected_test,
            "selected_review_url": selected_review_url,
            "vocabulary_root_url": vocabulary_root_url,
            "phrase_count": (
                selected.phrase_count
                if selected
                else (
                    phrase_qs.count()
                    if selected_test
                    else sum(
                    category.phrase_count
                    for category in functional_categories
                    )
                )
            ),
        },
    )


def search(request, part_slug=None, task_slug=None):
    if "part" in request.GET or "task" in request.GET:
        raise Http404
    if bool(part_slug) != bool(task_slug):
        raise Http404
    task = (
        _route_task(part_slug, task_slug)
        if part_slug and task_slug
        else None
    )
    query = request.GET.get("q", "").strip()
    prompt_results = []
    phrase_results = []
    comprehension_results = []
    prompt_result_count = 0
    phrase_result_count = 0
    comprehension_result_count = 0
    result_limit = 12
    if query:
        prompt_qs = Prompt.objects.filter(is_active=True).filter(
            Q(text__icontains=query) | Q(response__body__icontains=query)
        )
        phrase_qs = Phrase.objects.filter(
            Q(is_active=True),
            Q(expression__icontains=query)
            | Q(english_cue__icontains=query)
            | Q(example__icontains=query)
            | Q(note__icontains=query)
        )
        if task:
            prompt_qs = prompt_qs.filter(theme__task=task)
            phrase_qs = phrase_qs.filter(
                source_prompts__theme__task=task
            ).distinct()
        prompt_result_count = prompt_qs.count()
        phrase_result_count = phrase_qs.count()
        prompt_results = list(
            prompt_qs
            .select_related("response", "theme__task__part", "family")
            .order_by("theme__order", "number")[:result_limit]
        )
        prompt_progress = subject_progress_by_response(
            request.user,
            {prompt.response_id for prompt in prompt_results},
        )
        for prompt in prompt_results:
            prompt.subject_progress = prompt_progress[prompt.response_id]
        phrase_results = list(
            phrase_qs
            .select_related("category")
            .order_by("order")[:result_limit]
        )
        if not task:
            comprehension_qs = (
                ComprehensionQuestion.objects.filter(
                    test__is_active=True,
                    test__is_published=True,
                    is_active=True,
                )
                .filter(
                    Q(passage_fr__icontains=query)
                    | Q(prompt_fr__icontains=query)
                    | Q(passage_en__icontains=query)
                    | Q(prompt_en__icontains=query)
                    | Q(choices__text_fr__icontains=query)
                    | Q(choices__text_en__icontains=query)
                )
                .select_related("test")
                .distinct()
                .order_by("test__number", "number")
            )
            comprehension_result_count = comprehension_qs.count()
            comprehension_results = list(
                comprehension_qs[:result_limit]
            )
    result_count = (
        prompt_result_count
        + phrase_result_count
        + comprehension_result_count
    )
    visible_result_count = (
        len(prompt_results)
        + len(phrase_results)
        + len(comprehension_results)
    )
    return render(
        request,
        "study/search.html",
        {
            "part": task.part if task else None,
            "task": task,
            "search_url": request.path,
            "query": query,
            "prompt_results": prompt_results,
            "phrase_results": phrase_results,
            "comprehension_results": comprehension_results,
            "prompt_result_count": prompt_result_count,
            "phrase_result_count": phrase_result_count,
            "comprehension_result_count": comprehension_result_count,
            "result_count": result_count,
            "visible_result_count": visible_result_count,
            "results_truncated": result_count > visible_result_count,
            "prompt_total": (
                Prompt.objects.filter(
                    theme__task=task,
                    is_active=True,
                ).count()
                if task
                else Prompt.objects.filter(is_active=True).count()
            ),
            "phrase_total": (
                _task_phrases(task).count()
                if task
                else Phrase.objects.filter(is_active=True).count()
            ),
        },
    )


def _stats_scope_cards(scope, user):
    cards = Card.objects.current_content().filter(user=user)
    if not scope:
        return cards

    response_ids = queue_module.scoped_cards(
        {**scope, "content": "spine"},
        user=user,
        include_suspended=True,
    ).values("pk")
    vocabulary_ids = queue_module.scoped_cards(
        {**scope, "content": "vocabulary"},
        user=user,
        include_suspended=True,
    ).values("pk")
    return cards.filter(
        Q(pk__in=response_ids) | Q(pk__in=vocabulary_ids)
    ).distinct()


def stats(request, part_slug=None, task_slug=None):
    now = timezone.now()
    today = timezone.localtime(now).date()
    if bool(task_slug) and not part_slug:
        raise Http404
    forced_task = (
        _route_task(part_slug, task_slug)
        if part_slug is not None and task_slug is not None
        else None
    )
    filters = _scope_filters(
        request,
        forced_task,
        forced_part_slug=part_slug if forced_task is None else None,
    )
    scope = filters["scope"]

    scoped_history_cards = _stats_scope_cards(scope, request.user)
    active_cards = scoped_history_cards.filter(suspended=False)
    logs_base = ReviewLog.objects.filter(user=request.user)
    if scope:
        logs_base = logs_base.filter(
            card_id__in=scoped_history_cards.values("pk")
        )

    since = now - timezone.timedelta(days=90)
    logs = logs_base.filter(reviewed_at__gte=since)
    per_day: dict = {}
    for reviewed_at in logs.values_list("reviewed_at", flat=True):
        day = timezone.localtime(reviewed_at).date()
        per_day[day] = per_day.get(day, 0) + 1

    daily = []
    for offset in range(29, -1, -1):
        day = today - timezone.timedelta(days=offset)
        daily.append({"date": day, "count": per_day.get(day, 0)})
    max_daily = max((d["count"] for d in daily), default=0) or 1
    reviews_30_days = sum(d["count"] for d in daily)

    heat = []
    for offset in range(89, -1, -1):
        day = today - timezone.timedelta(days=offset)
        count = per_day.get(day, 0)
        level = min(4, 1 + count // 15) if count else 0
        heat.append({"date": day, "count": count, "level": level})

    mature_logs = logs_base.filter(
        reviewed_at__gte=now - timezone.timedelta(days=30),
        interval_before__gte=MATURE_DAYS,
    )
    mature_total = mature_logs.count()
    mature_pass = mature_logs.exclude(rating=Rating.AGAIN).count()
    retention = round(100 * mature_pass / mature_total) if mature_total else None

    forecast = []
    active = active_cards.filter(
        state__in=[CardState.REVIEW, CardState.LEARNING, CardState.RELEARNING]
    )
    for offset in range(0, 14):
        day = today + timezone.timedelta(days=offset)
        start = timezone.make_aware(
            timezone.datetime.combine(day, timezone.datetime.min.time())
        )
        end = start + timezone.timedelta(days=1)
        if offset == 0:
            count = active.filter(due__lt=end).count()
        else:
            count = active.filter(due__gte=start, due__lt=end).count()
        forecast.append({"date": day, "count": count})
    max_forecast = max((f["count"] for f in forecast), default=0) or 1

    overall = deck_stats(active_cards, now)
    mastery_percentage = (
        round(100 * overall["mature"] / overall["total"])
        if overall["total"]
        else 0
    )

    theme_qs = Theme.objects.select_related("task__part").filter(
        is_active=True,
        task__isnull=False,
    )
    if scope.get("task"):
        theme_qs = theme_qs.filter(
            task__slug=scope["task"],
            task__part__slug=scope["part"],
        )
    elif scope.get("part"):
        theme_qs = theme_qs.filter(task__part__slug=scope["part"])
    themes = [
        {
            "theme": theme,
            "stats": deck_stats(
                Card.objects.active().filter(
                    user=request.user,
                    card_type=CardType.SPINE, response__theme=theme
                ),
                now,
            ),
            "review_url": review_url(
                {
                    "kind": "spine",
                    "part": theme.task.part.slug,
                    "task": theme.task.slug,
                    "theme": theme.slug,
                }
            ),
        }
        for theme in theme_qs
    ]

    context = {
        "daily": daily,
        "max_daily": max_daily,
        "reviews_30_days": reviews_30_days,
        "heat": heat,
        "reviews_90_days": sum(cell["count"] for cell in heat),
        "retention": retention,
        "mature_total": mature_total,
        "forecast": forecast,
        "max_forecast": max_forecast,
        "forecast_total": sum(item["count"] for item in forecast),
        "overall": overall,
        "mastery_percentage": mastery_percentage,
        "themes": themes,
        "streak": current_streak(now, logs_base, request.user),
        "total_reviews": logs_base.count(),
        "reviews_today": per_day.get(today, 0),
        "recent_sessions": recent_review_sessions(logs_base),
        "expression_weak_count": queue_module.queue_counts(
            {**scope, "kind": "weak", "content": "spine"},
            now,
            user=request.user,
        )["weak_total"],
        "vocabulary_weak_count": queue_module.queue_counts(
            {
                **scope,
                "kind": "weak",
                "content": "vocabulary",
            },
            now,
            user=request.user,
        )["weak_total"],
        "expression_weak_url": review_url(
            {**scope, "kind": "weak", "content": "spine"}
        ),
        "vocabulary_weak_url": review_url(
            {**scope, "kind": "weak", "content": "vocabulary"}
        ),
        **filters,
    }
    return render(request, "study/stats.html", context)
