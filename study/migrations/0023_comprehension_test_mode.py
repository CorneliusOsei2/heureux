from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("study", "0022_comprehension_vocabulary"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="comprehensiontest",
            options={"ordering": ["mode", "order", "number"]},
        ),
        migrations.AddField(
            model_name="comprehensiontest",
            name="mode",
            field=models.CharField(
                choices=[("ecrite", "Écrite"), ("orale", "Orale")],
                default="ecrite",
                max_length=8,
            ),
        ),
        migrations.AlterField(
            model_name="comprehensiontest",
            name="number",
            field=models.PositiveSmallIntegerField(),
        ),
        migrations.AddConstraint(
            model_name="comprehensiontest",
            constraint=models.UniqueConstraint(
                fields=("mode", "number"),
                name="unique_comprehension_mode_test_number",
            ),
        ),
    ]
