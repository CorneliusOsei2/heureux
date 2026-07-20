"""Compréhension écrite/orale test-taking views."""

from __future__ import annotations

import copy

from django.contrib.auth import (
    get_user_model,
)
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from ..models import (
    ComprehensionAnswer,
    ComprehensionAttempt,
    ComprehensionAttemptStatus,
    ComprehensionChoice,
    ComprehensionMode,
    ComprehensionQuestion,
    ComprehensionTest,
    Phrase,
    PhraseTier,
)
from ..progress import progress_summary

COMPREHENSION_GROUP_SIZE = 5


COMPREHENSION_GROUP_COUNTS = {
    ComprehensionMode.ECRITE: 8,
    ComprehensionMode.ORALE: 2,
}


COMPREHENSION_ROUTE_NAMES = {
    ComprehensionMode.ECRITE: {
        "overview": "study:comprehension_overview",
        "group": "study:comprehension_group",
        "test": "study:comprehension_test",
        "study": "study:comprehension_question_study",
        "start": "study:comprehension_start",
        "question": "study:comprehension_question",
        "results": "study:comprehension_results",
    },
    ComprehensionMode.ORALE: {
        "overview": "study:comprehension_oral_overview",
        "group": "study:comprehension_oral_group",
        "test": "study:comprehension_oral_test",
        "study": "study:comprehension_oral_question_study",
        "start": "study:comprehension_oral_start",
        "question": "study:comprehension_oral_question",
        "results": "study:comprehension_oral_results",
    },
}


def _comprehension_group_count(mode):
    return COMPREHENSION_GROUP_COUNTS.get(mode, 0)


def _prepare_comprehension_test(test):
    routes = COMPREHENSION_ROUTE_NAMES[test.mode]
    test.group_number = _comprehension_group_number(test.number)
    test.overview_route = routes["overview"]
    test.group_route = routes["group"]
    test.detail_route = routes["test"]
    test.study_route = routes["study"]
    test.start_route = routes["start"]
    test.question_route = routes["question"]
    test.results_route = routes["results"]
    test.mode_title = f"Compréhension {test.get_mode_display().lower()}"
    test.source_label = (
        "Document"
        if test.mode == ComprehensionMode.ECRITE
        else "Dialogue"
    )
    test.source_instruction = (
        "Lisez le texte"
        if test.mode == ComprehensionMode.ECRITE
        else "Lisez le dialogue"
    )
    return test


def _comprehension_test_cards(user, *, mode=None, published_only=False):
    attempts = (
        ComprehensionAttempt.objects.filter(user=user)
        .annotate(
            answer_total=Count("answers"),
        )
        .order_by("-started_at", "-pk")
    )
    tests = ComprehensionTest.objects.filter(
        Q(is_active=True) | Q(attempts__user=user)
    ).distinct()
    if mode:
        tests = tests.filter(mode=mode)
    if published_only:
        tests = tests.filter(is_active=True, is_published=True)
    tests = list(
        tests.annotate(
            active_question_count=Count(
                "questions",
                filter=Q(questions__is_active=True),
                distinct=True,
            )
        ).prefetch_related(
            Prefetch("attempts", queryset=attempts, to_attr="user_attempts")
        )
    )
    for test in tests:
        _prepare_comprehension_test(test)
        test.active_attempt = next(
            (
                attempt
                for attempt in test.user_attempts
                if attempt.status == ComprehensionAttemptStatus.IN_PROGRESS
            ),
            None,
        )
        test.completed_attempts = [
            attempt
            for attempt in test.user_attempts
            if attempt.status == ComprehensionAttemptStatus.COMPLETED
        ]
        test.latest_attempt = (
            test.completed_attempts[0] if test.completed_attempts else None
        )
        test.best_attempt = max(
            test.completed_attempts,
            key=lambda attempt: (attempt.percentage, attempt.score or 0),
            default=None,
        )
        test.active_answered_count = (
            test.active_attempt.answer_total if test.active_attempt else 0
        )
        test.attempt_question_count = (
            test.active_attempt.total_questions
            if test.active_attempt
            else test.active_question_count
        )
    return tests


