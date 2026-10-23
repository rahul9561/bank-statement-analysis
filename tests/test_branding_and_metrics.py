"""Branding (logo, fonts), account-total presentation, FOIR and bounce reporting."""

import re
from datetime import date
from decimal import Decimal

import pymupdf
import pytest
from fontTools.ttLib import TTFont as FTFont
from reportlab.pdfbase import pdfmetrics

import helpers
from bank_statement_engine import BankStatementAnalyzer
from bank_statement_engine.analysis.emi import analyze_emi
from bank_statement_engine.config import AnalysisConfig
from bank_statement_engine.reports import assets, fonts
from bank_statement_engine.reports.formatting import inr

D = Decimal
LOGO_ASPECT = 2425 / 573


def _doc(pdf: bytes) -> pymupdf.Document:
    return pymupdf.open(stream=pdf, filetype="pdf")


def _text(pdf: bytes) -> str:
    # Collapse whitespace so phrases that wrap across lines still match.
    return re.sub(r"\s+", " ", " ".join(p.get_text() for p in _doc(pdf)))


def _render(result, **report):
    a = BankStatementAnalyzer({"ocr": {"mode": "off"}, "report": report})
    return a.generate_pdf(result)


# ------------------------------------------------------------------------------------------- logo

def test_logo_in_header_with_correct_aspect_ratio(known_result):
    page = _doc(_render(known_result))[0]
    images = page.get_images(full=True)
    assert images, "logo missing from page 1"
    rects = [r for img in images for r in page.get_image_rects(img[0])]
    header_logo = min(rects, key=lambda r: r.y0)          # topmost image = header logo
    assert header_logo.y1 < 140
    assert abs(header_logo.width / header_logo.height - LOGO_ASPECT) / LOGO_ASPECT < 0.01


def test_logo_also_in_footer(known_result):
    doc = _doc(_render(known_result))
    last = doc[doc.page_count - 1]
    assert len(last.get_image_rects(last.get_images(full=True)[0][0])) >= 1
    assert any(r.y0 > last.rect.height - 80 for img in last.get_images(full=True) for r in last.get_image_rects(img[0]))


def test_logo_path_resolved_without_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)          # unrelated working directory
    path = assets.resolve_asset(assets.LOGO_FILE)
    assert path is not None and path.is_file()
    assert assets.load_logo() is not None


@pytest.mark.parametrize("variant", ["missing", "corrupt", "disabled"])
def test_report_renders_without_logo(known_result, tmp_path, variant):
    if variant == "missing":
        opts = {"logo_path": str(tmp_path / "nope.png")}
    elif variant == "corrupt":
        bad = tmp_path / "bad.png"
        bad.write_bytes(b"not an image at all")
        opts = {"logo_path": str(bad)}
    else:
        opts = {"show_logo": False}
    pdf = _render(known_result, **opts)
    doc = _doc(pdf)
    assert doc.page_count > 2
    assert not doc[0].get_images()
    assert "BANK STATEMENT ANALYSIS REPORT" in doc[0].get_text()


def test_bundled_assets_exist_for_installed_package():
    assert (assets.PACKAGE_ASSETS / assets.LOGO_FILE).is_file()
    for family, pattern in fonts.FAMILY_FILES.items():
        for weight in fonts.WEIGHTS:
            assert (assets.PACKAGE_ASSETS / pattern.format(w=weight)).is_file(), (family, weight)


# ------------------------------------------------------------------------------------------ fonts

def test_fonts_registered_and_embedded(known_result):
    fs = fonts.register_fonts()
    assert fs.heading.startswith("Montserrat") and fs.display.startswith("Poppins") and fs.body == "Roboto-Regular"
    embedded = {f[3].split("+")[-1] for p in _doc(_render(known_result)) for f in p.get_fonts()}
    assert {"Montserrat-Bold", "Poppins-SemiBold", "Roboto-Regular", "Roboto-Medium"} <= embedded


def test_font_fallback_when_files_missing(monkeypatch, known_result):
    monkeypatch.setattr(fonts, "resolve_asset", lambda *a, **k: None)
    monkeypatch.setattr(fonts, "_REGISTRY", {})
    fs = fonts.register_fonts("missing-dir")
    assert fs.heading == fs.fallback_bold == "DejaVu-Bold" and fs.body == "DejaVu"
    assert fs.registered == () and fs.rupee == "₹"
    from bank_statement_engine.reports import theme
    monkeypatch.setattr(theme, "register_fonts", lambda assets_dir=None: fs)
    text = _text(_render(known_result))
    assert inr(helpers.EXPECTED_CLOSING) in text


