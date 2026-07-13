from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('study', '0006_reviewsession_presentation_token_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='LoginThrottle',
            fields=[
                ('key_hash', models.CharField(max_length=64, primary_key=True, serialize=False)),
                ('failures', models.PositiveSmallIntegerField(default=0)),
                ('window_started_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('locked_until', models.DateTimeField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True, db_index=True)),
            ],
        ),
        migrations.AddField(
            model_name='card',
            name='user',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='study_cards', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='reviewlog',
            name='user',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='study_review_logs', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='reviewsession',
            name='user',
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='review_session', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='settings',
            name='user',
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='study_settings', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddIndex(
            model_name='card',
            index=models.Index(fields=['user', 'state', 'due'], name='study_card_user_id_31524d_idx'),
        ),
        migrations.AddConstraint(
            model_name='card',
            constraint=models.UniqueConstraint(fields=('user', 'card_type', 'response'), name='unique_user_response_card'),
        ),
        migrations.AddConstraint(
            model_name='card',
            constraint=models.UniqueConstraint(fields=('user', 'card_type', 'phrase'), name='unique_user_phrase_card'),
        ),
    ]