def _comprehension_mode_summary(tests, *, group_count=0):
    available_tests = [
        test
        for test in tests
        if test.is_active and test.is_published
    ]
    active_test = next(
        (test for test in available_tests if test.active_attempt),
        None,
    )
    completed_attempts = [
        attempt
        for test in tests
        for attempt in test.completed_attempts
    ]
    next_test = active_test or next(
        (
            test
            for test in available_tests
            if not test.completed_attempts
        ),
        available_tests[0] if available_tests else None,
    )
    available_completed_count = sum(
        bool(test.completed_attempts) for test in available_tests
    )
    mode_progress = progress_summary(
        total=len(available_tests),
        started=available_completed_count + bool(active_test),
        completed=available_completed_count,
    )
    return {
        "group_count": group_count,
        "test_count": len(tests),
        "available_test_count": len(available_tests),
        "path_available": bool(tests),
        "completed_test_count": sum(
            bool(test.completed_attempts) for test in tests
        ),
        "progress": mode_progress,
        "active_attempt": (
            active_test.active_attempt if active_test else None
        ),
        "active_answered_count": (
            active_test.active_answered_count if active_test else 0
        ),
        "active_question_count": (
            active_test.attempt_question_count if active_test else 0
        ),
        "best_percentage": max(
            (attempt.percentage for attempt in completed_attempts),
            default=None,
        ),
        "next_test": next_test,
        "next_test_url": (
            reverse(next_test.detail_route, args=[next_test.slug])
            if next_test
            else ""
        ),
        "active_attempt_url": (
            reverse(
                active_test.question_route,
                args=[
                    active_test.slug,
                    active_test.active_attempt.pk,
                    active_test.active_attempt.current_question,
                ],
            )
            if active_test
            else ""
        ),
    }


def _comprehension_summary(user):
    tests = [
        test
        for test in _comprehension_test_cards(user)
        if (
            (test.is_active and test.is_published)
            or test.user_attempts
        )
    ]
    written_tests = [
        test for test in tests if test.mode == ComprehensionMode.ECRITE
    ]
    oral_tests = [
        test for test in tests if test.mode == ComprehensionMode.ORALE
    ]
    summary = _comprehension_mode_summary(
        tests,
        group_count=_comprehension_group_count(ComprehensionMode.ECRITE),
    )
    summary["ecrite"] = _comprehension_mode_summary(
        written_tests,
        group_count=_comprehension_group_count(ComprehensionMode.ECRITE),
    )
    summary["orale"] = _comprehension_mode_summary(
        oral_tests,
        group_count=_comprehension_group_count(ComprehensionMode.ORALE),
    )
    return summary


@require_GET
def comprehension_hub(request):
    return render(
        request,
        "study/comprehension_hub.html",
        {"comprehension": _comprehension_summary(request.user)},
    )


