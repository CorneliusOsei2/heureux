"""Parse the bundled answer bank into structured, importable data.

Pure functions only — no Django imports — so the parser is easy to test and
reuse. ``load_content`` returns themes, families, unique responses (with their
prompt aliases and structured arguments) and phrases.
"""

from __future__ import annotations

import csv
import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

CONTENT_DIR = Path(__file__).resolve().parent / "content"
RESPONSES_DIR = CONTENT_DIR / "responses"
STUDY_SHEETS_PATH = CONTENT_DIR / "study_sheets.md"
PHRASES_PATH = CONTENT_DIR / "phrases.tsv"
THEMES_PATH = CONTENT_DIR / "themes.json"
SECTIONS_PATH = CONTENT_DIR / "sections.json"

EXPECTED_PROMPTS = 167
EXPECTED_UNIQUE = 130
EXPECTED_FAMILIES = 17
EXPECTED_PHRASES = 1412
PHRASE_FIELDS = (
    "id",
    "category",
    "english_cue",
    "expression",
    "anchor",
    "example",
    "sources",
    "note",
)
PHRASE_MAX_LENGTHS = {
    "id": 16,
    "category": 120,
    "english_cue": 200,
    "expression": 300,
    "anchor": 300,
}

# study_sheets label -> responses directory name.
LABEL_TO_THEME = {
    "Culture": "Culture",
    "Famille": "Famille",
    "Education": "Education",
    "Santé": "Sante",
    "Techno": "Technologie",
    "Environ": "Environnement",
    "Economie": "Economie",
}


@dataclass(frozen=True)
class ThemeData:
    slug: str
    name: str
    display: str
    order: int
    color: str
    emoji: str
    task: str = ""


@dataclass(frozen=True)
class TaskData:
    slug: str
    name: str
    subtitle: str
    emoji: str
    color: str
    order: int
    available: bool


@dataclass(frozen=True)
class SectionData:
    slug: str
    name: str
    short_name: str
    emoji: str
    color: str
    order: int
    available: bool
    tasks: Tuple[TaskData, ...]


@dataclass(frozen=True)
class ArgumentData:
    order: int
    idea: str
    developpement: str
    exemple: str
    consequence: str


@dataclass(frozen=True)
class PromptData:
    theme: str
    number: int
    text: str
    family: str
    is_canonical: bool


@dataclass
class ResponseData:
    body_hash: str
    theme: str
    family: str
    prompt: str
    reformulation: str
    position: str
    position_claire: str
    nuance: str
    conclusion: str
    body: str
    body_html: str
    arguments: List[ArgumentData]
    prompts: List[PromptData] = field(default_factory=list)


@dataclass(frozen=True)
class PhraseData:
    phrase_id: str
    category: str
    english_cue: str
    expression: str
    anchor: str
    example: str
    note: str
    sources_raw: str
    sources: Tuple[Tuple[str, int], ...]
    order: int


def _slugify(value: str) -> str:
    replacements = {
        "à": "a", "â": "a", "ä": "a", "ç": "c", "é": "e", "è": "e",
        "ê": "e", "ë": "e", "î": "i", "ï": "i", "ô": "o", "ö": "o",
        "ù": "u", "û": "u", "ü": "u", "œ": "oe", "’": "", "'": "",
    }
    value = value.lower()
    for src, dst in replacements.items():
        value = value.replace(src, dst)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value[:110] or "x"


def _normalize(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in value.strip().splitlines())


def _natural_key(path: Path) -> Tuple[int, ...]:
    numbers = tuple(int(n) for n in re.findall(r"\d+", path.stem))
    return numbers or (0,)


def load_themes() -> List[ThemeData]:
    raw = json.loads(THEMES_PATH.read_text(encoding="utf-8"))
    themes = [
        ThemeData(
            slug=_slugify(name),
            name=name,
            display=meta["display"],
            order=meta["order"],
            color=meta["color"],
            emoji=meta["emoji"],
            task=meta.get("task", ""),
        )
        for name, meta in raw.items()
    ]
    themes.sort(key=lambda t: t.order)
    return themes


