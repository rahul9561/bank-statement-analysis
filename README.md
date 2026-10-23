# Bank Statement Analysis Engine (India)

A reusable, framework-independent Python engine that reads Indian bank statement PDFs,
normalises every transaction into one schema, **reconciles them against the printed running
balance**, analyses income, spending, EMIs, bounces and balance behaviour, and renders a
multi-page PDF report. A thin Django/DRF app exposes it as an authenticated upload API.

* Local processing only — no statement data is sent to any external service.
* Money is `Decimal` end to end; nothing is silently "repaired" — every interpretation is
  flagged and every inconsistency is reported.
* No credit score, eligibility figure or lending decision is produced.

```
bank_statement_engine/   core engine (PyMuPDF + ReportLab only)       → docs/ARCHITECTURE.md
bank_statement_django/   Django + DRF integration (models, API, retention)
demo_project/            runnable Django project using the app
examples/                script, Django service and DRF endpoint examples, sample config
tests/                   171 tests (synthetic fixtures, audit regressions, real samples in input/)
output/reports/          generated sample analyses (PDF + JSON) for every statement in input/
```

## Supported statements

The engine uses a **generic, geometry-based table parser** (header vocabulary + data-driven
column calibration + ruling-line/gap row segmentation). Results on the sample statements in
`input/` (all text-based PDFs):

| Bank | Pages | Transactions | Status | Completeness | Independent cross-check (audit) |
|---|---|---|---|---|---|
| Axis Bank | 53 | 1,773 | VERIFIED | printed opening & closing | balances identical to PyMuPDF `find_tables` (1,773 rows) |
| Bank of India | 46 | 1,022 | VERIFIED | chain only | printed serial numbers 1–1,022 contiguous; `find_tables` identical |
| Equitas SFB | 273 | 2,424 | VERIFIED | chain only | row census 2,424 = parsed |
| HDFC Bank | 22 | 237 | VERIFIED | chain only (2 periods) | row census 237 = parsed |
| ICICI Bank | 20 | 266 | VERIFIED | chain only | printed serial numbers 1–266 contiguous |
| IDFC FIRST Bank | 6 | 40 | VERIFIED | printed opening & closing (2 periods) | `find_tables` identical |
| Kotak Mahindra | 29 | 1,062 | VERIFIED | printed opening & closing | serials 1–1,062 contiguous; `find_tables` identical; matches reference report |
| Punjab National Bank | 5 | 69 | VERIFIED | chain only | `find_tables` identical |
| SBI | 66 | 842 | VERIFIED | printed opening & closing | printed totals and Dr/Cr counts (604/238) matched exactly |
| Utkarsh SFB | 63 | 1,209 | VERIFIED | printed opening & closing | `find_tables` identical |

For all 8,944 sample transactions the extracted amount and printed balance were confirmed to
appear on the cited source page; no sign errors, misordering or double-read rows were found,
and opening + credits − debits = closing holds exactly (₹0.00 difference) for every file.

**What the statuses mean.** `VERIFIED` = every running-balance link is exactly consistent, every
page was readable and undamaged, and a layout-independent census found no unparsed
date-and-amount lines. `validation.completeness` adds: `CONFIRMED_BY_PRINTED_BALANCES` (the
statement prints opening and closing balances and both agree) or `CHAIN_ONLY` (the bank prints
no boundary figure, so rows absent at the very start/end of the readable text could not be
ruled out from balances alone; an INFO issue states this).

Other Indian banks (Bank of Baroda, Canara, Union, Yes, IndusInd, …) are recognised by IFSC
and handled by the same generic parser **on a best-effort basis — they have not been tested
against real statements**. If a layout cannot be read reliably the engine raises a clear
error or marks the result `REQUIRES_REVIEW`; it never silently produces a misleading report.

Scanned and mixed PDFs are handled through local OCR (Tesseract). **Real OCR has not been
tested**: Tesseract is not installed on the development machine and no scanned sample exists.
What is tested: the OCR code path with an injected provider (synthetic and a real Kotak
statement with one page rasterised), and that a scanned page without OCR makes the result
`REQUIRES_REVIEW` instead of `VERIFIED`.