def _comprehension_group_number(test_number):
    return ((test_number - 1) // COMPREHENSION_GROUP_SIZE) + 1


def _comprehension_groups(tests, group_count):
    groups = []
    for number in range(1, group_count + 1):
        start = ((number - 1) * COMPREHENSION_GROUP_SIZE) + 1
        end = start + COMPREHENSION_GROUP_SIZE - 1
        group_tests = [
            test
            for test in tests
            if start <= test.number <= end
        ]
        published = [
            test
            for test in group_tests
            if test.is_active and test.is_published
        ]
        published_completed_count = sum(
            bool(test.completed_attempts) for test in published
        )
        active_attempt = next(
            (
                test.active_attempt
                for test in published
                if test.active_attempt
            ),
            None,
        )
        groups.append(
            {
                "number": number,
                "start": start,
                "end": end,
                "tests": group_tests,
                "available_count": len(published),
                "completed_count": sum(
                    bool(test.completed_attempts) for test in group_tests
                ),
                "active_attempt": active_attempt,
                "progress": progress_summary(
                    total=len(published),
                    started=published_completed_count + bool(active_attempt),
                    completed=published_completed_count,
                ),
            }
        )
    return groups


def _comprehension_question_snapshot(
    question,
    selected_choice=None,
    *,
    choices=None,
):
    choices = choices if choices is not None else question.choices.all()
    choices = [
        {
            "id": choice.pk,
            "letter": choice.letter,
            "text_fr": choice.text_fr,
            "text_en": choice.text_en,
            "rationale": choice.rationale,
            "is_correct": choice.is_correct,
        }
        for choice in choices
    ]
    return {
        "id": question.pk,
        "content_key": question.content_key,
        "number": question.number,
        "passage_fr": question.passage_fr,
        "passage_en": question.passage_en,
        "prompt_fr": question.prompt_fr,
        "prompt_en": question.prompt_en,
        "correct_explanation": question.correct_explanation,
        "choices": choices,
        "selected_letter": selected_choice.letter if selected_choice else "",
    }


def _build_comprehension_test_snapshot(test):
    questions = (
        test.questions.filter(is_active=True)
        .prefetch_related(
            Prefetch(
                "choices",
                queryset=ComprehensionChoice.objects.filter(is_active=True),
            )
        )
        .order_by("number")
    )
    return {
        "questions": [
            _comprehension_question_snapshot(question)
            for question in questions
        ]
    }


def _comprehension_attempt_questions(attempt):
    snapshot = attempt.content_snapshot
    if isinstance(snapshot, dict):
        questions = snapshot.get("questions")
        if (
            isinstance(questions, list)
            and questions
            and all(
                isinstance(question, dict)
                and question.get("id")
                and question.get("choices")
                for question in questions
            )
        ):
            return questions

    answers = {
        answer.question_id: answer
        for answer in attempt.answers.select_related("selected_choice")
    }
    questions = (
        attempt.test.questions.filter(
            Q(is_active=True) | Q(pk__in=answers)
        )
        .prefetch_related("choices")
        .order_by("number")
    )
    serialized = []
    for question in questions:
        answer = answers.get(question.pk)
        if answer and answer.question_snapshot:
            question_data = copy.deepcopy(answer.question_snapshot)
            question_data.setdefault("id", question.pk)
            question_data.setdefault("content_key", question.content_key)
            for choice_data, choice in zip(
                question_data.get("choices", []),
                question.choices.all(),
            ):
                choice_data.setdefault("id", choice.pk)
        else:
            available_choices = [
                choice
                for choice in question.choices.all()
                if answer or choice.is_active
            ]
            question_data = _comprehension_question_snapshot(
                question,
                answer.selected_choice if answer else None,
                choices=available_choices,
            )
        serialized.append(question_data)

    attempt.content_snapshot = {"questions": serialized}
    attempt.total_questions = len(serialized)
    attempt.save(update_fields=["content_snapshot", "total_questions"])
    return serialized


def _snapshot_with_selected_choice(question, selected_letter):
    snapshot = copy.deepcopy(question)
    snapshot["selected_letter"] = selected_letter
    return snapshot


def _comprehension_answer_snapshot(answer):
    snapshot = answer.question_snapshot
    if (
        isinstance(snapshot, dict)
        and snapshot.get("choices")
        and snapshot.get("number")
    ):
        return snapshot
    for question in _comprehension_attempt_questions(answer.attempt):
        if question["id"] == answer.question_id:
            return _snapshot_with_selected_choice(
                question,
                answer.selected_choice.letter,
            )
    return _comprehension_question_snapshot(
        answer.question,
        answer.selected_choice,
    )


def _comprehension_overview_response(request, *, mode, template):
    tests = _comprehension_test_cards(request.user, mode=mode)
    published = [
        test
        for test in tests
        if test.is_active and test.is_published
    ]
    completed_attempts = [
        attempt
        for test in published
        for attempt in test.completed_attempts
    ]
    return render(
        request,
        template,
        {
            "groups": _comprehension_groups(
                tests,
                _comprehension_group_count(mode),
            ),
            "published_count": len(published),
            "completed_count": sum(
                bool(test.completed_attempts) for test in published
            ),
            "best_percentage": max(
                (attempt.percentage for attempt in completed_attempts),
                default=None,
            ),
        },
    )


@require_GET
def comprehension_overview(request):
    return _comprehension_overview_response(
        request,
        mode=ComprehensionMode.ECRITE,
        template="study/comprehension_overview.html",
    )


@require_GET
def comprehension_oral_overview(request):
    return _comprehension_overview_response(
        request,
        mode=ComprehensionMode.ORALE,
        template="study/comprehension_oral_overview.html",
    )


def _comprehension_group_detail_response(request, *, mode, group_number):
    group_count = _comprehension_group_count(mode)
    if not 1 <= group_number <= group_count:
        raise Http404

    tests = _comprehension_test_cards(request.user, mode=mode)
    group = _comprehension_groups(tests, group_count)[group_number - 1]
    tests_by_number = {
        test.number: test
        for test in group["tests"]
    }
    group["slots"] = [
        {
            "number": number,
            "test": tests_by_number.get(number),
        }
        for number in range(group["start"], group["end"] + 1)
    ]
    routes = COMPREHENSION_ROUTE_NAMES[mode]
    return render(
        request,
        "study/comprehension_group.html",
        {
            "group": group,
            "overview_route": routes["overview"],
            "mode_label": f"Compréhension {mode.label.lower()}",
        },
    )


@require_GET
def comprehension_group_detail(request, group_number):
    return _comprehension_group_detail_response(
        request,
        mode=ComprehensionMode.ECRITE,
        group_number=group_number,
    )


@require_GET
def comprehension_oral_group_detail(request, group_number):
    return _comprehension_group_detail_response(
        request,
        mode=ComprehensionMode.ORALE,
        group_number=group_number,
    )


@require_GET
def comprehension_test_detail(
    request,
    test_slug,
    mode=ComprehensionMode.ECRITE,
):
    test = next(
        (
            item
            for item in _comprehension_test_cards(request.user, mode=mode)
            if item.slug == test_slug
            and (
                (item.is_active and item.is_published)
                or item.user_attempts
            )
        ),
        None,
    )
    if test is None:
        raise Http404
    questions = []
    if test.is_active and test.is_published:
        question_qs = (
            test.questions.filter(is_active=True)
            .prefetch_related(
                Prefetch(
                    "choices",
                    queryset=ComprehensionChoice.objects.filter(
                        is_active=True,
                    ),
                )
            )
            .order_by("number")
        )
        progress_attempt = test.active_attempt or test.latest_attempt
        answers = (
            {
                answer.question_id: answer
                for answer in progress_attempt.answers.all()
            }
            if progress_attempt
            else {}
        )
        questions = [
            {
                "question": question,
                "answer": answers.get(question.pk),
            }
            for question in question_qs
        ]
    return render(
        request,
        "study/comprehension_test.html",
        {
            "test": test,
            "questions": questions,
            "attempt_history": test.completed_attempts[:6],
        },
    )


@require_GET
def comprehension_question_study(
    request,
    test_slug,
    number,
    mode=ComprehensionMode.ECRITE,
):
    test = next(
        (
            item
            for item in _comprehension_test_cards(
                request.user,
                mode=mode,
                published_only=True,
            )
            if item.slug == test_slug
        ),
        None,
    )
    if test is None:
        raise Http404

    questions = list(
        test.questions.filter(is_active=True)
        .prefetch_related(
            Prefetch(
                "choices",
                queryset=ComprehensionChoice.objects.filter(
                    is_active=True,
                ),
            )
        )
        .order_by("number")
    )
    try:
        position = next(
            index
            for index, question in enumerate(questions)
            if question.number == number
        )
    except StopIteration as error:
        raise Http404 from error

    question = questions[position]
    choices = list(question.choices.all())
    correct_choice = next(
        (choice for choice in choices if choice.is_correct),
        None,
    )
    if correct_choice is None:
        raise Http404

    return render(
        request,
        "study/comprehension_question_study.html",
        {
            "test": test,
            "question": question,
            "choices": choices,
            "correct_choice": correct_choice,
            "position": position + 1,
            "total_questions": len(questions),
            "previous_question": (
                questions[position - 1] if position > 0 else None
            ),
            "next_question": (
                questions[position + 1]
                if position + 1 < len(questions)
                else None
            ),
        },
    )


def _comprehension_question_url(attempt, number):
    _prepare_comprehension_test(attempt.test)
    return reverse(
        attempt.test.question_route,
        args=[attempt.test.slug, attempt.pk, number],
    )


def _comprehension_test_url(test):
    _prepare_comprehension_test(test)
    return reverse(test.detail_route, args=[test.slug])


def _comprehension_overview_url(test):
    _prepare_comprehension_test(test)
    return reverse(test.overview_route)


def _comprehension_results_url(attempt):
    _prepare_comprehension_test(attempt.test)
    return reverse(
        attempt.test.results_route,
        args=[attempt.test.slug, attempt.pk],
    )


def _sync_comprehension_attempt_position(attempt):
    if attempt.status != ComprehensionAttemptStatus.IN_PROGRESS:
        return None
    questions = _comprehension_attempt_questions(attempt)
    answered_question_ids = set(
        attempt.answers.values_list("question_id", flat=True)
    )
    next_question = next(
        (
            question
            for question in questions
            if question["id"] not in answered_question_ids
        ),
        None,
    )
    if next_question:
        if attempt.current_question != next_question["number"]:
            attempt.current_question = next_question["number"]
            attempt.save(update_fields=["current_question", "updated_at"])
        return next_question["number"]

    attempt.status = ComprehensionAttemptStatus.COMPLETED
    attempt.score = attempt.answers.filter(is_correct=True).count()
    attempt.total_questions = len(questions)
    attempt.completed_at = timezone.now()
    attempt.save(
        update_fields=[
            "status",
            "score",
            "total_questions",
            "completed_at",
            "updated_at",
        ]
    )
    return None


@require_POST
def comprehension_start(
    request,
    test_slug,
    mode=ComprehensionMode.ECRITE,
):
    action = request.POST.get("action", "continue")
    if action not in {"continue", "restart", "errors"}:
        return HttpResponseBadRequest("Action de test invalide.")

    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=request.user.pk)
        test = get_object_or_404(
            ComprehensionTest.objects.select_for_update(),
            slug=test_slug,
            mode=mode,
            is_active=True,
            is_published=True,
        )
        _prepare_comprehension_test(test)
        active_attempt = (
            ComprehensionAttempt.objects.select_for_update()
            .filter(
                user=request.user,
                test=test,
                status=ComprehensionAttemptStatus.IN_PROGRESS,
            )
            .first()
        )
        focused_snapshot = None
        if action == "errors" and active_attempt is None:
            source_attempt_id = request.POST.get("attempt_id", "")
            if not source_attempt_id.isdecimal():
                return HttpResponseBadRequest(
                    "Tentative source invalide."
                )
            source_attempt = get_object_or_404(
                ComprehensionAttempt.objects.select_for_update(),
                pk=source_attempt_id,
                user=request.user,
                test=test,
                status=ComprehensionAttemptStatus.COMPLETED,
            )
            wrong_question_ids = set(
                source_attempt.answers.filter(
                    is_correct=False,
                ).values_list("question_id", flat=True)
            )
            focused_questions = [
                copy.deepcopy(question)
                for question in _comprehension_attempt_questions(
                    source_attempt
                )
                if question["id"] in wrong_question_ids
            ]
            if not focused_questions:
                return redirect(_comprehension_results_url(source_attempt))
            focused_snapshot = {
                "practice_mode": "errors",
                "source_attempt_id": source_attempt.pk,
                "questions": focused_questions,
            }
        if active_attempt and action == "restart":
            active_attempt.status = ComprehensionAttemptStatus.ABANDONED
            active_attempt.completed_at = timezone.now()
            active_attempt.save(update_fields=["status", "completed_at", "updated_at"])
            active_attempt = None

        if active_attempt is None:
            content_snapshot = (
                focused_snapshot
                if focused_snapshot is not None
                else _build_comprehension_test_snapshot(test)
            )
            questions = content_snapshot["questions"]
            if not questions:
                return HttpResponseBadRequest("Ce test ne contient aucune question.")
            active_attempt = ComprehensionAttempt.objects.create(
                user=request.user,
                test=test,
                current_question=questions[0]["number"],
                total_questions=len(questions),
                content_snapshot=content_snapshot,
            )
        resume_number = _sync_comprehension_attempt_position(active_attempt)

    if resume_number is None:
        return redirect(_comprehension_results_url(active_attempt))
    return redirect(
        _comprehension_question_url(
            active_attempt,
            resume_number,
        )
    )


