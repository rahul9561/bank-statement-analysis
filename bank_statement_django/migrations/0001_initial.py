import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="StatementAnalysis",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("report_id", models.CharField(db_index=True, max_length=40, unique=True)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("completed", "Completed"), ("failed", "Failed")],
                                            default="pending", max_length=16)),
                ("reconciliation_status", models.CharField(blank=True, max_length=20)),
                ("bank_code", models.CharField(blank=True, max_length=20)),
                ("page_count", models.PositiveIntegerField(default=0)),
                ("transaction_count", models.PositiveIntegerField(default=0)),
                ("source_sha256", models.CharField(blank=True, max_length=64)),
                ("summary", models.JSONField(blank=True, default=dict)),
                ("validation", models.JSONField(blank=True, default=dict)),
                ("result", models.JSONField(blank=True, default=dict)),
                ("report_file", models.CharField(blank=True, max_length=255)),
                ("error_code", models.CharField(blank=True, max_length=40)),
                ("error_message", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("expires_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                            related_name="statement_analyses", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "statement analysis",
                "verbose_name_plural": "statement analyses",
                "ordering": ["-created_at"],
            },
        ),
    ]
