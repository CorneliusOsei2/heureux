# Heureux · Fiches de révision (French oral-exam flashcards)

A multi-user French-exam learning app that combines expression, comprehension,
rich vocabulary, private notes, and spaced repetition. Its expression corpus
contains 167 prompts that collapse into **130 unique argued answers** across
7 themes and 17 topic families; its comprehension and vocabulary collections
turn the same source material into focused tests and active-recall decks.

Built with Django. Clean, fast, keyboard-driven UI with light/dark themes.

---

## What's inside

- **Six canonical areas** — Accueil, Compréhension, Expression, Vocabulaire,
  Notes, and Stats each own one part of the learning experience. Review is
  launched contextually from the area that owns the content rather than from a
  separate, mixed destination.
- **Expression orale → Tâche 3** — subjects, complete argued responses, and
  response-recall practice share one clear workspace.
- **Compréhension écrite → Test N** — persisted multiple-choice tests present
  one French document at a time, save each answer immediately, and unlock the
  English translation plus detailed answer rationales after submission.
  Unfinished tests resume at the next unanswered question; completed attempts
  keep their score, full correction, history, and retake path.
- **Vocabulaire** — one library groups reusable expression turns, the 50-entry
  deck for every expression subject, and a 50-entry source-linked deck for
  every published written-comprehension test.
- **Sessions** — review responses, expressions, or the revisit list without
  distractions. Reveal with `Space`, then choose `1` (Revoir) or `2` (Correct).
  Unfinished sessions reopen on the exact card where you stopped, and the
  immediately preceding card remains available in a read-only view.
- **À revoir** — a persistent list for difficult cards, with its own focused
  review pass.
- **Points à renforcer** — an automatic drill built from cards that are still
  hesitant or were missed repeatedly during the last 30 days. It works across
  the whole collection or inside one task.
- **Sujets & réponses** — browse Tâche 3 by theme or topic family.
- **Fiche complète** — the learning view includes the reformulation, position,
  each argument's development, concrete example and consequence, then the
  nuance, conclusion, equivalent prompts, and related expressions. Flashcard
  practice deliberately keeps only each argument's main idea.
- **Expressions & vocabulaire** — 226 reusable chunks form a curated shared
  catalog with accurate topical and functional categories. The remaining 1,184
  source-grounded chunks stay with their response, preserving at least 12 useful
  expressions per answer without mixing local vocabulary into global decks. A
  separate subject deck adds exactly 50 carefully grounded words, collocations,
  expressions and idioms, turns of phrase, and sentence models to every unique
  response.
  Expression and subject-vocabulary review lots contain at most 10 entries.
- **Private notes & highlights** — one searchable library can be filtered by
  task and has a dedicated highlights subsection.
  Select text anywhere to copy it, translate it, save it to Notes, or highlight
  it persistently. Search the complete private annotation library, mark chosen
  notes or passages “À étudier”, then decide whether each should remain in or
  leave the active-recall deck. Translation uses the browser's local English model with
  an explicit Google Translate fallback when local translation is unavailable.
- **Practice without a daily cap** — every new card and due review stays
  available; expression categories and subject decks provide optional 10-entry
  lots with not-started, in-progress, and completed states plus next-lot
  navigation.
- **Progression** — 30-day review bars, 90-day activity heatmap, 14-day forecast,
  mature-card retention, streak, per-theme mastery, and a private recent-session
  timeline grouped by natural study breaks.
- **Comptes privés** — a unique username and hashed six-digit PIN protect each
  learner's cards, history, revisit list, and resumable session. One-time
  recovery codes allow safe PIN recovery without requiring an email address.
- **Réglages** — PIN rotation, recovery-code regeneration, private JSON export,
  suspended-card recovery, guarded progress reset, and account deletion.

### Card model

Importing the corpus produces:

| Type | Count | Front → Back |
|------|-------|--------------|
| Response spine | 130 | Prompt → compact speaking spine |
| Expression — production | 1,410 | English cue + blanked example → the expression |
| Subject vocabulary | 6,500 | English cue + source example → the French target |
| Comprehension vocabulary | 250 | English cue + source-linked example → the French target |
| Expression — recognition | 226 shared expressions | Expression → meaning + example |

Equivalent prompts (same answer in different themes) share one Response and one
spine card, so you never memorise the same answer twice. Response-local
vocabulary uses production cards only and appears from its response sheet;
shared production and recognition twins always remain in the same lot. Each
response also has five dedicated subject-vocabulary lots of 10 cards.
Each published written-comprehension test also has five source-linked lots of
10 cards. The complete imported library currently contains 8,516 learner cards.

---

## Run it locally

Requires Python 3.11 or newer (see `runtime.txt`).

```bash
cd flashcards

# 1. Virtual environment + dependencies
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# 2. Database + content
./.venv/bin/python manage.py migrate
./.venv/bin/python manage.py import_content

# 3. Serve
./.venv/bin/python manage.py runserver
```

Open http://127.0.0.1:8000/ and create an account with a unique username and
six-digit PIN. On an upgraded installation, the first account automatically
claims the existing study progress.

Optional — the Django admin (browse/edit raw data):

```bash
./.venv/bin/python manage.py createsuperuser
# then visit /admin/
```

---