## Prerequisites

* Python 3.10+ (developed and tested on 3.11, Windows 11)
* PyMuPDF ≥ 1.23, ReportLab ≥ 4.0 (fonts with the ₹ glyph are bundled)
* Optional: Tesseract OCR + `pytesseract` + Pillow for scanned statements
* Optional: Django ≥ 4.2 and djangorestframework ≥ 3.14 for the API

## Installation

```powershell
# from this folder (Windows PowerShell)
python -m pip install -e .                 # engine only
python -m pip install -e ".[django]"       # + Django integration
python -m pip install -e ".[ocr]"          # + pytesseract/Pillow (install Tesseract separately)
```

In another project, install from a path, a wheel or your private index:

```bash
pip install /path/to/bankstatementananysis            # or: pip install bank_statement_engine-1.0.0-py3-none-any.whl
# requirements.txt:  bank-statement-engine @ file:///opt/libs/bankstatementananysis
```

### OCR / Tesseract setup

OCR runs only for pages without usable text (`ocr.mode = "auto"`). If Tesseract is missing,
text PDFs still work; scanned pages raise `OCRUnavailableError` with a clear message.

**Windows**
1. Install Tesseract from the UB Mannheim build (https://github.com/UB-Mannheim/tesseract/wiki).
2. `python -m pip install pytesseract Pillow`
3. Either add `C:\Program Files\Tesseract-OCR` to `PATH` or configure
   `{"ocr": {"tesseract_cmd": "C:/Program Files/Tesseract-OCR/tesseract.exe"}}`.

**Linux (Debian/Ubuntu)**
```bash
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng
pip install pytesseract Pillow
```

Another OCR engine can be plugged in by implementing `extractors.base.OCRProvider`
(`is_available()`, `recognize(png_bytes, dpi) -> list[Word]` in PDF points) and passing it as
`BankStatementAnalyzer(ocr_provider=...)`. Keep it local: do not send statements to third parties.

## Basic Python usage

```python
from bank_statement_engine import BankStatementAnalyzer, BankStatementError

analyzer = BankStatementAnalyzer()                      # or BankStatementAnalyzer({"ocr": {"mode": "off"}})
try:
    result = analyzer.analyze(file_path="statement.pdf")      # also: analyze(bytes) / analyze(file_obj)
    # result = analyzer.analyze(data, password="...", holder_names=["RAVI KUMAR"])
except BankStatementError as exc:
    print(exc.code, exc.safe_message)                   # safe to show to end users
else:
    print(result.status)                                # VERIFIED | REQUIRES_REVIEW | FAILED
    print(result.summary())                             # JSON-safe dict
    report_path = analyzer.generate_pdf(result, output_path="analysis_report.pdf")
    pdf_bytes = analyzer.generate_pdf(result)           # bytes when no path is given
    payload = result.to_dict()                          # full JSON-compatible result
```

Command line:

```powershell
python -m bank_statement_engine analyze input\kotakstatement_bank_statement_46393f6c2ba8.pdf `
    --pdf output\kotak_analysis.pdf --json output\kotak_analysis.json
python -m bank_statement_engine analyze statement.pdf --password --config examples\sample_config.json
```

### Public API (stable)

| Call | Returns |
|---|---|
| `BankStatementAnalyzer(config=None, *, ocr_provider=None, parser_registry=None)` | analyzer |
| `analyzer.analyze(source=None, *, file_path=None, password=None, holder_names=None)` | `AnalysisResult` |
| `analyzer.generate_pdf(result, output_path=None)` | `Path` (or `bytes` if no path) |
| `analyzer.analyze_to_pdf(source, output_path, **kwargs)` | `(AnalysisResult, Path)` |
| `AnalysisResult.to_dict(include_transactions=True)` / `.to_json()` / `.summary()` | JSON-safe data |

Exceptions (all subclass `BankStatementError`, each with `code`, `safe_message`, `http_status`):
`InvalidPDFError`, `FileTooLargeError`, `TooManyPagesError`, `EncryptedPDFError`,
`UnsupportedPDFError`, `NoExtractableTextError`, `OCRUnavailableError`,
`NoTransactionsFoundError`, `UnsupportedStatementFormatError`, `InvalidTransactionDataError`,
`ReconciliationError`, `ReportGenerationError`, `ProcessingTimeoutError`.

The JSON schema, serialization rules and validation statuses are documented in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Django integration

1. Install: `pip install -e ".[django]"` (or add the package to your requirements).
2. Settings:

```python
INSTALLED_APPS += ["rest_framework", "bank_statement_django"]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",      # or SessionAuthentication / JWT
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_THROTTLE_RATES": {"bank_statement_upload": "30/hour"},
}

