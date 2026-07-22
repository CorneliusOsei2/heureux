"""Shared view helpers, scope resolution, and constants."""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from django.utils import timezone

from .. import content as content_module
from .. import queue as queue_module
from ..models import (
    CardState,
    MemoryQuestionProgress,
    Phrase,
    PhraseTier,
    Prompt,
    Rating,
    Response,
    ReviewLog,
    Task,
    Theme,
)
from ..progress import (
    ProgressSummary,
    SubjectProgress,
    combine_progress,
    progress_summary,
    subject_progress_by_response,
    summarize_subject_progress,
)
from ..routing import review_url

MATURE_DAYS = 21


RECENT_SESSION_GAP = timezone.timedelta(minutes=30)


FUNCTIONAL_PHRASE_CATEGORY_NAMES = frozenset(
    {
        "Structurer et prendre position",
        "Nuancer et comparer",
        "Cause, conséquence et évaluation",
        "Schémas d'argumentation",
    }
)


def _memory_progress(user, memories):
    memories = tuple(memories)
    completed_by_memory = {
        memory.number: set()
        for memory in memories
    }
    for memory_number, question_key in (
        MemoryQuestionProgress.objects.filter(
            user=user,
            memory_number__in=completed_by_memory,
        ).values_list("memory_number", "question_key")
    ):
        completed_by_memory[memory_number].add(question_key)

    states = {}
    for memory in memories:
        valid_keys = set(memory.question_keys)
        completed_keys = frozenset(
            completed_by_memory[memory.number] & valid_keys
        )
        states[memory.number] = {
            "completed_keys": completed_keys,
            "progress": progress_summary(
                total=memory.question_count,
                started=len(completed_keys),
                completed=len(completed_keys),
            ),
        }
    return states


_EMPTY_SUBJECT_PROGRESS = SubjectProgress(
    status="new",
    label="À commencer",
    has_highlight=False,
    response_practice_started=False,
    vocabulary_total=0,
    vocabulary_started=0,
    vocabulary_completed=0,
    vocabulary_mastered=0,
    vocabulary_due=0,
)


def _tache_two_progress(user, months):
    """Attach material-specific progress to Tâche 2 months and subjects."""
    months = tuple(months)
    content_keys = [
        content_module.tache_two_subject_content_key(
            month.slug,
            batch.number,
            subject.number,
        )
        for month in months
        for batch in month.batches
        for subject in batch.subjects
    ]
    response_id_by_content_key = dict(
        Response.objects.filter(
            content_key__in=content_keys,
            is_active=True,
        ).values_list("content_key", "pk")
    )
    progress_by_response = subject_progress_by_response(
        user,
        response_id_by_content_key.values(),
    )
    progress_by_content_key = {
        content_key: progress_by_response[response_id]
        for content_key, response_id in response_id_by_content_key.items()
    }

    all_progress = []
    month_rows = []
    for month in months:
        month_progress = []
        batch_rows = []
        for batch in month.batches:
            batch_progress = []
            subjects = []
            for subject in batch.subjects:
                content_key = content_module.tache_two_subject_content_key(
                    month.slug,
                    batch.number,
                    subject.number,
                )
                progress = progress_by_content_key.get(
                    content_key,
                    _EMPTY_SUBJECT_PROGRESS,
                )
                vocabulary_progress = progress.vocabulary_progress
                batch_progress.append(progress)
                month_progress.append(progress)
                all_progress.append(progress)
                subjects.append(
                    {
                        "number": subject.number,
                        "number_label": subject.number_label,
                        "title": subject.title,
                        "prompt": subject.prompt,
                        "questions": subject.questions,
                        "question_count": subject.question_count,
                        "memory_question_count": subject.memory_question_count,
                        "content_key": content_key,
                        "response_id": response_id_by_content_key.get(
                            content_key
                        ),
                        "progress": progress,
                        "vocabulary_progress": vocabulary_progress,
                        "vocabulary_started_only": max(
                            vocabulary_progress.started
                            - vocabulary_progress.completed,
                            0,
                        ),
                    }
                )
            batch_summary = summarize_subject_progress(batch_progress)
            batch_rows.append(
                {
                    "number": batch.number,
                    "number_label": batch.number_label,
                    "subjects": tuple(subjects),
                    "subject_count": batch.subject_count,
                    "question_count": batch.question_count,
                    "first_subject_number": batch.first_subject_number,
                    "last_subject_number": batch.last_subject_number,
                    **batch_summary,
                }
            )
        month_summary = summarize_subject_progress(month_progress)
        month_rows.append(
            {
                "number": month.number,
                "slug": month.slug,
                "name": month.name,
                "batches": tuple(batch_rows),
                "batch_count": month.batch_count,
                "subject_count": month.subject_count,
                "question_count": month.question_count,
                **month_summary,
            }
        )

    summary = summarize_subject_progress(all_progress)
    return {
        "months": tuple(month_rows),
        "progress_by_content_key": progress_by_content_key,
        "summary": summary,
        **summary,
    }


