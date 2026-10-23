"""Multi-page PDF analysis report (ReportLab).

Layout and styling follow the reference analysis report in ``sample/``: navy
header band, quick-navigation chips, numbered section bars, label/value grids,
KPI cards, pill badges, spending bars, a monthly credit/debit chart and an
indicator scorecard. Deliberate departures from the reference:

* no "credit score" and no lending verdict — the header shows the
  reconciliation status and the closing panel summarises indicators;
* no third-party branding; the organisation name is configurable;
* values that cannot be calculated are shown as "Not available".

All statement text is escaped before being placed in Paragraph markup.
"""

from __future__ import annotations

from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Frame,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from .. import __version__
from ..analysis.categorize import CATEGORY_LABELS
from ..config import ReportConfig
from ..schemas import AnalysisResult
from .charts import eod_balance_chart, monthly_flow_chart
from .flowables import (
    draw_logo,
    Callout,
    Dot,
    HBarList,
    HeaderBand,
    KpiCards,
    LogoChip,
    MoneyTiles,
    NavChips,
    Pill,
    SectionBar,
    StatusBox,
    SubHeading,
)
from .assets import load_logo
from .fonts import draw_string, markup
from .formatting import NA, fdate, fdatetime, inr, inr0, pct, title_case
from .theme import Theme, load_template, tint

_FOIR_TEXT = {"NO_RECURRING_EMI_DETECTED": "Not available", "INCOME_UNAVAILABLE": "Insufficient data"}
_FOIR_SHORT = {"NO_RECURRING_EMI_DETECTED": "No EMI found", "INCOME_UNAVAILABLE": "Insufficient data"}
_FOIR_REASON = {"NO_RECURRING_EMI_DETECTED": "No recurring EMI detected",
                "INCOME_UNAVAILABLE": "Monthly income could not be identified"}

STATUS_TEXT = {
    "VERIFIED": ("RECONCILED", "Balances chain end-to-end"),
    "REQUIRES_REVIEW": ("REVIEW NEEDED", "Reconciliation issues found"),
    "FAILED": ("NOT RELIABLE", "Data failed reconciliation"),
}
TYPE_LABELS = {"SELF": "Self", "EXTERNAL": "External", "CASH": "Cash", "THIRD_PARTY": "Third party",
               "MIXED": "Mixed", "REFUND_REVERSAL": "Refund", "LOAN_DISBURSAL": "Loan", "INVESTMENT_INCOME": "Investment",
               "INTEREST": "Interest"}


class _NumberedCanvas(Canvas):
    """Canvas that knows the total page count when drawing footers."""

    footer = None  # set per build

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_pages: list[dict] = []

    def showPage(self):
        self._saved_pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved_pages)
        for state in self._saved_pages:
            self.__dict__.update(state)
            if self.footer:
                self.footer(self, total)
            super().showPage()
        super().save()


