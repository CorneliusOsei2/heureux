from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.db import migrations


PART_CODE_ALIASES = {
    "oral": "eo",
    "orale": "eo",
    "ecrit": "ee",
    "ecrite": "ee",
}
COMPREHENSION_CODE_ALIASES = {
    "ecrite": "ce",
    "orale": "co",
}
SOURCE_PATH_ALIASES = (
    ("/comprehension-ecrite/", "/comprehension/ce/"),
    ("/comprehension-orale/", "/comprehension/co/"),
    ("/expression/ecrite/", "/expression/ee/"),
    ("/expression/ecrit/", "/expression/ee/"),
    ("/expression/orale/", "/expression/eo/"),
    ("/expression/oral/", "/expression/eo/"),
    ("/epreuve/ecrite/", "/expression/ee/"),
    ("/epreuve/ecrit/", "/expression/ee/"),
    ("/epreuve/orale/", "/expression/eo/"),
    ("/epreuve/oral/", "/expression/eo/"),
    ("/epreuve/ee/", "/expression/ee/"),
    ("/epreuve/eo/", "/expression/eo/"),
)
HIGHLIGHT_TEXT_FIELDS = (
    "title",
    "body",
    "quote",
    "source_title",
    "prefix",
    "suffix",
)


def canonical_source_path(value):
    parsed = urlsplit(value)
    path = parsed.path
    changed = False
    for old_prefix, new_prefix in SOURCE_PATH_ALIASES:
        if path.startswith(old_prefix):
            path = new_prefix + path[len(old_prefix) :]
            changed = True
            break

    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    canonical_items = []
    for key, item_value in query_items:
        canonical_value = item_value
        if key == "part":
            canonical_value = PART_CODE_ALIASES.get(item_value, item_value)
        elif key == "mode":
            canonical_value = COMPREHENSION_CODE_ALIASES.get(
                item_value,
                item_value,
            )
        changed = changed or canonical_value != item_value
        canonical_items.append((key, canonical_value))

    if not changed:
        return value
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            path,
            urlencode(canonical_items),
            parsed.fragment,
        )
    )


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


def migrate_skill_codes(apps, schema_editor):
    ExamPart = apps.get_model("study", "ExamPart")
    ReviewSession = apps.get_model("study", "ReviewSession")
    Annotation = apps.get_model("study", "Annotation")

    for old_slug, new_slug in PART_CODE_ALIASES.items():
        part = ExamPart.objects.filter(slug=old_slug).first()
        if part is None:
            continue
        if ExamPart.objects.filter(slug=new_slug).exclude(pk=part.pk).exists():
            raise RuntimeError(
                f"Cannot rename expression part {old_slug!r}: "
                f"{new_slug!r} already exists."
            )
        part.slug = new_slug
        part.short_name = new_slug.upper()
        part.save(update_fields=["slug", "short_name"])

    for session in ReviewSession.objects.all().iterator():
        scope = session.scope
        if not isinstance(scope, dict):
            continue
        old_part = scope.get("part")
        new_part = PART_CODE_ALIASES.get(old_part, old_part)
        if new_part != old_part:
            session.scope = {**scope, "part": new_part}
            session.save(update_fields=["scope"])

    for annotation in Annotation.objects.all().iterator():
        source_path = canonical_source_path(annotation.source_path)
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
        ("study", "0023_comprehension_test_mode"),
    ]

    operations = [
        migrations.RunPython(
            migrate_skill_codes,
            migrations.RunPython.noop,
        ),
    ]
