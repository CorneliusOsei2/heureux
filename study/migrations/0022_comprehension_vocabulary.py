from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("study", "0021_pin_comprehension_attempt_content"),
    ]

    operations = [
        migrations.AlterField(
            model_name="phrase",
            name="tier",
            field=models.CharField(
                choices=[
                    ("shared", "Shared catalog"),
                    ("response", "Response vocabulary"),
                    ("subject", "Subject vocabulary"),
                    ("comprehension", "Comprehension vocabulary"),
                ],
                db_index=True,
                default="response",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="phrase",
            name="source_questions",
            field=models.ManyToManyField(
                blank=True,
                related_name="vocabulary",
                to="study.comprehensionquestion",
            ),
        ),
    ]
