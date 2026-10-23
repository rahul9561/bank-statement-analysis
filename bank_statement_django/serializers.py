"""DRF serializers for the upload request and the analysis response."""

from __future__ import annotations

from django.urls import reverse
from rest_framework import serializers

from .models import StatementAnalysis


class StatementUploadSerializer(serializers.Serializer):
    statement = serializers.FileField(allow_empty_file=False, use_url=False)
    #: Statement password for protected PDFs. Used once in memory; never stored or logged.
    password = serializers.CharField(required=False, allow_blank=True, write_only=True, max_length=128,
                                     trim_whitespace=False)
    #: Optional holder name(s) to recognise own-account transfers.
    holder_name = serializers.CharField(required=False, allow_blank=True, max_length=120)
    include_transactions = serializers.BooleanField(required=False, default=False)


class StatementAnalysisSerializer(serializers.ModelSerializer):
    report_url = serializers.SerializerMethodField()
    result_url = serializers.SerializerMethodField()

    class Meta:
        model = StatementAnalysis
        fields = ["report_id", "status", "reconciliation_status", "bank_code", "page_count", "transaction_count",
                  "summary", "validation", "created_at", "expires_at", "report_url", "result_url"]
        read_only_fields = fields

    def get_report_url(self, obj) -> str:
        return reverse("bank_statement_analysis:report-download", kwargs={"report_id": obj.report_id})

    def get_result_url(self, obj) -> str:
        return reverse("bank_statement_analysis:analysis-detail", kwargs={"report_id": obj.report_id})
