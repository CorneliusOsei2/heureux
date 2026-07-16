"""Validation tests for the bundled phrase bank."""

from __future__ import annotations

import csv
import tempfile
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from study import content


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
