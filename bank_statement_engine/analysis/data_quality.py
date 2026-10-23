"""Extraction quality summary."""

from __future__ import annotations

from collections import Counter

from ..schemas import Transaction, ValidationReport


def assess_data_quality(
    txns: list[Transaction], validation: ValidationReport, extraction: dict, build_info: dict
) -> dict:
    flags = Counter(f for t in txns for f in t.flags)
    categorized = sum(1 for t in txns if t.category != "OTHER")
    low_conf = sum(1 for t in txns if 0 < t.category_confidence < 0.6)
    pages = extraction.get("page_count", 0)
    unreadable = extraction.get("unreadable_pages", [])
    gaps = extraction.get("table_pages_without_rows", [])
    links, breaks = validation.balance_links_checked, validation.balance_chain_breaks

    if validation.status == "VERIFIED" and not unreadable and not gaps:
        confidence = "HIGH"
    elif links and breaks / links <= 0.02 and len(unreadable) <= 1:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"
    return {
        "extraction_confidence": confidence,
        "page_count": pages,
        "pages_with_transactions": len(extraction.get("pages_with_rows", [])),
        "ocr_pages": extraction.get("ocr_pages", []),
        "unreadable_pages": unreadable,
        "table_pages_without_rows": gaps,
        "extraction_method": extraction.get("method", "text"),
        "parser": extraction.get("parser"),
        "table_columns": extraction.get("layout_columns", []),
        "layout_inferred": extraction.get("layout_inferred", False),
        "row_segmentation": extraction.get("row_modes", {}),
        "rows_read": build_info.get("rows_read", 0),
        "rows_without_amount": build_info.get("rows_without_amount", 0),
        "transactions": len(txns),
        "balance_links_checked": links,
        "balance_chain_breaks": breaks,
        "transactions_without_balance": sum(1 for t in txns if t.balance is None),
        "direction_inferred": build_info.get("direction_inferred", 0),
        "reordered_within_day": flags.get("REORDERED_WITHIN_DAY", 0),
        "categorized_pct": round(100 * categorized / len(txns), 1) if txns else 0.0,
        # Confidence < 0.6. Channel-only categories (e.g. a UPI transfer to a person) stay in this count
        # by design: the channel shows how money moved, not why.
        "low_confidence_categories": low_conf,
        "category_basis_counts": dict(Counter(t.category_basis for t in txns)),
        "channel_only_categories": sum(1 for t in txns if t.category_basis == "CHANNEL_ONLY"),
        "transaction_flags": dict(flags),
    }
