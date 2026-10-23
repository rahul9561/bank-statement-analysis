"""Persistence for analysis runs.

Only derived data is stored: the analysis summary/result JSON and the generated
report PDF (on private storage). The uploaded statement itself is never saved.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class StatementAnalysis(models.Model):
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_PENDING = "pending"
    STATUS_CHOICES = [(STATUS_PENDING, "Pending"), (STATUS_COMPLETED, "Completed"), (STATUS_FAILED, "Failed")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    report_id = models.CharField(max_length=40, unique=True, db_index=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="statement_analyses")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    reconciliation_status = models.CharField(max_length=20, blank=True)
    bank_code = models.CharField(max_length=20, blank=True)
    page_count = models.PositiveIntegerField(default=0)
    transaction_count = models.PositiveIntegerField(default=0)
    source_sha256 = models.CharField(max_length=64, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    validation = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    #: Path relative to the private storage directory. Never exposed through the API.
    report_file = models.CharField(max_length=255, blank=True)
    error_code = models.CharField(max_length=40, blank=True)
    error_message = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "statement analysis"
        verbose_name_plural = "statement analyses"

    def __str__(self) -> str:
        return f"{self.report_id} ({self.status})"

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at and self.expires_at <= timezone.now())
