"""Test helpers: build a minimal but complete content + study graph."""

from __future__ import annotations

from django.contrib.auth import get_user_model

from study.models import (
    Argument,
    Card,
    CardType,
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


def make_part(slug="orale", available=True) -> ExamPart:
    part, _ = ExamPart.objects.get_or_create(
        slug=slug,
        defaults={
            "name": f"Expression {slug}",
            "short_name": slug.title(),
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


def make_response(theme=None, family=None) -> Response:
    theme = theme or make_theme()
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
