# Heureux · Fiches de révision (French oral-exam flashcards)

A multi-user spaced-repetition web app for memorising a French oral-exam answer
bank. It parses the existing corpus — 167 exam prompts that collapse into
**130 unique argued answers** across 7 themes and 17 topic families, plus a
source-grounded bank of reusable expressions — and turns everything into
Anki-style flashcards with an SM-2 scheduler.

Built with Django. Clean, fast, keyboard-driven UI with light/dark themes.

---

## What's inside

- **Expression orale → Tâche 3** — the current corpus has one clear home. Its
  subjects, argued responses, expression bank, review queue, revisit list,
  search, and progress are all scoped to this task.
- **Réviser, Expressions, Notes, Stats** — each top-level tab first follows the same
  Expression orale/écrite → Tâches hierarchy as Accueil, then opens the chosen
  task's focused workspace.
- **Sessions** — review responses, expressions, or the revisit list without
  distractions. Reveal with `Space`, then choose `1` (Revoir) or `2` (Correct).
  Unfinished sessions reopen on the exact card where you stopped, and the
  immediately preceding card remains available in a read-only view.
- **À revoir** — a persistent list for difficult cards, with its own focused
  review pass.
- **Sujets & réponses** — browse Tâche 3 by theme or topic family.
- **Fiche complète** — the learning view includes the reformulation, position,
  each argument's development, concrete example and consequence, then the
  nuance, conclusion, equivalent prompts, and related expressions. Flashcard
  practice deliberately keeps only each argument's main idea.
- **Expressions & vocabulaire** — reusable chunks with an English cue and a
  verbatim example from the answer bank, grouped into accurate topical and
  functional categories, then divided into review lots of 15 cards.
- **Private notes & highlights** — notes follow the same Expression
  orale/écrite → Tâche hierarchy, with a dedicated highlights subsection.
  Select text anywhere to copy it, translate it, save it to Notes, or highlight
  it persistently. Translation uses the browser's local English model with an
  explicit Google Translate fallback when local translation is unavailable.
- **Practice without a daily cap** — every new card and due review stays
  available; themes and expression categories provide optional 15-card lots
  with not-started, in-progress, and completed states plus next-lot navigation.
- **Progression** — 30-day review bars, 90-day activity heatmap, 14-day forecast,
  mature-card retention, streak, and per-theme mastery.
- **Comptes privés** — a unique username and hashed six-digit PIN protect each
  learner's cards, history, revisit list, and resumable session.
- **Réglages** — suspended-card recovery and private progress reset.

### Card model

Importing the corpus produces:

| Type | Count | Front → Back |
|------|-------|--------------|
| Response spine | 130 | Prompt → compact speaking spine |
| Expression — production | One per expression | English cue + blanked example → the expression |
| Expression — recognition | One per expression | Expression → meaning + example |

Equivalent prompts (same answer in different themes) share one Response and one
spine card, so you never memorise the same answer twice.

---

## Run it locally

Requires Python 3.11 (see `runtime.txt`; 3.9+ works too).

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
  progress (cards are matched by natural keys and orphans are pruned).
- `sync_content --from <path-to-t3>` — refreshes the snapshot from the live
  `t3/` tree (response batches, `study_sheets.md`, `anki/data/phrases.tsv`).
  Run `import_content` afterwards to load the changes.

So the normal loop after editing the answer bank is:

```bash
./.venv/bin/python manage.py sync_content
./.venv/bin/python manage.py import_content
```

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
│   ├── models.py           # Theme, Family, Response, Argument, Prompt,
│   │                       #   Phrase, Card, ReviewLog, Settings
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
