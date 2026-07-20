"""Test helpers: build a minimal but complete content + study graph."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.utils import timezone

from study.models import (
    Argument,
    Card,
    CardType,
    ComprehensionAnswer,
    ComprehensionAttempt,
    ComprehensionAttemptStatus,
    ComprehensionChoice,
    ComprehensionMode,
    ComprehensionQuestion,
    ComprehensionTest,
    ExamPart,
    Family,
    Phrase,
    PhraseCategory,
    Prompt,
    Response,
    Task,
    Theme,
)

_seq = {"n": 0}


def _uid() -> int:
    _seq["n"] += 1
    return _seq["n"]


def make_user(username=None, pin="123456"):
    username = username or f"user{_uid()}"
    return get_user_model().objects.create_user(
        username=username,
        password=pin,
    )


def _default_user():
    users = list(get_user_model().objects.order_by("pk")[:2])
    return users[0] if len(users) == 1 else None


def make_theme(slug="culture", order=1, task=None) -> Theme:
    theme, _ = Theme.objects.get_or_create(
        slug=slug,
        defaults={
            "name": slug.title(),
            "display_name": slug.title(),
            "order": order,
            "task": task,
        },
    )
    if task and theme.task_id != task.id:
        theme.task = task
        theme.save(update_fields=["task"])
    return theme


def make_family(slug="famille-1") -> Family:
    family, _ = Family.objects.get_or_create(
        slug=slug,
        defaults={
            "name": f"Family {slug}",
            "content_key": f"test-family:{slug}",
            "order": _uid(),
        },
    )
    return family


def make_part(slug="eo", available=True) -> ExamPart:
    names = {
        "eo": ("Expression orale", "EO"),
        "ee": ("Expression écrite", "EE"),
    }
    name, short_name = names.get(
        slug,
        (f"Expression {slug}", slug.title()),
    )
    part, _ = ExamPart.objects.get_or_create(
        slug=slug,
        defaults={
            "name": name,
            "short_name": short_name,
            "available": available,
            "order": _uid(),
        },
    )
    return part


def make_task(part=None, slug="tache-3", available=True) -> Task:
    part = part or make_part()
    task, _ = Task.objects.get_or_create(
        part=part,
        slug=slug,
        defaults={"name": slug.replace("-", " ").title(), "available": available},
    )
    return task


def make_comprehension_test(
    *,
    number=1,
    question_count=3,
    first_question_number=1,
    mode=ComprehensionMode.ECRITE,
    is_published=True,
) -> ComprehensionTest:
    slug = (
        f"test-{number}"
        if mode == ComprehensionMode.ECRITE
        else f"oral-test-{number}"
    )
    content_prefix = "ce" if mode == ComprehensionMode.ECRITE else "co"
    test = ComprehensionTest.objects.create(
        slug=slug,
        mode=mode,
        number=number,
        title=f"Test {number}",
        description=f"Test de compréhension {number}",
        expected_question_count=question_count,
        order=number,
        is_published=is_published,
    )
    for question_number in range(
        first_question_number,
        first_question_number + question_count,
    ):
        question = ComprehensionQuestion.objects.create(
            test=test,
            content_key=(
                f"{content_prefix}:{slug}:q{question_number:02d}"
            ),
            number=question_number,
            passage_fr=f"Passage français {question_number}.",
            passage_en=f"English passage {question_number}.",
            prompt_fr=f"Question française {question_number} ?",
            prompt_en=f"English question {question_number}?",
            correct_explanation=f"Correct explanation {question_number}.",
        )
        for letter in "ABCD":
            ComprehensionChoice.objects.create(
                question=question,
                letter=letter,
                text_fr=f"Choix {letter} français {question_number}",
                text_en=f"English choice {letter} {question_number}",
                rationale=(
                    ""
                    if letter == "A"
                    else f"Rationale for {letter} on question {question_number}."
                ),
                is_correct=(letter == "A"),
            )
    return test


def make_comprehension_attempt(
    *,
    user,
    test,
    status=ComprehensionAttemptStatus.IN_PROGRESS,
    answered_questions=0,
) -> ComprehensionAttempt:
    questions = list(test.questions.prefetch_related("choices").order_by("number"))
    attempt = ComprehensionAttempt.objects.create(
        user=user,
        test=test,
        status=status,
        current_question=questions[
            min(answered_questions, len(questions) - 1)
        ].number,
        total_questions=len(questions),
    )
    for question in questions[:answered_questions]:
        choice = question.choices.get(letter="A")
        ComprehensionAnswer.objects.create(
            attempt=attempt,
            question=question,
            selected_choice=choice,
            is_correct=True,
        )
    if status == ComprehensionAttemptStatus.COMPLETED:
        attempt.score = attempt.answers.filter(is_correct=True).count()
        attempt.completed_at = timezone.now()
        attempt.save(update_fields=["score", "completed_at"])
    return attempt


def make_response(theme=None, family=None) -> Response:
    theme = theme or make_theme(task=make_task())
    family = family or make_family()
    n = _uid()
    response = Response.objects.create(
        content_key=f"test-response:{n}",
        body_hash=f"hash{n:028d}",
        theme=theme,
        family=family,
        prompt=f"Prompt canonique {n} ?",
        body=f"Corps de la réponse {n}.",
        body_html=f"<p>Corps de la réponse {n}.</p>",
    )
    Prompt.objects.create(
        content_key=f"test-prompt:{n}",
        response=response,
        theme=theme,
        family=family,
        number=n,
        text=f"Prompt canonique {n} ?",
        is_canonical=True,
    )
    Argument.objects.create(
        response=response, order=1, idea=f"Idée {n}", exemple=f"Exemple {n}"
    )
    return response


def make_spine_card(**overrides) -> Card:
    theme = overrides.pop("theme", None)
    family = overrides.pop("family", None)
    user = overrides.pop("user", _default_user())
    response = make_response(theme=theme, family=family)
    return Card.objects.create(
        user=user,
        card_type=CardType.SPINE,
        response=response,
        **overrides,
    )


def make_phrase(category=None, **overrides) -> Phrase:
    if category is None:
        category, _ = PhraseCategory.objects.get_or_create(
            slug="nuancer",
            defaults={
                "name": "Nuancer",
                "content_key": "test-category:nuancer",
                "order": _uid(),
            },
        )
    n = _uid()
    return Phrase.objects.create(
        phrase_id=f"p{n}",
        tier=overrides.pop("tier", "shared"),
        category=category,
        english_cue=f"cue {n}",
        expression=f"expression {n}",
        anchor=f"expression {n}",
        example=f"Voici une expression {n} en contexte.",
        order=n,
        lot_order=overrides.pop("lot_order", n),
        **overrides,
    )


def make_phrase_card(card_type=CardType.PHRASE_PRODUCTION, **overrides) -> Card:
    phrase = overrides.pop("phrase", None) or make_phrase()
    user = overrides.pop("user", _default_user())
    return Card.objects.create(
        user=user,
        card_type=card_type,
        phrase=phrase,
        **overrides,
    )


def make_content():
    """A minimal end-to-end graph for view smoke tests."""
    part = make_part()
    task = make_task(part=part)
    theme = make_theme(task=task)
    family = make_family()
    response = make_response(theme=theme, family=family)
    make_spine_card(theme=theme, family=family)
    category, _ = PhraseCategory.objects.get_or_create(
        slug="nuancer",
        defaults={
            "name": "Nuancer",
            "content_key": "test-category:nuancer",
            "order": 1,
        },
    )
    phrase = make_phrase(category=category)
    phrase.source_prompts.add(response.prompts.first())
    make_phrase_card(phrase=phrase)
    return {"part": part, "task": task, "theme": theme, "family": family}