def deck_stats(qs, now=None) -> dict:
    now = now or timezone.now()
    total = qs.count()
    new = qs.filter(state=CardState.NEW).count()
    started_new = qs.filter(
        state=CardState.NEW,
        started_at__isnull=False,
    ).count()
    learning = qs.filter(
        state__in=[CardState.LEARNING, CardState.RELEARNING]
    ).count()
    review = qs.filter(state=CardState.REVIEW).count()
    mature = qs.filter(
        state=CardState.REVIEW, interval_days__gte=MATURE_DAYS
    ).count()
    due = qs.filter(
        state__in=[CardState.LEARNING, CardState.RELEARNING, CardState.REVIEW],
        due__lte=now,
    ).count()
    seen = total - new + started_new
    return {
        "total": total,
        "new": new,
        "started_new": started_new,
        "learning": learning,
        "review": review,
        "mature": mature,
        "review_young": review - mature,
        "due": due,
        "seen": seen,
        "reviewed": total - new,
        "pct": round(100 * seen / total) if total else 0,
        "mature_pct": round(100 * mature / total) if total else 0,
    }


def _review_batches(scope: dict, user) -> list[dict]:
    """Describe stable lots and each lot's first-pass progress."""
    base_scope = {key: value for key, value in scope.items() if key != "batch"}
    rows = list(
        queue_module.scoped_cards(
            base_scope,
            user=user,
            include_suspended=True,
        )
        .order_by(*queue_module.batch_ordering(base_scope))
        .values(
            "id",
            "phrase_id",
            "state",
            "due",
            "suspended",
            "started_at",
            "response_practice_started_at",
        )
    )
    phrase_batches = queue_module._uses_phrase_batches(base_scope)
    if phrase_batches:
        grouped_rows = {}
        for row in rows:
            grouped_rows.setdefault(row["phrase_id"], []).append(row)
        units = list(grouped_rows.values())
    else:
        units = [[row] for row in rows]

    now = timezone.now()
    size = queue_module.batch_size(base_scope)
    batches = []
    for number, start in enumerate(
        range(0, len(units), size),
        start=1,
    ):
        units_in_batch = units[start : start + size]
        active_units = [
            [row for row in unit if not row["suspended"]]
            for unit in units_in_batch
        ]
        active_units = [unit for unit in active_units if unit]
        started_count = sum(
            any(
                row["state"] != CardState.NEW
                or (
                    row["started_at"]
                    if phrase_batches
                    else row["response_practice_started_at"]
                )
                is not None
                for row in unit
            )
            for unit in active_units
        )
        completed_count = sum(
            all(row["state"] != CardState.NEW for row in unit)
            for unit in active_units
        )
        available_now = sum(
            row["state"] == CardState.NEW
            or (
                row["due"] is not None
                and row["due"] <= now
                and row["state"]
                in {
                    CardState.LEARNING,
                    CardState.RELEARNING,
                    CardState.REVIEW,
                }
            )
            for unit in active_units
            for row in unit
        )
        if not active_units:
            status = "unavailable"
            status_label = "Suspendu"
        elif completed_count == len(active_units):
            status = "complete"
            status_label = "Terminé"
        elif started_count:
            status = "in-progress"
            status_label = "En cours"
        else:
            status = "not-started"
            status_label = "À commencer"
        end = start + len(units_in_batch)
        batch_scope = {**base_scope, "batch": str(number)}
        batches.append(
            {
                "number": number,
                "start": start + 1,
                "end": end,
                "card_count": sum(len(unit) for unit in units_in_batch),
                "phrase_count": (
                    len(units_in_batch) if phrase_batches else None
                ),
                "active_count": len(active_units),
                "completed_count": completed_count,
                "seen_count": completed_count,
                "started_count": started_count,
                "available_now": available_now,
                "phrase_batch": phrase_batches,
                "status": status,
                "status_label": status_label,
                "progress_status": {
                    "complete": "done",
                    "in-progress": "active",
                    "not-started": "new",
                    "unavailable": "new",
                }[status],
                "can_review": available_now > 0,
                "review_url": review_url(batch_scope),
            }
        )
    next_batch_found = False
    for batch in batches:
        batch["is_next"] = batch["can_review"] and not next_batch_found
        next_batch_found = next_batch_found or batch["can_review"]
    return batches


