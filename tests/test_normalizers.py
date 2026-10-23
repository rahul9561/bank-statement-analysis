from datetime import date
from decimal import Decimal

import pytest

from bank_statement_engine.normalizers.amounts import parse_amount, parse_signed_balance
from bank_statement_engine.normalizers.dates import find_statement_period, parse_date
from bank_statement_engine.normalizers.narration import detect_channel, extract_counterparty
from bank_statement_engine.utils.redaction import mask_account_number, redact


@pytest.mark.parametrize("text,expected", [
    ("21/09/2025", date(2025, 9, 21)),
    ("21-09-2025", date(2025, 9, 21)),
    ("21.09.2025", date(2025, 9, 21)),
    ("01/04/26", date(2026, 4, 1)),
    ("21-Sep-2025", date(2025, 9, 21)),
    ("21 Oct 2025", date(2025, 10, 21)),
    ("21-SEP-25", date(2025, 9, 21)),
    ("05 Sept 2025", date(2025, 9, 5)),
    ("2026-06-02", date(2026, 6, 2)),
    ("Sep 21, 2025", date(2025, 9, 21)),
])
def test_indian_date_formats(text, expected):
    assert parse_date(text) == expected


@pytest.mark.parametrize("text", ["31/02/2026", "13/13/2026", "hello", "", None, "1234.50", "21/09"])
def test_invalid_dates_rejected(text):
    assert parse_date(text) is None


def test_statement_period_detection():
    assert find_statement_period("Statement of Axis Account No: 9 for the period (From: 21-09-2025  To: 21-09-2026)") == (
        date(2025, 9, 21), date(2026, 9, 21))
    assert find_statement_period("Account Statement\n21 Oct 2025 - 21 Oct 2026") == (date(2025, 10, 21), date(2026, 10, 21))
    assert find_statement_period("STATEMENT PERIOD : 2026-06-02 TO 2026-06-30") == (date(2026, 6, 2), date(2026, 6, 30))


@pytest.mark.parametrize("text,expected", [
    ("2,58,006.05", Decimal("258006.05")),
    ("1,000.00", Decimal("1000.00")),
    ("₹ 31,644.64", Decimal("31644.64")),
    ("Rs. 1,234.5", Decimal("1234.50")),
    ("INR 99.99", Decimal("99.99")),
    ("952029.80", Decimal("952029.80")),
    ("-22000.00", Decimal("-22000.00")),
    ("(1,500.00)", Decimal("-1500.00")),
    ("7,190.31 CR", Decimal("7190.31")),
])
def test_amount_parsing(text, expected):
    value = parse_amount(text)
    assert value == expected
    assert isinstance(value, Decimal)


@pytest.mark.parametrize("text", ["-", "", "   ", "N/A", "abc", "12,34,5", None, "0.00"])
def test_amount_placeholders(text):
    assert parse_amount(text) is None


def test_signed_balance_with_cr_dr():
    assert parse_signed_balance("3,59,677.14CR") == Decimal("359677.14")
    assert parse_signed_balance("1,234.50 Dr") == Decimal("-1234.50")
    assert parse_signed_balance("2,58,006.05 Cr.") == Decimal("258006.05")
    assert parse_signed_balance("0.00") == Decimal("0.00")


def test_decimal_arithmetic_is_exact():
    total = sum((parse_amount(x) for x in ["0.10", "0.20", "0.30"]), Decimal("0"))
    assert total == Decimal("0.60")


@pytest.mark.parametrize("narration,channel", [
    ("UPI/P2A/412313057392/RAMESH/State Ban/Payment/", "UPI"),
    ("NEFT-HDFCN52025081107838811-GOVIND SHARMA", "NEFT"),
    ("RTGS/BKIDA26160290284/UTIB/SAMPLE TRADERS", "RTGS"),
    ("MMT/IMPS/522311117633/IMPS transactio/ANITA", "IMPS"),
    ("NWD-416021XXXXXX5619-PUON1188-PUNE", "ATM"),
    ("BY CASH DEPOSIT-BNA/DERO325301", "CASH"),
    ("ACH D- SAMPLE HOUSING FINANCE-70213180EMI0", "NACH"),
    ("UPI/DR/746922402653/Story TV/NSPB/storytv@cas/Mandate Re", "UPI"),
])
def test_channel_detection(narration, channel):
    assert detect_channel(narration.upper()) == channel


@pytest.mark.parametrize("narration,expected", [
    ("UPI/P2A/412313057392/RAMESH KUMAR/State Ban/Payment/", "RAMESH KUMAR"),
    ("UPI/Uday Chaurasiya/566053252985/UPI", "UDAY CHAURASIYA"),
    ("UPI-MR ANIL PATIL-9999999999@YBL-YESB0YBLUPI-1234-UPI", "ANIL PATIL"),
    ("ACH D- SAMPLE HOUSING FINANCE-70213180EMI0", "SAMPLE HOUSING FINANCE"),
    ("NEFT IN42531155733971 SAMPLE MEDIA PRIVATE LIMITED", "SAMPLE MEDIA PRIVATE LIMITED"),
    ("NEFT/HSBCN27881563823/SAMPLE INSURANCE LIMI/HSBC BANK//ACC", "SAMPLE INSURANCE LIMI"),
    ("UPI REF NO 119507872082 P2P-VISU-PAYMENT FROM-HDFC0004859- HEAD OFFICE", "VISU"),
    ("NWD-416021XXXXXX5619-PUON1188-PUNE", None),
])
def test_counterparty_extraction(narration, expected):
    assert extract_counterparty(narration) == expected


def test_redaction_helpers():
    assert mask_account_number("921010037565621") == "XXXXXXXXXXX5621"
    text = redact("Acct 921010037565621 PAN ABCDE1234F mail a.b@example.com phone 9876543210")
    assert "921010037565621" not in text and "ABCDE1234F" not in text
    assert "a.b@example.com" not in text and "9876543210" not in text
