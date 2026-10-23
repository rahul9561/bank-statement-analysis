from django.contrib import admin

from .models import StatementAnalysis


@admin.register(StatementAnalysis)
class StatementAnalysisAdmin(admin.ModelAdmin):
    list_display = ("report_id", "owner", "status", "reconciliation_status", "bank_code", "transaction_count",
                    "created_at", "expires_at")
    list_filter = ("status", "reconciliation_status", "bank_code")
    search_fields = ("report_id",)
    # Derived financial data is read-only in the admin; the stored JSON is not shown in full.
    readonly_fields = [f.name for f in StatementAnalysis._meta.fields if f.name != "result"]
    exclude = ("result",)

    def has_add_permission(self, request):
        return False
