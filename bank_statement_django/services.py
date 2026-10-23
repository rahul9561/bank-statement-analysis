"""Service layer: the only module that calls the engine.

Views stay thin and other code (management commands, Celery tasks, other apps)
can call ``analyze_statement`` directly.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from bank_statement_engine import AnalysisResult, BankStatementAnalyzer, BankStatementError

from . import conf
from .models import StatementAnalysis

logger = logging.getLogger("bank_statement_django")


def get_analyzer() -> BankStatementAnalyzer:
    return BankStatementAnalyzer(conf.engine_config())


def analyze_statement(
    owner,
    data: bytes,
    *,
    password: str | None = None,
    holder_names: list[str] | None = None,
    analyzer: BankStatementAnalyzer | None = None,
) -> StatementAnalysis:
    """Analyse statement bytes for ``owner``, store the report PDF privately and return the record.

    Raises ``BankStatementError`` for statement problems (nothing is persisted then).
    """
    analyzer = analyzer or get_analyzer()
    result: AnalysisResult = analyzer.analyze(data, password=password, holder_names=holder_names or [])
    pdf = analyzer.generate_pdf(result)

    relative = _report_relative_path(result)
    target = safe_report_path(relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    try:
        tmp.write_bytes(pdf)
        tmp.replace(target)
    finally:
        if tmp.exists():
            tmp.unlink()

    stored = result.to_dict(include_transactions=bool(conf.get("STORE_TRANSACTIONS")))
    retention = conf.get("RETENTION_HOURS")
    try:
        with transaction.atomic():
            record = StatementAnalysis.objects.create(
                report_id=result.report_id,
                owner=owner,
                status=StatementAnalysis.STATUS_COMPLETED,
                reconciliation_status=result.status,
                bank_code=result.metadata.bank_code or "",
                page_count=result.metadata.page_count,
                transaction_count=len(result.transactions),
                source_sha256=result.source_sha256,
                summary=result.summary(),
                validation=_validation_payload(result),
                result=stored,
                report_file=relative.as_posix(),
                expires_at=timezone.now() + timedelta(hours=retention) if retention else None,
            )
    except Exception:
        target.unlink(missing_ok=True)
        raise
    logger.info("Statement analysis stored report=%s owner=%s status=%s", record.report_id, owner.pk, result.status)
    record._engine_result = result  # available to the caller of this request only
    return record


def _report_relative_path(result: AnalysisResult) -> Path:
    # Generated name only: the uploaded file name is never used on disk.
    now = timezone.now()
    return Path(f"{now:%Y}") / f"{now:%m}" / f"{result.report_id}.pdf"


def safe_report_path(relative: str | Path) -> Path:
    """Resolve ``relative`` inside the storage directory, rejecting path traversal."""
    base = conf.storage_dir()
    candidate = (base / relative).resolve()
    if base != candidate and base not in candidate.parents:
        raise PermissionError("Report path escapes the storage directory.")
    return candidate


def _validation_payload(result: AnalysisResult) -> dict:
    v = result.validation.to_dict()
    return {
        "status": v["status"],
        "reconciled": v["reconciled"],
        "balance_links_checked": v["balance_links_checked"],
        "balance_chain_breaks": v["balance_chain_breaks"],
        "completeness": v["completeness"],
        "issues": [{"code": i["code"], "severity": i["severity"], "message": i["message"]} for i in v["issues"]],
    }


def delete_analysis(record: StatementAnalysis) -> None:
    if record.report_file:
        try:
            safe_report_path(record.report_file).unlink(missing_ok=True)
        except PermissionError:
            logger.warning("Refusing to delete report outside storage for %s", record.report_id)
    record.delete()


def purge_expired(now=None) -> int:
    """Delete analyses (and report files) past their retention time. Returns the count removed."""
    now = now or timezone.now()
    count = 0
    for record in StatementAnalysis.objects.filter(expires_at__isnull=False, expires_at__lte=now).iterator():
        delete_analysis(record)
        count += 1
    return count


__all__ = ["analyze_statement", "get_analyzer", "safe_report_path", "purge_expired", "delete_analysis",
           "BankStatementError"]
