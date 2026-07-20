import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.db import migrations


HIGHLIGHT_TEXT_FIELDS = (
    "title",
    "body",
    "quote",
    "source_title",
    "prefix",
    "suffix",
)

ACCOUNT_PATHS = {
    "/login/": "/compte/connexion/",
    "/register/": "/compte/inscription/",
    "/recover/": "/compte/recuperation/",
    "/recovery-codes/": "/compte/codes-recuperation/",
    "/logout/": "/compte/deconnexion/",
    "/settings/": "/compte/parametres/",
    "/settings/pin/": "/compte/parametres/pin/",
    "/settings/recovery-codes/": (
        "/compte/parametres/codes-recuperation/"
    ),
    "/settings/progress/reset/": (
        "/compte/parametres/progression/reinitialiser/"
    ),
    "/settings/export/": "/compte/parametres/exporter/",
    "/settings/account/delete/": "/compte/parametres/supprimer/",
}

REVIEW_ENDPOINT_PATHS = {
    "/review/next/": "/revision/suivante/",
    "/review/previous/": "/revision/precedente/",
    "/review/answer/": "/revision/repondre/",
    "/review/undo/": "/revision/annuler/",
}


def _query_parts(parsed):
    items = parse_qsl(parsed.query, keep_blank_values=True)
    values = {}
    for key, value in items:
        values.setdefault(key, value)
    return items, values


def _rebuilt(parsed, path, items, *, drop=()):
    drop = set(drop)
    query = urlencode(
        [(key, value) for key, value in items if key not in drop]
    )
    return urlunsplit(
        (parsed.scheme, parsed.netloc, path, query, parsed.fragment)
    )


def _prompt_path(prompt):
    task = prompt.theme.task
    if task is None or task.part.slug not in {"eo", "ee"}:
        return None
    return f"/{task.part.slug}/{task.slug}/sujets/{prompt.pk}/"


def _response_path(Prompt, response_id):
    prompts = Prompt.objects.filter(
        response_id=response_id,
        is_active=True,
        theme__task__isnull=False,
    ).select_related("theme__task__part")
    prompt = prompts.filter(is_canonical=True).first() or prompts.first()
    return _prompt_path(prompt) if prompt is not None else None


def _test_skill(ComprehensionTest, test_slug):
    mode = (
        ComprehensionTest.objects.filter(slug=test_slug)
        .values_list("mode", flat=True)
        .first()
    )
    return {"ecrite": "ce", "orale": "co"}.get(mode)


