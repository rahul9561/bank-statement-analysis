"""Helpers that keep personal data out of logs and outputs."""

from __future__ import annotations

import re

_LONG_DIGITS = re.compile(r"\b[0-9Xx*]{8,20}\b")
_PAN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\d)(?:\+?91[- ]?)?[6-9]\d{9}(?!\d)")


def mask_account_number(value: str | None, visible: int = 4) -> str | None:
    """Return ``XXXXXX1234`` style masking; keeps at most ``visible`` trailing characters."""
    if not value:
        return None
    clean = re.sub(r"[^0-9A-Za-z]", "", value)
    if len(clean) <= visible:
        return "X" * len(clean)
    return "X" * (len(clean) - visible) + clean[-visible:]


def redact(text: str) -> str:
    """Redact account numbers, PANs, e-mail addresses and phone numbers from free text."""
    text = _EMAIL.sub("[email]", text)
    text = _PAN.sub("[pan]", text)
    text = _PHONE.sub("[phone]", text)
    return _LONG_DIGITS.sub(lambda m: mask_account_number(m.group(0)) or "", text)
