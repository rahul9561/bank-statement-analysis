from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/bank-statement-analysis/", include("bank_statement_django.urls")),
    path("api-auth/", include("rest_framework.urls")),
]
