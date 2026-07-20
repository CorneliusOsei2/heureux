from django.db import migrations


PART_ICONS = {
    "🎙️": "microphone",
    "🎙": "microphone",
    "✍️": "pen-line",
    "✍": "pen-line",
    "📝": "file-text",
}

TASK_ICONS = {
    "👋": "hand-wave",
    "🗨️": "messages",
    "🗨": "messages",
    "🎯": "target",
    "✉️": "mail",
    "✉": "mail",
    "📝": "file-text",
    "⚖️": "scale",
    "⚖": "scale",
}

THEME_ICONS = {
    "🎭": "theater",
    "👪": "users",
    "🎓": "graduation-cap",
    "🩺": "stethoscope",
    "💻": "laptop",
    "🌍": "globe",
    "💶": "euro",
    "📘": "book-open",
}


def backfill_icon_names(apps, schema_editor):
    for model_name, mapping in (
        ("ExamPart", PART_ICONS),
        ("Task", TASK_ICONS),
        ("Theme", THEME_ICONS),
    ):
        model = apps.get_model("study", model_name)
        for old_value, icon_name in mapping.items():
            model.objects.filter(icon=old_value).update(icon=icon_name)


def restore_emoji_values(apps, schema_editor):
    for model_name, mapping in (
        ("ExamPart", PART_ICONS),
        ("Task", TASK_ICONS),
        ("Theme", THEME_ICONS),
    ):
        model = apps.get_model("study", model_name)
        for old_value, icon_name in mapping.items():
            model.objects.filter(icon=icon_name).update(icon=old_value)


class Migration(migrations.Migration):
    dependencies = [
        ("study", "0030_rename_emoji_fields_to_icon"),
    ]

    operations = [
        migrations.RunPython(backfill_icon_names, restore_emoji_values),
    ]
