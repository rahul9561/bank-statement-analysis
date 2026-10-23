from django.core.management.base import BaseCommand

from bank_statement_django.services import purge_expired


class Command(BaseCommand):
    help = "Delete statement analyses and report PDFs past their retention period (RETENTION_HOURS)."

    def handle(self, *args, **options):
        removed = purge_expired()
        self.stdout.write(self.style.SUCCESS(f"Removed {removed} expired analysis record(s)."))
