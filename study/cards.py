"""Presentation helpers: turn a Card into front/back display data.

Kept separate from views so the same payload feeds the review screen and the
card-detail pages.
"""

from __future__ import annotations

from .models import Card, CardType, PhraseTier
from .personalization import effective_response


def scope_from_request(request) -> dict:
    """Parse and whitelist deck-scope parameters from a request."""
    data = request.POST if request.method == "POST" else request.GET
    scope = {}
    kind = data.get("kind")
    if kind in {"spine", "phrase", "vocab", "revisit", "weak"}:
        scope["kind"] = kind
    content = data.get("content")
    if content in {"spine", "vocabulary"}:
        scope["content"] = content
    for key in ("part", "task", "theme", "family", "category", "test"):
        value = (data.get(key) or "").strip()
        if value:
            scope[key] = value
    batch = (data.get("batch") or "").strip()
    if batch.isdigit() and int(batch) > 0:
        scope["batch"] = batch
    response_id = (data.get("response") or "").strip()
    if response_id.isdigit():
        scope["response"] = response_id
    return scope


def scope_label(scope: dict) -> str:
    """Human label for the current study scope."""
    from .models import (
        ComprehensionTest,
        ExamPart,
        Family,
        PhraseCategory,
        Response,
        Task,
        Theme,
    )

    def with_batch(label: str) -> str:
        if scope.get("batch"):
            return f"{label} · Lot {scope['batch']}"
        return label

    if not scope:
        return "Toutes les cartes"
    if scope.get("theme"):
        theme = Theme.objects.filter(
            slug=scope["theme"],
            is_active=True,
        ).first()
        if theme:
            return with_batch(f"Thème · {theme.display_name}")
    if scope.get("category"):
        category = PhraseCategory.objects.filter(
            slug=scope["category"],
            is_active=True,
        ).first()
        if category:
            return with_batch(f"Expressions · {category.name}")
    if scope.get("test"):
        test = ComprehensionTest.objects.filter(
            slug=scope["test"],
            is_active=True,
        ).first()
        if test:
            return with_batch(f"Vocabulaire · {test.title}")
    if scope.get("response"):
        response = Response.objects.select_related("theme").filter(
            pk=scope["response"],
            is_active=True,
        ).first()
        if response:
            deck_name = (
                "Vocabulaire"
                if scope.get("kind") == "vocab"
                else "Expressions"
            )
            return with_batch(
                f"{deck_name} · {response.theme.display_name} "
                f"· P{response.canonical_prompt.number}"
            )
    if scope.get("task"):
        tasks = Task.objects.filter(
            slug=scope["task"],
            is_active=True,
            part__is_active=True,
        ).select_related("part")
        if scope.get("part"):
            tasks = tasks.filter(part__slug=scope["part"])
        task = tasks.first()
        if task:
            return with_batch(f"{task.part.short_name} · {task.name}")
    if scope.get("part"):
        part = ExamPart.objects.filter(
            slug=scope["part"],
            is_active=True,
        ).first()
        if part:
            return with_batch(part.name)
    if scope.get("family"):
        family = Family.objects.filter(
            slug=scope["family"],
            is_active=True,
        ).first()
        if family:
            return with_batch(f"Famille · {family.name}")
    if scope.get("kind") == "spine":
        return with_batch("Réponses argumentées")
    if scope.get("kind") == "phrase":
        return with_batch("Expressions")
    if scope.get("kind") == "vocab":
        return with_batch("Vocabulaire des sujets")
    if scope.get("kind") == "revisit":
        return with_batch("Liste à revoir")
    if scope.get("kind") == "weak":
        return with_batch("Points à renforcer")
    if scope.get("content") == "spine":
        return with_batch("Réponses argumentées")
    if scope.get("content") == "vocabulary":
        return with_batch("Vocabulaire")
    return with_batch("Sélection")


def card_payload(card: Card) -> dict:
    """Everything the front/back templates need for a single card."""
    if card.card_type == CardType.SPINE:
        return _spine_payload(card)
    return _phrase_payload(card)


def _spine_payload(card: Card) -> dict:
    response = card.response
    content = effective_response(response, card.user)
    canonical = response.canonical_prompt
    aliases = [
        prompt
        for prompt in response.prompts.filter(is_active=True)
        if not prompt.is_canonical
    ]
    return {
        "card": card,
        "kind": "spine",
        "kind_label": "Réponse argumentée",
        "theme": response.theme,
        "family": response.family,
        "prompt": canonical.text if canonical else response.prompt,
        "canonical_prompt": canonical,
        "aliases": aliases,
        "response": response,
        "response_content": content,
        "arguments": content.arguments,
        "annotation_source_key": f"response:{response.content_key}",
    }


def _phrase_payload(card: Card) -> dict:
    phrase = card.phrase
    production = card.card_type == CardType.PHRASE_PRODUCTION
    subject_vocabulary = phrase.tier == PhraseTier.SUBJECT
    comprehension_vocabulary = phrase.tier == PhraseTier.COMPREHENSION
    return {
        "card": card,
        "kind": "phrase",
        "production": production,
        "subject_vocabulary": subject_vocabulary,
        "comprehension_vocabulary": comprehension_vocabulary,
        "kind_label": (
            "Vocabulaire du sujet"
            if subject_vocabulary
            else (
                "Vocabulaire de compréhension"
                if comprehension_vocabulary
                else (
                    "Expression · production"
                    if production
                    else "Expression · sens"
                )
            )
        ),
        "phrase": phrase,
        "category": phrase.category,
        "example_html": phrase.example_html,
        "cloze_example": phrase.cloze_example,
        "sources": list(
            phrase.source_prompts.filter(is_active=True).select_related("theme")
        ),
        "question_sources": list(
            phrase.source_questions.filter(
                is_active=True,
                test__is_active=True,
            ).select_related("test")
        ),
        "annotation_source_key": (
            f"phrase:{phrase.phrase_id}:{card.card_type}"
        ),
    }
