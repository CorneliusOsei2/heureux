"""Parse the bundled study banks into structured, importable data.

Pure functions only — no Django imports — so the parser is easy to test and
reuse. The module owns both the Tâche 3 response corpus and the Tâche 2 master
question bank.
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
SUBJECT_VOCABULARY_DIR = CONTENT_DIR / "subject_vocabulary"
COMPREHENSION_VOCABULARY_DIR = CONTENT_DIR / "comprehension_vocabulary"
THEMES_PATH = CONTENT_DIR / "themes.json"
SECTIONS_PATH = CONTENT_DIR / "sections.json"
COMPREHENSION_DIR = CONTENT_DIR / "comprehension"
COMPREHENSION_TESTS_PATH = COMPREHENSION_DIR / "tests.json"
QUESTION_BANK_PATH = CONTENT_DIR / "tache_2" / "master_question_bank.json"
QUESTION_BANK_DIR = QUESTION_BANK_PATH.parent
QUESTION_BANK_TASK = ("eo", "tache-2")

EXPECTED_PROMPTS = 167
EXPECTED_UNIQUE = 130
EXPECTED_FAMILIES = 17
EXPECTED_PHRASES = 1410
SUBJECT_VOCABULARY_PER_RESPONSE = 50
SUBJECT_VOCABULARY_PER_KIND = 10
SUBJECT_VOCABULARY_FIELDS = (
    "id",
    "kind",
    "french",
    "english",
    "example",
    "usage",
)
SUBJECT_VOCABULARY_KINDS = (
    "mot-cle",
    "collocation",
    "expression",
    "tournure",
    "phrase-modele",
)
SUBJECT_VOCABULARY_CATEGORIES = {
    "mot-cle": "Mots clés du sujet",
    "collocation": "Collocations du sujet",
    "expression": "Expressions du sujet",
    "tournure": "Tournures pour l'oral",
    "phrase-modele": "Phrases modèles",
}
COMPREHENSION_VOCABULARY_PER_TEST = 50
COMPREHENSION_VOCABULARY_PER_KIND = 10
COMPREHENSION_VOCABULARY_KINDS = (
    "mot-cle",
    "verbe-action",
    "expression",
    "reformulation",
    "phrase-modele",
)
COMPREHENSION_VOCABULARY_CATEGORIES = {
    "mot-cle": "Compréhension · Mots clés",
    "verbe-action": "Compréhension · Verbes et actions",
    "expression": "Compréhension · Expressions",
    "reformulation": "Compréhension · Reformulations",
    "phrase-modele": "Compréhension · Phrases modèles",
}
COMPREHENSION_VOCABULARY_FIELDS = (
    "id",
    "kind",
    "french",
    "english",
    "example",
    "usage",
    "questions",
)
PHRASE_FIELDS = (
    "id",
    "tier",
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
    "tier": 16,
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
    icon: str
    task: str = ""


@dataclass(frozen=True)
class TaskData:
    slug: str
    name: str
    subtitle: str
    icon: str
    color: str
    order: int
    available: bool


@dataclass(frozen=True)
class SectionData:
    slug: str
    name: str
    short_name: str
    icon: str
    color: str
    order: int
    available: bool
    tasks: Tuple[TaskData, ...]


@dataclass(frozen=True)
class QuestionBankQuestionData:
    content_key: str
    text: str
    note: str = ""


@dataclass(frozen=True)
class QuestionBankGroupData:
    title: str
    guidance: str
    questions: Tuple[QuestionBankQuestionData, ...]


@dataclass(frozen=True)
class QuestionBankSectionData:
    number: int
    title: str
    guidance: str
    groups: Tuple[QuestionBankGroupData, ...]

    @property
    def anchor(self) -> str:
        return f"banque-partie-{self.number}"

    @property
    def number_label(self) -> str:
        return f"{self.number:02d}"

    @property
    def question_count(self) -> int:
        return sum(len(group.questions) for group in self.groups)

    @property
    def question_keys(self) -> Tuple[str, ...]:
        return tuple(
            question.content_key
            for group in self.groups
            for question in group.questions
        )


@dataclass(frozen=True)
class QuestionBankData:
    number: int
    title: str
    label: str
    icon: str
    subtitle: str
    sections: Tuple[QuestionBankSectionData, ...]

    @property
    def category_count(self) -> int:
        return len(self.sections)

    @property
    def question_count(self) -> int:
        return sum(section.question_count for section in self.sections)

    @property
    def question_keys(self) -> Tuple[str, ...]:
        return tuple(
            question_key
            for section in self.sections
            for question_key in section.question_keys
        )

    @property
    def annotation_key_prefix(self) -> str:
        if self.number == 1:
            return "question-bank"
        return f"question-bank:memory-{self.number:02d}"


@dataclass(frozen=True)
class ArgumentData:
    order: int
    idea: str
    developpement: str
    exemple: str
    consequence: str


@dataclass(frozen=True)
class PromptData:
    content_key: str
    theme: str
    number: int
    text: str
    family: str
    is_canonical: bool


@dataclass
class ResponseData:
    content_key: str
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
    tier: str
    category: str
    english_cue: str
    expression: str
    anchor: str
    example: str
    note: str
    sources_raw: str
    sources: Tuple[Tuple[str, int], ...]
    order: int


@dataclass(frozen=True)
class ComprehensionVocabularyData:
    phrase: PhraseData
    test_slug: str
    question_numbers: Tuple[int, ...]


@dataclass(frozen=True)
class ComprehensionChoiceData:
    letter: str
    text_fr: str
    text_en: str
    rationale: str
    is_correct: bool


@dataclass(frozen=True)
class ComprehensionQuestionData:
    content_key: str
    number: int
    passage_fr: str
    passage_en: str
    prompt_fr: str
    prompt_en: str
    correct_explanation: str
    choices: Tuple[ComprehensionChoiceData, ...]


@dataclass(frozen=True)
class ComprehensionTestData:
    slug: str
    mode: str
    number: int
    title: str
    description: str
    expected_question_count: int
    order: int
    is_published: bool
    questions: Tuple[ComprehensionQuestionData, ...]


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


def prompt_content_key(theme_slug: str, number: int) -> str:
    return f"{theme_slug}:p{number}"


def family_content_key(order: int) -> str:
    return f"family:{order:02d}"


def phrase_category_content_key(name: str) -> str:
    return f"phrase-category:{_slugify(name)}"


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
            slug=meta.get("slug") or _slugify(name),
            name=name,
            display=meta["display"],
            order=meta["order"],
            color=meta["color"],
            icon=meta["icon"],
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
                icon=t.get("icon", "target"),
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
                icon=part.get("icon", "file-text"),
                color=part.get("color", "#6366f1"),
                order=part.get("order", 0),
                available=bool(part.get("available", True)),
                tasks=tasks,
            )
        )
    sections.sort(key=lambda s: s.order)
    return sections


def load_question_bank(
    path: Path = QUESTION_BANK_PATH,
) -> QuestionBankData:
    raw = json.loads(path.read_text(encoding="utf-8"))
    memory_number = raw.get("number")
    title = str(raw.get("title", "")).strip()
    label = str(raw.get("label", "")).strip()
    icon = str(raw.get("icon", "")).strip()
    subtitle = str(raw.get("subtitle", "")).strip()
    if not isinstance(memory_number, int) or memory_number < 1:
        raise ValueError("The question bank needs a positive memory number")
    if not title or not label or not icon or not subtitle:
        raise ValueError(
            "The question bank needs a title, label, icon, and subtitle"
        )
    sections: List[QuestionBankSectionData] = []
    seen_questions = set()
    for raw_section in raw.get("sections", []):
        number = int(raw_section["number"])
        section_title = str(raw_section.get("title", "")).strip()
        if not section_title:
            raise ValueError(f"Question-bank section {number} has no title")

        groups: List[QuestionBankGroupData] = []
        for raw_group in raw_section.get("groups", []):
            questions: List[QuestionBankQuestionData] = []
            for raw_question in raw_group.get("questions", []):
                if isinstance(raw_question, str):
                    text = raw_question.strip()
                    note = ""
                else:
                    text = str(raw_question.get("text", "")).strip()
                    note = str(raw_question.get("note", "")).strip()
                if not text:
                    raise ValueError(
                        f"Question-bank section {number} contains an empty question"
                    )
                normalized = text.casefold()
                if normalized in seen_questions:
                    raise ValueError(f"Duplicate question-bank phrase: {text}")
                seen_questions.add(normalized)
                digest = hashlib.sha256(
                    normalized.encode("utf-8")
                ).hexdigest()
                questions.append(
                    QuestionBankQuestionData(
                        content_key=(
                            f"memory:{memory_number}:question:{digest}"
                        ),
                        text=text,
                        note=note,
                    )
                )
            if not questions:
                raise ValueError(
                    f"Question-bank section {number} contains an empty group"
                )
            groups.append(
                QuestionBankGroupData(
                    title=str(raw_group.get("title", "")).strip(),
                    guidance=str(raw_group.get("guidance", "")).strip(),
                    questions=tuple(questions),
                )
            )
        if not groups:
            raise ValueError(f"Question-bank section {number} has no groups")
        sections.append(
            QuestionBankSectionData(
                number=number,
                title=section_title,
                guidance=str(raw_section.get("guidance", "")).strip(),
                groups=tuple(groups),
            )
        )

    expected_numbers = list(range(1, len(sections) + 1))
    actual_numbers = [section.number for section in sections]
    if actual_numbers != expected_numbers:
        raise ValueError(
            "Question-bank sections must be ordered consecutively from 1"
        )
    if not sections:
        raise ValueError("The question bank has no sections")

    return QuestionBankData(
        number=memory_number,
        title=title,
        label=label,
        icon=icon,
        subtitle=subtitle,
        sections=tuple(sections),
    )


def load_question_banks(
    directory: Path = QUESTION_BANK_DIR,
) -> Tuple[QuestionBankData, ...]:
    banks = tuple(
        sorted(
            (
                load_question_bank(path)
                for path in directory.glob("*.json")
            ),
            key=lambda bank: bank.number,
        )
    )
    if not banks:
        raise ValueError("Tâche 2 needs at least one memory")
    numbers = [bank.number for bank in banks]
    if numbers != list(range(1, len(banks) + 1)):
        raise ValueError(
            "Tâche 2 memories must be numbered consecutively from 1"
        )
    return banks


def _ce_plain_text(value: str) -> str:
    value = value.replace("\u00a0", " ").replace("**", "")
    value = re.sub(r"\n---\s*$", "", value.strip())
    return re.sub(r"\s+", " ", value).strip()


def _parse_comprehension_source(
    path: Path,
    *,
    slug: str,
    mode: str = "ecrite",
    first_question_number: int = 1,
    allow_missing_passage_translations: bool = False,
) -> Tuple[ComprehensionQuestionData, ...]:
    if mode not in {"ecrite", "orale"}:
        raise ValueError(f"Invalid comprehension mode: {mode!r}")
    if first_question_number < 1:
        raise ValueError("Comprehension question numbering must start above zero")

    text = path.read_text(encoding="utf-8")
    parts = re.split(
        r"(?m)^## \*\*Q(\d+)\*\*\s*$",
        text,
    )[1:]
    if not parts or len(parts) % 2:
        raise ValueError(f"No valid comprehension questions in {path.name}")

    questions: List[ComprehensionQuestionData] = []
    for index in range(0, len(parts), 2):
        number = int(parts[index])
        block = parts[index + 1]
        passage_match = re.search(
            r"### \*\*(?:Passage|Dialogue)\*\*\s*```\s*\n(.*?)\n```",
            block,
            flags=re.DOTALL,
        )
        if not passage_match:
            raise ValueError(f"{path.name} Q{number} has no passage")
        passage = passage_match.group(1).strip()
        translation_match = re.search(
            r"\n\s*\((.+)\)\s*$",
            passage,
            flags=re.DOTALL,
        )
        if translation_match:
            passage_fr = _ce_plain_text(passage[:translation_match.start()])
            passage_en = _ce_plain_text(translation_match.group(1))
        elif allow_missing_passage_translations:
            passage_fr = _ce_plain_text(passage)
            passage_en = ""
        else:
            raise ValueError(f"{path.name} Q{number} has no passage translation")

        prompt_match = re.search(
            r"(?m)^\|\s*\*\*Prompt\*\*\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*$",
            block,
        )
        if not prompt_match:
            raise ValueError(f"{path.name} Q{number} has no prompt row")

        choice_rows = re.findall(
            r"(?m)^\|\s*(\*\*)?([A-D])(?:\*\*)?\s*"
            r"\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*$",
            block,
        )
        if len(choice_rows) != 4:
            raise ValueError(
                f"{path.name} Q{number} must have four choices, "
                f"found {len(choice_rows)}"
            )
        choice_letters = [letter for _marker, letter, _fr, _en in choice_rows]
        if len(set(choice_letters)) != 4 or set(choice_letters) != set("ABCD"):
            raise ValueError(
                f"{path.name} Q{number} choices must use A, B, C and D exactly once"
            )
        bold_answers = [
            letter for marker, letter, _text_fr, _text_en in choice_rows if marker
        ]
        heading_match = re.search(
            r"### \*\*Correct Answer:\s*([A-D])\s*--.*?\*\*",
            block,
        )
        if heading_match:
            correct_letter = heading_match.group(1)
            if bold_answers and bold_answers != [correct_letter]:
                raise ValueError(
                    f"{path.name} Q{number} has conflicting correct answers"
                )
        elif len(bold_answers) == 1:
            correct_letter = bold_answers[0]
        else:
            raise ValueError(f"{path.name} Q{number} has no correct answer")

        correct_explanation = ""
        if heading_match:
            explanation_end = re.search(
                r"### \*\*Why the others are wrong\*\*",
                block[heading_match.end():],
            )
            raw_explanation = block[heading_match.end():]
            if explanation_end:
                raw_explanation = raw_explanation[:explanation_end.start()]
            correct_explanation = _ce_plain_text(raw_explanation)

        rationales: Dict[str, str] = {}
        why_match = re.search(
            r"### \*\*Why the others are wrong\*\*(.*)$",
            block,
            flags=re.DOTALL,
        )
        if why_match:
            rationale_parts = re.split(
                r"(?m)^\*\*([A-D])\s*--.*?\*\*\s*",
                why_match.group(1),
            )[1:]
            for rationale_index in range(0, len(rationale_parts), 2):
                letter = rationale_parts[rationale_index]
                rationale = rationale_parts[rationale_index + 1]
                rationales[letter] = _ce_plain_text(rationale)

        choices = tuple(
            ComprehensionChoiceData(
                letter=letter,
                text_fr=_ce_plain_text(text_fr),
                text_en=_ce_plain_text(text_en),
                rationale=rationales.get(letter, ""),
                is_correct=(letter == correct_letter),
            )
            for _marker, letter, text_fr, text_en in choice_rows
        )
        if sum(choice.is_correct for choice in choices) != 1:
            raise ValueError(
                f"{path.name} Q{number} must have exactly one correct choice"
            )
        questions.append(
            ComprehensionQuestionData(
                content_key=(
                    f"{'ce' if mode == 'ecrite' else 'co'}:"
                    f"{slug}:q{number:02d}"
                ),
                number=number,
                passage_fr=passage_fr,
                passage_en=passage_en,
                prompt_fr=_ce_plain_text(prompt_match.group(1)),
                prompt_en=_ce_plain_text(prompt_match.group(2)),
                correct_explanation=correct_explanation,
                choices=choices,
            )
        )

    question_numbers = [question.number for question in questions]
    expected_numbers = list(
        range(
            first_question_number,
            first_question_number + len(questions),
        )
    )
    if question_numbers != expected_numbers:
        raise ValueError(
            f"{path.name} question numbers must be consecutive from "
            f"Q{first_question_number}"
        )
    return tuple(questions)


def load_comprehension_tests() -> List[ComprehensionTestData]:
    raw = json.loads(COMPREHENSION_TESTS_PATH.read_text(encoding="utf-8"))
    tests: List[ComprehensionTestData] = []
    seen_slugs = set()
    seen_numbers = set()
    for item in raw.get("tests", []):
        mode = item.get("mode", "ecrite")
        if mode not in {"ecrite", "orale"}:
            raise ValueError(
                f"Invalid comprehension mode for {item.get('slug')!r}: "
                f"{mode!r}"
            )
        source_name = item["source"]
        if Path(source_name).name != source_name:
            raise ValueError(f"Invalid comprehension source path: {source_name!r}")
        path = COMPREHENSION_DIR / source_name
        questions = _parse_comprehension_source(
            path,
            slug=item["slug"],
            mode=mode,
            first_question_number=int(item.get("first_question_number", 1)),
            allow_missing_passage_translations=bool(
                item.get("allow_missing_passage_translations", False)
            ),
        )
        expected_count = int(item.get("expected_question_count", 36))
        is_published = bool(item.get("is_published", False))
        if is_published and len(questions) != expected_count:
            raise ValueError(
                f"Published {item['slug']} needs {expected_count} questions, "
                f"found {len(questions)}"
            )
        number_key = (mode, int(item["number"]))
        if item["slug"] in seen_slugs or number_key in seen_numbers:
            raise ValueError(
                "Comprehension test slugs and mode/number pairs must be unique"
            )
        seen_slugs.add(item["slug"])
        seen_numbers.add(number_key)
        tests.append(
            ComprehensionTestData(
                slug=item["slug"],
                mode=mode,
                number=int(item["number"]),
                title=item.get("title") or f"Test {item['number']}",
                description=item.get("description", ""),
                expected_question_count=expected_count,
                order=int(item.get("order", item["number"])),
                is_published=is_published,
                questions=questions,
            )
        )
    mode_order = {"ecrite": 0, "orale": 1}
    tests.sort(
        key=lambda item: (
            mode_order[item.mode],
            item.order,
            item.number,
        )
    )
    return tests


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
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()

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
    theme_data = load_themes()
    order_map = {theme.name: theme.order for theme in theme_data}
    slug_map = {theme.name: theme.slug for theme in theme_data}
    themes = [theme.name for theme in theme_data]

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
                content_key=prompt_content_key(
                    slug_map[member.theme],
                    member.number,
                ),
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
                content_key=prompt_content_key(
                    slug_map[canonical.theme],
                    canonical.number,
                ),
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

            if values["tier"] not in {"shared", "response"}:
                raise ValueError(
                    f"Phrase {values['id']} has invalid tier "
                    f"{values['tier']!r}"
                )

            anchor_key = values["anchor"].casefold()
            if anchor_key in seen_anchors:
                raise ValueError(
                    f"Duplicate phrase anchor {values['anchor']!r} on rows "
                    f"{seen_anchors[anchor_key]} and {line_number}"
                )
            seen_anchors[anchor_key] = line_number

            anchor_count = values["example"].casefold().count(anchor_key)
            if anchor_count == 0:
                raise ValueError(
                    f"Phrase {values['id']} anchor is not present in its example"
                )
            if anchor_count > 1:
                raise ValueError(
                    f"Phrase {values['id']} anchor occurs more than once in "
                    "its example"
                )
            expression_key = values["expression"].casefold()
            if (
                "[" not in values["expression"]
                and expression_key in values["example"].casefold()
                and anchor_key != expression_key
            ):
                raise ValueError(
                    f"Phrase {values['id']} anchor does not cover its full "
                    "literal expression"
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
            phrases.append(
                PhraseData(
                    phrase_id=values["id"],
                    tier=values["tier"],
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


def parse_subject_vocabulary(
    responses: Optional[List[ResponseData]] = None,
) -> List[PhraseData]:
    """Load the dedicated 50-entry vocabulary deck for every response."""
    if responses is None:
        responses = parse_responses()

    response_by_key = {response.content_key: response for response in responses}
    seen_response_keys: Dict[str, str] = {}
    seen_ids: Dict[str, str] = {}
    phrases: List[PhraseData] = []
    paths = sorted(SUBJECT_VOCABULARY_DIR.glob("*.json"))
    if not paths:
        raise ValueError("No subject-vocabulary JSON files found")

    expected_kinds = tuple(
        kind
        for kind in SUBJECT_VOCABULARY_KINDS
        for _ in range(SUBJECT_VOCABULARY_PER_KIND)
    )
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError(f"{path.name} must use subject-vocabulary version 1")
        response_rows = payload.get("responses")
        if not isinstance(response_rows, list):
            raise ValueError(f"{path.name} must contain a responses list")

        for response_index, response_row in enumerate(response_rows, start=1):
            location = f"{path.name} response {response_index}"
            if not isinstance(response_row, dict):
                raise ValueError(f"{location} must be an object")
            response_key = response_row.get("response_key")
            if not isinstance(response_key, str) or not response_key.strip():
                raise ValueError(f"{location} has no response_key")
            response_key = response_key.strip()
            if response_key in seen_response_keys:
                raise ValueError(
                    f"Duplicate subject vocabulary for {response_key!r} in "
                    f"{seen_response_keys[response_key]} and {path.name}"
                )
            response = response_by_key.get(response_key)
            if response is None:
                raise ValueError(
                    f"{location} references unknown response {response_key!r}"
                )
            seen_response_keys[response_key] = path.name

            entries = response_row.get("entries")
            if not isinstance(entries, list):
                raise ValueError(f"{location} must contain an entries list")
            if len(entries) != SUBJECT_VOCABULARY_PER_RESPONSE:
                raise ValueError(
                    f"{response_key} must have exactly "
                    f"{SUBJECT_VOCABULARY_PER_RESPONSE} vocabulary entries, "
                    f"got {len(entries)}"
                )
            actual_kinds = tuple(
                entry.get("kind") if isinstance(entry, dict) else None
                for entry in entries
            )
            if actual_kinds != expected_kinds:
                raise ValueError(
                    f"{response_key} must contain ten ordered entries for each "
                    "subject-vocabulary kind"
                )

            seen_targets = set()
            sources = tuple(
                (prompt.theme, prompt.number) for prompt in response.prompts
            )
            sources_raw = "; ".join(
                f"{theme} P{number}" for theme, number in sources
            )
            for entry_index, entry in enumerate(entries, start=1):
                entry_location = f"{response_key} entry {entry_index}"
                if not isinstance(entry, dict):
                    raise ValueError(f"{entry_location} must be an object")
                values = {}
                for field_name in SUBJECT_VOCABULARY_FIELDS:
                    value = entry.get(field_name)
                    if not isinstance(value, str) or not value.strip():
                        raise ValueError(
                            f"{entry_location} has an empty {field_name!r} field"
                        )
                    values[field_name] = value.strip()

                phrase_id = values["id"]
                phrase_id_key = phrase_id.casefold()
                if len(phrase_id) > PHRASE_MAX_LENGTHS["id"]:
                    raise ValueError(
                        f"{entry_location} id exceeds "
                        f"{PHRASE_MAX_LENGTHS['id']} characters"
                    )
                if phrase_id_key in seen_ids:
                    raise ValueError(
                        f"Duplicate subject-vocabulary id {phrase_id!r} in "
                        f"{seen_ids[phrase_id_key]} and {entry_location}"
                    )
                seen_ids[phrase_id_key] = entry_location

                french = values["french"]
                english = values["english"]
                example = values["example"]
                if len(french) > PHRASE_MAX_LENGTHS["expression"]:
                    raise ValueError(
                        f"{entry_location} french target exceeds "
                        f"{PHRASE_MAX_LENGTHS['expression']} characters"
                    )
                if len(english) > PHRASE_MAX_LENGTHS["english_cue"]:
                    raise ValueError(
                        f"{entry_location} english cue exceeds "
                        f"{PHRASE_MAX_LENGTHS['english_cue']} characters"
                    )
                target_key = french.casefold()
                if target_key in seen_targets:
                    raise ValueError(
                        f"{response_key} repeats french target {french!r}"
                    )
                seen_targets.add(target_key)
                if french not in response.body:
                    raise ValueError(
                        f"{entry_location} french target is not verbatim in "
                        "the response"
                    )
                if example not in response.body:
                    raise ValueError(
                        f"{entry_location} example is not verbatim in the response"
                    )
                if example.casefold().count(target_key) != 1:
                    raise ValueError(
                        f"{entry_location} example must contain its french "
                        "target exactly once"
                    )

                phrases.append(
                    PhraseData(
                        phrase_id=phrase_id,
                        tier="subject",
                        category=SUBJECT_VOCABULARY_CATEGORIES[values["kind"]],
                        english_cue=english,
                        expression=french,
                        anchor=french,
                        example=example,
                        note=values["usage"],
                        sources_raw=sources_raw,
                        sources=sources,
                        order=EXPECTED_PHRASES + len(phrases) + 1,
                    )
                )

    missing = sorted(set(response_by_key) - set(seen_response_keys))
    if missing:
        raise ValueError(
            "Missing subject vocabulary for responses: " + ", ".join(missing)
        )
    expected_total = (
        len(response_by_key) * SUBJECT_VOCABULARY_PER_RESPONSE
    )
    if len(phrases) != expected_total:
        raise ValueError(
            f"Expected {expected_total} subject-vocabulary entries, "
            f"got {len(phrases)}"
        )
    return phrases


def parse_comprehension_vocabulary(
    tests: Optional[List[ComprehensionTestData]] = None,
) -> List[ComprehensionVocabularyData]:
    """Load one rich, source-linked vocabulary deck per comprehension test."""
    if tests is None:
        tests = load_comprehension_tests()

    tests_by_slug = {
        test.slug: test for test in tests if test.mode == "ecrite"
    }
    seen_tests: Dict[str, str] = {}
    seen_ids: Dict[str, str] = {}
    vocabulary: List[ComprehensionVocabularyData] = []
    paths = sorted(COMPREHENSION_VOCABULARY_DIR.glob("*.json"))
    if not paths:
        raise ValueError("No comprehension-vocabulary JSON files found")

    expected_kinds = tuple(
        kind
        for kind in COMPREHENSION_VOCABULARY_KINDS
        for _ in range(COMPREHENSION_VOCABULARY_PER_KIND)
    )
    base_order = (
        EXPECTED_PHRASES
        + EXPECTED_UNIQUE * SUBJECT_VOCABULARY_PER_RESPONSE
    )

    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"{path.name} must contain a JSON object")
        if set(payload) != {"test_slug", "mode", "entries"}:
            raise ValueError(
                f"{path.name} must contain test_slug, mode and entries"
            )
        test_slug = payload.get("test_slug")
        if not isinstance(test_slug, str) or test_slug not in tests_by_slug:
            raise ValueError(
                f"{path.name} references unknown test {test_slug!r}"
            )
        if test_slug in seen_tests:
            raise ValueError(
                f"Duplicate comprehension vocabulary for {test_slug!r} in "
                f"{seen_tests[test_slug]} and {path.name}"
            )
        seen_tests[test_slug] = path.name
        if payload.get("mode") != "ecrite":
            raise ValueError(f"{path.name} mode must be 'ecrite'")

        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise ValueError(f"{path.name} must contain an entries list")
        if len(entries) != COMPREHENSION_VOCABULARY_PER_TEST:
            raise ValueError(
                f"{test_slug} must have exactly "
                f"{COMPREHENSION_VOCABULARY_PER_TEST} vocabulary entries, "
                f"got {len(entries)}"
            )
        actual_kinds = tuple(
            entry.get("kind") if isinstance(entry, dict) else None
            for entry in entries
        )
        if actual_kinds != expected_kinds:
            raise ValueError(
                f"{test_slug} must contain ten ordered entries for every "
                "comprehension-vocabulary kind"
            )

        test = tests_by_slug[test_slug]
        questions_by_number = {
            question.number: question for question in test.questions
        }
        seen_targets = set()
        for index, entry in enumerate(entries, start=1):
            location = f"{test_slug} entry {index}"
            if not isinstance(entry, dict):
                raise ValueError(f"{location} must be an object")
            if set(entry) != set(COMPREHENSION_VOCABULARY_FIELDS):
                raise ValueError(
                    f"{location} fields must be "
                    f"{COMPREHENSION_VOCABULARY_FIELDS}"
                )
            values = {}
            for field_name in COMPREHENSION_VOCABULARY_FIELDS[:-1]:
                value = entry.get(field_name)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"{location} has an empty {field_name!r} field"
                    )
                values[field_name] = value.strip()

            phrase_id = values["id"]
            expected_id = (
                f"CE{test.number:02d}V{index:03d}"
            )
            if phrase_id != expected_id:
                raise ValueError(
                    f"{location} id must be {expected_id!r}, got "
                    f"{phrase_id!r}"
                )
            phrase_id_key = phrase_id.casefold()
            if phrase_id_key in seen_ids:
                raise ValueError(
                    f"Duplicate comprehension-vocabulary id {phrase_id!r}"
                )
            seen_ids[phrase_id_key] = location

            question_numbers = entry.get("questions")
            if (
                not isinstance(question_numbers, list)
                or not question_numbers
                or any(
                    not isinstance(number, int)
                    or number not in questions_by_number
                    for number in question_numbers
                )
                or len(set(question_numbers)) != len(question_numbers)
            ):
                raise ValueError(
                    f"{location} must cite unique valid question numbers"
                )
            question_numbers = tuple(question_numbers)

            french = values["french"]
            english = values["english"]
            example = values["example"]
            target_key = french.casefold()
            if target_key in seen_targets:
                raise ValueError(
                    f"{test_slug} repeats french target {french!r}"
                )
            seen_targets.add(target_key)
            if len(french) > PHRASE_MAX_LENGTHS["expression"]:
                raise ValueError(f"{location} french target is too long")
            if len(english) > PHRASE_MAX_LENGTHS["english_cue"]:
                raise ValueError(f"{location} english cue is too long")
            if example.casefold().count(target_key) != 1:
                raise ValueError(
                    f"{location} example must contain its french target "
                    "exactly once"
                )

            cited_source = " ".join(
                " ".join(
                    [
                        questions_by_number[number].passage_fr,
                        questions_by_number[number].prompt_fr,
                        *(
                            choice.text_fr
                            for choice in questions_by_number[number].choices
                        ),
                    ]
                )
                for number in question_numbers
            ).casefold()
            if target_key not in cited_source:
                raise ValueError(
                    f"{location} french target is not present in a cited "
                    "source question"
                )

            sources_raw = "; ".join(
                f"CE · {test.title} · Q{number}"
                for number in question_numbers
            )
            phrase = PhraseData(
                phrase_id=phrase_id,
                tier="comprehension",
                category=COMPREHENSION_VOCABULARY_CATEGORIES[
                    values["kind"]
                ],
                english_cue=english,
                expression=french,
                anchor=french,
                example=example,
                note=values["usage"],
                sources_raw=sources_raw,
                sources=(),
                order=base_order + len(vocabulary) + 1,
            )
            vocabulary.append(
                ComprehensionVocabularyData(
                    phrase=phrase,
                    test_slug=test_slug,
                    question_numbers=question_numbers,
                )
            )

    missing_tests = sorted(set(tests_by_slug) - set(seen_tests))
    if missing_tests:
        raise ValueError(
            "Missing comprehension vocabulary for tests: "
            + ", ".join(missing_tests)
        )
    expected_total = len(tests_by_slug) * COMPREHENSION_VOCABULARY_PER_TEST
    if len(vocabulary) != expected_total:
        raise ValueError(
            f"Expected {expected_total} comprehension-vocabulary entries, "
            f"got {len(vocabulary)}"
        )
    return vocabulary


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