def canonical_source_path(
    value,
    *,
    Prompt,
    Theme,
    Family,
    ComprehensionTest,
    normalize_next=True,
):
    parsed = urlsplit(value)
    path = parsed.path
    items, query = _query_parts(parsed)

    path = ACCOUNT_PATHS.get(path, path)
    path = REVIEW_ENDPOINT_PATHS.get(path, path)
    path = {
        "/expressions/": "/vocabulaire/",
        "/reviser/": "/review/",
    }.get(path, path)

    if path == "/browse/":
        if query.get("part") and query.get("task"):
            path = f"/{query['part']}/{query['task']}/sujets/"
        else:
            path = "/expression/"
        items = [
            (key, item_value)
            for key, item_value in items
            if key not in {"part", "task"}
        ]

    response_match = re.fullmatch(r"/response/(?P<pk>\d+)/", path)
    if response_match:
        canonical = _response_path(Prompt, int(response_match.group("pk")))
        if canonical:
            return _rebuilt(
                parsed,
                canonical,
                items,
                drop={"prompt", "saved", "reset"},
            )

    theme_match = re.fullmatch(r"/theme/(?P<slug>[-\w]+)/", path)
    if theme_match:
        theme = (
            Theme.objects.filter(
                slug=theme_match.group("slug"),
                task__isnull=False,
            )
            .select_related("task__part")
            .first()
        )
        if theme and theme.task.part.slug in {"eo", "ee"}:
            path = (
                f"/{theme.task.part.slug}/{theme.task.slug}/"
                f"themes/{theme.slug}/"
            )

    family_match = re.fullmatch(r"/family/(?P<slug>[-\w]+)/", path)
    if family_match:
        family = Family.objects.filter(slug=family_match.group("slug")).first()
        prompt = (
            Prompt.objects.filter(
                family=family,
                is_active=True,
                theme__task__isnull=False,
            )
            .select_related("theme__task__part")
            .order_by("theme__task__part__order", "theme__task__order", "pk")
            .first()
            if family
            else None
        )
        if prompt:
            task = prompt.theme.task
            path = (
                f"/{task.part.slug}/{task.slug}/familles/{family.slug}/"
            )

    expression_match = re.fullmatch(
        r"/expression/(?P<part>eo|ee)/"
        r"(?:(?P<task>[-\w]+)/)?(?P<tail>.*)",
        path,
    )
    if expression_match:
        part = expression_match.group("part")
        task = expression_match.group("task")
        tail = expression_match.group("tail")
        if not task:
            path = f"/{part}/"
        else:
            if tail == "expressions/" and query.get("category"):
                tail = (
                    "vocabulaire/categories/"
                    f"{query['category']}/"
                )
            else:
                tail = re.sub(r"^expressions/$", "vocabulaire/", tail)
            tail = re.sub(r"^reviser/$", "revision/", tail)
            tail = re.sub(r"^a-revoir/$", "revision/a-revoir/", tail)
            tail = re.sub(r"^famille/", "familles/", tail)
            path = f"/{part}/{task}/{tail}"
        items = [
            (key, item_value)
            for key, item_value in items
            if key not in {"part", "task", "category"}
        ]

    comprehension_match = re.fullmatch(
        r"/comprehension/(?P<skill>ce|co)/(?P<tail>.*)",
        path,
    )
    if comprehension_match:
        skill = comprehension_match.group("skill")
        tail = comprehension_match.group("tail")
        if not tail:
            path = f"/{skill}/"
        elif tail == "vocabulaire/":
            path = f"/{skill}/vocabulaire/"
        else:
            tail = re.sub(r"^groupe/", "groupes/", tail)
            tail = re.sub(
                r"^(?P<test>[-\w]+)/question/",
                r"tests/\g<test>/questions/",
                tail,
            )
            tail = re.sub(
                r"^(?P<test>[-\w]+)/commencer/$",
                r"tests/\g<test>/commencer/",
                tail,
            )
            tail = re.sub(
                r"^(?P<test>[-\w]+)/tentative/(?P<attempt>\d+)/question/",
                r"tests/\g<test>/tentatives/\g<attempt>/questions/",
                tail,
            )
            tail = re.sub(
                r"^(?P<test>[-\w]+)/tentative/(?P<attempt>\d+)/resultats/$",
                r"tests/\g<test>/tentatives/\g<attempt>/resultats/",
                tail,
            )
            if not tail.startswith(("groupes/", "tests/")):
                tail = f"tests/{tail}"
            path = f"/{skill}/{tail}"
        items = [
            (key, item_value)
            for key, item_value in items
            if key != "mode"
        ]

    task_notes_match = re.fullmatch(
        r"/notes/(?P<part>eo|ee)/(?P<task>[-\w]+)/(?P<study>etudier/)?",
        path,
    )
    if task_notes_match:
        path = (
            f"/{task_notes_match.group('part')}/"
            f"{task_notes_match.group('task')}/notes/"
            f"{task_notes_match.group('study') or ''}"
        )
    elif path in {"/notes/general/", "/notes/"} and (
        path == "/notes/general/" or query.get("scope") == "general"
    ):
        path = "/notes/generales/"
        items = [
            (key, item_value)
            for key, item_value in items
            if key != "scope"
        ]
    elif path == "/notes/etudier/" and query.get("scope") == "general":
        path = "/notes/generales/etudier/"
        items = [
            (key, item_value)
            for key, item_value in items
            if key != "scope"
        ]
    elif (
        path == "/notes/etudier/"
        and query.get("part")
        and query.get("task")
    ):
        path = (
            f"/{query['part']}/{query['task']}/"
            "notes/etudier/"
        )
        items = [
            (key, item_value)
            for key, item_value in items
            if key not in {"part", "task"}
        ]
    elif path == "/notes/" and query.get("part") and query.get("task"):
        path = f"/{query['part']}/{query['task']}/notes/"
        items = [
            (key, item_value)
            for key, item_value in items
            if key not in {"part", "task"}
        ]

    if path in {"/vocabulaire/", "/phrases/"}:
        part = query.get("part")
        task = query.get("task")
        category = query.get("category")
        test_slug = query.get("test")
        if part and task and category:
            path = f"/{part}/{task}/vocabulaire/categories/{category}/"
        elif part and task:
            path = f"/{part}/{task}/vocabulaire/"
        elif test_slug:
            skill = _test_skill(ComprehensionTest, test_slug)
            if skill:
                path = f"/{skill}/tests/{test_slug}/vocabulaire/"
        elif category:
            path = f"/vocabulaire/categories/{category}/"
        elif query.get("domain") == "comprehension":
            skill = {
                "ce": "ce",
                "co": "co",
                "ecrite": "ce",
                "orale": "co",
            }.get(query.get("mode"))
            if skill:
                path = f"/{skill}/vocabulaire/"
        else:
            path = "/vocabulaire/"
        items = [
            (key, item_value)
            for key, item_value in items
            if key
            not in {"part", "task", "category", "test", "domain", "mode"}
        ]

    if path in {"/search/", "/recherche/"}:
        if query.get("part") and query.get("task"):
            path = f"/{query['part']}/{query['task']}/recherche/"
        else:
            path = "/recherche/"
        items = [
            (key, item_value)
            for key, item_value in items
            if key not in {"part", "task"}
        ]

    if path in {"/stats/", "/progression/"}:
        if query.get("part") and query.get("task"):
            path = f"/{query['part']}/{query['task']}/progression/"
        elif query.get("part"):
            path = f"/{query['part']}/progression/"
        else:
            path = "/progression/"
        items = [
            (key, item_value)
            for key, item_value in items
            if key not in {"part", "task"}
        ]

    if path == "/review/":
        part = query.get("part")
        task = query.get("task")
        test_slug = query.get("test")
        if part and task:
            path = f"/{part}/{task}/revision/cartes/"
        elif part:
            path = f"/{part}/revision/"
        elif test_slug:
            skill = _test_skill(ComprehensionTest, test_slug)
            if skill:
                path = f"/{skill}/tests/{test_slug}/vocabulaire/revision/"
        else:
            path = "/revision/"
        items = [
            (key, item_value)
            for key, item_value in items
            if key not in {"part", "task", "test"}
        ]
    elif path == "/revisit/":
        part = query.get("part")
        task = query.get("task")
        if part and task:
            path = f"/{part}/{task}/revision/a-revoir/"
        elif part:
            path = f"/{part}/revision/a-revoir/"
        else:
            path = "/revision/a-revoir/"
        items = [
            (key, item_value)
            for key, item_value in items
            if key not in {"part", "task"}
        ]

    if normalize_next:
        items = [
            (
                key,
                canonical_source_path(
                    item_value,
                    Prompt=Prompt,
                    Theme=Theme,
                    Family=Family,
                    ComprehensionTest=ComprehensionTest,
                    normalize_next=False,
                )
                if key == "next" and item_value.startswith("/")
                else item_value,
            )
            for key, item_value in items
        ]

    return _rebuilt(parsed, path, items)


