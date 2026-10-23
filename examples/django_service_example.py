"""Using the engine from your own Django service function, without the bundled app.

Drop this into any Django project (e.g. ``loans/services/statements.py``). Only
the framework-independent ``bank_statement_engine`` package is required.
"""

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile

from bank_statement_engine import BankStatementAnalyzer, BankStatementError

_analyzer = BankStatementAnalyzer(getattr(settings, "BANK_STATEMENT_ENGINE_CONFIG", {}))


def analyse_upload(upload: UploadedFile, *, password: str | None = None) -> dict:
    """Analyse an uploaded statement and return JSON-safe data plus the report PDF bytes."""
    data = b"".join(upload.chunks())          # never trust upload.name for anything on disk
    try:
        result = _analyzer.analyze(data, password=password)
    except BankStatementError as exc:
        # exc.safe_message is suitable for end users; exc.code for programmatic handling.
        return {"ok": False, "error": exc.to_dict()}
    return {
        "ok": True,
        "report_id": result.report_id,
        "status": result.status,
        "summary": result.summary(),
        "validation": result.validation.to_dict(),
        "indicators": [r.to_dict() for r in result.risk_indicators],
        "report_pdf": _analyzer.generate_pdf(result),   # bytes: store privately or stream to the user
    }