def load_sections() -> List[SectionData]:
    raw = json.loads(SECTIONS_PATH.read_text(encoding="utf-8"))
    sections: List[SectionData] = []
    for part in raw.get("parts", []):
        tasks = tuple(
            TaskData(
                slug=t["slug"],
                name=t["name"],
                subtitle=t.get("subtitle", ""),
                emoji=t.get("emoji", "🎯"),
                color=t.get("color", part.get("color", "#6366f1")),
                order=t.get("order", 0),
                available=bool(t.get("available", True)),
            )
            for t in part.get("tasks", [])
        )
        tasks = tuple(sorted(tasks, key=lambda t: t.order))
        sections.append(
            SectionData(
                slug=part["slug"],
                name=part["name"],
                short_name=part.get("short_name", part["name"]),
                emoji=part.get("emoji", "📝"),
                color=part.get("color", "#6366f1"),
                order=part.get("order", 0),
                available=bool(part.get("available", True)),
                tasks=tasks,
            )
        )
    sections.sort(key=lambda s: s.order)
    return sections


def theme_order_map() -> Dict[str, int]:
    return {t.name: t.order for t in load_themes()}


def parse_families() -> Tuple[Dict[Tuple[str, int], str], List[Tuple[str, int]]]:
    """Return ((theme, number) -> family name) and ordered [(family, order)]."""
    family_map: Dict[Tuple[str, int], str] = {}
    families: List[Tuple[str, int]] = []
    current_family = ""
    order = 0

    for line in STUDY_SHEETS_PATH.read_text(encoding="utf-8").splitlines():
        header = re.match(r"^## (\d+)\. (.+)$", line)
        if header:
            order = int(header.group(1))
            current_family = header.group(2).strip()
            families.append((current_family, order))
            continue

        card = re.match(r"^\*\*(.+)\*\*$", line)
        if not card or not current_family:
            continue
        for label in card.group(1).split(" = "):
            match = re.fullmatch(r"(.+?) P(\d+)", label.strip())
            if not match:
                raise ValueError(f"Bad study-sheet label: {label!r}")
            display_theme, number = match.groups()
            theme = LABEL_TO_THEME.get(display_theme)
            if theme is None:
                raise ValueError(f"Unknown theme label: {display_theme!r}")
            key = (theme, int(number))
            if key in family_map:
                raise ValueError(f"Prompt in two families: {key}")
            family_map[key] = current_family

    if len(family_map) != EXPECTED_PROMPTS:
        raise ValueError(
            f"Expected {EXPECTED_PROMPTS} family assignments, "
            f"got {len(family_map)}"
        )
    if len(families) != EXPECTED_FAMILIES:
        raise ValueError(
            f"Expected {EXPECTED_FAMILIES} families, got {len(families)}"
        )
    return family_map, families


def _section(block: str, start: str, end: str) -> str:
    match = re.search(
        rf"{re.escape(start)}\n+(.*?)(?=\n+{re.escape(end)})",
        block,
        flags=re.DOTALL,
    )
    if not match:
        raise ValueError(f"Missing section {start!r}")
    return _normalize(match.group(1)).replace("\n", " ")


def _labeled_part(section: str, label: str) -> str:
    match = re.search(
        rf"\*\*{label}\*\*\s*\n+(.*?)"
        rf"(?=\n+\*\*(?:Idée|Développement|Exemple|Conséquence)\*\*|\Z)",
        section,
        flags=re.DOTALL,
    )
    if not match:
        return ""
    return _normalize(match.group(1)).replace("\n", " ")


def _parse_arguments(block: str) -> List[ArgumentData]:
    headers = list(
        re.finditer(r"### \*\*([234])\. Argument \d+ - (.*?)\*\*", block)
    )
    if len(headers) != 3:
        raise ValueError(f"Expected 3 arguments, found {len(headers)}")

    arguments: List[ArgumentData] = []
    for index, header in enumerate(headers):
        idea_title = header.group(2).strip()
        section_start = header.end()
        section_end = (
            headers[index + 1].start()
            if index + 1 < len(headers)
            else re.search(r"### \*\*5\. Nuance\*\*", block).start()
        )
        section = block[section_start:section_end]
        arguments.append(
            ArgumentData(
                order=index + 1,
                idea=_labeled_part(section, "Idée") or idea_title,
                developpement=_labeled_part(section, "Développement"),
                exemple=_labeled_part(section, "Exemple"),
                consequence=_labeled_part(section, "Conséquence"),
            )
        )
    return arguments


