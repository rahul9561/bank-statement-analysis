"""Synthetic statement fixtures (fictional people, accounts and amounts only).

``KNOWN_ROWS`` has a fixed opening balance and transactions whose totals are
asserted exactly in the tests. ``build_statement_pdf`` renders them as a
text-based PDF in several layouts so the generic parser is exercised the same
way real statements exercise it.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

D = Decimal

OPENING = D("10000.00")
HOLDER = "RAVI KUMAR SHARMA"


@dataclass(frozen=True)
class Row:
    date: date
    narration: str
    debit: Decimal = D("0")
    credit: Decimal = D("0")
    ref: str = ""


KNOWN_ROWS: list[Row] = [
    Row(date(2026, 1, 1), "NEFT-ACME TECHNOLOGIES PVT LTD-SALARY JAN 2026", credit=D("50000.00"), ref="N001"),
    Row(date(2026, 1, 3), "ACH D- BAJAJ FINANCE LTD-EMI4455012", debit=D("12500.00")),
    Row(date(2026, 1, 5), "UPI/SURESH NAIR/rentpay@okaxis/House Rent Jan", debit=D("15000.00")),
    Row(date(2026, 1, 10), "NWD-416021XXXXXX1234-ATM CASH WDL-MUMBAI", debit=D("5000.00")),
    Row(date(2026, 1, 15), "UPI/SWIGGY/swiggy@icici/Food order", debit=D("450.50")),
    Row(date(2026, 1, 20), "UPI/RAVI KUMAR SHARMA/ravi@okhdfc/own account", credit=D("2000.00")),
    Row(date(2026, 2, 2), "NEFT-ACME TECHNOLOGIES PVT LTD-SALARY FEB 2026", credit=D("50000.00"), ref="N002"),
    Row(date(2026, 2, 3), "ACH D- BAJAJ FINANCE LTD-EMI4455012", debit=D("12500.00")),
    Row(date(2026, 2, 4), "ECS RTN CHGS INSUFFICIENT BALANCE", debit=D("590.00")),
    Row(date(2026, 2, 5), "UPI/SURESH NAIR/rentpay@okaxis/House Rent Feb", debit=D("15000.00")),
    Row(date(2026, 2, 12), "NWD-416021XXXXXX1234-ATM CASH WDL-PUNE", debit=D("3000.00")),
    Row(date(2026, 2, 18), "UPI/AMAZON/amazon@apl/Order 408-1234567", debit=D("1299.00")),
    Row(date(2026, 3, 1), "NEFT-ACME TECHNOLOGIES PVT LTD-SALARY MAR 2026", credit=D("51000.00"), ref="N003"),
    Row(date(2026, 3, 3), "ACH D- BAJAJ FINANCE LTD-EMI4455012", debit=D("12500.00")),
    Row(date(2026, 3, 5), "UPI/SURESH NAIR/rentpay@okaxis/House Rent Mar", debit=D("15000.00")),
    Row(date(2026, 3, 9), "BBPS/BESCOM ELECTRICITY/BILL 552211", debit=D("1234.56")),
    Row(date(2026, 3, 14), "UPI/CHAI POINT/chaipoint@ybl/Tea", debit=D("20.00")),
    Row(date(2026, 3, 14), "UPI/CHAI POINT/chaipoint@ybl/Tea", debit=D("20.00")),
    Row(date(2026, 4, 1), "NEFT-ACME TECHNOLOGIES PVT LTD-SALARY APR 2026", credit=D("50000.00"), ref="N004"),
    Row(date(2026, 4, 3), "ACH D- BAJAJ FINANCE LTD-EMI4455012", debit=D("12500.00")),
    Row(date(2026, 4, 6), "UPI/REV/AMAZON/Refund order 408-1234567", credit=D("1299.00")),
    Row(date(2026, 4, 30), "INT.PD:SB INTEREST 01-01-2026 TO 31-03-2026", credit=D("123.45")),
]

# Hand-calculated expectations for KNOWN_ROWS (independent of the engine).
#   credits: 50,000 + 2,000 + 50,000 + 51,000 + 50,000 + 1,299 + 123.45            = 2,04,422.45
#   debits:  Jan 32,950.50 + Feb 32,389.00 + Mar 28,774.56 + Apr 12,500.00          = 1,06,614.06
EXPECTED_TOTAL_CREDITS = D("204422.45")
EXPECTED_TOTAL_DEBITS = D("106614.06")
EXPECTED_NET = D("97808.39")
EXPECTED_CLOSING = D("107808.39")


def balances(rows: list[Row], opening: Decimal = OPENING) -> list[Decimal]:
    out, bal = [], opening
    for r in rows:
        bal = bal + r.credit - r.debit
        out.append(bal)
    return out


def money(value: Decimal) -> str:
    """Indian grouping, e.g. 1,10,308.39."""
    sign = "-" if value < 0 else ""
    integer, frac = f"{abs(value):.2f}".split(".")
    if len(integer) > 3:
        head, tail = integer[:-3], integer[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        integer = ",".join(groups) + "," + tail
    return f"{sign}{integer}.{frac}"


def fmt_date(d: date, style: str) -> str:
    if style == "dd/mm/yyyy":
        return d.strftime("%d/%m/%Y")
    if style == "dd-mon-yyyy":
        return d.strftime("%d-%b-%Y")
    if style == "dd mon yyyy":
        return d.strftime("%d %b %Y")
    if style == "dd/mm/yy":
        return d.strftime("%d/%m/%y")
    raise ValueError(style)


def _wrap(text: str, width: int = 34) -> list[str]:
    lines, current = [], ""
    for word in text.split(" "):
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


def build_statement_pdf(
    rows: list[Row] | None = None,
    *,
    opening: Decimal = OPENING,
    date_style: str = "dd/mm/yyyy",
    reverse: bool = False,
    ruled: bool = False,
    header_text: bool = True,
    printed_summary: bool = True,
    rows_per_page: int = 14,
    bank_line: str = "HDFC BANK LIMITED",
    ifsc: str = "HDFC0001234",
    tamper_balance_at: int | None = None,
    misplace_date_rows: frozenset[int] = frozenset(),
    amount_text: dict[int, str] | None = None,
    penny_format: bool = False,
) -> bytes:
    """``misplace_date_rows``: row indexes whose date is printed in the narration column (the table
    parser cannot anchor them, the layout-free census still sees them). ``amount_text``: row index ->
    literal text printed in that row's amount cell. ``penny_format``: print amounts below 1 as ".01"."""
    amount_text = amount_text or {}
    rows = list(rows if rows is not None else KNOWN_ROWS)
    bals = balances(rows, opening)
    if tamper_balance_at is not None:
        bals[tamper_balance_at] += D("100.00")
    entries = list(zip(range(len(rows)), rows, bals))
    if reverse:
        entries.reverse()

    buf = io.BytesIO()
    c = Canvas(buf, pagesize=A4)
    width, height = A4
    pages = [entries[i:i + rows_per_page] for i in range(0, len(entries), rows_per_page)] or [[]]
    x_date, x_narr, x_ref, x_wd, x_dep, x_bal = 36, 100, 290, 410, 480, 560

    def header(first: bool) -> float:
        y = height - 50
        if first:
            c.setFont("Helvetica-Bold", 11)
            c.drawString(36, y, bank_line)
            c.setFont("Helvetica", 8)
            c.drawString(36, y - 14, f"MR. {HOLDER}")
            c.drawString(36, y - 26, "12 GARDEN ROAD, SAMPLE NAGAR")
            c.drawString(330, y - 14, "Account No : 501000123454321")
            c.drawString(330, y - 26, f"IFSC : {ifsc}")
            c.drawString(330, y - 38, "Account Type : SAVINGS")
            first_d, last_d = min(r.date for r in rows), max(r.date for r in rows)
            c.drawString(36, y - 50, f"Statement of account From : {first_d:%d/%m/%Y} To : {last_d:%d/%m/%Y}")
            y -= 72
        else:
            y -= 10
        c.setFont("Helvetica-Bold", 8)
        if header_text:
            c.drawString(x_date, y, "Date")
            c.drawString(x_narr, y, "Narration")
            c.drawString(x_ref, y, "Chq./Ref.No.")
            c.drawRightString(x_wd, y, "Withdrawal Amt.")
            c.drawRightString(x_dep, y, "Deposit Amt.")
            c.drawRightString(x_bal, y, "Closing Balance")
        else:
            c.drawRightString(x_bal, y, "Balance")
        c.line(30, y - 5, width - 30, y - 5)
        return y - 18

    for p_index, page_entries in enumerate(pages):
        y = header(p_index == 0)
        c.setFont("Helvetica", 8)
        if p_index == 0 and not reverse:
            c.drawString(x_narr, y, "Opening Balance")
            c.drawRightString(x_bal, y, money(opening))
            y -= 16
        for index, row, bal in page_entries:
            lines = _wrap(row.narration)
            row_top = y
            date_text = fmt_date(row.date, date_style)
            if index in misplace_date_rows:
                c.drawString(x_narr, y, f"{date_text} {lines[0]}")
            else:
                c.drawString(x_date, y, date_text)
                c.drawString(x_narr, y, lines[0])
            c.drawString(x_ref, y, row.ref or ("0000" + row.date.strftime("%m%d") + "77"))

            def amt(value: Decimal) -> str:
                if not value:
                    return ""
                text = money(value)
                return text[1:] if penny_format and text.startswith("0.") else text

            c.drawRightString(x_wd, y, amount_text.get(index, amt(row.debit)) if row.debit else "")
            c.drawRightString(x_dep, y, amount_text.get(index, amt(row.credit)) if row.credit else "")
            c.drawRightString(x_bal, y, money(bal))
            for extra in lines[1:]:
                y -= 11
                c.drawString(x_narr, y, extra)
            y -= 16
            if ruled:
                c.line(30, row_top + 10, width - 30, row_top + 10)
                c.line(30, y + 10, width - 30, y + 10)
        if p_index == len(pages) - 1 and printed_summary:
            y -= 20
            c.setFont("Helvetica-Bold", 8)
            c.drawString(36, y, "STATEMENT SUMMARY")
            labels = ["Opening Balance", "Dr Count", "Cr Count", "Total Debits", "Total Credits", "Closing Balance"]
            values = [money(opening), str(sum(1 for r in rows if r.debit)), str(sum(1 for r in rows if r.credit)),
                      money(sum((r.debit for r in rows), D(0))), money(sum((r.credit for r in rows), D(0))),
                      money(balances(rows, opening)[-1])]
            xs = [36, 130, 190, 250, 350, 450]
            c.setFont("Helvetica", 7.5)
            for x, label, value in zip(xs, labels, values):
                c.drawString(x, y - 14, label)
                c.drawString(x, y - 26, value)
        c.setFont("Helvetica", 6.5)
        c.drawString(36, 30, f"Page {p_index + 1} of {len(pages)}  -  This is a computer generated statement.")
        c.showPage()
    c.save()
    return buf.getvalue()


def text_pdf(lines: list[str]) -> bytes:
    buf = io.BytesIO()
    c = Canvas(buf, pagesize=A4)
    y = 800
    for line in lines:
        c.drawString(40, y, line)
        y -= 14
    c.showPage()
    c.save()
    return buf.getvalue()
