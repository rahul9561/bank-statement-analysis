"""App settings, read from ``settings.BANK_STATEMENT_ANALYSIS`` with safe defaults.

Example::

    BANK_STATEMENT_ANALYSIS = {
        "STORAGE_DIR": BASE_DIR / "private" / "bank_statement_reports",  # not under MEDIA_ROOT
        "RETENTION_HOURS": 72,
        "MAX_UPLOAD_BYTES": 20 * 1024 * 1024,
        "MAX_PAGES": 500,
        "STORE_TRANSACTIONS": False,
        "ENGINE_CONFIG": {"report": {"organization_name": "Acme Credit"}},
    }
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.conf import settings

DEFAULTS: dict[str, Any] = {
    "STORAGE_DIR": None,  # defaults to BASE_DIR / "private_reports"
    "RETENTION_HOURS": 72,
    "MAX_UPLOAD_BYTES": 25 * 1024 * 1024,
    "MAX_PAGES": 600,
    "STORE_TRANSACTIONS": False,
    "INCLUDE_TRANSACTIONS_IN_RESPONSE": False,
    "ENGINE_CONFIG": {},
    "THROTTLE_SCOPE": "bank_statement_upload",
}


def get(name: str) -> Any:
    user = getattr(settings, "BANK_STATEMENT_ANALYSIS", {}) or {}
    return user.get(name, DEFAULTS[name])


def storage_dir() -> Path:
    configured = get("STORAGE_DIR")
    base = Path(configured) if configured else Path(getattr(settings, "BASE_DIR", ".")) / "private_reports"
    base.mkdir(parents=True, exist_ok=True)
    return base.resolve()


def engine_config() -> dict:
    cfg = {k: dict(v) for k, v in (get("ENGINE_CONFIG") or {}).items()}
    limits = cfg.setdefault("limits", {})
    limits.setdefault("max_file_size_bytes", get("MAX_UPLOAD_BYTES"))
    limits.setdefault("max_pages", get("MAX_PAGES"))
    return cfg
