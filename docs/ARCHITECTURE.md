# Architecture and data model

## Components

```
bank_statement_engine/            framework-independent engine (no Django imports)
├── pipeline.py                   BankStatementAnalyzer – public API, orchestration, timeouts
├── config.py                     EngineConfig (ocr / limits / analysis / report sections)
├── exceptions.py                 BankStatementError hierarchy with safe messages
├── schemas.py                    Transaction, StatementMetadata, ValidationReport, RiskIndicator, AnalysisResult
├── extractors/
│   ├── base.py                   Word / PageContent model, OCRProvider interface
│   ├── pdf_text.py               DocumentExtractor: PyMuPDF text, ruling lines, per-page OCR fallback
│   └── ocr.py                    TesseractOCRProvider (optional, local)
├── parsers/
│   ├── layout.py                 line clustering, header → columns, numeric/text calibration,
│   │                             header-less layout inference, row segmentation
│   ├── generic.py                GenericTableParser (works for all 10 sample banks)
│   ├── metadata.py               bank / IFSC / masked account / period / printed summary
│   ├── registry.py               BankProfile list, ParserRegistry (bank-specific parsers plug in here)
│   └── base.py                   RawRow, ParsedStatement, StatementParser ABC
├── normalizers/                  dates, amounts (Decimal), narration (channel, counterparty), transactions
├── analysis/                     reconciliation, categorize, balances, monthly, income, expenses,
│                                 emi, bounces, cashflow, risk_indicators, data_quality
├── reports/                      ReportLab PDF: theme, flowables, charts, templates/*.json, fonts/
└── utils/                        serialization, redaction, input handling

bank_statement_django/            thin Django + DRF layer (models, services, views, urls, permissions)
demo_project/                     runnable Django project wiring the app
```

## Pipeline

1. **Input** (`utils/io.py`): path, bytes or binary file object → bytes. Size limit and `%PDF-`
   signature are enforced before parsing.
2. **Extraction** (`extractors/pdf_text.py`): the PDF is opened from memory; encrypted files
   need a password; page-count limit. Pages are processed lazily. Each page yields positioned
   words plus the y-positions of horizontal ruling lines. A page with too little readable text
   is OCR'd when `ocr.mode="auto"` and an OCR provider is available.
3. **Parser selection** (`parsers/registry.py`): the bank is identified from page-1 content
   (IFSC next to its label, then bank-name patterns) — never from the file name. A
   bank-specific parser is used if one is registered and recognises the layout; otherwise
   the generic parser.
4. **Generic table parsing** (`parsers/layout.py`, `parsers/generic.py`):
   * words → visual lines;
   * header found from vocabulary (Date, Narration/Particulars/Description/Remarks,
     Withdrawal/Debit, Deposit/Credit, Balance …), including multi-line headers;
   * numeric columns are calibrated to where amounts are actually printed (amounts are
     often right-aligned far from their label); text columns are widened to whitespace
     gutters (labels are centred, text is left-aligned);
   * if the header is not text (e.g. SBI draws it as vector graphics) columns are
     **inferred** from the data: dates on the left, amount clusters on the right, right-most
     = balance; debit/credit orientation is verified against the running balance;
   * row segmentation uses ruling lines when each dated row has its own ruled band,
     otherwise vertical gaps (handles top-, centre- and bottom-aligned cells);
   * narration lines continuing from the previous page are attached and flagged.
5. **Normalisation** (`normalizers/transactions.py`): newest-first statements are re-ordered,
   negative withdrawals (reversals) become credits, a single "Amount" column takes its
   direction from a Dr/Cr marker or — flagged — from the balance. Every interpretation is
   recorded as a flag and a validation issue.
6. **Enrichment** (`analysis/categorize.py`): normalised narration, channel, counterparty,
   own-account detection, rule-based category with rule id and confidence.
7. **Reconciliation** (`analysis/reconciliation.py`): see below.
8. **Analysis**: balances (EOD series, ADB), monthly, income, expenses, EMI, bounces,
   cash flow, risk indicators, data quality.
9. **Report** (`reports/pdf_report.py`) and JSON (`AnalysisResult.to_dict()`).

## Canonical transaction schema

| Field | Type (JSON) | Notes |
|---|---|---|
| `id` | string | `T00001` …, chronological order after normalisation |
| `date` | `YYYY-MM-DD` | transaction / posting date |
| `value_date` | `YYYY-MM-DD` or null | when printed |
| `narration` | string | original narration (wrapped lines joined) |
| `narration_normalized` | string | upper-cased, whitespace collapsed |
| `reference_number` | string or null | cheque / reference column |
| `transaction_type` | `CREDIT` \| `DEBIT` | always consistent with the amounts |
| `debit`, `credit` | decimal string | exactly one is non-zero |
| `balance` | decimal string or null | printed running balance (Dr balances negative) |
| `currency` | `INR` | |
| `category` | string | see `CATEGORY_LABELS` |
| `category_confidence` | number 0–1 | |
| `category_rule` | string | id of the matching rule |
| `channel` | string | UPI, NEFT, IMPS, RTGS, ATM, CASH, NACH, CARD, CHEQUE, INTERNAL, CHARGES, INTEREST, OTHER |
| `counterparty` | string or null | best effort, as printed (banks truncate names) |
| `is_self_transfer` | bool | holder-name match (≥ 2 name tokens) or own-account wording |
| `source_page` | int | page the row was read from |
| `source_text` | string or null | only when `analysis.keep_source_text=true` |
| `flags` | string[] | e.g. `REORDERED_WITHIN_DAY`, `DUPLICATE_LOOKING`, `DIRECTION_INFERRED_FROM_BALANCE` |