def _comprehension_attempt(request, test_slug, attempt_id, mode):
    attempt = get_object_or_404(
        ComprehensionAttempt.objects.select_related("test"),
        pk=attempt_id,
        user=request.user,
        test__slug=test_slug,
        test__mode=mode,
    )
    _prepare_comprehension_test(attempt.test)
    return attempt


def _comprehension_question_context(attempt, question_number, error=""):
    questions = _comprehension_attempt_questions(attempt)
    if not questions:
        return None
    question_index = next(
        (
            index
            for index, question in enumerate(questions)
            if question["number"] == question_number
        ),
        None,
    )
    if question_index is None:
        return None

    answers = {
        answer.question_id: answer
        for answer in attempt.answers.select_related(
            "question",
            "selected_choice",
        )
    }
    question = questions[question_index]
    answer = answers.get(question["id"])

    question_model = ComprehensionQuestion.objects.filter(
        pk=question["id"],
        test=attempt.test,
    ).first()
    if question_model is None:
        return None
    display_question = question
    display_choices = question["choices"]
    selected_choice = answer.selected_choice if answer else None
    selected_letter = selected_choice.letter if selected_choice else ""
    if answer and answer.question_snapshot:
        display_question = _comprehension_answer_snapshot(answer)
        display_choices = display_question["choices"]
        selected_letter = display_question.get("selected_letter") or selected_letter
        selected_choice = next(
            (
                choice
                for choice in display_choices
                if choice["letter"] == selected_letter
            ),
            None,
        )
    correct_choice = next(
        (
            choice
            for choice in display_choices
            if choice["is_correct"]
        ),
        None,
    )
    navigator = [
        {
            "number": item["number"],
            "is_answered": item["id"] in answers,
            "is_current": item["id"] == question["id"],
        }
        for item in questions
    ]
    return {
        "attempt": attempt,
        "test": attempt.test,
        "group_number": attempt.test.group_number,
        "question": display_question,
        "question_model": question_model,
        "choices": display_choices,
        "answer": answer,
        "selected_choice": selected_choice,
        "selected_letter": selected_letter,
        "correct_choice": correct_choice,
        "answered_count": len(answers),
        "total_questions": len(questions),
        "position": question_index + 1,
        "previous_number": (
            questions[question_index - 1]["number"] if question_index else None
        ),
        "next_number": (
            questions[question_index + 1]["number"]
            if question_index + 1 < len(questions)
            else None
        ),
        "navigator": navigator,
        "answer_error": error,
    }