## Content pipeline

The app ships a self-contained snapshot of the answer bank in
`study/content/` so it deploys without the rest of the repo.

- `import_content` — (re)builds the database from that snapshot. **Idempotent**:
  re-running upserts shared content and preserves every learner's private review
  progress. Source items that disappear are archived, not deleted, so their
  cards, review history, notes, highlights, and comprehension attempts remain
  intact. CE test metadata and source questions live in
  `study/content/comprehension/`; its source-linked vocabulary lives in
  `study/content/comprehension_vocabulary/`.
- `sync_content --from <path-to-t3>` — refreshes the snapshot from the live
  `t3/` tree (response batches, `study_sheets.md`, `anki/data/phrases.tsv`).
  The app-owned `subject_vocabulary/` bank is preserved. Run `import_content`
  afterwards to load the changes.

So the normal loop after editing the answer bank is:

```bash
./.venv/bin/python manage.py sync_content
./.venv/bin/python manage.py import_content
```

The identifiers in `study/content/themes.json` and the `id` column in
`study/content/phrases.tsv` are persistent database identities. Do not change
them when editing display text. Prompt identity is the theme slug plus prompt
number, so renumbering a prompt is a content migration rather than a wording
edit. Response edits keep the same row through that prompt identity; response
splits copy the prior schedule to the new card, while merges retain the most
recent schedule.

---

## Configuration

All configuration is via environment variables (or a local `.env`, never
committed). Copy the template:

```bash
cp .env.example .env
```

| Variable | Default | Purpose |
|----------|---------|---------|
| `SECRET_KEY` | dev key | **Set a strong value in production.** |
| `DEBUG` | `True` | Turn **off** in production. |
| `ALLOWED_HOSTS` | localhost | Comma-separated hostnames. |
| `CSRF_TRUSTED_ORIGINS` | — | Comma-separated `https://…` origins. |
| `TRUST_X_FORWARDED_FOR` | `False` | Trust the rightmost forwarded client address; enable only behind a trusted proxy. |
| `TRUSTED_PROXY_CIDRS` | — | Comma-separated trusted proxy networks to skip right-to-left when resolving the client address. |
| `TIME_ZONE` | `America/Los_Angeles` | Drives "due today" and streaks. |
| `DATABASE_URL` | — | PostgreSQL connection URL used in production. |
| `DATABASE_PATH` | `db.sqlite3` | Absolute path for the SQLite file. |

When `DEBUG=False`, security hardening (SSL redirect, HSTS, secure cookies,
nosniff, manifest+compressed static files via WhiteNoise) switches on
automatically.

---

## Deploy

The app is deployment-ready (`Procfile`, `runtime.txt`, WhiteNoise for static
files, gunicorn). On a Heroku-style platform:

```
web:     gunicorn config.wsgi --log-file -
release: python manage.py migrate --noinput && python manage.py import_content
```

Set at least `SECRET_KEY`, `DEBUG=False`, `DATABASE_URL`, `ALLOWED_HOSTS`, and
`CSRF_TRUSTED_ORIGINS`. Production should use persistent PostgreSQL storage;
SQLite remains the zero-configuration local-development default.

Take a restorable database backup before deploying migrations that alter the
content schema or importer. Run migrations and `import_content` exactly once per
release; the importer uses a PostgreSQL transaction lock to prevent overlap.
The included free-plan Render Blueprint runs both commands at process startup
before Gunicorn because Render pre-deploy commands are available only on paid
web services. Cold starts still perform Django's inexpensive migration check,
but `import_content --if-changed` fingerprints the bundled corpus and importer
code, so the full import runs only when a release actually changes them.

Static files for any non-Procfile host:

```bash
DEBUG=False ./.venv/bin/python manage.py collectstatic --noinput
```

---

## How the scheduler works

Anki-style SM-2 (`study/srs.py`):

- **Learning steps** 1 min → 10 min, then graduates to **1 day** (or **4 days**
  internally for the highest rating).
- The streamlined interface exposes two decisions: **Revoir** returns the card
  to learning; **Correct** advances it through the schedule.
- Review intervals scale with ease (start 2.5, floor 1.3), while a lapse sends
  the card to relearning and trims the interval.
- A card is **mature** once its interval reaches 21 days.

Every grade is written to `ReviewLog`, which powers the stats page.

---

## Project layout

```
flashcards/
├── config/                 # Django project (settings, urls, wsgi/asgi)
├── study/
│   ├── models.py           # Shared study content, comprehension quizzes,
│   │                       #   learner cards, attempts, history, and settings
│   ├── content.py          # Pure parser for the answer bank
│   ├── accounts.py · forms.py · middleware.py
│   │                       # Account provisioning, PIN auth, access control
│   ├── srs.py              # SM-2 scheduler
│   ├── queue.py            # Daily study queue (+ scope)
│   ├── cards.py            # Card → front/back presentation
│   ├── views.py            # All pages + AJAX review endpoints
│   ├── management/commands/ import_content, sync_content
│   ├── content/            # Self-contained answer-bank snapshot
│   ├── templates/ · static/
│   └── migrations/
├── requirements.txt · Procfile · runtime.txt · .env.example
└── manage.py
```

Shared learning content; private scheduling state and progress per account.
