"""URLconf used by the Django tests (not a test module)."""

from django.urls import include, path

urlpatterns = [path("api/bank-statement-analysis/", include("bank_statement_django.urls"))]

__test__ = False
