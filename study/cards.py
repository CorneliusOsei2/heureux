"""Presentation helpers: turn a Card into front/back display data.

Kept separate from views so the same payload feeds the review screen and the
card-detail pages.
"""

from __future__ import annotations

import html
import re
from typing import Optional

from .models import Card, CardType


def _highlight(example: str, anchor: str) -> str:
    escaped = html.escape(example)
    if not anchor:
        return escaped
    match = re.search(re.escape(html.escape(anchor)), escaped, flags=re.IGNORECASE)
    if not match:
        return escaped
    start, end = match.span()
    return f"{escaped[:start]}<mark>{escaped[start:end]}</mark>{escaped[end:]}"


def scope_from_request(request) -> dict:
    """Parse and whitelist deck-scope parameters from a request."""
    data = request.POST if request.method == "POST" else request.GET
    scope = {}
    kind = data.get("kind")
    if kind in {"spine", "phrase"}:
        scope["kind"] = kind
    for key in ("task", "theme", "family", "category"):
        value = (data.get(key) or "").strip()
        if value:
            scope[key] = value
    return scope


def scope_label(scope: dict) -> str:
    """Human label for the current study scope."""
    from .models import Family, PhraseCategory, Task, Theme

    if not scope:
        return "Toutes les cartes"
    if scope.get("theme"):
        theme = Theme.objects.filter(slug=scope["theme"]).first()
        if theme:
            return f"Thème · {theme.display_name}"
    if scope.get("task"):
        task = Task.objects.filter(slug=scope["task"]).select_related("part").first()
        if task:
            return f"{task.part.short_name} · {task.name}"
    if scope.get("family"):
        family = Family.objects.filter(slug=scope["family"]).first()
        if family:
            return f"Famille · {family.name}"
    if scope.get("category"):
        category = PhraseCategory.objects.filter(slug=scope["category"]).first()
        if category:
            return f"Expressions · {category.name}"
    if scope.get("kind") == "spine":
        return "Réponses argumentées"
    if scope.get("kind") == "phrase":
        return "Expressions"
    return "Sélection"


def card_payload(card: Card) -> dict:
    """Everything the front/back templates need for a single card."""
    if card.card_type == CardType.SPINE:
        return _spine_payload(card)
    return _phrase_payload(card)


def _spine_payload(card: Card) -> dict:
    response = card.response
    canonical = response.canonical_prompt
    aliases = [p for p in response.prompts.all() if not p.is_canonical]
    return {
        "card": card,
        "kind": "spine",
        "kind_label": "Réponse argumentée",
        "theme": response.theme,
        "family": response.family,
        "prompt": canonical.text if canonical else response.prompt,
        "aliases": aliases,
        "response": response,
        "arguments": list(response.arguments.all()),
    }


def _phrase_payload(card: Card) -> dict:
    phrase = card.phrase
    production = card.card_type == CardType.PHRASE_PRODUCTION
    return {
        "card": card,
        "kind": "phrase",
        "production": production,
        "kind_label": (
            "Expression · production" if production else "Expression · sens"
        ),
        "phrase": phrase,
        "category": phrase.category,
        "example_html": _highlight(phrase.example, phrase.anchor),
        "cloze_example": phrase.cloze_example,
        "sources": list(phrase.source_prompts.select_related("theme").all()),
    }
