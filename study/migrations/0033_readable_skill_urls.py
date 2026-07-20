from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.db import migrations


FORWARD_ROOTS = (
    ("/eo/", "/expression/orale/"),
    ("/ee/", "/expression/ecrite/"),
    ("/ce/", "/comprehension/ecrite/"),
    ("/co/", "/comprehension/orale/"),
)
REVERSE_ROOTS = tuple((new, old) for old, new in FORWARD_ROOTS)

HIGHLIGHT_TEXT_FIELDS = (
    "title",
    "body",
    "quote",
    "source_title",
    "prefix",
    "suffix",
)


def rewrite_public_url(value, replacements, *, normalize_next=True):
    parsed = urlsplit(value)
    path = parsed.path
    for old_root, new_root in replacements:
        if path.startswith(old_root):
            path = f"{new_root}{path[len(old_root):]}"
            break
        if path == old_root.rstrip("/"):
            path = new_root
            break

    items = parse_qsl(parsed.query, keep_blank_values=True)
    if normalize_next:
        items = [
            (
                key,
                rewrite_public_url(
                    item_value,
                    replacements,
                    normalize_next=False,
                )
                if key == "next" and item_value.startswith("/")
                else item_value,
            )
            for key, item_value in items
        ]

    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            path,
            urlencode(items),
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


def migrate_annotation_paths(apps, replacements):
    Annotation = apps.get_model("study", "Annotation")
    for annotation in Annotation.objects.all().iterator():
        source_path = rewrite_public_url(annotation.source_path, replacements)
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


def use_readable_skill_urls(apps, schema_editor):
    migrate_annotation_paths(apps, FORWARD_ROOTS)


def restore_skill_code_urls(apps, schema_editor):
    migrate_annotation_paths(apps, REVERSE_ROOTS)


class Migration(migrations.Migration):
    dependencies = [
        ("study", "0032_memory_question_progress"),
    ]

    operations = [
        migrations.RunPython(
            use_readable_skill_urls,
            restore_skill_code_urls,
        ),
    ]