## Serialization strategy

`AnalysisResult.to_dict()` / `to_json()` return JSON-compatible data:
`Decimal` → string with two decimals (`"1234.50"`), `date` → `"YYYY-MM-DD"`,
`datetime` → ISO 8601 with offset. Inside Python, money is always `Decimal`.

Result top-level keys: `report_id, engine_version, generated_at, status, source_sha256,
metadata, summary, overview, validation, monthly, balances, income, expenses, emi, bounces,
cashflow, risk_indicators, data_quality, limitations, transactions`.

The holder name is excluded from `metadata` unless `report.show_holder_name=true`; account
numbers are only ever stored masked (`XXXXXX1281`).

## Validation and status

Checks: running-balance chain, opening balance → first transaction, closing balance → last
transaction, `opening + credits − debits = closing`, printed totals and Dr/Cr counts, dates
within the printed period and in order, duplicate-looking rows (kept), pages inside the
transaction range without rows, unreadable (scanned without OCR) and damaged pages (MuPDF
decode diagnostics), unreadable amount cells, and a **row census**: a layout-independent count
of lines carrying a date and an amount, compared with parsed rows per page.

The default `balance_tolerance` is **₹0.00**: text extraction is exact to the paisa, and any
tolerance can hide a missed one-paisa row. `validation.completeness` is
`CONFIRMED_BY_PRINTED_BALANCES` when printed opening and closing balances bound the chain,
otherwise `CHAIN_ONLY` (with INFO issues naming the unprinted boundary).

* **VERIFIED** – every balance link checked and consistent, no error-level issue.
* **REQUIRES_REVIEW** – any error-level issue or unverifiable data (e.g. no balance column).
* **FAILED** – more than `max_chain_break_ratio_for_review` (5 %) of links broken.

Same-day ordering: when the printed order of a day's transactions breaks the chain but a
posting order exists that chains exactly, that day is re-sequenced, each row is flagged
`REORDERED_WITHIN_DAY` and an INFO issue lists the rows. Amounts and balances never change.

Files that concatenate several statement periods (seen in the HDFC and IDFC samples) are
analysed as one history; balance continuity across periods is checked, while per-period
printed totals are not compared with whole-file totals (an INFO issue says so).

## Risk indicators

Each indicator has `code, severity (HIGH|MEDIUM|LOW|INFO), title, description, evidence[],
confidence, limitations[]`. Rules and thresholds live in `analysis/risk_indicators.py` and
`AnalysisConfig`. There is no score, eligibility percentage or lending decision.

| Code | Rule (defaults) |
|---|---|
| `DATA_NOT_FULLY_RECONCILED` | status ≠ VERIFIED |
| `LOW_BALANCE_FREQUENCY` | ≥ 20 % of days with EOD < ₹1,000 |
| `NEGATIVE_BALANCE` | any negative EOD balance |
| `PAYMENT_RETURN_EVIDENCE` | narration explicitly mentions return/bounce/return charges |
| `POSSIBLE_REVERSED_PAYMENTS` | debit followed ≤ 3 days by same-amount credit worded as reversal |
| `HIGH_CASH_WITHDRAWALS` / `HIGH_CASH_DEPOSITS` | ≥ 30 % of debits / credits |
| `INCOME_VOLATILITY` | CV of monthly external inflows ≥ 50 % |
| `HIGH_REPAYMENT_BURDEN` | estimated EMI ≥ 50 % of estimated monthly income |
| `FREQUENT_MONTHLY_DEFICITS` | negative net flow in more than half of full months |
| `INFLOW_CONCENTRATION` | top external source ≥ 60 % of external inflows |
| `HIGH_SELF_TRANSFER_INFLOWS` | ≥ 30 % of credits from own accounts |
| `PASS_THROUGH_ACTIVITY` | credit ≥ ₹50,000 followed within 1 day by ≥ 80 % outflow |
| `GAMING_BETTING_PAYMENTS` | payments matching gaming/betting platform names |
| `LARGE_TRANSACTIONS` | ≥ 10× median and ≥ ₹50,000 |
| `TRANSACTION_VOLUME_CHANGE` | month-to-month count change ≥ 60 % |
| `DUPLICATE_LOOKING_TRANSACTIONS` | same date, amount and narration |