def _body_to_html(body: str) -> str:
    out: List[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line == "---":
            continue
        if line.startswith("### "):
            title = re.sub(r"^###\s+\*\*(.*?)\*\*$", r"\1", line)
            out.append(f"<h3>{html.escape(title)}</h3>")
        elif re.fullmatch(r"`[^`]+`", line):
            out.append(
                f'<div class="sec-label">{html.escape(line.strip("`"))}</div>'
            )
        elif re.fullmatch(r"\*\*[^*]+\*\*", line):
            out.append(f"<h4>{html.escape(line.strip('*'))}</h4>")
        else:
            out.append(f"<p>{html.escape(line)}</p>")
    return "".join(out)


@dataclass
class _RawPrompt:
    theme: str
    number: int
    prompt: str
    family: str
    reformulation: str
    position: str
    position_claire: str
    nuance: str
    conclusion: str
    body: str
    body_html: str
    body_hash: str
    arguments: List[ArgumentData]


def _parse_theme_file(path: Path, theme: str, family_map) -> List[_RawPrompt]:
    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"(?=^## \*\*Prompt \d+\*\*$)", text, flags=re.MULTILINE)
    raws: List[_RawPrompt] = []
    for block in blocks:
        header = re.match(
            r"^## \*\*Prompt (\d+)\*\*$", block.strip(), flags=re.MULTILINE
        )
        if not header:
            continue
        number = int(header.group(1))

        prompt_match = re.search(r"```markdown\n(.*?)\n```", block, re.DOTALL)
        if not prompt_match:
            raise ValueError(f"Missing prompt text in {path} P{number}")
        prompt = _normalize(prompt_match.group(1)).replace("\n", " ")

        reformulation = _section(block, "`Reformulation`", "`Position`")
        position = _section(block, "`Position`", "### **1. Position claire**")
        position_claire = _section(
            block, "### **1. Position claire**", "### **2. Argument 1"
        )
        arguments = _parse_arguments(block)
        nuance = _section(block, "### **5. Nuance**", "### **6. Conclusion**")
        conclusion = _normalize(
            re.split(r"### \*\*6\. Conclusion\*\*", block)[1]
        )
        conclusion = re.sub(r"\n---\s*$", "", conclusion).strip()
        conclusion = conclusion.replace("\n", " ")

        body_start = block.find("`Reformulation`")
        body = _normalize(block[body_start:])
        body = re.sub(r"\n---\s*$", "", body).strip()
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]

        family = family_map.get((theme, number))
        if family is None:
            raise ValueError(f"No family for {theme} P{number}")

        raws.append(
            _RawPrompt(
                theme=theme,
                number=number,
                prompt=prompt,
                family=family,
                reformulation=reformulation,
                position=position,
                position_claire=position_claire,
                nuance=nuance,
                conclusion=conclusion,
                body=body,
                body_html=_body_to_html(body),
                body_hash=body_hash,
                arguments=arguments,
            )
        )
    return raws


def parse_responses() -> List[ResponseData]:
    family_map, _ = parse_families()
    order_map = theme_order_map()
    themes = [t.name for t in load_themes()]

    raws: List[_RawPrompt] = []
    for theme in themes:
        theme_dir = RESPONSES_DIR / theme
        for path in sorted(theme_dir.glob("batch_*.md"), key=_natural_key):
            raws.extend(_parse_theme_file(path, theme, family_map))

    if len(raws) != EXPECTED_PROMPTS:
        raise ValueError(f"Expected {EXPECTED_PROMPTS} prompts, got {len(raws)}")

    groups: Dict[str, List[_RawPrompt]] = {}
    for raw in raws:
        groups.setdefault(raw.body_hash, []).append(raw)

    if len(groups) != EXPECTED_UNIQUE:
        raise ValueError(
            f"Expected {EXPECTED_UNIQUE} unique responses, got {len(groups)}"
        )

    responses: List[ResponseData] = []
    for body_hash, members in groups.items():
        members.sort(key=lambda r: (order_map[r.theme], r.number))
        canonical = members[0]
        prompts = [
            PromptData(
                theme=member.theme,
                number=member.number,
                text=member.prompt,
                family=member.family,
                is_canonical=(member is canonical),
            )
            for member in members
        ]
        responses.append(
            ResponseData(
                body_hash=body_hash,
                theme=canonical.theme,
                family=canonical.family,
                prompt=canonical.prompt,
                reformulation=canonical.reformulation,
                position=canonical.position,
                position_claire=canonical.position_claire,
                nuance=canonical.nuance,
                conclusion=canonical.conclusion,
                body=canonical.body,
                body_html=canonical.body_html,
                arguments=canonical.arguments,
                prompts=prompts,
            )
        )

    responses.sort(key=lambda r: (order_map[r.theme], r.prompts[0].number))
    return responses