def test_glyph_fallback_helpers():
    fonts.register_fonts()
    assert not fonts.has_glyph("Roboto-Regular", "→") and fonts.has_glyph("Roboto-Regular", "₹")
    assert fonts.markup("a → b", "Roboto-Regular") == 'a <font name="DejaVu">→</font> b'
    assert fonts.runs("₹1 →", "Roboto-Regular") == [("₹1 ", "Roboto-Regular"), ("→", "DejaVu")]


def test_every_character_drawn_with_a_font_that_has_it(known_result):
    """No missing glyphs: each character in the PDF exists in the font it is drawn with."""
    cmaps = {}
    for name in pdfmetrics.getRegisteredFontNames():
        face = getattr(pdfmetrics.getFont(name), "face", None)
        if getattr(face, "filename", None):
            cmaps[face.name.decode() if isinstance(face.name, bytes) else face.name] = FTFont(face.filename).getBestCmap()
    missing = set()
    for page in _doc(_render(known_result)):
        for block in page.get_text("rawdict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    cmap = cmaps.get(span["font"].split("+")[-1])
                    for ch in span["chars"]:
                        c = ch["c"]
                        assert c != "�"
                        if cmap is not None and c.strip() and ord(c) not in cmap:
                            missing.add((span["font"], c))
    assert not missing, missing


# ------------------------------------------------------------------- account totals (not a score)

def _section(text: str, start: str, end: str) -> str:
    return text[text.index(start):text.index(end)]


def test_account_totals_are_rupee_amounts_not_scores(known_result):
    text = _text(_render(known_result))
    summary = _section(text, "ACCOUNT FINANCIAL SUMMARY", "EXECUTIVE SUMMARY")
    o = known_result.overview
    for value in (o["total_credits"], o["total_debits"], o["opening_balance"], o["closing_balance"],
                  o["net_cash_flow"], known_result.balances["average_daily_balance"]):
        assert inr(value) in summary
    assert inr(D("204422.45")) == "₹2,04,422.45"
    for label in ("TOTAL CREDITS", "TOTAL DEBITS", "OPENING BALANCE", "CLOSING BALANCE", "NET CASH FLOW",
                  "Total transactions", "Credit / debit transactions", "Statement start date", "Statement end date"):
        assert label in summary
    assert f"{o['credit_count']} / {o['debit_count']}" in summary
    assert "XXXXXXXXXXX4321" in summary
    assert not re.search(r"score|marks|rating|/\s*100|out of", summary, re.I)
    assert "%" not in summary
    # Estimates live in a separately labelled block.
    assert "ANALYTICAL HIGHLIGHTS — ESTIMATES, NOT ACCOUNT TOTALS" in text


def test_unavailable_average_balance_is_not_invented(analyzer):
    rows = [helpers.Row(date(2026, 1, 1), "NEFT-ACME LTD-SALARY", credit=D("100.00"))]
    result = analyzer.analyze(helpers.build_statement_pdf(rows))
    result.balances = {"available": False, "reason": "No running balances were extracted."}
    summary = _section(_text(_render(result)), "ACCOUNT FINANCIAL SUMMARY", "EXECUTIVE SUMMARY")
    assert "Not available" in summary and "Insufficient data" in summary


# -------------------------------------------------------------------------------------------- FOIR

def test_foir_calculated_from_engine_values(known_result):
    emi = known_result.emi
    assert emi["foir_status"] == "CALCULATED"
    assert emi["foir_income_used"] == D("50000.00") and emi["estimated_monthly_emi"] == D("12500.00")
    assert emi["foir_pct"] == D("25.00") == emi["estimated_emi_to_income_pct"]
    assert emi["foir_pct"] == (emi["estimated_monthly_emi"] / emi["foir_income_used"] * 100).quantize(D("0.01"))
    assert emi["recurring_obligations"][0]["evidence_level"] == "PROBABLE"
    assert emi["loan_repayment_debits"]["amount"] == D("50000.00")
    assert emi["foir_income_basis"]
    text = _text(_render(known_result))
    foir = _section(text, "FOIR & REPAYMENT OBLIGATIONS", "CHEQUE RETURN & PAYMENT BOUNCE ANALYSIS")
    assert "25.0%" in foir and "₹12,500.00" in foir and "₹50,000.00" in foir and "PROBABLE EMI" in foir


def test_foir_not_available_without_emi(analyzer):
    rows = [r for r in helpers.KNOWN_ROWS if "BAJAJ" not in r.narration]
    result = analyzer.analyze(helpers.build_statement_pdf(rows))
    assert result.emi["foir_status"] == "NO_RECURRING_EMI_DETECTED"
    assert result.emi["foir_pct"] is None and result.emi["monthly_emi_status"] == "NONE_DETECTED"
    foir = _section(_text(_render(result)), "FOIR & REPAYMENT OBLIGATIONS", "CHEQUE RETURN & PAYMENT BOUNCE ANALYSIS")
    assert "Not available" in foir and "None detected" in foir
    assert "0.0%" not in foir and "0.00%" not in foir


@pytest.mark.parametrize("income", [None, D("0")])
def test_foir_insufficient_income(known_result, income):
    emi = analyze_emi(known_result.transactions, AnalysisConfig(), date(2026, 4, 30), income)
    assert emi["foir_status"] == "INCOME_UNAVAILABLE" and emi["foir_pct"] is None
    assert emi["estimated_monthly_emi"] == D("12500.00")


def test_unspecified_nach_debits_are_possible_not_probable(analyzer):
    rows = [r for r in helpers.KNOWN_ROWS if "BAJAJ" not in r.narration] + [
        helpers.Row(date(2026, m, 7), "NACH-10-DR-SAMPLE CORP-1986769417", debit=D("3000.00")) for m in (1, 2, 3, 4)]
    rows.sort(key=lambda r: r.date)
    result = analyzer.analyze(helpers.build_statement_pdf(rows))
    levels = {o["lender_or_payee"]: o["evidence_level"] for o in result.emi["recurring_obligations"]}
    assert levels == {"SAMPLE CORP": "POSSIBLE"}
    assert result.emi["foir_probable_only_pct"] is None
    assert result.emi["foir_confidence"] == "LOW"


# ----------------------------------------------------------------------------------------- bounces

def test_confirmed_bounce_reported_with_details(known_result):
    b = known_result.bounces
    assert b["confirmed_incident_count"] == 1 and b["confirmed_events"][0]["status"] == "CONFIRMED"
    assert b["nach_ecs_return_count"] == 1 and b["returned_payment_amount"] is None
    text = _text(_render(known_result))
    sec = _section(text, "CHEQUE RETURN & PAYMENT BOUNCE ANALYSIS", "MONTHLY CASH FLOW")
    assert "ECS RTN CHGS INSUFFICIENT BALANCE" in sec and "04-02-2026" in sec and "₹590.00" in sec
    # A return charge without a rejected-instrument entry is one incident, labelled "charge only".
    assert "CHARGE ONLY" in sec and "No confirmed bounce events detected" not in sec


def test_reversal_alone_is_possible_not_confirmed(analyzer):
    rows = [r for r in helpers.KNOWN_ROWS if "ECS RTN" not in r.narration] + [
        helpers.Row(date(2026, 3, 20), "UPI/SAMPLE SHOP/shop@ybl/Order", debit=D("777.00")),
        helpers.Row(date(2026, 3, 21), "UPI/REV/SAMPLE SHOP/Refund", credit=D("777.00")),
    ]
    rows.sort(key=lambda r: r.date)
    result = analyzer.analyze(helpers.build_statement_pdf(rows))
    b = result.bounces
    assert b["confirmed_event_count"] == 0 and b["confirmed_incident_count"] == 0
    assert b["pattern_based_count"] >= 1 and all(p["status"] == "POSSIBLE" for p in b["pattern_based_reversals"])
    sec = _section(_text(_render(result)), "CHEQUE RETURN & PAYMENT BOUNCE ANALYSIS", "MONTHLY CASH FLOW")
    assert "No confirmed bounce events detected" in sec
    assert "does not verify a clean repayment history" in sec
    assert "POSSIBLE" in sec


# ----------------------------------------------------------------------------------- long reports

def test_long_report_repeats_table_headers(analyzer):
    rows = []
    for i in range(600):
        day = date(2026, 1 + (i // 160), 1 + (i % 28))
        rows.append(helpers.Row(day, f"UPI/PAYEE {i % 37}/payee{i}@ybl/Payment for order {i}",
                                credit=D("1500.00") if i % 5 == 0 else D("0"), debit=D("0") if i % 5 == 0 else D("120.50")))
    rows.sort(key=lambda r: r.date)
    result = analyzer.analyze(helpers.build_statement_pdf(rows, rows_per_page=20))   # 20 two-line rows fit an A4 page
    assert result.status == "VERIFIED" and len(result.transactions) == 600
    doc = _doc(_render(result, max_transactions_in_report=1000))
    txn_pages = [p for p in doc if "UPI/PAYEE" in p.get_text()]
    assert len(txn_pages) >= 10
    for page in txn_pages:
        words = page.get_text()
        assert "NARRATION" in words and "BALANCE" in words      # repeated table header
        for rect in (b[:4] for b in page.get_text("blocks")):
            assert rect[2] <= page.rect.width + 0.5 and rect[3] <= page.rect.height + 0.5  # no overflow
