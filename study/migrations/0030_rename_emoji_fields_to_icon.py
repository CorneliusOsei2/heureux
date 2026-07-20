from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("study", "0029_backfill_response_practice_started_at"),
    ]

    operations = [
        migrations.RenameField(
            model_name="exampart",
            old_name="emoji",
            new_name="icon",
        ),
        migrations.RenameField(
            model_name="task",
            old_name="emoji",
            new_name="icon",
        ),
        migrations.RenameField(
            model_name="theme",
            old_name="emoji",
            new_name="icon",
        ),
        migrations.AlterField(
            model_name="exampart",
            name="icon",
            field=models.CharField(default="file-text", max_length=32),
        ),
        migrations.AlterField(
            model_name="task",
            name="icon",
            field=models.CharField(default="target", max_length=32),
        ),
        migrations.AlterField(
            model_name="theme",
            name="icon",
            field=models.CharField(default="book-open", max_length=32),
        ),
    ]