def parse_phrases(
    responses: Optional[List[ResponseData]] = None,
) -> List[PhraseData]:
    if responses is None:
        responses = parse_responses()

    prompt_bodies = {
        (prompt.theme, prompt.number): response.body
        for response in responses
        for prompt in response.prompts
    }
    seen_ids: Dict[str, int] = {}
    seen_anchors: Dict[str, int] = {}
    phrases: List[PhraseData] = []
    with PHRASES_PATH.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if tuple(reader.fieldnames or ()) != PHRASE_FIELDS:
            raise ValueError(
                f"Phrase TSV columns must be {PHRASE_FIELDS}, "
                f"got {tuple(reader.fieldnames or ())}"
            )
        for order, row in enumerate(reader, start=1):
            line_number = order + 1
            if None in row:
                raise ValueError(
                    f"Phrase row {line_number} has extra tab-separated fields"
                )

            values = {field: (row.get(field) or "").strip() for field in PHRASE_FIELDS}
            for field in PHRASE_FIELDS[:-1]:
                if not values[field]:
                    raise ValueError(
                        f"Phrase row {line_number} has an empty {field!r} field"
                    )
            for field, max_length in PHRASE_MAX_LENGTHS.items():
                if len(values[field]) > max_length:
                    raise ValueError(
                        f"Phrase row {line_number} {field!r} exceeds "
                        f"{max_length} characters"
                    )

            phrase_id_key = values["id"].casefold()
            if phrase_id_key in seen_ids:
                raise ValueError(
                    f"Duplicate phrase id {values['id']!r} on rows "
                    f"{seen_ids[phrase_id_key]} and {line_number}"
                )
            seen_ids[phrase_id_key] = line_number

            anchor_key = values["anchor"].casefold()
            if anchor_key in seen_anchors:
                raise ValueError(
                    f"Duplicate phrase anchor {values['anchor']!r} on rows "
                    f"{seen_anchors[anchor_key]} and {line_number}"
                )
            seen_anchors[anchor_key] = line_number

            if anchor_key not in values["example"].casefold():
                raise ValueError(
                    f"Phrase {values['id']} anchor is not present in its example"
                )

            sources_raw = values["sources"]
            sources: List[Tuple[str, int]] = []
            seen_sources = set()
            for token in sources_raw.split(";"):
                token = token.strip()
                if not token:
                    raise ValueError(
                        f"Phrase {values['id']} has an empty source token"
                    )
                match = re.fullmatch(r"(.+?) P(\d+)", token)
                if not match:
                    raise ValueError(
                        f"Phrase {values['id']} has malformed source {token!r}"
                    )
                display_theme, number = match.groups()
                theme = _display_to_theme(display_theme)
                if theme is None:
                    raise ValueError(
                        f"Phrase {values['id']} has unknown source theme "
                        f"{display_theme!r}"
                    )
                source = (theme, int(number))
                if source not in prompt_bodies:
                    raise ValueError(
                        f"Phrase {values['id']} references unknown prompt "
                        f"{display_theme} P{number}"
                    )
                if source in seen_sources:
                    raise ValueError(
                        f"Phrase {values['id']} repeats source "
                        f"{display_theme} P{number}"
                    )
                seen_sources.add(source)
                sources.append(source)

            matching_bodies = [prompt_bodies[source] for source in sources]
            if not any(values["example"] in body for body in matching_bodies):
                raise ValueError(
                    f"Phrase {values['id']} example is not verbatim in a cited "
                    "response"
                )
            missing_anchor_sources = [
                source
                for source, body in zip(sources, matching_bodies)
                if anchor_key not in body.casefold()
            ]
            if missing_anchor_sources:
                labels = ", ".join(
                    f"{theme} P{number}"
                    for theme, number in missing_anchor_sources
                )
                raise ValueError(
                    f"Phrase {values['id']} anchor is absent from cited "
                    f"responses: {labels}"
                )

            phrases.append(
                PhraseData(
                    phrase_id=values["id"],
                    category=values["category"],
                    english_cue=values["english_cue"],
                    expression=values["expression"],
                    anchor=values["anchor"],
                    example=values["example"],
                    note=values["note"],
                    sources_raw=sources_raw,
                    sources=tuple(sources),
                    order=order,
                )
            )
    if len(phrases) != EXPECTED_PHRASES:
        raise ValueError(
            f"Expected {EXPECTED_PHRASES} phrases, got {len(phrases)}"
        )
    return phrases


def _display_to_theme(display_theme: str) -> Optional[str]:
    direct = {
        "Culture": "Culture",
        "Famille": "Famille",
        "Education": "Education",
        "Éducation": "Education",
        "Sante": "Sante",
        "Santé": "Sante",
        "Technologie": "Technologie",
        "Techno": "Technologie",
        "Environnement": "Environnement",
        "Environ": "Environnement",
        "Economie": "Economie",
        "Économie": "Economie",
    }
    return direct.get(display_theme)
