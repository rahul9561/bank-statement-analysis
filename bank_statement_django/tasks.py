"""Optional Celery integration for asynchronous analysis.

The engine is synchronous and reusable; for very large statements a project can
run the same service in a worker. Celery and a broker are *not* required by this
app. If Celery is installed, ``analyze_statement_task`` is registered as a shared task.

Usage (in the upload view, instead of calling the service inline)::

    record = StatementAnalysis.objects.create(report_id=..., owner=request.user, status="pending")
    analyze_statement_task.delay(user_id, temp_file_token)

The task receives a reference to bytes kept in private, short-lived storage
(never the raw bytes over the broker, which may not be encrypted).
"""

from __future__ import annotations

from pathlib import Path

try:  # pragma: no cover - exercised only when Celery is configured
    from celery import shared_task
except ImportError:  # pragma: no cover
    shared_task = None


def run_analysis_from_private_file(user_id: int, private_path: str, password: str | None = None) -> str:
    """Analyse a statement stored in private storage, then delete the uploaded copy. Returns the report_id."""
    from django.contrib.auth import get_user_model

    from . import services

    path = services.safe_report_path(private_path)
    try:
        data = Path(path).read_bytes()
        user = get_user_model().objects.get(pk=user_id)
        record = services.analyze_statement(user, data, password=password)
        return record.report_id
    finally:
        Path(path).unlink(missing_ok=True)


if shared_task is not None:  # pragma: no cover
    analyze_statement_task = shared_task(name="bank_statement_django.analyze_statement")(run_analysis_from_private_file)
