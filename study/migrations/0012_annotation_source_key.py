from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("study", "0011_reviewsession_previous_review"),
    ]

    operations = [
        migrations.AddField(
            model_name="annotation",
            name="source_key",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.RemoveConstraint(
            model_name="annotation",
            name="unique_user_page_highlight",
        ),
        migrations.AddConstraint(
            model_name="annotation",
            constraint=models.UniqueConstraint(
                condition=Q(kind="highlight"),
                fields=(
                    "user",
                    "source_path",
                    "source_key",
                    "start_offset",
                    "end_offset",
                ),
                name="unique_user_source_highlight",
            ),
        ),
    ]
