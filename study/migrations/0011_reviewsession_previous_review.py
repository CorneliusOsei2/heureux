from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("study", "0010_accountrecoverycode"),
    ]

    operations = [
        migrations.AddField(
            model_name="reviewsession",
            name="previous_review",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="study.reviewlog",
            ),
        ),
    ]