@require_http_methods(["GET", "POST"])
def comprehension_question(
    request,
    test_slug,
    attempt_id,
    number,
    mode=ComprehensionMode.ECRITE,
):
    attempt = _comprehension_attempt(
        request,
        test_slug,
        attempt_id,
        mode,
    )
    if (
        not attempt.test.is_active
        or not attempt.test.is_published
    ):
        return redirect(_comprehension_overview_url(attempt.test))
    if (
        attempt.status == ComprehensionAttemptStatus.COMPLETED
        and not (
            request.method == "GET"
            and request.GET.get("correction") == "1"
        )
    ):
        return redirect(_comprehension_results_url(attempt))
    if attempt.status == ComprehensionAttemptStatus.ABANDONED:
        return redirect(_comprehension_test_url(attempt.test))

    context = _comprehension_question_context(attempt, number)
    if context is None:
        with transaction.atomic():
            locked_attempt = get_object_or_404(
                ComprehensionAttempt.objects.select_for_update().select_related(
                    "test"
                ),
                pk=attempt.pk,
                user=request.user,
            )
            resume_number = _sync_comprehension_attempt_position(locked_attempt)
        if resume_number is None:
            return redirect(_comprehension_results_url(attempt))
        return redirect(
            _comprehension_question_url(locked_attempt, resume_number)
        )
    if request.method != "POST":
        return render(request, "study/comprehension_question.html", context)

    if context["answer"] is not None:
        return redirect(_comprehension_question_url(attempt, number))
    selected_choice_id = request.POST.get("choice")
    selected_choice_data = next(
        (
            choice
            for choice in context["choices"]
            if str(choice["id"]) == selected_choice_id
        ),
        None,
    )
    selected_choice = (
        ComprehensionChoice.objects.filter(
            pk=selected_choice_id,
            question=context["question_model"],
        ).first()
        if (
            selected_choice_data
            and selected_choice_id
            and selected_choice_id.isdecimal()
        )
        else None
    )
    if selected_choice is None:
        context["answer_error"] = "Choisissez une réponse."
        return render(
            request,
            "study/comprehension_question.html",
            context,
            status=400,
        )

    with transaction.atomic():
        locked_attempt = get_object_or_404(
            ComprehensionAttempt.objects.select_for_update(),
            pk=attempt.pk,
            user=request.user,
        )
        if locked_attempt.status == ComprehensionAttemptStatus.COMPLETED:
            return redirect(
                f"{_comprehension_question_url(locked_attempt, number)}"
                "?correction=1"
            )
        if locked_attempt.status != ComprehensionAttemptStatus.IN_PROGRESS:
            return redirect(_comprehension_test_url(attempt.test))
        ComprehensionAnswer.objects.get_or_create(
            attempt=locked_attempt,
            question=context["question_model"],
            defaults={
                "selected_choice": selected_choice,
                "is_correct": selected_choice_data["is_correct"],
                "question_snapshot": _snapshot_with_selected_choice(
                    context["question"],
                    selected_choice_data["letter"],
                ),
            },
        )
        next_question_number = _sync_comprehension_attempt_position(locked_attempt)
        if next_question_number is None:
            return redirect(
                f"{_comprehension_question_url(locked_attempt, number)}"
                "?correction=1"
            )

    return redirect(_comprehension_question_url(attempt, number))