BANK_STATEMENT_ANALYSIS = {
    "STORAGE_DIR": BASE_DIR / "private_reports",   # NOT under MEDIA_ROOT; served only via the API
    "RETENTION_HOURS": 72,                         # reports and results are purged after this
    "MAX_UPLOAD_BYTES": 25 * 1024 * 1024,
    "MAX_PAGES": 600,
    "STORE_TRANSACTIONS": False,                   # keep transaction-level data out of the DB
    "ENGINE_CONFIG": {"report": {"organization_name": "Your Org"}, "ocr": {"mode": "auto"}},
}
```

3. URLs:

```python
urlpatterns += [path("api/bank-statement-analysis/", include("bank_statement_django.urls"))]
```

4. `python manage.py migrate`, and schedule `python manage.py purge_bank_statement_reports`
   (cron / Task Scheduler / Celery beat) to enforce retention.

### Endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/api/bank-statement-analysis/analyze/` | multipart: `statement` (PDF), optional `password`, `holder_name` |
| GET | `/api/bank-statement-analysis/analyses/` | caller's analyses |
| GET/DELETE | `/api/bank-statement-analysis/analyses/<report_id>/` | stored result JSON / delete |
| GET | `/api/bank-statement-analysis/reports/<report_id>/` | download the PDF report |

Example request and (abridged) response:

```bash
curl -X POST http://127.0.0.1:8000/api/bank-statement-analysis/analyze/ \
     -H "Authorization: Token <token>" \
     -F "statement=@statement.pdf;type=application/pdf"
```
```json
{
  "success": true,
  "status": "completed",
  "report_id": "BSA-20261023-8667C09CEA",
  "reconciliation_status": "VERIFIED",
  "summary": {"bank_name": "Kotak Mahindra Bank", "total_credits": "910968.00", "total_debits": "909478.09",
              "net_cash_flow": "1489.91", "transaction_count": 1062, "...": "..."},
  "validation": {"status": "VERIFIED", "reconciled": true, "balance_chain_breaks": 0, "issues": [...]},
  "risk_indicators": [{"code": "INCOME_VOLATILITY", "severity": "MEDIUM", "title": "Volatile monthly inflows"}],
  "report_url": "/api/bank-statement-analysis/reports/BSA-20261023-8667C09CEA/",
  "result_url": "/api/bank-statement-analysis/analyses/BSA-20261023-8667C09CEA/"
}
```

Errors return `{"success": false, "status": "failed", "error": {"code", "message", "details"}}`
with 400/413/422/503 as appropriate; never stack traces, paths or statement text.

### Security behaviour of the app

* Authentication required; reports/results visible only to the uploader (or staff with the
  `view_statementanalysis` permission). Other users get **404** so report ids cannot be probed.
* Upload checks: size limit while streaming, `.pdf` name, declared MIME type, **and** the
  `%PDF-` signature of the bytes; page limit and encryption handled by the engine.
* The uploaded statement is processed in memory and never written by the app; Django's own
  temporary upload file (large uploads) is deleted by Django after the request.
* Reports are written with a generated name (`<report_id>.pdf`) under the private storage
  directory via an atomic temp-file rename; stored paths are re-validated against path
  traversal before every read or delete.
* Scoped rate limiting (`bank_statement_upload`), `Cache-Control: no-store` on downloads.
* Logs contain report ids, counts and status codes only.
* Statement passwords are used once in memory and never stored or logged.

### Running the demo API (Windows PowerShell)

