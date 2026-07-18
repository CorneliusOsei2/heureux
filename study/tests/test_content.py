"""Validation tests for the bundled phrase bank."""

from __future__ import annotations

import csv
import json
import re
import tempfile
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from study import content


class AppCopyTests(SimpleTestCase):
    def test_user_facing_sources_omit_exam_brand_acronyms(self):
        project_root = Path(__file__).resolve().parents[2]
        roots = (project_root / "study", project_root / "templates")
        suffixes = {
            ".css",
            ".html",
            ".js",
            ".json",
            ".md",
            ".py",
            ".tsv",
            ".txt",
        }
        forbidden = ("T" + "CF", "T" + "EF")
        pattern = re.compile(
            r"\b(?:" + "|".join(re.escape(item) for item in forbidden) + r")\b",
            re.IGNORECASE,
        )
        violations = []

        for root in roots:
            for path in root.rglob("*"):
                if path.suffix.lower() not in suffixes:
                    continue
                text = path.read_text(encoding="utf-8")
                if match := pattern.search(text):
                    violations.append(
                        f"{path.relative_to(project_root)}: {match.group(0)}"
                    )

        self.assertEqual(violations, [])


class PhraseParserTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.responses = content.parse_responses()
        cls.response = cls.responses[0]
        cls.prompt = cls.response.prompts[0]

    def valid_row(self, phrase_id="TEST1", **overrides):
        example = self.response.position_claire
        row = {
            "id": phrase_id,
            "tier": "shared",
            "category": "Test",
            "english_cue": "Test cue",
            "expression": example[:30],
            "anchor": example[:30],
            "example": example,
            "sources": f"{self.prompt.theme} P{self.prompt.number}",
            "note": "",
        }
        row.update(overrides)
        return row

    def parse_rows(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phrases.tsv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=content.PHRASE_FIELDS,
                    delimiter="\t",
                    lineterminator="\n",
                )
                writer.writeheader()
                writer.writerows(rows)
            with (
                patch.object(content, "PHRASES_PATH", path),
                patch.object(content, "EXPECTED_PHRASES", len(rows)),
            ):
                return content.parse_phrases(self.responses)

    def test_accepts_verbatim_phrase(self):
        phrases = self.parse_rows([self.valid_row()])
        self.assertEqual(phrases[0].phrase_id, "TEST1")
        self.assertEqual(phrases[0].tier, "shared")

    def test_rejects_unknown_tier(self):
        with self.assertRaisesRegex(ValueError, "invalid tier"):
            self.parse_rows([self.valid_row(tier="global")])

    def test_rejects_anchor_missing_from_example(self):
        with self.assertRaisesRegex(ValueError, "anchor is not present"):
            self.parse_rows([self.valid_row(anchor="not in the response")])

    def test_rejects_partial_highlight_for_a_literal_expression(self):
        row = self.valid_row()
        row["anchor"] = row["expression"][:10]
        with self.assertRaisesRegex(ValueError, "does not cover its full"):
            self.parse_rows([row])

    def test_rejects_ambiguous_repeated_highlight_target(self):
        with self.assertRaisesRegex(ValueError, "occurs more than once"):
            self.parse_rows(
                [
                    self.valid_row(
                        expression="[…] certaines habitudes […]",
                        anchor="certaines habitudes",
                    )
                ]
            )

    def test_rejects_non_verbatim_example(self):
        row = self.valid_row()
        row["example"] = f"{row['example']} This was not in the source."
        with self.assertRaisesRegex(ValueError, "example is not verbatim"):
            self.parse_rows([row])

    def test_accepts_reuse_with_a_different_surface_form(self):
        row = self.valid_row()
        other_prompt = next(
            prompt
            for response in self.responses[1:]
            for prompt in response.prompts
            if row["anchor"].casefold() not in response.body.casefold()
        )
        row["sources"] += (
            f"; {other_prompt.theme} P{other_prompt.number}"
        )

        phrases = self.parse_rows([row])

        self.assertEqual(len(phrases[0].sources), 2)

    def test_rejects_values_too_long_for_database_fields(self):
        with self.assertRaisesRegex(ValueError, "english_cue.*exceeds 200"):
            self.parse_rows([self.valid_row(english_cue="x" * 201)])

    def test_rejects_malformed_or_unknown_sources(self):
        for source, error in (
            ("Culture #1", "malformed source"),
            ("Unknown P1", "unknown source theme"),
            ("Culture P999", "unknown prompt"),
        ):
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValueError, error):
                    self.parse_rows([self.valid_row(sources=source)])

    def test_rejects_duplicate_ids_and_anchors(self):
        first = self.valid_row()
        with self.assertRaisesRegex(ValueError, "Duplicate phrase id"):
            self.parse_rows([first, self.valid_row()])

        with self.assertRaisesRegex(ValueError, "Duplicate phrase anchor"):
            self.parse_rows([first, self.valid_row(phrase_id="TEST2")])

    def test_bundled_bank_keeps_rich_coverage_outside_the_shared_catalog(self):
        phrases = content.parse_phrases(self.responses)
        prompt_to_response = {
            (prompt.theme, prompt.number): response.content_key
            for response in self.responses
            for prompt in response.prompts
        }
        coverage = Counter()
        for phrase in phrases:
            for response_key in {
                prompt_to_response[source] for source in phrase.sources
            }:
                coverage[response_key] += 1

        self.assertEqual(
            Counter(phrase.tier for phrase in phrases),
            {"response": 1184, "shared": 226},
        )
        self.assertEqual(len(coverage), 130)
        self.assertGreaterEqual(min(coverage.values()), 12)

    def test_response_vocabulary_uses_its_semantic_topic_category(self):
        categories = {
            phrase.phrase_id: phrase.category
            for phrase in content.parse_phrases(self.responses)
        }
        expected = {
            "A34": "Santé",
            "C55": "Famille et relations",
            "C102": "Éducation et apprentissage",
            "C116": "Éducation et apprentissage",
            "C149": "Famille et relations",
            "H18": "Travail et économie",
            "A149": "Éducation et apprentissage",
            "A182": "Environnement et transports",
            "A183": "Environnement et transports",
            "A184": "Environnement et transports",
            "A246": "Environnement et transports",
            "C251": "Famille et relations",
            "C258": "Famille et relations",
            "C338": "Environnement et transports",
            "C360": "Travail et économie",
            "N145": "Famille et relations",
        }
        self.assertEqual(
            {phrase_id: categories[phrase_id] for phrase_id in expected},
            expected,
        )

    def test_subject_vocabulary_has_fifty_grounded_entries_per_response(self):
        vocabulary = content.parse_subject_vocabulary(self.responses)
        prompt_to_response = {
            (prompt.theme, prompt.number): response.content_key
            for response in self.responses
            for prompt in response.prompts
        }
        prompts_by_response = {
            response.content_key: {
                (prompt.theme, prompt.number) for prompt in response.prompts
            }
            for response in self.responses
        }
        coverage = Counter()
        for phrase in vocabulary:
            self.assertEqual(phrase.tier, "subject")
            response_keys = {
                prompt_to_response[source] for source in phrase.sources
            }
            self.assertEqual(len(response_keys), 1)
            response_key = response_keys.pop()
            self.assertEqual(
                set(phrase.sources),
                prompts_by_response[response_key],
            )
            coverage[response_key] += 1

        self.assertEqual(len(vocabulary), 130 * 50)
        self.assertEqual(
            set(coverage),
            {response.content_key for response in self.responses},
        )
        self.assertEqual(set(coverage.values()), {50})
        self.assertTrue(
            {
                phrase.phrase_id.casefold()
                for phrase in content.parse_phrases(self.responses)
            }.isdisjoint(
                phrase.phrase_id.casefold() for phrase in vocabulary
            )
        )
        self.assertEqual(
            Counter(phrase.category for phrase in vocabulary),
            {
                "Mots clés du sujet": 1300,
                "Collocations du sujet": 1300,
                "Expressions du sujet": 1300,
                "Tournures pour l'oral": 1300,
                "Phrases modèles": 1300,
            },
        )


class ComprehensionVocabularyParserTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tests = content.load_comprehension_tests()

    def test_bundled_bank_has_fifty_source_grounded_entries_per_test(self):
        vocabulary = content.parse_comprehension_vocabulary(self.tests)
        response_ids = {
            phrase.phrase_id.casefold()
            for phrase in content.parse_phrases(content.parse_responses())
        }
        subject_ids = {
            phrase.phrase_id.casefold()
            for phrase in content.parse_subject_vocabulary(
                content.parse_responses()
            )
        }

        self.assertEqual(len(vocabulary), 450)
        self.assertEqual(
            Counter(item.test_slug for item in vocabulary),
            {
                f"test-{number}": 50
                for number in (1, 2, 3, 4, 5, 6, 7, 9, 10)
            },
        )
        self.assertEqual(
            Counter(item.phrase.category for item in vocabulary),
            {
                category: 90
                for category in content.COMPREHENSION_VOCABULARY_CATEGORIES.values()
            },
        )
        self.assertTrue(
            all(item.phrase.tier == "comprehension" for item in vocabulary)
        )
        comprehension_ids = {
            item.phrase.phrase_id.casefold() for item in vocabulary
        }
        self.assertEqual(len(comprehension_ids), 450)
        self.assertTrue(comprehension_ids.isdisjoint(response_ids))
        self.assertTrue(comprehension_ids.isdisjoint(subject_ids))

    def test_parser_rejects_a_target_absent_from_its_cited_questions(self):
        payload = json.loads(
            (
                content.COMPREHENSION_VOCABULARY_DIR / "test_01.json"
            ).read_text(encoding="utf-8")
        )
        payload["entries"][0]["french"] = "terme totalement absent"
        payload["entries"][0]["example"] = (
            "Ce terme totalement absent ne vient pas de la source."
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test_01.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            with patch.object(
                content,
                "COMPREHENSION_VOCABULARY_DIR",
                Path(directory),
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "french target is not present",
                ):
                    content.parse_comprehension_vocabulary([self.tests[0]])