@require_GET
def comprehension_results(
    request,
    test_slug,
    attempt_id,
    mode=ComprehensionMode.ECRITE,
):
    attempt = _comprehension_attempt(
        request,
        test_slug,
        attempt_id,
        mode,
    )
    if attempt.status == ComprehensionAttemptStatus.IN_PROGRESS:
        return redirect(
            _comprehension_question_url(attempt, attempt.current_question)
        )
    if attempt.status != ComprehensionAttemptStatus.COMPLETED:
        return redirect(_comprehension_test_url(attempt.test))

    submitted_answers = list(
        attempt.answers.select_related(
            "question",
            "selected_choice",
        )
        .prefetch_related("question__choices")
        .order_by("question__number")
    )
    review_items = []
    for answer in submitted_answers:
        question = _comprehension_answer_snapshot(answer)
        choices = question["choices"]
        review_items.append(
            {
                "question": question,
                "choices": choices,
                "answer": answer,
                "selected_letter": (
                    question.get("selected_letter")
                    or answer.selected_choice.letter
                ),
                "correct_choice": next(
                    (choice for choice in choices if choice["is_correct"]),
                    None,
                ),
            }
        )
    return render(
        request,
        "study/comprehension_results.html",
        {
            "attempt": attempt,
            "test": attempt.test,
            "group_number": attempt.test.group_number,
            "review_items": review_items,
            "wrong_count": attempt.total_questions - (attempt.score or 0),
            "is_error_practice": (
                isinstance(attempt.content_snapshot, dict)
                and attempt.content_snapshot.get("practice_mode") == "errors"
            ),
            "has_vocabulary": Phrase.objects.filter(
                is_active=True,
                tier=PhraseTier.COMPREHENSION,
                source_questions__test=attempt.test,
            ).exists(),
        },
    )