def summarize_review_batches(batches) -> ProgressSummary:
    """Bubble active lot completion into one parent progress summary."""
    available = [batch for batch in batches if batch["status"] != "unavailable"]
    return progress_summary(
        total=len(available),
        started=sum(
            batch["status"] in {"in-progress", "complete"}
            for batch in available
        ),
        completed=sum(batch["status"] == "complete" for batch in available),
    )


def current_streak(now=None, logs=None, user=None) -> int:
    """Consecutive days (up to today) with at least one review."""
    now = now or timezone.now()
    logs = ReviewLog.objects.filter(user=user) if logs is None else logs
    days = {
        timezone.localtime(dt).date()
        for dt in logs.values_list("reviewed_at", flat=True)
    }
    if not days:
        return 0
    today = timezone.localtime(now).date()
    cursor = today
    if cursor not in days:
        cursor = today - timezone.timedelta(days=1)
        if cursor not in days:
            return 0
    streak = 0
    while cursor in days:
        streak += 1
        cursor = cursor - timezone.timedelta(days=1)
    return streak


def recent_review_sessions(logs, *, limit=8) -> list[dict]:
    """Group recent review logs into focused sessions separated by 30 minutes."""
    recent_logs = list(
        logs.select_related(
            "card__response__theme",
            "card__phrase",
        ).order_by("-reviewed_at")[:400]
    )
    sessions = []
    current = None
    for log in recent_logs:
        if (
            current is None
            or current["started_at"] - log.reviewed_at > RECENT_SESSION_GAP
        ):
            if len(sessions) >= limit:
                break
            current = {
                "started_at": log.reviewed_at,
                "ended_at": log.reviewed_at,
                "review_count": 0,
                "correct_count": 0,
                "revisit_count": 0,
                "response_count": 0,
                "phrase_count": 0,
                "elapsed_ms": 0,
                "topics_set": set(),
            }
            sessions.append(current)

        current["started_at"] = log.reviewed_at
        current["review_count"] += 1
        current["elapsed_ms"] += log.elapsed_ms
        if log.rating == Rating.AGAIN:
            current["revisit_count"] += 1
        else:
            current["correct_count"] += 1
        if log.card.response_id:
            current["response_count"] += 1
            current["topics_set"].add(log.card.response.theme.display_name)
        else:
            current["phrase_count"] += 1
            current["topics_set"].add("Expressions")

    for session in sessions:
        session["accuracy"] = round(
            100 * session["correct_count"] / session["review_count"]
        )
        session["study_minutes"] = (
            max(1, round(session["elapsed_ms"] / 60000))
            if session["elapsed_ms"]
            else None
        )
        topics = sorted(session.pop("topics_set"))
        session["topics"] = topics[:3]
        session["extra_topics"] = max(0, len(topics) - 3)
    return sessions


def _task_scope(task) -> dict:
    return {"part": task.part.slug, "task": task.slug}


def _task_cards(task, user=None, kind=None):
    scope = _task_scope(task)
    if kind:
        scope["kind"] = kind
    return queue_module.scoped_cards(scope, user=user)


def _task_phrases(task):
    return Phrase.objects.filter(
        is_active=True,
        tier=PhraseTier.SHARED,
        source_prompts__is_active=True,
        source_prompts__theme__is_active=True,
        source_prompts__theme__task=task,
    ).distinct()


def _route_task(part_slug, task_slug):
    return get_object_or_404(
        Task.objects.select_related("part"),
        slug=task_slug,
        part__slug=part_slug,
        is_active=True,
        part__is_active=True,
    )


