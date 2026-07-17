"""Refresh the bundled content snapshot from the live t3 answer bank.

Copies the response batches, study sheets and phrase inventory from the source
tree (default: the t3 directory above this app) into ``study/content`` so the
app stays self-contained and deployable. The app-owned subject-vocabulary bank
is left untouched. Run ``import_content`` afterwards.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from study import content as content_module

THEMES = [
    "Culture",
    "Famille",
    "Education",
    "Sante",
    "Technologie",
    "Environnement",
    "Economie",
]


class Command(BaseCommand):
    help = "Copy the latest answer bank into the app's content bundle."

    def add_arguments(self, parser):
        parser.add_argument(
            "--from",
            dest="source",
            default=str(Path(settings.BASE_DIR).parent),
            help="Path to the t3 directory holding agent_kit/ and study_sheets.md.",
        )

    def handle(self, *args, **options):
        source = Path(options["source"]).resolve()
        theme_data = source / "agent_kit" / "theme_data"
        study_sheets = source / "study_sheets.md"
        phrases = source / "anki" / "data" / "phrases.tsv"

        for path in (theme_data, study_sheets, phrases):
            if not path.exists():
                raise CommandError(f"Source not found: {path}")

        responses_root = content_module.RESPONSES_DIR
        copied = 0
        for theme in THEMES:
            src_dir = theme_data / theme / "responses"
            dst_dir = responses_root / theme
            if dst_dir.exists():
                shutil.rmtree(dst_dir)
            dst_dir.mkdir(parents=True, exist_ok=True)
            for batch in sorted(src_dir.glob("batch_*.md")):
                shutil.copy2(batch, dst_dir / batch.name)
                copied += 1

        shutil.copy2(study_sheets, content_module.STUDY_SHEETS_PATH)
        shutil.copy2(phrases, content_module.PHRASES_PATH)

        self.stdout.write(
            self.style.SUCCESS(
                f"Synced {copied} response files, study sheets and phrases "
                f"from {source}."
            )
        )
