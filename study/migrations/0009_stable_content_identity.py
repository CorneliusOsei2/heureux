from django.db import migrations, models
import django.db.models.deletion


def _deduplicated(base, used, pk):
    candidate = base[:120]
    if candidate not in used:
        used.add(candidate)
        return candidate
    suffix = f":legacy-{pk}"
    candidate = f"{base[:120 - len(suffix)]}{suffix}"
    used.add(candidate)
    return candidate


def populate_content_keys(apps, schema_editor):
    Family = apps.get_model("study", "Family")
    PhraseCategory = apps.get_model("study", "PhraseCategory")
    Prompt = apps.get_model("study", "Prompt")
    Response = apps.get_model("study", "Response")

    used = set()
    for family in Family.objects.order_by("pk"):
        family.content_key = _deduplicated(
            f"family:{family.order:02d}",
            used,
            family.pk,
        )
        family.save(update_fields=["content_key"])

    used = set()
    for category in PhraseCategory.objects.order_by("pk"):
        category.content_key = _deduplicated(
            f"phrase-category:{category.slug}",
            used,
            category.pk,
        )
        category.save(update_fields=["content_key"])

    used = set()
    for prompt in Prompt.objects.select_related("theme").order_by("pk"):
        prompt.content_key = _deduplicated(
            f"{prompt.theme.slug}:p{prompt.number}",
            used,
            prompt.pk,
        )
        prompt.save(update_fields=["content_key"])

    used = set()
    for response in Response.objects.order_by("pk"):
        prompt = (
            response.prompts.filter(is_canonical=True).order_by("pk").first()
            or response.prompts.order_by("pk").first()
        )
        base = (
            prompt.content_key
            if prompt and prompt.content_key
            else f"legacy-response:{response.pk}"
        )
        response.content_key = _deduplicated(base, used, response.pk)
        response.save(update_fields=["content_key"])


class Migration(migrations.Migration):
    dependencies = [
        ("study", "0008_reviewsession_previous_card_annotation_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="exampart",
            name="is_active",
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AddField(
            model_name="task",
            name="is_active",
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AddField(
            model_name="theme",
            name="is_active",
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AddField(
            model_name="family",
            name="content_key",
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name="family",
            name="is_active",
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AddField(
            model_name="response",
            name="content_key",
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name="response",
            name="is_active",
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AlterField(
            model_name="response",
            name="body_hash",
            field=models.CharField(db_index=True, max_length=64),
        ),
        migrations.AddField(
            model_name="prompt",
            name="content_key",
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name="prompt",
            name="is_active",
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AddField(
            model_name="phrasecategory",
            name="content_key",
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
        migrations.AddField(
            model_name="phrasecategory",
            name="is_active",
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AddField(
            model_name="phrase",
            name="is_active",
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AlterField(
            model_name="annotation",
            name="task",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="annotations",
                to="study.task",
            ),
        ),
        migrations.RunPython(
            populate_content_keys,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="family",
            name="content_key",
            field=models.CharField(max_length=120, unique=True),
        ),
        migrations.AlterField(
            model_name="response",
            name="content_key",
            field=models.CharField(max_length=120, unique=True),
        ),
        migrations.AlterField(
            model_name="prompt",
            name="content_key",
            field=models.CharField(max_length=120, unique=True),
        ),
        migrations.AlterField(
            model_name="phrasecategory",
            name="content_key",
            field=models.CharField(max_length=120, unique=True),
        ),
    ]