def merge_highlights(Annotation, survivor, duplicate):
    newer, older = (
        (duplicate, survivor)
        if duplicate.updated_at > survivor.updated_at
        else (survivor, duplicate)
    )
    updates = {
        field: getattr(newer, field) or getattr(older, field)
        for field in HIGHLIGHT_TEXT_FIELDS
    }
    updates.update(
        {
            "task_id": newer.task_id or older.task_id,
            "study_later": survivor.study_later or duplicate.study_later,
            "created_at": min(survivor.created_at, duplicate.created_at),
            "updated_at": max(survivor.updated_at, duplicate.updated_at),
        }
    )
    Annotation.objects.filter(pk=survivor.pk).update(**updates)
    duplicate.delete()


def migrate_public_urls(apps, schema_editor):
    Annotation = apps.get_model("study", "Annotation")
    Prompt = apps.get_model("study", "Prompt")
    Theme = apps.get_model("study", "Theme")
    Family = apps.get_model("study", "Family")
    ComprehensionTest = apps.get_model("study", "ComprehensionTest")

    for annotation in Annotation.objects.all().iterator():
        source_path = canonical_source_path(
            annotation.source_path,
            Prompt=Prompt,
            Theme=Theme,
            Family=Family,
            ComprehensionTest=ComprehensionTest,
        )
        if source_path == annotation.source_path:
            continue
        duplicate = None
        if annotation.kind == "highlight":
            duplicate = (
                Annotation.objects.filter(
                    kind="highlight",
                    user_id=annotation.user_id,
                    source_path=source_path,
                    source_key=annotation.source_key,
                    start_offset=annotation.start_offset,
                    end_offset=annotation.end_offset,
                )
                .exclude(pk=annotation.pk)
                .first()
            )
        if duplicate:
            merge_highlights(Annotation, duplicate, annotation)
        else:
            Annotation.objects.filter(pk=annotation.pk).update(
                source_path=source_path
            )


class Migration(migrations.Migration):
    dependencies = [
        ("study", "0026_backfill_card_started_at"),
    ]

    operations = [
        migrations.RunPython(
            migrate_public_urls,
            migrations.RunPython.noop,
        ),
    ]