```powershell
cd demo_project
$env:DJANGO_SECRET_KEY = "<long random string>"
python manage.py migrate
python manage.py createsuperuser
python manage.py drf_create_token <username>
python manage.py runserver
# then:
curl.exe -X POST http://127.0.0.1:8000/api/bank-statement-analysis/analyze/ `
  -H "Authorization: Token <token>" -F "statement=@..\input\kotakstatement_bank_statement_46393f6c2ba8.pdf;type=application/pdf"
curl.exe -H "Authorization: Token <token>" -o report.pdf http://127.0.0.1:8000/api/bank-statement-analysis/reports/<report_id>/
```

### Background processing (optional)

The engine is synchronous (≈0.1–3 s for the samples; the 273-page Equitas file takes ~3 s).
For very large files, `bank_statement_django/tasks.py` shows a Celery task wrapping the same
service. Celery/Redis are not required and not configured by default; if you add them, pass a
reference to privately stored bytes to the task, never the statement over the broker.

## PDF report

The layout follows the reference report in `sample/`: navy header band with the company logo,
quick-navigation chips (clickable), numbered section bars, label/value grids, pill badges,
spending bars, a monthly credit/debit chart with an average-balance strip, an EOD balance chart
and an indicator scorecard. Sections (default order):

1. **Account Financial Summary** — actual totals in rupees: total credits, total debits, net cash
   flow, opening and closing balance (each tagged *printed on statement* / *derived*), average
   daily balance, transaction counts (total, credit, debit), statement start/end dates, bank and
   masked account number. These are money amounts, never a score or rating.
2. Executive summary, with an **Analytical highlights** row clearly labelled
   "estimates, not account totals".
3. Extraction & reconciliation, income (recurring vs other inflows).
4. **FOIR & Repayment Obligations** — estimated monthly income and its basis, estimated monthly
   EMI, FOIR = EMI ÷ income × 100 (with confidence, a "probable EMIs only" variant and notes),
   detected obligations tagged *Probable EMI* (lender/EMI wording) or *Possible EMI* (unspecified
   NACH/ECS debit), loan debits and credit-card payments shown separately. When no recurring EMI
   is detected or income cannot be identified, the report says "None detected" / "Not available"
   / "Insufficient data" — never 0 %. None of these are verified loan liabilities.
5. **Cheque Return & Payment Bounce Analysis** — confirmed incidents (explicit return/bounce
   narration), EMI/NACH/ECS and cheque return entries, return charges, returned-payment amount
   (only when a returned-payment credit exists), each event with date, narration, amount and
   reference; pattern-based reversals listed separately as *Possible* and unexplained
   reversal-worded credits as *Unclear*. With no confirmed events it states "No confirmed bounce
   events detected" and that this does not verify a clean repayment history.
6. Monthly cash flow, spending, investments & insurance, money flow, balance behaviour,
   own-account transfers, risk indicators with evidence, transaction details (headers repeat on
   every page), methodology & limitations.

All amounts use full Indian formatting (₹2,70,422.77); compact lakh/crore notation is used only
on chart axes. Deliberate differences from the reference: no "credit score" and no "PROCEED"
verdict (the header shows the reconciliation status instead), full account numbers are masked,
and values that cannot be calculated show "Not available".

### Branding: logo and fonts

* **Logo** — `av-logo.png` appears in the header band and, on a navy chip (the artwork is made
  for dark backgrounds), in the page footer and closing footer card, always at its natural
  aspect ratio. A missing or unreadable logo is logged and the report renders without it.
  Options: `report.logo_path` (explicit file), `report.show_logo` (default `true`).
* **Fonts** — Montserrat for titles, Poppins for summary figures, Roboto for tables and body
  text; DejaVu Sans is used automatically for any character a font lacks (e.g. `→` in Roboto,
  `●` in Montserrat). Only static TTFs are used; all are embedded (subset) in the PDF. If a font
  file is missing, the next weight, then the next family, then DejaVu is used.
* **Where assets are found** (`bank_statement_engine/reports/assets.py`, no absolute paths):
  `report.assets_dir` → `$BSA_ASSETS_DIR` → `<project>/assets` (this repository's `assets/`)
  → the copy bundled in the package (`bank_statement_engine/reports/assets/`, shipped in the
  wheel so other Django projects are branded without extra setup). To rebrand, replace
  `assets/av-logo.png` (and the bundled copy for installed packages) or point `assets_dir` at
  your own folder.

### Customising the report template

Templates are JSON files in `bank_statement_engine/reports/templates/`. Create e.g.
`my_template.json` (only the keys you change are needed — it is merged over `default.json`):

```json
{
  "theme": {"navy": "#0b3d2e", "navy_light": "#1f7a5c", "accent": "#22c55e"},
  "sections": ["profile", "executive_summary", "reconciliation", "monthly", "risk_indicators", "methodology"],
  "titles": {"profile": "Customer Account Overview"}
}
```

and select it with `{"report": {"template": "my_template"}}` (a file path also works).
Section builders live in `reports/pdf_report.py` (`sec_<name>` methods); add a method and list
its name in `sections` to add a section.

## Configuration

`EngineConfig.from_dict({...})` (or a JSON file for the CLI) — see `examples/sample_config.json`.

| Section | Key options |
|---|---|
| `ocr` | `mode` (`auto`/`off`/`force`), `language`, `dpi`, `tesseract_cmd`, `min_text_chars`, `max_pages` |
| `limits` | `max_file_size_bytes`, `max_pages`, `max_processing_seconds` |
| `analysis` | `balance_tolerance`, low-balance thresholds, recurring/EMI rules and minimum amounts, cash/volatility/EMI/concentration thresholds, `holder_names`, `own_account_hints`, `extra_category_rules`, `keep_source_text` |
| `report` | `title`, `subtitle`, `organization_name`, `template`, `include_transactions`, `max_transactions_in_report`, `show_holder_name`, `utc_offset_minutes`, `logo_path`, `show_logo`, `assets_dir` |

Custom categorisation rules are evaluated before the built-in ones:

```python
{"analysis": {"extra_category_rules": [
    {"id": "society", "category": "RENT", "pattern": "SOCIETY\\s*MAINT", "direction": "DEBIT", "confidence": 0.8}]}}