def _task_card(task, now, user):
    """Build a dashboard/part card for a single task."""
    question_bank = None
    subject_state = None
    if (
        task.available
        and (task.part.slug, task.slug) == content_module.QUESTION_BANK_TASK
    ):
        banks = content_module.load_question_banks()
        subject_state = _tache_two_progress(
            user,
            content_module.load_tache_two_subject_months(),
        )
        memory_states = _memory_progress(user, banks)
        memory_progress = progress_summary(
            total=sum(
                state["progress"].total
                for state in memory_states.values()
            ),
            started=sum(
                state["progress"].started
                for state in memory_states.values()
            ),
            completed=sum(
                state["progress"].completed
                for state in memory_states.values()
            ),
        )
        task_progress = combine_progress(
            [memory_progress, subject_state["progress"]]
        )
        question_bank = {
            "title": f"{len(banks)} mémoire{'s' if len(banks) > 1 else ''}",
            "memory_count": len(banks),
            "subject_count": subject_state["total"],
            "category_count": sum(bank.category_count for bank in banks),
            "question_count": sum(bank.question_count for bank in banks),
            "progress": task_progress,
            "memory_progress": memory_progress,
            "subject_progress": subject_state["progress"],
            "active_count": max(
                task_progress.started - task_progress.completed,
                0,
            ),
        }
    if task.available:
        if subject_state is None:
            response_ids = set(
                Prompt.objects.filter(
                    theme__task=task,
                    theme__is_active=True,
                    is_active=True,
                    response__is_active=True,
                ).values_list("response_id", flat=True)
            )
            response_progress = subject_progress_by_response(
                user,
                response_ids,
            )
            response_stats = summarize_subject_progress(
                response_progress.values()
            )
        else:
            response_stats = dict(subject_state["summary"])
        response_stats["due"] = deck_stats(
            _task_cards(task, user, "spine"),
            now,
        )["due"]
        phrase_stats = deck_stats(_task_cards(task, user, "phrase"), now)
        functional_phrase_stats = deck_stats(
            _task_cards(task, user, "phrase").filter(
                phrase__tier=PhraseTier.SHARED,
                phrase__category__name__in=FUNCTIONAL_PHRASE_CATEGORY_NAMES,
            ),
            now,
        )
        stats = response_stats
        counts = queue_module.queue_counts(
            _task_scope(task),
            now,
            user=user,
        )
        phrase_counts = queue_module.queue_counts(
            {**_task_scope(task), "kind": "phrase"},
            now,
            user=user,
        )
        revisit_count = _task_cards(task, user, "revisit").count()
        theme_count = Theme.objects.filter(task=task, is_active=True).count()
        prompt_count = Prompt.objects.filter(
            theme__task=task,
            is_active=True,
        ).count()
        phrase_count = _task_phrases(task).count()
        functional_phrase_count = _task_phrases(task).filter(
            category__name__in=FUNCTIONAL_PHRASE_CATEGORY_NAMES
        ).count()
        subject_vocabulary_count = Phrase.objects.filter(
            is_active=True,
            tier=PhraseTier.SUBJECT,
            source_prompts__is_active=True,
            source_prompts__theme__is_active=True,
            source_prompts__theme__task=task,
        ).distinct().count()
        subject_vocabulary_prompt_count = Prompt.objects.filter(
            is_active=True,
            response__is_active=True,
            theme__is_active=True,
            theme__task=task,
            phrases__is_active=True,
            phrases__tier=PhraseTier.SUBJECT,
        ).distinct().count()
    else:
        response_stats = None
        phrase_stats = None
        functional_phrase_stats = None
        stats = None
        counts = None
        phrase_counts = None
        revisit_count = 0
        theme_count = 0
        prompt_count = 0
        phrase_count = 0
        functional_phrase_count = 0
        subject_vocabulary_count = 0
        subject_vocabulary_prompt_count = 0
    return {
        "task": task,
        "stats": stats,
        "response_stats": response_stats,
        "phrase_stats": phrase_stats,
        "functional_phrase_stats": functional_phrase_stats,
        "counts": counts,
        "phrase_counts": phrase_counts,
        "revisit_count": revisit_count,
        "theme_count": theme_count,
        "prompt_count": prompt_count,
        "phrase_count": phrase_count,
        "functional_phrase_count": functional_phrase_count,
        "subject_vocabulary_count": subject_vocabulary_count,
        "subject_vocabulary_prompt_count": subject_vocabulary_prompt_count,
        "question_bank": question_bank,
    }