class ReportBuilder:
    def __init__(self, result: AnalysisResult, config: ReportConfig):
        self.r = result
        self.cfg = config
        self.tpl = load_template(config.template)
        self.t = Theme.from_template(self.tpl, config.assets_dir)
        self.R = self.t.rupee
        self.logo = load_logo(config.logo_path, config.assets_dir) if config.show_logo else None
        margin = float(self.tpl["page"].get("margin_mm", 13)) * mm
        self.margin = margin
        self.page_w, self.page_h = A4
        self.width = self.page_w - 2 * margin
        t = self.t
        self.st = {
            "body": ParagraphStyle("body", fontName=t.font, fontSize=7.4, leading=10, textColor=t.ink),
            "small": ParagraphStyle("small", fontName=t.font, fontSize=6.4, leading=8.4, textColor=t.muted),
            "cell": ParagraphStyle("cell", fontName=t.font, fontSize=6.7, leading=8.4, textColor=t.ink),
            "cell_r": ParagraphStyle("cell_r", fontName=t.font, fontSize=6.7, leading=8.4, textColor=t.ink, alignment=2),
            "cell_b": ParagraphStyle("cell_b", fontName=t.font_medium, fontSize=6.7, leading=8.4, textColor=t.ink),
            "label": ParagraphStyle("label", fontName=t.font_medium, fontSize=6.8, leading=8.6, textColor=t.navy),
            "head": ParagraphStyle("head", fontName=t.font_heading_semi, fontSize=6.2, leading=7.6, textColor=colors.white,
                                   alignment=1),
            "bullet": ParagraphStyle("bullet", fontName=t.font, fontSize=7.4, leading=10.2, textColor=t.ink,
                                     leftIndent=10, bulletIndent=2, spaceAfter=2, bulletFontName=t.font),
            "h4": ParagraphStyle("h4", fontName=t.font_heading_semi, fontSize=7.8, leading=10, textColor=t.navy, spaceBefore=4),
            "center": ParagraphStyle("center", fontName=t.font, fontSize=7, leading=9, textColor=t.ink, alignment=1),
        }

    # ------------------------------------------------------------------ helpers
    def p(self, text, style="body") -> Paragraph:
        st = self.st[style]
        return Paragraph(markup(text, st.fontName), st)

    def bullet(self, text: str) -> Paragraph:
        st = self.st["bullet"]
        return Paragraph(markup(text, st.fontName), st, bulletText="•")

    def esc(self, text) -> str:
        return escape(str(text)) if text is not None else NA

    def money(self, value) -> str:
        """Rupee amount in full Indian format, e.g. ₹2,70,422.77 (used for every amount in tables)."""
        return inr(value, self.R)

    def kv(self, pairs: list[tuple[str, str]], highlight: set[str] | None = None) -> Table:
        """Two pairs per row: label | value | label | value."""
        highlight = highlight or set()
        rows, styles = [], []
        for i in range(0, len(pairs), 2):
            chunk = pairs[i:i + 2]
            row = []
            for label, value in chunk:
                row += [self.p(self.esc(label), "label"), self.p(value if isinstance(value, str) else str(value), "cell_b")]
            while len(row) < 4:
                row += ["", ""]
            rows.append(row)
            for j, (label, _) in enumerate(chunk):
                if label in highlight:
                    styles.append(("BACKGROUND", (j * 2, len(rows) - 1), (j * 2 + 1, len(rows) - 1), tint(self.t.accent, 0.9)))
        w = self.width
        table = Table(rows, colWidths=[w * 0.2, w * 0.3, w * 0.2, w * 0.3])
        base = [
            ("GRID", (0, 0), (-1, -1), 0.5, self.t.border),
            ("BACKGROUND", (0, 0), (0, -1), self.t.label_bg),
            ("BACKGROUND", (2, 0), (2, -1), self.t.label_bg),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ]
        table.setStyle(TableStyle(base + styles))
        return table

    def table(self, headers: list[str], rows: list[list], widths: list[float], align: list[str] | None = None,
              font_rows=None) -> Table:
        """Data table with a navy header row and zebra striping. ``widths`` are fractions of the page width."""
        align = align or ["LEFT"] * len(headers)
        data = [[self.p(self.esc(h).upper(), "head") for h in headers]]
        for row in rows:
            cells = []
            for value, a in zip(row, align):
                if isinstance(value, str):
                    cells.append(self.p(value, "cell_r" if a == "RIGHT" else "cell"))
                else:
                    cells.append(value)
            data.append(cells)
        t = Table(data, colWidths=[self.width * f for f in widths], repeatRows=1)
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), self.t.navy),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, self.t.border),
            ("BOX", (0, 0), (-1, -1), 0.5, self.t.border),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3.6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3.6),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ]
        for i in range(1, len(data)):
            if i % 2 == 0:
                style.append(("BACKGROUND", (0, i), (-1, i), self.t.zebra))
        for j, a in enumerate(align):
            style.append(("ALIGN", (j, 1), (j, -1), a))
        t.setStyle(TableStyle(style))
        return t

    def pill(self, text: str, kind: str = "neutral") -> Pill:
        color = {"good": self.t.positive, "bad": self.t.negative, "warn": self.t.warning, "info": self.t.info}.get(
            kind, self.t.muted)
        return Pill(self.t, text, color)

    def type_pill(self, code: str) -> Pill:
        kind = {"SELF": "bad", "EXTERNAL": "good", "THIRD_PARTY": "good", "CASH": "warn"}.get(code, "info")
        return self.pill(TYPE_LABELS.get(code, title_case(code)), kind)

    def empty(self, text: str) -> Paragraph:
        return self.p(f"<i>{self.esc(text)}</i>", "small")

    # ------------------------------------------------------------------ build
    def build(self) -> bytes:
        buf = BytesIO()
        r = self.r
        doc = BaseDocTemplate(
            buf, pagesize=A4, leftMargin=self.margin, rightMargin=self.margin, topMargin=self.margin,
            bottomMargin=self.margin + 6 * mm,
            title=f"Bank Statement Analysis — {r.report_id}",
            author=self.cfg.organization_name or "bank_statement_engine",
            subject="Automated bank statement analysis (not issued by any bank)",
            creator=f"bank_statement_engine {__version__}",
        )
        frame = Frame(self.margin, self.margin + 6 * mm, self.width, self.page_h - 2 * self.margin - 6 * mm, id="main",
                      leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        doc.addPageTemplates([PageTemplate(id="page", frames=[frame])])

        theme, cfg = self.t, self.cfg
        generated = fdatetime(r.generated_at, cfg.timezone_label)

        logo = self.logo

        def footer(canv: Canvas, total: int) -> None:
            canv.saveState()
            canv.setStrokeColor(theme.border)
            canv.line(self.margin, self.margin + 3 * mm, self.page_w - self.margin, self.margin + 3 * mm)
            x = self.margin
            if logo is not None:
                chip_h = 10
                lw, _ = logo.size_for_height(chip_h - 4)
                canv.setFillColor(theme.navy)
                canv.roundRect(x, self.margin - 2.5, lw + 8, chip_h, 2.5, stroke=0, fill=1)
                draw_logo(canv, logo, x + 4, self.margin - 0.5, lw, chip_h - 4)
                x += lw + 14
            canv.setFillColor(theme.muted)
            draw_string(canv, x, self.margin, f"Report {r.report_id}  ·  Generated {generated}  ·  "
                                              "Confidential — automated analysis, not issued by any bank", theme.font, 6)
            draw_string(canv, self.page_w - self.margin, self.margin, f"Page {canv.getPageNumber()} of {total}",
                        theme.font, 6, anchor="end")
            canv.restoreState()

        canvas_cls = type("ReportCanvas", (_NumberedCanvas,), {"footer": staticmethod(footer)})
        doc.build(self.story(), canvasmaker=canvas_cls)
        return buf.getvalue()

    def story(self) -> list:
        r, t = self.r, self.t
        status = r.validation.status
        label, caption = STATUS_TEXT.get(status, (status, ""))
        o = r.overview
        lines = [f"{(r.metadata.bank_name or 'Bank not identified')}  ·  {fdate(o['period_start'])} → {fdate(o['period_end'])}",
                 f"Generated {fdatetime(r.generated_at, self.cfg.timezone_label)}"]
        story: list = [
            HeaderBand(t, self.cfg.title, self.cfg.subtitle, lines, label,
                       f"{caption}\nRef {r.report_id}\n{o['transaction_count']} transactions analysed",
                       t.status_color(status), org=self.cfg.organization_name, logo=self.logo),
            Spacer(1, 8),
        ]
        sections = [s for s in self.tpl["sections"] if hasattr(self, f"sec_{s}")]
        nav = [(self.tpl["nav_labels"].get(s, title_case(s)), f"sec_{s}") for s in sections if s in self.tpl["nav_labels"]]
        story += [NavChips(t, nav), Spacer(1, 4)]
        for index, name in enumerate(sections, start=1):
            title = self.tpl["titles"].get(name, title_case(name))
            content = _bind_subheadings(getattr(self, f"sec_{name}")())
            bar = SectionBar(t, title, index, f"sec_{name}")
            story.append(CondPageBreak(42 * mm))
            if content:
                story.append(KeepTogether([bar, content[0]]) if _small(content[0]) else bar)
                story += content[1:] if _small(content[0]) else content
            else:
                story.append(bar)
        story += self.footer_cards()
        return story

    # ------------------------------------------------------------------ sections
    def sec_financial_summary(self) -> list:
        """Actual account totals in rupees — never a score, rating or percentage."""
        r, o, m, v = self.r, self.r.overview, self.r.metadata, self.r.validation
        bal = r.balances
        verified = v.status == "VERIFIED"
        source = {"statement": "Printed on statement", "derived": "Derived from first transaction",
                  "last_transaction": "Balance after last transaction"}
        totals_caption = "Reconciled with running balance" if verified else "Extracted — reconciliation incomplete"
        adb = bal.get("average_daily_balance") if bal.get("available") else None
        adb_caption = ("Mean end-of-day balance" if bal.get("reliable") else
                       "Estimate — statement not fully reconciled") if adb is not None else "Insufficient data"
        net = o["net_cash_flow"]
        tiles = [
            ("Total credits (money received)", self.money(o["total_credits"]),
             f"{o['credit_count']} credit transactions · {totals_caption}", self.t.positive),
            ("Total debits (money spent)", self.money(o["total_debits"]),
             f"{o['debit_count']} debit transactions · {totals_caption}", self.t.negative),
            ("Net cash flow", self.money(net), "Total credits − total debits",
             self.t.positive if net >= 0 else self.t.negative),
            ("Opening balance", self.money(o["opening_balance"]),
             source.get(o["opening_balance_source"], "Not available"), self.t.navy),
            ("Closing balance", self.money(o["closing_balance"]),
             source.get(o["closing_balance_source"], "Not available"), self.t.navy),
            ("Average daily balance", self.money(adb) if adb is not None else "Not available", adb_caption,
             self.t.accent),
        ]
        period_src = "printed statement period" if o.get("period_source") == "statement" else "transaction dates"
        pairs = [
            ("Bank", self.esc(m.bank_name or NA)),
            ("Account number", self.esc(m.account_number_masked or NA)),
            ("Statement start date", fdate(o["period_start"])),
            ("Statement end date", fdate(o["period_end"])),
            ("Total transactions", str(o["transaction_count"])),
            ("Credit / debit transactions", f"{o['credit_count']} / {o['debit_count']}"),
            ("Account type", self.esc(title_case(m.account_type) if m.account_type else NA)),
            ("IFSC", self.esc(m.ifsc or NA)),
            ("Months covered", f"{o['months_covered']} ({o['calendar_months']} calendar months)"),
            ("Reconciliation", f"{title_case(v.status)} · {'printed opening & closing' if v.completeness == 'CONFIRMED_BY_PRINTED_BALANCES' else 'running balance only'}"),
        ]
        if self.cfg.show_holder_name and m.holder_name:
            pairs.insert(0, ("Account holder", self.esc(m.holder_name)))
        note = (f"Opening {self.money(o['opening_balance'])} + credits {self.money(o['total_credits'])} − debits "
                f"{self.money(o['total_debits'])} = closing {self.money(v.computed_closing_balance)}"
                if v.computed_closing_balance is not None else "Opening or closing balance not available.")
        return [MoneyTiles(self.t, tiles), self.kv(pairs),
                self.p(f"Period source: {period_src}. {self.esc(note)}. All amounts in Indian Rupees.", "small")]

    def analytical_highlights(self) -> list:
        """Analytical estimates, visually and verbally separate from account totals."""
        inc, emi, cf, bal, r = self.r.income, self.r.emi, self.r.cashflow, self.r.balances, self.r
        foir = emi.get("foir_pct")
        cards = [
            ("Est. monthly income", self.money(inc.get("estimated_monthly_income")),
             "Salary-labelled credits" if inc["salary_like"]["status"] == "SALARY_LIKE_RECURRING" else "Median external inflow",
             self.t.accent),
            ("FOIR (estimate)", pct(foir) if foir is not None else _FOIR_SHORT.get(emi.get("foir_status"), NA),
             "EMI ÷ income × 100", self.t.positive),
            ("Own-account inflows", pct(cf.get("self_transfer_in_pct")), "Share of all credits", self.t.positive),
            ("Bounce / return incidents", str(r.bounces.get("confirmed_incident_count", 0)), "One per returned instrument",
             self.t.bars[1]),
            ("Days below " + inr0(bal.get("low_balance_threshold"), self.R),
             pct(bal.get("pct_days_below_low_threshold")) if bal.get("available") else NA, "Share of days tracked",
             self.t.navy),
        ]
        return [SubHeading(self.t, "Analytical highlights — estimates, not account totals"), KpiCards(self.t, cards)]

    def sec_profile(self) -> list:
        r, o, m = self.r, self.r.overview, self.r.metadata
        bal = r.balances
        adb = bal.get("average_daily_balance")
        adb_text = self.money(adb) + ("" if bal.get("reliable") or adb is None else " (estimate)")
        pairs = [
            ("Bank", self.esc(m.bank_name or NA)),
            ("Account", self.esc(m.account_number_masked or NA)),
            ("IFSC", self.esc(m.ifsc or NA)),
            ("Account type", self.esc(title_case(m.account_type) if m.account_type else NA)),
            ("Period", f"{fdate(o['period_start'])} → {fdate(o['period_end'])} ({o['months_covered']} months)"),
            ("Transactions", f"{o['transaction_count']} ({o['credit_count']} Cr / {o['debit_count']} Dr)"),
            ("Total credits", self.money(o["total_credits"])),
            ("Total debits", self.money(o["total_debits"])),
            ("Avg daily balance", adb_text),
            ("Net cash flow", self.money(o["net_cash_flow"])),
        ]
        if self.cfg.show_holder_name and m.holder_name:
            pairs.insert(0, ("Account holder", self.esc(m.holder_name)))
        inc, emi, cf = r.income, r.emi, r.cashflow
        ratio = emi.get("estimated_emi_to_income_pct")
        cards = [
            ("Income / month (est.)", self.money(inc.get("estimated_monthly_income")),
             "Salary-labelled credits" if inc["salary_like"]["status"] == "SALARY_LIKE_RECURRING" else "Median external inflow",
             self.t.accent),
            ("EMI to income (est.)", pct(ratio) if ratio is not None else "None detected",
             f"EMI {self.money(emi.get('estimated_monthly_emi'))}/month", self.t.positive),
            ("Own-account transfers", pct(cf.get("self_transfer_in_pct")), "Share of all inflows", self.t.positive),
            ("Avg EOD balance", self.money(bal.get("average_daily_balance")),
             f"{bal.get('days_tracked', 0)} days tracked", self.t.navy),
            ("Return events", str(r.bounces["confirmed_event_count"]), "Narration-confirmed", self.t.bars[1]),
        ]
        return [self.kv(pairs), KpiCards(self.t, cards)]

    def sec_executive_summary(self) -> list:
        r, o, v = self.r, self.r.overview, self.r.validation
        R = self.R
        points = [
            f"The statement covers <b>{fdate(o['period_start'])}</b> to <b>{fdate(o['period_end'])}</b> "
            f"({o['months_covered']} months) with <b>{o['transaction_count']}</b> transactions"
            + (f" from {self.esc(r.metadata.bank_name)}." if r.metadata.bank_name else "."),
        ]
        if v.status == "VERIFIED":
            points.append(
                f"<b>Reconciled:</b> all {v.balance_links_checked} running-balance links agree; opening "
                f"{inr(o['opening_balance'], R)} + credits {inr(o['total_credits'], R)} − debits "
                f"{inr(o['total_debits'], R)} = closing {inr(o['closing_balance'], R)}.")
        else:
            errors = sum(1 for i in v.issues if i.severity == "ERROR")
            points.append(f"<b>Requires review:</b> {v.balance_chain_breaks} balance-chain break(s) and {errors} "
                          "error-level issue(s) were found. Figures below should be treated with caution.")
        cf = r.cashflow
        months = len(r.monthly)
        points.append(
            f"Total inflows {inr(o['total_credits'], R)} and outflows {inr(o['total_debits'], R)} "
            f"(net {inr(o['net_cash_flow'], R)}); net inflow was positive in {len(cf['surplus_months'])} of {months} months.")
        inc = r.income
        points.append(
            f"Estimated monthly income {inr(inc['estimated_monthly_income'], R)} — {self.esc(inc['estimated_monthly_income_basis'])} "
            f"Stability: <b>{title_case(inc['income_stability']['label'])}</b>"
            + (f" (CV {inc['income_stability']['cv_pct']}%)." if inc['income_stability']['cv_pct'] is not None else "."))
        emi = r.emi
        if emi["recurring_obligations"]:
            points.append(
                f"{len(emi['recurring_obligations'])} recurring repayment pattern(s) detected; "
                f"{emi['active_recurring_loan_count']} seen recently, estimated EMI "
                f"{inr(emi['estimated_monthly_emi'], R)}/month (estimate, not a verified liability).")
        else:
            points.append("No recurring EMI/loan repayment pattern met the detection rules.")
        bal = r.balances
        if bal.get("available"):
            points.append(
                f"Average daily balance {inr(bal['average_daily_balance'], R)}; end-of-day balance was below "
                f"{inr0(bal['low_balance_threshold'], R)} on {pct(bal['pct_days_below_low_threshold'])} of days.")
        b = r.bounces
        points.append(f"{b['confirmed_event_count']} narration-confirmed return/bounce event(s)"
                      + (f" and {b['pattern_based_count']} possible reversal pattern(s)." if b['pattern_based_count'] else "."))
        c = o["indicator_counts"]
        points.append(f"Indicators: <b>{c['HIGH']}</b> high, <b>{c['MEDIUM']}</b> medium, <b>{c['LOW']}</b> low, "
                      f"{c['INFO']} informational. No score or lending decision is produced.")
        return self.analytical_highlights() + [self.bullet(pt) for pt in points]

    def sec_reconciliation(self) -> list:
        r, v, dq = self.r, self.r.validation, self.r.data_quality
        status_kind = {"VERIFIED": "good", "REQUIRES_REVIEW": "warn"}.get(v.status, "bad")
        src = {"statement": "printed on statement", "derived": "derived from first transaction",
               "last_transaction": "last transaction balance"}
        pairs = [
            ("Status", f"<font color='{_hex(self._kind_color(status_kind))}'>{title_case(v.status)}</font>"),
            ("Extraction confidence", title_case(dq.get("extraction_confidence"))),
            ("Balance links checked", f"{v.balance_links_checked}"),
            ("Chain breaks", f"{v.balance_chain_breaks}"),
            ("Opening balance", f"{inr(v.opening_balance, self.R)} ({src.get(v.opening_balance_source, NA)})"),
            ("Closing balance", f"{inr(v.closing_balance, self.R)} ({src.get(v.closing_balance_source, NA)})"),
            ("Computed closing", inr(v.computed_closing_balance, self.R)),
            ("Pages", f"{dq.get('pages_with_transactions')} with transactions / {dq.get('page_count')} total"),
            ("Extraction method", title_case(dq.get("extraction_method"))
             + (f" (OCR pages: {len(dq['ocr_pages'])})" if dq.get("ocr_pages") else "")),
            ("Column layout", "Inferred from data" if dq.get("layout_inferred") else "Read from table header"),
            ("Completeness", "Bounded by printed opening & closing" if v.completeness == "CONFIRMED_BY_PRINTED_BALANCES"
             else "Running balance only (no printed boundary)"),
            ("Row census", "Every date+amount line parsed" if not any(i.code == "POSSIBLE_MISSED_ROWS" for i in v.issues)
             else "Unparsed transaction-like lines found"),
        ]
        out: list = [self.kv(pairs)]
        if v.checks:
            rows = []
            for c in v.checks:
                res = {True: self.pill("Pass", "good"), False: self.pill("Fail", "bad"), None: self.pill("N/A")}[c.passed]
                rows.append([self.esc(title_case(c.name)), self._fmt_check(c.expected), self._fmt_check(c.actual), res])
            out += [SubHeading(self.t, "Validation checks"),
                    self.table(["Check", "Expected", "Extracted", "Result"], rows, [0.46, 0.2, 0.2, 0.14],
                               ["LEFT", "RIGHT", "RIGHT", "CENTER"])]
        if v.issues:
            rows = []
            for i in sorted(v.issues, key=lambda i: {"ERROR": 0, "WARNING": 1, "INFO": 2}[i.severity])[:15]:
                kind = {"ERROR": "bad", "WARNING": "warn"}.get(i.severity, "info")
                rows.append([self.pill(i.severity, kind), self.esc(title_case(i.code)), self.esc(i.message)])
            out += [SubHeading(self.t, f"Validation notes ({len(v.issues)})"),
                    self.table(["Severity", "Code", "Detail"], rows, [0.11, 0.24, 0.65], ["CENTER", "LEFT", "LEFT"])]
        return out

    def sec_account_summary(self) -> list:
        o, cf = self.r.overview, self.r.cashflow
        pairs = [
            ("Opening → Closing", f"{self.money(o['opening_balance'])} → {self.money(o['closing_balance'])}"),
            ("Net flow (period)", self.money(o["net_cash_flow"])),
            ("Total transactions", f"{o['transaction_count']} ({o['credit_count']} Cr / {o['debit_count']} Dr)"),
            ("Min / Max balance", f"{self.money(o['min_observed_balance'])} — {self.money(o['max_observed_balance'])}"),
            ("Credit-to-debit ratio", str(cf.get("credit_to_debit_ratio") or NA)),
            ("Calendar months", str(o["calendar_months"])),
        ]
        return [self.kv(pairs)]

    def sec_income(self) -> list:
        inc = self.r.income
        stab = inc["income_stability"]
        pairs = [
            ("Gross monthly credit", self.money(inc["gross_monthly_credit"])),
            ("Own-account transfers in", f"{self.money(inc['self_transfers_in']['amount'])} "
                                         f"({pct(inc['self_transfers_in']['pct_of_credits'])})"),
            ("Loan disbursals in", f"{self.money(inc['loan_disbursals']['amount'])} ({pct(inc['loan_disbursals']['pct_of_credits'])})"),
            ("Refunds / reversals in", f"{self.money(inc['refunds_reversals']['amount'])} ({inc['refunds_reversals']['count']})"),
            ("Cash deposits", f"{self.money(inc['cash_deposits']['amount'])} ({pct(inc['cash_deposits']['pct_of_credits'])})"),
            ("Interest credited", self.money(inc["interest"]["amount"])),
            ("Est. monthly income", self.money(inc["estimated_monthly_income"])),
            ("Income stability (CV)", f"{pct(stab['cv_pct'])} — {title_case(stab['label'])}"),
        ]
        out: list = [self.kv(pairs, highlight={"Est. monthly income", "Income stability (CV)"})]
        rows = [[str(i), self.esc(s["source"]), str(s["count"]), self.money(s["amount"]), pct(s["pct_of_credits"], decimals=2),
                 self.type_pill(s["type"])] for i, s in enumerate(inc["top_income_sources"][:8], 1)]
        out += [SubHeading(self.t, "Top income sources (counterparty as printed)")]
        out += [self.table(["#", "Source / counterparty", "Txns", "Amount", "% share", "Type"], rows,
                           [0.05, 0.43, 0.08, 0.15, 0.13, 0.16], ["CENTER", "LEFT", "CENTER", "RIGHT", "RIGHT", "CENTER"])
                if rows else self.empty("No credits found.")]
        rec = inc["recurring_credits"]
        out.append(SubHeading(self.t, "Recurring credits (≥3 months, similar amount)"))
        if rec:
            rows = [[str(i), self.esc(x["source"]), self.money(x["median_amount"]), str(x["month_count"]),
                     str(x["typical_day_of_month"]), fdate(x["last_date"]),
                     self.pill("Salary label", "info") if x["is_salary_labelled"] else self.pill("Pattern")]
                    for i, x in enumerate(rec[:8], 1)]
            out.append(self.table(["#", "Source", "Typical amount", "Months", "Day", "Last seen", "Basis"], rows,
                                  [0.05, 0.37, 0.15, 0.09, 0.07, 0.13, 0.14],
                                  ["CENTER", "LEFT", "RIGHT", "CENTER", "CENTER", "CENTER", "CENTER"]))
        else:
            out.append(self.empty("No inflow met the recurring-income rules."))
        out.append(Callout(self.t, self.p(f"<b>Salary check:</b> {self.esc(inc['salary_like']['description'])}"),
                           self.t.info))
        return out

    def sec_monthly(self) -> list:
        rows = []
        for m in self.r.monthly:
            net = m["net_cash_flow"]
            color = _hex(self.t.positive if net >= 0 else self.t.negative)
            rows.append([self.esc(m["label"]) + (" *" if m["partial_month"] else ""), str(m["transaction_count"]),
                         self.money(m["total_credits"]), self.money(m["total_debits"]),
                         f"<font color='{color}'>{self.money(net)}</font>",
                         self.money(m["average_daily_balance"]), self.money(m["closing_balance"])])
        out: list = [self.table(["Month", "Txns", "Credits", "Debits", "Net", "Avg daily bal.", "Closing bal."], rows,
                                [0.13, 0.07, 0.16, 0.16, 0.16, 0.16, 0.16],
                                ["LEFT", "CENTER", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "RIGHT"])]
        if any(m["partial_month"] for m in self.r.monthly):
            out.append(self.p("* Partial month (the statement period starts or ends within the month).", "small"))
        if len(self.r.monthly) >= 2:
            out += [Spacer(1, 6), monthly_flow_chart(self.t, self.r.monthly, self.width)]
        return out

    def sec_spending(self) -> list:
        exp = self.r.expenses
        cats = exp["categories"][:12]
        if not cats:
            return [self.empty("No debits found.")]
        rows = [(c["label"], float(c["amount"]), f"{self.money(c['amount'])} ({pct(c['pct_of_debits'])})") for c in cats]
        out: list = [HBarList(self.t, rows)]
        if exp["insights"]:
            out.append(Callout(self.t, self.p("<b>Spending insight:</b> " + " ".join(self.esc(i) for i in exp["insights"])),
                               self.t.warning))
        return out

    def sec_emi(self) -> list:
        """FOIR & repayment obligations, from the engine's EMI/FOIR results (no recalculation here)."""
        emi = self.r.emi
        status = emi.get("foir_status")
        foir = emi.get("foir_pct")
        income = emi.get("foir_income_used")
        detected = emi.get("monthly_emi_status") == "DETECTED"
        if foir is None:
            foir_color = self.t.muted
        elif foir <= 40:
            foir_color = self.t.positive
        elif foir <= 60:
            foir_color = self.t.warning
        else:
            foir_color = self.t.negative
        conf = emi.get("foir_confidence")
        tiles = [
            ("Est. genuine monthly income", self.money(income) if income else "Insufficient data",
             "Excl. own transfers, refunds, loans, redemptions", self.t.accent),
            ("Est. monthly EMI burden", self.money(emi["estimated_monthly_emi"]) if detected else "None detected",
             f"{emi['active_recurring_loan_count']} recurring loan obligation(s) seen in last 45 days", self.t.warning),
            ("FOIR (EMI ÷ income × 100)", pct(foir) if foir is not None else _FOIR_TEXT.get(status, NA),
             f"Confidence: {title_case(conf)}" if conf else _FOIR_REASON.get(status, ""), foir_color),
        ]
        probable = emi.get("foir_probable_only_pct")
        loans, cards = emi.get("loan_repayment_debits", {}), emi["credit_card_payments"]
        pairs = [
            ("Recurring loan obligations", str(emi.get("recurring_loan_obligation_count", 0))),
            ("Seen in last 45 days", str(emi["active_recurring_loan_count"])),
            ("FOIR — probable EMIs only", pct(probable) if probable is not None else NA),
            ("FOIR status", title_case(status)),
            ("Loan / EMI debits in period", f"{self.money(loans.get('amount'))} ({loans.get('count', 0)} debits)"),
            ("Credit card payments in period", f"{self.money(cards['amount'])} ({cards['count']} payments)"),
            ("All repayment-like debits", self.money(emi["total_repayment_debits"])),
            ("Income basis", self.esc(emi.get("foir_income_basis") or NA)),
        ]
        out: list = [MoneyTiles(self.t, tiles), self.kv(pairs)]
        if emi["recurring_obligations"]:
            level_pill = {"PROBABLE": ("Probable EMI", "warn"), "POSSIBLE": ("Possible EMI", "info"),
                          "CARD_PAYMENT": ("Card payment", "neutral")}
            rows = []
            for i, o in enumerate(emi["recurring_obligations"][:12], 1):
                text, kind = level_pill.get(o.get("evidence_level"), ("Pattern", "neutral"))
                seen = self.pill("Seen recently", "warn") if o["status"] == "SEEN_RECENTLY" else self.pill("Not recent", "good")
                rows.append([str(i), self.esc(o["lender_or_payee"]), self.pill(text, kind),
                             self.money(o["estimated_monthly_amount"]), str(o["occurrences"]), self.money(o["total_paid"]),
                             fdate(o["last_date"]), seen])
            out += [SubHeading(self.t, "Detected recurring repayment obligations"),
                    self.table(["#", "Lender / payee", "Evidence", "Monthly amount", "Txns", "Total paid", "Last paid", "Status"],
                               rows, [0.04, 0.27, 0.13, 0.13, 0.06, 0.14, 0.11, 0.12],
                               ["CENTER", "LEFT", "CENTER", "RIGHT", "CENTER", "RIGHT", "CENTER", "CENTER"])]
        else:
            out.append(self.empty("No recurring EMI or loan repayment pattern met the detection rules "
                                  "(same payee and amount in at least three months)."))
        notes = list(emi.get("foir_notes") or []) + [
            emi["note"],
            "Probable = narration names a lender or EMI/loan; Possible = unspecified recurring NACH/ECS debit. "
            "None of these is a verified loan liability.",
        ]
        # Keep the qualifying notes on the same page as the figures they qualify.
        out.append(KeepTogether([out.pop()] + [self.p("• " + self.esc(n), "small") for n in notes]))
        return out

    def sec_investments(self) -> list:
        txns = [t for t in self.r.transactions if t.category in ("INVESTMENT", "INSURANCE", "INVESTMENT_INCOME")]
        if not txns:
            return [self.empty("No investment, SIP or insurance transactions were identified.")]
        cats = {c["category"]: c for c in self.r.expenses["categories"]}
        pairs = [
            ("Investments / SIPs (debits)", f"{self.money(cats.get('INVESTMENT', {}).get('amount'))} "
                                             f"({cats.get('INVESTMENT', {}).get('count', 0)})"),
            ("Insurance premiums", f"{self.money(cats.get('INSURANCE', {}).get('amount'))} "
                                   f"({cats.get('INSURANCE', {}).get('count', 0)})"),
            ("Redemptions / dividends", f"{self.money(self.r.income['investment_income']['amount'])} "
                                        f"({self.r.income['investment_income']['count']})"),
        ]
        rows = [[str(i), fdate(t.date), self.esc(t.counterparty or t.narration[:40]), self.esc(CATEGORY_LABELS.get(t.category, t.category)),
                 self.money(t.amount), self.pill("Credit", "good") if t.is_credit else self.pill("Debit", "info")]
                for i, t in enumerate(sorted(txns, key=lambda t: -t.amount)[:15], 1)]
        return [self.kv(pairs), SubHeading(self.t, f"Largest investment & insurance transactions ({len(txns)} total)"),
                self.table(["#", "Date", "Entity", "Type", "Amount", "Direction"], rows, [0.05, 0.12, 0.4, 0.17, 0.13, 0.13],
                           ["CENTER", "CENTER", "LEFT", "LEFT", "RIGHT", "CENTER"])]

    def sec_money_flow(self) -> list:
        exp = self.r.expenses

        def modes(items):
            rows = [[self.esc(m["label"]), str(m["count"]), self.money(m["amount"]), pct(m["pct"], decimals=2)] for m in items[:6]]
            data = [[self.p(h, "head") for h in ("MODE", "TXNS", "AMOUNT", "%")]] + [
                [self.p(r[0], "cell"), self.p(r[1], "cell"), self.p(r[2], "cell_r"), self.p(r[3], "cell_r")] for r in rows]
            half = (self.width - 8) / 2
            t = Table(data, colWidths=[half * 0.42, half * 0.14, half * 0.26, half * 0.18], repeatRows=1)
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), self.t.navy), ("BOX", (0, 0), (-1, -1), 0.5, self.t.border),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, self.t.border), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3.4), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.4),
                *[("BACKGROUND", (0, i), (-1, i), self.t.zebra) for i in range(2, len(data), 2)],
            ]))
            return t

        half = (self.width - 8) / 2
        heads = Table([[SubHeading(self.t, "Top inflow modes", half), SubHeading(self.t, "Top outflow modes", half)],
                       [modes(exp["inflow_modes"]), modes(exp["outflow_modes"])]],
                      colWidths=[half + 4, half + 4])
        heads.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 0)]))
        rows = [[str(i), self.esc(d["destination"]), str(d["count"]), self.money(d["amount"]), pct(d["pct_of_debits"], decimals=2),
                 self.type_pill(d["type"])] for i, d in enumerate(exp["top_destinations"][:8], 1)]
        return [heads, SubHeading(self.t, "Top outflow destinations"),
                self.table(["#", "Destination", "Txns", "Amount", "%", "Type"], rows, [0.05, 0.43, 0.08, 0.15, 0.12, 0.17],
                           ["CENTER", "LEFT", "CENTER", "RIGHT", "RIGHT", "CENTER"])]

    def sec_balances(self) -> list:
        b = self.r.balances
        if not b.get("available"):
            return [self.empty(b.get("reason", "Balances not available."))]
        pairs = [
            ("Days tracked", f"{b['days_tracked']} ({b['days_with_transactions']} with transactions)"),
            ("Average EOD balance", self.money(b["average_daily_balance"])),
            ("Min EOD", f"{self.money(b['min_eod_balance'])} on {fdate(b['min_eod_date'])}"),
            ("Max EOD", f"{self.money(b['max_eod_balance'])} on {fdate(b['max_eod_date'])}"),
            (f"Days < {inr0(b['low_balance_threshold'], self.R)}", f"{b['days_below_low_threshold']} ({pct(b['pct_days_below_low_threshold'])})"),
            (f"Days < {inr0(b['secondary_threshold'], self.R)}",
             f"{b['days_below_secondary_threshold']} ({pct(b['pct_days_below_secondary_threshold'])})"),
            ("Negative balance days", str(b["negative_balance_days"])),
            ("Min / Max observed", f"{self.money(b['min_observed_balance'])} — {self.money(b['max_observed_balance'])}"),
        ]
        out: list = [self.kv(pairs, highlight={"Average EOD balance"})]
        if b.get("reliability_note"):
            out.append(Callout(self.t, self.p(self.esc(b["reliability_note"])), self.t.warning))
        out += [Spacer(1, 6), eod_balance_chart(self.t, b["eod_series"], self.width),
                self.p(self.esc(b["basis"]) + " Observed minimum/maximum include intraday balances.", "small")]
        return out

    def sec_bounces(self) -> list:
        """Incidents (one per returned instrument), their entries, possible and unclear events — kept separate."""
        b = self.r.bounces
        by_id = {t.id: t for t in self.r.transactions}
        incidents = b.get("incidents") or []
        returned_amt = b.get("returned_payment_amount")
        received, own = b.get("deposited_instrument_return_count", 0), b.get("outgoing_payment_return_count", 0)
        tiles = [
            ("Bounce / return incidents", str(b.get("confirmed_incident_count", 0)),
             f"{b.get('rejected_instrument_count', 0)} rejected instrument(s) · {b.get('charge_only_incident_count', 0)} "
             "charge-only", self.t.negative if incidents else self.t.positive),
            ("Returned instrument amount", self.money(returned_amt) if returned_amt is not None else "Not available",
             "Counted once per incident" if returned_amt is not None else "No returned amount shown on the statement",
             self.t.navy),
            ("Return / bounce charges", self.money(b["return_charges_total"]) if b["return_charge_count"] else "None found",
             f"{b['return_charge_count']} charge entries (separate from amounts)", self.t.warning),
        ]
        pairs = [
            ("Received cheques/payments returned", f"{received} ({self.money(b.get('deposited_instrument_return_amount'))})"),
            ("Holder's own payments returned", f"{own} ({self.money(b.get('outgoing_payment_return_amount'))})"),
            ("EMI / NACH / ECS incidents", str(b.get("nach_ecs_return_count", 0))),
            ("Cheque incidents", str(b.get("cheque_return_count", 0))),
            ("Possible reversals (pattern)", str(b["pattern_based_count"])),
            ("Unclear reversal-worded credits", str(b.get("unclear_count", 0))),
        ]
        out: list = [MoneyTiles(self.t, tiles), self.kv(pairs)]
        if not incidents:
            out.append(Callout(self.t, self.p(
                "<b>No confirmed bounce events detected.</b> No narration in this statement explicitly reports a "
                "returned cheque, failed mandate or bounce charge. This does not verify a clean repayment history: "
                "returns on other accounts, or narrations without return wording, are not visible."), self.t.info))
        else:
            whose = {"DEPOSITED_INSTRUMENT_RETURNED": "Received by holder", "OUTGOING_PAYMENT_RETURNED": "Paid by holder",
                     "UNKNOWN": "Not stated"}
            rows = []
            for inc in incidents[:20]:
                status = self.pill("Confirmed", "bad") if inc["evidence"] != "RETURN_CHARGE_ONLY" else self.pill("Charge only", "warn")
                rows.append([self.esc(inc["reference"] or inc["incident_id"]), fdate(inc["date"]),
                             title_case(inc["instrument"]), whose.get(inc["direction"], NA),
                             self.money(inc["returned_amount"]) if inc["returned_amount"] is not None else NA,
                             self.money(inc["charges_total"]) if inc["charge_count"] else "—",
                             self.esc(title_case(inc["reason"] or "") or NA), status,
                             ", ".join(str(p) for p in inc["source_pages"]) or NA])
            out += [SubHeading(self.t, f"Return incidents ({len(incidents)}) — one per returned instrument"),
                    self.table(["Ref", "Date", "Instrument", "Whose payment", "Returned", "Charges", "Reason", "Status", "Page"],
                               rows, [0.1, 0.09, 0.09, 0.13, 0.12, 0.1, 0.15, 0.12, 0.1],
                               ["LEFT", "CENTER", "LEFT", "LEFT", "RIGHT", "RIGHT", "LEFT", "CENTER", "CENTER"])]
            role_label = {"REJECTED_INSTRUMENT": "Rejected instrument", "RETURN_CHARGE": "Return charge",
                          "RETURNED_PAYMENT_CREDIT": "Returned payment", "RETURN_DEBIT": "Return-worded debit"}
            rows = []
            for e in b["confirmed_events"][:30]:
                t = by_id.get(e["transaction_id"])
                rows.append([fdate(e["date"]), self.esc((t.narration if t else "")[:80]), self.money(e["amount"]),
                             self.esc(role_label.get(e["role"], title_case(e["role"]))), self.esc(e.get("incident_id") or NA),
                             str(e.get("source_page") or NA)])
            out += [SubHeading(self.t, f"Statement entries behind the incidents ({b['confirmed_event_count']})"),
                    self.table(["Date", "Narration", "Amount", "Role", "Incident", "Page"], rows,
                               [0.1, 0.44, 0.13, 0.15, 0.09, 0.09], ["CENTER", "LEFT", "RIGHT", "LEFT", "CENTER", "CENTER"])]
            originals = [inc for inc in incidents if inc["original"]]
            if originals:
                rows = [[self.esc(inc["reference"] or inc["incident_id"]), fdate(inc["original"]["date"]),
                         self.esc((by_id[inc["original"]["transaction_id"]].narration if inc["original"]["transaction_id"] in by_id else "")[:80]),
                         self.money(inc["original"]["amount"]), str(inc["original"].get("source_page") or NA)]
                        for inc in originals[:20]]
                out += [SubHeading(self.t, "Original instruments later returned (excluded from income)"),
                        self.table(["Ref", "Date", "Narration", "Amount", "Page"], rows, [0.12, 0.11, 0.52, 0.15, 0.1],
                                   ["LEFT", "CENTER", "LEFT", "RIGHT", "CENTER"])]
        if b["pattern_based_reversals"]:
            rows = [[fdate(p["debit"]["date"]), self.money(p["debit"]["amount"]), fdate(p["credit"]["date"]),
                     self.money(p["credit"]["amount"]), self.esc(p.get("counterparty") or NA), self.pill("Possible", "info")]
                    for p in b["pattern_based_reversals"][:10]]
            out += [SubHeading(self.t, "Possible reversals — pattern only, not proof of a bounce"),
                    self.table(["Debit date", "Debit", "Credit date", "Credit back", "Counterparty", "Status"], rows,
                               [0.13, 0.15, 0.13, 0.15, 0.29, 0.15], ["CENTER", "RIGHT", "CENTER", "RIGHT", "LEFT", "CENTER"])]
        if b.get("unclear_events"):
            rows = []
            for e in b["unclear_events"][:8]:
                t = by_id.get(e["transaction_id"])
                rows.append([fdate(e["date"]), self.esc((t.narration if t else "")[:90]), self.money(e["amount"]),
                             self.pill("Unclear", "neutral")])
            out += [SubHeading(self.t, "Unclear — reversal wording without a matching debit"),
                    self.table(["Date", "Narration", "Credit", "Status"], rows, [0.12, 0.58, 0.15, 0.15],
                               ["CENTER", "LEFT", "RIGHT", "CENTER"])]
        out.append(KeepTogether([out.pop(), self.p(self.esc(b["note"]) + " A refund or same-amount reversal alone is "
                                                   "not treated as a bounced EMI.", "small")]))
        return out

    def sec_self_transfers(self) -> list:
        cf, m = self.r.cashflow, self.r.metadata
        basis = "Holder name matched in narrations" if m.holder_name else "Holder name not available (narration keywords only)"
        pairs = [
            ("Own-account in", f"{self.money(cf['self_transfer_in'])} ({pct(cf['self_transfer_in_pct'])} of inflows)"),
            ("Own-account out", f"{self.money(cf['self_transfer_out'])} ({pct(cf['self_transfer_out_pct'])} of outflows)"),
            ("Matching basis", basis),
            ("Quick round trips", str(cf["round_trip_count"])),
            ("Large inflows moved out ≤1 day", str(cf["pass_through_count"])),
            ("Top source share", pct(cf.get("top_source_share_pct"))),
        ]
        out: list = [self.kv(pairs)]
        if cf["pass_through_events"]:
            rows = [[fdate(e["credit"]["date"]), self.money(e["credit"]["amount"]), self.money(e["outflow_within_1_day"]),
                     pct(e["outflow_pct"]), self.esc(e["credit"]["transaction_id"])] for e in cf["pass_through_events"][:8]]
            out += [SubHeading(self.t, "Large inflows followed by outflows within one day"),
                    self.table(["Credit date", "Credit", "Out within 1 day", "% moved", "Ref"], rows,
                               [0.18, 0.2, 0.22, 0.18, 0.22], ["CENTER", "RIGHT", "RIGHT", "RIGHT", "CENTER"])]
        out.append(self.p("Own-account transfers are excluded from income. Matching requires at least two name tokens, "
                          "so transfers to relatives sharing a surname are not treated as own-account.", "small"))
        return out

    def sec_risk_indicators(self) -> list:
        r = self.r
        inds = r.risk_indicators
        out: list = []
        if inds:
            rows = []
            for ind in inds:
                rows.append([self.p(f"<b>{self.esc(ind.title)}</b>", "cell"), self.esc(ind.description),
                             f"{int(ind.confidence * 100)}%", Dot(self.t.severity_color(ind.severity))])
            out.append(self.table(["Indicator", "Finding", "Conf.", "Level"], rows, [0.24, 0.57, 0.09, 0.1],
                                  ["LEFT", "LEFT", "CENTER", "CENTER"]))
        else:
            out.append(self.empty("No indicator rules were triggered."))
        c = r.overview["indicator_counts"]
        status = r.validation.status
        title = {"VERIFIED": "DATA RECONCILED — READY FOR ANALYST REVIEW",
                 "REQUIRES_REVIEW": "REQUIRES REVIEW — RECONCILIATION ISSUES",
                 "FAILED": "DATA NOT RELIABLE — MANUAL REVIEW REQUIRED"}.get(status, status)
        body = self.p("These indicators are analytical observations from the statement data. They are not a credit "
                      "score, eligibility assessment or lending decision, and do not by themselves establish fraud, "
                      "income or default. Verify flagged items with documents and the account holder.", "center")
        out.append(StatusBox(self.t, title, f"High: {c['HIGH']}  ·  Medium: {c['MEDIUM']}  ·  Low: {c['LOW']}  ·  Info: {c['INFO']}",
                             body, self.t.status_color(status)))
        for ind in inds:
            block = [self.p(f"<font color='{_hex(self.t.severity_color(ind.severity))}'>●</font> <b>{self.esc(ind.title)}</b> "
                            f"<font size=6 color='{_hex(self.t.muted)}'>[{ind.code} · {ind.severity}]</font>", "h4"),
                     self.p(self.esc(ind.description))]
            ev = [_evidence_text(e, self.R) for e in ind.evidence[:5]]
            if ev:
                block.append(self.p("<b>Evidence:</b> " + "; ".join(self.esc(x) for x in ev if x), "small"))
            if ind.limitations:
                block.append(self.p("<b>Limitations:</b> " + " ".join(self.esc(x) for x in ind.limitations), "small"))
            out.append(KeepTogether(block))
        return out

    def sec_transactions(self) -> list:
        if not self.cfg.include_transactions:
            return [self.empty("Transaction listing disabled in report configuration.")]
        txns = self.r.transactions
        limit = self.cfg.max_transactions_in_report
        shown = txns[-limit:] if len(txns) > limit else txns
        rows = []
        for t in shown:
            mark = " †" if "REORDERED_WITHIN_DAY" in t.flags else ""
            rows.append([fdate(t.date) + mark, self.esc(t.narration[:160]), self.esc(CATEGORY_LABELS.get(t.category, t.category)),
                         inr(t.debit, self.R) if t.debit else "", inr(t.credit, self.R) if t.credit else "",
                         inr(t.balance, self.R)])
        out: list = []
        if len(txns) > limit:
            out.append(self.p(f"Showing the most recent {limit} of {len(txns)} transactions. The complete list is "
                              "available in the JSON result.", "small"))
        out.append(self.table(["Date", "Narration", "Category", "Debit", "Credit", "Balance"], rows,
                              [0.095, 0.365, 0.18, 0.12, 0.12, 0.12], ["LEFT", "LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT"]))
        if any("REORDERED_WITHIN_DAY" in t.flags for t in shown):
            out.append(self.p("† Re-sequenced within the day to match posting order (see reconciliation notes).", "small"))
        return out

    def sec_methodology(self) -> list:
        r = self.r
        dq = r.data_quality
        method = [
            f"<b>Extraction.</b> Text was read locally from the PDF ({title_case(dq.get('extraction_method'))}); "
            "table columns were located from the header vocabulary and the positions of printed values. No data was "
            "sent to external services.",
            "<b>Reconciliation.</b> Every transaction was checked against the printed running balance "
            "(previous balance + credit − debit = balance), and opening/closing balances and printed totals were "
            "compared where available. Data is never silently corrected.",
            "<b>Categorisation.</b> Deterministic, configurable narration rules assign a category, the matched rule "
            "and a confidence to each transaction. Own-account transfers are matched on the holder name.",
            "<b>Income & obligations.</b> Recurring inflows need the same counterparty and a similar amount in at "
            "least three months. EMI obligations need the same payee and amount in at least three months. Both are "
            "estimates.",
            "<b>Balances.</b> End-of-day balance is the printed balance after the day's last transaction, carried "
            "forward on days without transactions; the average daily balance is the mean over calendar days.",
        ]
        out: list = [self.bullet(m) for m in method]
        out.append(SubHeading(self.t, "Limitations"))
        out += [self.bullet(self.esc(item)) for item in r.limitations]
        return out

    def footer_cards(self) -> list:
        r, t = self.r, self.t
        brand = [LogoChip(t, self.logo), Spacer(1, 5)] if self.logo is not None else []
        left = brand + [self.p(f"<b>{self.esc((self.cfg.organization_name + ' · ') if self.cfg.organization_name else '')}"
                       "BANK STATEMENT ANALYSIS ENGINE</b>", "label"), Spacer(1, 3),
                self.p("This report is generated automatically from the uploaded bank statement. It assists — but does "
                       "not replace — independent assessment. It is not issued or endorsed by any bank. "
                       "All flagged items must be verified through documentation before any decision.", "small")]
        right = [self.p("<b>REPORT DETAILS</b>", "label"), Spacer(1, 3),
                 self.p(f"{fdatetime(r.generated_at, self.cfg.timezone_label)}<br/>Ref: {r.report_id}<br/>"
                        f"Engine v{r.engine_version} · Source SHA-256 {r.source_sha256[:12]}…<br/>Confidential", "small")]
        table = Table([[left, right]], colWidths=[self.width * 0.66, self.width * 0.34])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), t.label_bg), ("BOX", (0, 0), (0, 0), 0.6, t.border),
            ("BOX", (1, 0), (1, 0), 0.6, t.border), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ]))
        return [Spacer(1, 12), KeepTogether([table])]

    # ------------------------------------------------------------------ misc
    def _kind_color(self, kind: str):
        return {"good": self.t.positive, "bad": self.t.negative, "warn": self.t.warning}.get(kind, self.t.info)

    def _fmt_check(self, value) -> str:
        if value is None:
            return NA
        if isinstance(value, int):
            return str(value)
        try:
            return inr(value, self.R)
        except Exception:
            return self.esc(value)