```

## Privacy and production deployment

Bank statements are highly sensitive. The engine defaults are conservative (local processing,
masked account numbers, holder name excluded from output, no transaction storage in the
Django app, redacted logs), but each deployment **must** also:

* enforce authentication/authorisation appropriate to its users and roles;
* keep `STORAGE_DIR` outside any web-served directory, with OS permissions restricted to the
  app user, on encrypted storage (disk/volume encryption or an encrypted object store);
* configure `RETENTION_HOURS` per policy and schedule `purge_bank_statement_reports`;
* use HTTPS, secure cookies, a strong `SECRET_KEY` from the environment, and database encryption
  where results are stored;
* review log handlers so request bodies/files are never logged;
* treat OCR text and narrations strictly as data (the engine never evaluates them).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ENCRYPTED_PDF` | Statement is password protected: pass `password=` (API field `password`). |
| `OCR_UNAVAILABLE` | Scanned PDF and Tesseract not installed / not on PATH; see OCR setup. |
| `UNSUPPORTED_STATEMENT_FORMAT` | No transaction table found — not a statement, or an unusual layout (consider a bank-specific parser). |
| `NO_TRANSACTIONS_FOUND` | Header found but no dated rows; check the PDF is complete. |
| Status `REQUIRES_REVIEW` | Read `validation.issues`: chain breaks list the transaction ids, expected and printed balances. |
| `POSSIBLE_MISSED_ROWS` | A layout-free scan found date+amount lines the table parser did not read; `details.pages` lists where. |
| `UNPARSED_AMOUNT` | Text in an amount/balance cell could not be read as a number (unusual format or damaged text). |
| `DAMAGED_PAGES` / `UNREADABLE_PAGES` | A page's content could not be (fully) decoded, or it is scanned and OCR is unavailable. |
| `PAGES_WITHOUT_TRANSACTIONS` warning | A page inside the transaction range had no rows (trailer pages between statement periods are excluded). |
| `CLOSING_BALANCE_NOT_PRINTED` (info) | The bank prints no closing balance; completeness at the end rests on the running balance (`CHAIN_ONLY`). |
| Chain breaks on an OCR'd statement | OCR misreads digits; consider `analysis.balance_tolerance` (default `0.00`) only after review. |
| Wrong counterparty names | Narration formats vary; names are best effort and may be truncated by the bank. |
| `₹` shows as `Rs.` in reports | Bundled DejaVu fonts missing from the installed package (reinstall). |

