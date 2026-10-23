from django.urls import path

from . import views

app_name = "bank_statement_analysis"

urlpatterns = [
    path("analyze/", views.AnalyzeStatementView.as_view(), name="analyze"),
    path("analyses/", views.AnalysisListView.as_view(), name="analysis-list"),
    path("analyses/<str:report_id>/", views.AnalysisDetailView.as_view(), name="analysis-detail"),
    path("reports/<str:report_id>/", views.ReportDownloadView.as_view(), name="report-download"),
]