def _bind_subheadings(content: list) -> list:
    """Never leave a sub-heading alone at the bottom of a page.

    A sub-heading is kept together with a short following block; before a long table it gets a
    conditional page break so the heading, the repeated table header and some rows start together.
    """
    out: list = []
    i = 0
    while i < len(content):
        item = content[i]
        nxt = content[i + 1] if i + 1 < len(content) else None
        if isinstance(item, SubHeading) and nxt is not None:
            rows = len(getattr(nxt, "_cellvalues", [])) if isinstance(nxt, Table) else 0
            if rows <= 25:
                out.append(KeepTogether([item, nxt]))
            else:
                out += [CondPageBreak(70 * mm), item, nxt]
            i += 2
            continue
        out.append(item)
        i += 1
    return out


def _hex(color) -> str:
    return "#%02x%02x%02x" % (int(color.red * 255), int(color.green * 255), int(color.blue * 255))


def _small(flowable) -> bool:
    """Flowables small enough to keep together with their section heading."""
    return not isinstance(flowable, Table) or len(getattr(flowable, "_cellvalues", [])) <= 8


def _evidence_text(e: dict, rupee: str) -> str:
    if "transaction_id" in e:
        return f"{e['transaction_id']} {fdate(e.get('date'))} {inr(e.get('amount'), rupee)}"
    if "credit" in e and isinstance(e["credit"], dict):
        return f"{e['credit']['transaction_id']} {fdate(e['credit']['date'])} {inr(e['credit']['amount'], rupee)}"
    if "debit" in e and isinstance(e["debit"], dict):
        return f"{e['debit']['transaction_id']} → {e['credit']['transaction_id']}"
    parts = []
    for k, v in e.items():
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v[:6])
        parts.append(f"{k.replace('_', ' ')}: {v}")
    return "; ".join(parts)


def render_report(result: AnalysisResult, config: ReportConfig | None = None) -> bytes:
    return ReportBuilder(result, config or ReportConfig()).build()


__all__ = ["render_report", "ReportBuilder"]
