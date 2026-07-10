# EO · Fiches de révision (French oral-exam flashcards)

A personal, single-user spaced-repetition web app for memorising a French
oral-exam answer bank. It parses the existing corpus — 167 exam prompts that
collapse into **130 unique argued answers** across 7 themes and 17 topic
families, plus **160 reusable expressions** — and turns everything into
Anki-style flashcards with a real SM-2 scheduler.

Built with Django. Clean, fast, keyboard-driven UI with light/dark themes.

---

## What's inside

- **Réviser** — a distraction-free review session. Reveal with `Space`, grade
  with `1`–`4` (Encore / Difficile / Correct / Facile). Each button shows the
  next interval before you click. Study everything, or scope to one theme,
  family, or expression category.
- **Parcourir** — browse by theme, by topic family, or by expression category.
- **Fiche** — every answer in full: reformulation, position, three structured
  arguments (idée / développement / exemple / conséquence), nuance, conclusion,
  equivalent prompts, and related expressions. Shows each card's SRS state.
- **Expressions** — 160 reusable chunks with an English cue and a grounded
  example (drawn from the answers themselves), grouped by function.
- **Stats** — 30-day review bars, 90-day activity heatmap, 14-day forecast,
  mature-card retention, streak, and per-theme mastery.
- **Réglages** — daily new-card / max-review limits, and a full reset.

### Card model

Importing the corpus produces **450 cards**:

| Type | Count | Front → Back |
|------|-------|--------------|
| Response spine | 130 | Prompt → full argued answer |
| Expression — production | 160 | English cue + blanked example → the expression |
| Expression — recognition | 160 | Expression → meaning + example |

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

Open http://127.0.0.1:8000/.

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
  re-running upserts content and *preserves your review progress* (cards are
  matched by natural keys, orphans pruned).
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
| `TIME_ZONE` | `America/Los_Angeles` | Drives "due today" and streaks. |
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

Set at least `SECRET_KEY`, `DEBUG=False`, `ALLOWED_HOSTS`, and
`CSRF_TRUSTED_ORIGINS`. Persist the SQLite file (point `DATABASE_PATH` at a
mounted volume) so review progress survives restarts.

Static files for any non-Procfile host:

```bash
DEBUG=False ./.venv/bin/python manage.py collectstatic --noinput
```

---

## How the scheduler works

Anki-style SM-2 (`study/srs.py`):

- **Learning steps** 1 min → 10 min, then graduates to **1 day** (or **4 days**
  on *Facile*).
- Reviews multiply by ease (start 2.5, floor 1.3); *Difficile* ×1.2, *Facile*
  gets a ×1.3 bonus.
- A lapse (*Encore*) sends the card to relearning (10 min) and trims the
  interval.
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

Personal study tool — no user accounts; scheduling state lives on the cards.
