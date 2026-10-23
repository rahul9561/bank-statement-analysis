"""Geometry helpers for reading transaction tables from positioned words.

The generic approach, which does not depend on fixed coordinates:

1. Words are clustered into visual *lines* by vertical position.
2. The table *header* is found from vocabulary ("Date", "Narration",
   "Withdrawal", "Deposit", "Balance", ...). Header words are merged into
   *cells* (multi-line headers such as "Withdrawal / Amount (INR)" are handled)
   and each cell is classified into a semantic column.
3. Column spans are derived from the gaps between header cells.
4. A *row anchor* is a line whose date column holds a date.
5. Rows are segmented either by the table's ruling lines (when every anchor
   falls in its own ruled band) or by vertical gaps between lines, which handles
   top-aligned, centred and bottom-aligned cells.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from ..extractors.base import PageContent, Word
from ..normalizers.amounts import is_amount_token
from ..normalizers.dates import parse_date

# ---------------------------------------------------------------- lines


@dataclass
class Line:
    words: list[Word]

    @property
    def top(self) -> float:
        return min(w.y0 for w in self.words)

    @property
    def bottom(self) -> float:
        return max(w.y1 for w in self.words)

    @property
    def yc(self) -> float:
        return sum(w.yc for w in self.words) / len(self.words)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def median_word_height(words: list[Word], default: float = 8.0) -> float:
    heights = [w.height for w in words if w.height > 0]
    return statistics.median(heights) if heights else default


def cluster_lines(words: list[Word], tol_factor: float = 0.45) -> list[Line]:
    if not words:
        return []
    tol = max(1.5, median_word_height(words) * tol_factor)
    lines: list[Line] = []
    centres: list[float] = []
    for w in sorted(words, key=lambda w: w.yc):
        if lines and abs(w.yc - centres[-1]) <= tol:
            lines[-1].words.append(w)
            n = len(lines[-1].words)
            centres[-1] += (w.yc - centres[-1]) / n
        else:
            lines.append(Line([w]))
            centres.append(w.yc)
    for line in lines:
        line.words.sort(key=lambda w: w.x0)
    return lines


# ---------------------------------------------------------------- free-text segments


@dataclass
class Segment:
    """A run of words on one visual line, split at wide horizontal gaps."""

    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def segments(words: list[Word]) -> list[Segment]:
    if not words:
        return []
    h = median_word_height(words)
    out: list[Segment] = []
    for line in cluster_lines(words):
        current: list[Word] = []
        for w in line.words:
            if current and w.x0 - current[-1].x1 > 3.5 * h:
                out.append(Segment(current))
                current = []
            current.append(w)
        if current:
            out.append(Segment(current))
    return out


def context_text(words: list[Word]) -> str:
    return "\n".join(s.text for s in segments(words))



# ---------------------------------------------------------------- header / columns

DATE, VALUE_DATE, NARRATION, REF, DEBIT, CREDIT, BALANCE, AMOUNT, DRCR, SERIAL, OTHER = (
    "date", "value_date", "narration", "ref", "debit", "credit", "balance", "amount", "drcr", "serial", "other",
)
NUMERIC_COLUMNS = {DEBIT, CREDIT, BALANCE, AMOUNT}

_HEADER_VOCAB = {
    "date", "txn", "tran", "trans", "transaction", "transactions", "value", "dt", "narration", "particulars",
    "description", "remarks", "details", "chq", "cheque", "ref", "reference", "no", "number", "debit",
    "debits", "credit", "credits", "withdrawal", "withdrawals", "deposit", "deposits", "amount", "amt",
    "balance", "closing", "closingbalance", "inr", "dr", "cr", "s", "sr", "sl", "init", "br", "branch",
    "name", "mode", "type", "kims", "instrument", "id", "posting", "post", "rs", "₹", "chqrefno", "refno",
    "chqno", "of", "and", "in", "running", "",
}
_NARRATION_KEYS = ("narration", "particular", "description", "remarks", "details")
_REF_KEYS = ("chq", "cheque", "ref", "instrument")


def _norm(token: str) -> str:
    return re.sub(r"[^a-z0-9₹#]", "", token.lower())


def is_header_like(line: Line) -> bool:
    tokens = [_norm(w.text) for w in line.words]
    if not tokens:
        return False
    if any(is_amount_token(w.text) or parse_date(w.text) for w in line.words):
        return False
    # "Label: value" lines (e.g. "Transaction type: All") are form fields, not table headers.
    if any(w.text.endswith(":") for w in line.words):
        return False
    hits = sum(1 for t in tokens if t in _HEADER_VOCAB or t == "#")
    return hits / len(tokens) >= 0.6


@dataclass
class Column:
    kind: str
    label: str
    x0: float
    x1: float
    left: float = float("-inf")
    right: float = float("inf")

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class TableLayout:
    columns: list[Column]
    header_top: float
    header_bottom: float
    page_width: float
    #: True when columns were inferred from data because no textual header exists.
    inferred: bool = False

    def column(self, kind: str) -> Column | None:
        return next((c for c in self.columns if c.kind == kind), None)

    def has(self, kind: str) -> bool:
        return self.column(kind) is not None

    def column_for(self, word: Word) -> Column | None:
        for col in self.columns:
            if col.left <= word.xc < col.right:
                return col
        return None

    def scaled(self, page_width: float) -> "TableLayout":
        if abs(page_width - self.page_width) < 1:
            return self
        f = page_width / self.page_width
        cols = [Column(c.kind, c.label, c.x0 * f, c.x1 * f, c.left * f, c.right * f) for c in self.columns]
        return TableLayout(cols, 0.0, 0.0, page_width, self.inferred)

    def describe(self) -> list[str]:
        return [f"{c.kind}:{c.label}" for c in self.columns]


def _classify(label: str, taken: set[str]) -> str:
    t = label.lower()
    compact = _norm(t)
    if "balance" in t:
        return BALANCE if BALANCE not in taken else OTHER
    if "value" in t and ("date" in t or re.search(r"\bdt\b", t)):
        return VALUE_DATE
    if "date" in t or compact in {"dt", "txndt", "trandt"}:
        if DATE not in taken:
            return DATE
        return VALUE_DATE if VALUE_DATE not in taken else OTHER
    if "withdraw" in t or "debit" in t or re.fullmatch(r"dr\.?(\s*(amount|amt\.?))?(\s*\(.*\))?", t):
        return DEBIT if DEBIT not in taken else OTHER
    if "deposit" in t or "credit" in t or re.fullmatch(r"cr\.?(\s*(amount|amt\.?))?(\s*\(.*\))?", t):
        return CREDIT if CREDIT not in taken else OTHER
    if any(k in t for k in _NARRATION_KEYS):
        return NARRATION if NARRATION not in taken else OTHER
    if any(k in t for k in _REF_KEYS):
        return REF if REF not in taken else OTHER
    if compact in {"drcr", "crdr", "type"}:
        return DRCR
    if "amount" in t:
        return AMOUNT if AMOUNT not in taken else OTHER
    if compact in {"#", "sno", "srno", "slno", "txnno", "no", "s"}:
        return SERIAL
    return OTHER


def _merge_cells(words: list[Word], gap: float) -> list[list[Word]]:
    cells: list[list[Word]] = []
    for w in sorted(words, key=lambda w: w.x0):
        target = None
        for cell in cells:
            cx0 = min(c.x0 for c in cell)
            cx1 = max(c.x1 for c in cell)
            if w.x0 <= cx1 + gap and w.x1 >= cx0 - gap:
                target = cell
                break
        if target is None:
            cells.append([w])
        else:
            target.append(w)
    # Merging can make cells overlap each other; repeat until stable.
    changed = True
    while changed:
        changed = False
        for i in range(len(cells)):
            for j in range(i + 1, len(cells)):
                a, b = cells[i], cells[j]
                if min(w.x0 for w in b) <= max(w.x1 for w in a) + gap and max(w.x1 for w in b) >= min(w.x0 for w in a) - gap:
                    cells[i] = a + b
                    del cells[j]
                    changed = True
                    break
            if changed:
                break
    return sorted(cells, key=lambda c: min(w.x0 for w in c))


def find_header(lines: list[Line], page_width: float) -> TableLayout | None:
    """Locate the transaction table header among ``lines`` and build the column layout."""
    if not lines:
        return None
    h = median_word_height([w for l in lines for w in l.words])
    for i, line in enumerate(lines):
        lowered = line.text.lower()
        if not any(k in lowered for k in ("balance", "withdraw", "debit", "deposit", "credit")):
            continue
        if not is_header_like(line):
            continue
        band = [line]
        # Extend over adjacent header-like lines (multi-line headers).
        j = i - 1
        while j >= 0 and line.top - lines[j].bottom <= 1.6 * h and is_header_like(lines[j]):
            band.insert(0, lines[j])
            j -= 1
        j = i + 1
        while j < len(lines) and lines[j].top - band[-1].bottom <= 1.6 * h and is_header_like(lines[j]):
            band.append(lines[j])
            j += 1
        words = [w for l in band for w in l.words]
        cells = _merge_cells(words, gap=max(2.5, 0.55 * h))
        taken: set[str] = set()
        columns: list[Column] = []
        for cell in cells:
            ordered = sorted(cell, key=lambda w: (round(w.y0), w.x0))
            label = " ".join(w.text for w in ordered)
            kind = _classify(label, taken)
            if kind not in (OTHER, SERIAL, DRCR):
                taken.add(kind)
            columns.append(Column(kind, label, min(w.x0 for w in cell), max(w.x1 for w in cell)))
        kinds = {c.kind for c in columns}
        if DATE not in kinds or not ({DEBIT, CREDIT} <= kinds or AMOUNT in kinds):
            continue
        _set_spans(columns)
        return TableLayout(columns, min(l.top for l in band), max(l.bottom for l in band), page_width)
    return None


def _set_spans(columns: list[Column], margin: float = 0.0) -> None:
    """Derive column boundaries. Between a text column and a calibrated numeric
    column the boundary sits just left of the numbers, because narrations are
    left-aligned and often run far past their header label."""
    for k, col in enumerate(columns):
        if k == 0:
            col.left = float("-inf")
        else:
            prev = columns[k - 1]
            if col.kind in NUMERIC_COLUMNS and prev.kind not in NUMERIC_COLUMNS and margin:
                col.left = max(prev.x1, col.x0 - margin)
            else:
                col.left = (prev.x1 + col.x0) / 2
        if k > 0:
            columns[k - 1].right = col.left
    if columns:
        columns[-1].right = float("inf")


def _cluster_x(words: list[Word]) -> list[dict]:
    clusters: list[dict] = []
    for w in sorted(words, key=lambda w: w.x1):
        for c in clusters:
            if w.x0 <= c["x1"] and w.x1 >= c["x0"]:
                c["x0"], c["x1"], c["n"] = min(c["x0"], w.x0), max(c["x1"], w.x1), c["n"] + 1
                break
        else:
            clusters.append({"x0": w.x0, "x1": w.x1, "n": 1})
    # Merge clusters that overlap after growth.
    clusters.sort(key=lambda c: c["x0"])
    merged: list[dict] = []
    for c in clusters:
        if merged and c["x0"] <= merged[-1]["x1"]:
            m = merged[-1]
            m["x1"], m["n"] = max(m["x1"], c["x1"]), m["n"] + c["n"]
        else:
            merged.append(dict(c))
    return merged


def calibrate_numeric_columns(layout: TableLayout, lines: list[Line]) -> tuple[TableLayout, bool]:
    """Align numeric columns with where amounts are actually printed.

    Amounts are usually right-aligned and can sit well away from their header
    label. Amount tokens to the right of the narration column are clustered by
    x-extent; clusters are matched to the numeric header columns in left-to-right
    order (when the counts agree) or by nearest centre otherwise.
    """
    numeric_cols = [c for c in layout.columns if c.kind in NUMERIC_COLUMNS]
    if not numeric_cols:
        return layout, False
    narration = layout.column(NARRATION)
    min_x = narration.x0 if narration else float("-inf")
    data_lines = [l for l in lines if l.top > layout.header_bottom and anchor_date(l, layout) is not None]
    if len(data_lines) < 2:
        return layout, False
    tokens = [w for l in data_lines for w in l.words if w.x0 > min_x and is_amount_token(w.text)]
    clusters = [c for c in _cluster_x(tokens) if c["n"] >= max(1, len(data_lines) // 20)]
    if not clusters:
        return layout, False
    # Ignore clusters inside the narration span (numbers embedded in narrations).
    if narration is not None:
        first_numeric_x0 = min(c.x0 for c in numeric_cols)
        clusters = [c for c in clusters if c["x1"] > first_numeric_x0 - (narration.x1 - narration.x0)]
    matched: dict[int, dict] = {}
    exact = len(clusters) == len(numeric_cols)
    if exact:
        matched = {id(col): c for col, c in zip(numeric_cols, clusters)}
    else:
        for c in clusters:
            cxc = (c["x0"] + c["x1"]) / 2
            col = min(numeric_cols, key=lambda col: abs(col.xc - cxc))
            if id(col) not in matched or matched[id(col)]["n"] < c["n"]:
                matched[id(col)] = c
    if not matched:
        return layout, False
    h = median_word_height([w for l in data_lines for w in l.words])
    columns = []
    for col in layout.columns:
        c = matched.get(id(col))
        if c is not None:
            columns.append(Column(col.kind, col.label, c["x0"], c["x1"]))
        else:
            columns.append(Column(col.kind, col.label, col.x0, col.x1))
    columns.sort(key=lambda c: c.x0)
    _calibrate_text_columns(columns, lines, layout)
    _set_spans(columns, margin=0.6 * h)
    return TableLayout(columns, layout.header_top, layout.header_bottom, layout.page_width, layout.inferred), exact


def _coverage_blocks(lines: list[Line], page_width: float, min_count: int) -> list[tuple[float, float]]:
    """x-intervals covered by words (more than ``min_count`` times) across ``lines``."""
    width = int(page_width) + 2
    cov = [0] * width
    for line in lines:
        for w in line.words:
            for x in range(max(0, int(w.x0)), min(width, int(w.x1) + 1)):
                cov[x] += 1
    blocks: list[tuple[float, float]] = []
    start = None
    for x, count in enumerate(cov):
        if count > min_count and start is None:
            start = x
        elif count <= min_count and start is not None:
            blocks.append((float(start), float(x)))
            start = None
    if start is not None:
        blocks.append((float(start), float(width)))
    return blocks


def _calibrate_text_columns(columns: list[Column], lines: list[Line], layout: TableLayout) -> None:
    """Widen text columns to the whitespace-delimited blocks their data occupies.

    Header labels are often centred while cell text is left-aligned, so a
    boundary half-way between labels cuts narrations. Gutters (x ranges no data
    word covers) are the true column boundaries.
    """
    body = [l for l in lines if l.top > layout.header_bottom]
    idx = [i for i, l in enumerate(body) if anchor_date(l, layout) is not None]
    if len(idx) < 2:
        return
    region = body[idx[0]: idx[-1] + 1]
    blocks = _coverage_blocks(region, layout.page_width, min_count=len(region) // 200)
    assigned: dict[int, list[tuple[float, float]]] = {}
    for b0, b1 in blocks:
        overlapping = [c for c in columns if min(b1, c.x1) - max(b0, c.x0) > 0]
        if len(overlapping) == 1:
            target = overlapping[0]
        elif not overlapping:
            target = min(columns, key=lambda c: max(c.x0 - b1, b0 - c.x1))
        else:
            continue  # block spans several headers: ambiguous, keep header geometry
        if target.kind not in NUMERIC_COLUMNS:
            assigned.setdefault(id(target), []).append((b0, b1))
    for k, col in enumerate(columns):
        spans = assigned.get(id(col))
        if not spans:
            continue
        x0, x1 = min(s[0] for s in spans), max(s[1] for s in spans)
        prev_x1 = columns[k - 1].x1 if k > 0 else float("-inf")
        next_x0 = columns[k + 1].x0 if k + 1 < len(columns) else float("inf")
        if x0 > prev_x1 and x1 < next_x0:
            col.x0, col.x1 = x0, x1


_DASH = {"-", "--", "—", "–"}


def infer_layout(lines: list[Line], page_width: float) -> TableLayout | None:
    """Infer columns from the data when the header is not available as text.

    Used for statements whose header is drawn as vector graphics or an image, and
    for OCR output with garbled headers. A data line starts with one or two dates
    and ends with amount (or dash placeholder) tokens. Numeric x-clusters become
    columns: the right-most is the balance; with three numeric clusters the other
    two are debit then credit (the universal order on Indian statements). The
    debit/credit orientation is re-checked against the balance chain afterwards.
    """
    samples: list[tuple[list[Word], list[Word]]] = []
    for line in lines:
        words = line.words
        if len(words) < 3:
            continue
        dates: list[Word] = []
        for w in words:
            if parse_date(w.text) is not None:
                dates.append(w)
            else:
                break
        if not dates:
            continue
        tail: list[Word] = []
        for w in reversed(words):
            if is_amount_token(w.text) or w.text in _DASH or w.text.lower().rstrip(".") in {"cr", "dr"}:
                tail.insert(0, w)
            else:
                break
        numeric = [w for w in tail if is_amount_token(w.text)]
        if len(numeric) >= 2 or (numeric and len(tail) >= 2):
            samples.append((dates, tail))
    if len(samples) < 3:
        return None

    h = median_word_height([w for d, t in samples for w in d + t])
    clusters: list[dict] = []
    for _dates, tail in samples:
        for w in tail:
            if w.text.lower().rstrip(".") in {"cr", "dr"}:
                continue
            for c in clusters:
                if abs(c["xc"] - w.xc) <= 3 * h or (w.x0 <= c["x1"] and w.x1 >= c["x0"]):
                    c["x0"], c["x1"] = min(c["x0"], w.x0), max(c["x1"], w.x1)
                    c["n"] += 1
                    c["num"] += is_amount_token(w.text)
                    c["xc"] = (c["x0"] + c["x1"]) / 2
                    break
            else:
                clusters.append({"x0": w.x0, "x1": w.x1, "xc": w.xc, "n": 1, "num": int(is_amount_token(w.text))})
    clusters = [c for c in clusters if c["n"] >= max(2, len(samples) // 4)]
    clusters.sort(key=lambda c: c["x0"])
    numeric = [c for c in clusters if c["num"] > 0]
    if len(numeric) < 2:
        return None

    date_count = statistics.median([len(d) for d, _ in samples])
    first_dates = [d for d, _ in samples]
    columns: list[Column] = []
    d0 = [d[0] for d in first_dates]
    columns.append(Column(DATE, "Date (inferred)", min(w.x0 for w in d0), max(w.x1 for w in d0)))
    if date_count >= 2:
        d1 = [d[1] for d in first_dates if len(d) > 1]
        columns.append(Column(VALUE_DATE, "Date 2 (inferred)", min(w.x0 for w in d1), max(w.x1 for w in d1)))
    text_x0 = columns[-1].x1 + h
    first_numeric_x0 = numeric[0]["x0"] if not [c for c in clusters if c["num"] == 0] else clusters[0]["x0"]
    columns.append(Column(NARRATION, "Narration (inferred)", text_x0, max(text_x0 + 1, first_numeric_x0 - h)))
    for c in clusters:
        if c["num"] == 0:
            columns.append(Column(REF, "Reference (inferred)", c["x0"], c["x1"]))
    if len(numeric) == 2:
        names = [AMOUNT, BALANCE]
    else:
        names = [OTHER] * (len(numeric) - 3) + [DEBIT, CREDIT, BALANCE]
    for c, kind in zip(numeric, names):
        columns.append(Column(kind, f"{kind.title()} (inferred)", c["x0"], c["x1"]))
    columns.sort(key=lambda c: c.x0)
    _set_spans(columns)
    top = min(l.top for l in lines if any(w in l.words for w in first_dates[0])) - 3 * h
    return TableLayout(columns, top, top, page_width, inferred=True)


# ---------------------------------------------------------------- rows


@dataclass
class RowGeometry:
    anchor: Line
    lines: list[Line] = field(default_factory=list)
    page: int = 0

    @property
    def words(self) -> list[Word]:
        return [w for l in self.lines for w in l.words]


def anchor_date(line: Line, layout: TableLayout):
    col = layout.column(DATE)
    if col is None:
        return None
    text = " ".join(w.text for w in line.words if col.left <= w.xc < col.right)
    if not text:
        return None
    return parse_date(text) or parse_date(text.split(" ")[0])


def count_candidate_rows(lines: list[Line]) -> int:
    """Count lines that look like a transaction: a date near the start and a decimal amount.

    Independent of column detection, so it can expose rows the table parser missed.
    The date may span up to three words ("21 Oct 2025") within the first five words.
    """
    count = 0
    for line in lines:
        texts = [w.text for w in line.words]
        if not any(is_amount_token(t) for t in texts):
            continue
        head = texts[:5]
        found = False
        for i in range(len(head)):
            for n in (1, 3):
                if i + n <= len(head) and parse_date(" ".join(head[i:i + n])):
                    found = True
                    break
            if found:
                break
        if found:
            count += 1
    return count


_NOISE = re.compile(
    r"(page\s*(no\.?)?\s*:?\s*\d+(\s*of\s*\d+)?|statement\s+summary|end\s+of\s+(the\s+)?statement|"
    r"opening\s+balance|closing\s+balance|brought\s+forward|carried\s+forward|generated\s+on|"
    r"computer\s+generated|this\s+is\s+a\s+(system|computer))",
    re.I,
)


def _is_continuation(line: Line, layout: TableLayout) -> bool:
    """A continuation line holds only narration/reference text (no amounts, not page furniture)."""
    if _NOISE.search(line.text):
        return False
    for w in line.words:
        col = layout.column_for(w)
        if col is None or col.kind not in (NARRATION, REF, OTHER, VALUE_DATE):
            return False
    return True


def segment_rows(page: PageContent, lines: list[Line], layout: TableLayout, top: float) -> tuple[list[RowGeometry], list[Line], str]:
    """Split the table area of a page into transaction rows.

    Returns ``(rows, leading_lines, mode)``: ``leading_lines`` are continuation
    lines found above the first anchor that may belong to the previous page's
    last transaction.
    """
    body = [l for l in lines if l.top >= top - 0.5]
    anchors = [l for l in body if anchor_date(l, layout) is not None]
    if not anchors:
        return [], [], "none"

    seps = page.separators
    band_rows = _band_rows(body, anchors, seps) if len(seps) >= 2 else None
    if band_rows is not None:
        for r in band_rows:
            r.page = page.number
        return band_rows, [], "bands"
    return _gap_rows(body, anchors, layout, page.number)


def _band_rows(body: list[Line], anchors: list[Line], seps: list[float]) -> list[RowGeometry] | None:
    def band_of(y: float) -> int | None:
        for k in range(len(seps) - 1):
            if seps[k] - 0.5 <= y < seps[k + 1] + 0.5:
                return k
        return None

    bands = [band_of(a.yc) for a in anchors]
    if any(b is None for b in bands) or len(set(bands)) != len(bands):
        return None
    rows = {b: RowGeometry(anchor=a) for a, b in zip(anchors, bands)}
    for line in body:
        # Lines may straddle a band edge slightly; assign word-by-word.
        for w in line.words:
            b = band_of(w.yc)
            if b in rows:
                row = rows[b]
                if row.lines and abs(row.lines[-1].yc - line.yc) < 0.01:
                    row.lines[-1].words.append(w)
                else:
                    row.lines.append(Line([w]))
    ordered = [rows[b] for b in sorted(rows)]
    for row in ordered:
        row.lines.sort(key=lambda l: l.yc)
    return ordered


def _gap_rows(body: list[Line], anchors: list[Line], layout: TableLayout, page_no: int):
    anchor_ids = {id(a) for a in anchors}
    rows = [RowGeometry(anchor=a, lines=[a], page=page_no) for a in anchors]
    index = {id(a): k for k, a in enumerate(anchors)}
    leading: list[Line] = []

    # Lines between consecutive anchors: split at the largest vertical gap (ties -> lowest split).
    pos = [i for i, l in enumerate(body) if id(l) in anchor_ids]
    spacings: list[float] = []
    for a_pos, b_pos in zip(pos, pos[1:]):
        between = [l for l in body[a_pos + 1:b_pos] if _is_continuation(l, layout)]
        if not between:
            continue
        tops = [body[a_pos].top] + [l.top for l in between] + [body[b_pos].top]
        gaps = [b - a for a, b in zip(tops, tops[1:])]
        biggest = max(gaps)
        split = max(k for k, g in enumerate(gaps) if g >= biggest - 0.6)
        rows[index[id(body[a_pos])]].lines.extend(between[:split])
        rows[index[id(body[b_pos])]].lines[0:0] = between[split:]
        spacings.extend(g for g in gaps if g < biggest - 0.6)

    # Lines above the first anchor.
    first = pos[0]
    above = [l for l in body[:first] if _is_continuation(l, layout)]
    if above:
        tops = [l.top for l in above] + [body[first].top]
        gaps = [b - a for a, b in zip(tops, tops[1:])]
        h = median_word_height([w for l in above for w in l.words])
        # Lines tightly stacked on the anchor belong to it; the rest may continue the previous page.
        attach: list[Line] = []
        for line, gap in zip(reversed(above), reversed(gaps)):
            if gap > 1.2 * h:
                break
            attach.insert(0, line)
        rows[0].lines[0:0] = attach
        leading = [l for l in above if l not in attach]

    # Trailing lines after the last anchor (stop at the first non-continuation line or large gap).
    h = median_word_height([w for a in anchors for w in a.words])
    typical = statistics.median(spacings) if spacings else 1.6 * h
    last = rows[-1]
    prev_top = body[pos[-1]].top
    for line in body[pos[-1] + 1:]:
        if not _is_continuation(line, layout) or line.top - prev_top > 1.35 * typical + 0.5:
            break
        last.lines.append(line)
        prev_top = line.top
    return rows, leading, "gaps"