## Running tests

```powershell
python -m pip install pytest
python -m pytest            # 171 tests (~40 s); the real-sample tests skip automatically if input/ is absent
```

Tests use synthetic, fictional data (`tests/helpers.py`) with a known opening balance and
transactions; totals, running balances, ADB, monthly aggregates, EMI/salary/bounce detection,
OCR fallback (fake provider), invalid/encrypted PDFs, report content, JSON serialization, and
the Django API (validation, authentication, ownership, retention, path traversal, temp files)
are asserted exactly. `tests/test_audit_regressions.py` guards every defect found in the
production-readiness audit, including a 120-file corrupted-PDF sweep that must produce only
engine errors and never a `VERIFIED` result with wrong totals, and a check that the engine
imports and runs with Django unavailable.

## Audit (2026-10-23)

Defects found and fixed: `.01` amounts (no leading zero) were dropped (one Axis row); the
former ₹0.01 default tolerance hid that missing paisa (default is now exact); unreadable amount
cells were treated as blank; long digit strings crashed `Decimal` quantisation; damaged PDFs
could raise raw PyMuPDF errors; damaged/unreadable pages and unanchored trailing rows could
still yield `VERIFIED` (now detected via MuPDF diagnostics, page classification and a row
census); EMI/bounce disclaimers could be orphaned on the next report page; trailer pages between
statement periods raised false warnings. Verified: report figures equal the structured result
(267 values across four reports); Django auth/ownership (404 for other users), temp upload
files removed, logs free of names, account numbers, narrations and passwords.

## Adding a bank-specific parser

Only add one when a real statement shows the generic parser cannot read it.

```python
from bank_statement_engine.parsers.generic import GenericTableParser
from bank_statement_engine.parsers.registry import registry

@registry.register
class ExampleBankParser(GenericTableParser):
    name = "example_bank"
    bank_codes = ("EXAMPLE",)                     # BankProfile code (add a profile in registry.py)

    @classmethod
    def detect(cls, first_page, bank_code):
        # Return False for layouts you do not recognise -> the generic parser is used instead.
        return bank_code in cls.bank_codes and "Txn Posted Date" in first_page.text

    def postprocess_row(self, row):
        # Adjust a RawRow (e.g. move a misplaced reference out of the narration).
        return row
```

Then add a synthetic fixture reproducing the layout to `tests/` and a test asserting exact
reconciliation. Parsers can also be supplied per analyzer: `BankStatementAnalyzer(parser_registry=my_registry)`.

## Known limitations

* Only the ten sample layouts above have been verified; other banks are best effort.
* Real OCR (Tesseract) is untested here; OCR quality depends on scan quality, and OCR'd
  statements may need a non-zero `balance_tolerance` (review breaks before raising it).
* `CHAIN_ONLY` statements (BOI, PNB, ICICI, Equitas, HDFC samples): rows missing at the very
  start/end of the readable text cannot be excluded from balances alone; the row census and
  page-damage checks reduce but do not eliminate this.
* Dates are not cross-checkable by balances: a misread date with correct amounts would pass.
* Counterparty extraction is heuristic; own-account detection needs the holder name (read
  from the statement header, or supplied) and two matching name tokens.
* Salary, recurring income and EMI obligations are pattern-based estimates; loan principal,
  interest rates and sanctioned amounts are never inferred.
* Printed per-period totals in multi-period files are not compared with whole-file totals.
* The engine silences MuPDF's stderr diagnostics process-wide (to keep document fragments out
  of logs) and serialises page text extraction behind a lock to read them safely.
* Celery integration is a documented stub and has not been run against a broker.
