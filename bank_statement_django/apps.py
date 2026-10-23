from django.apps import AppConfig


class BankStatementDjangoConfig(AppConfig):
    name = "bank_statement_django"
    label = "bank_statement_django"
    verbose_name = "Bank statement analysis"
    default_auto_field = "django.db.models.BigAutoField"
