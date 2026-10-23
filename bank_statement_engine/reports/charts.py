"""Vector charts built with reportlab.graphics (no matplotlib dependency)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib.colors import white

from .formatting import inr_compact
from .theme import Theme, tint


def _nice_max(value: float) -> float:
    if value <= 0:
        return 1.0
    magnitude = 10 ** (len(str(int(value))) - 1)
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * magnitude:
            return step * magnitude
    return 10 * magnitude


def monthly_flow_chart(theme: Theme, monthly: list[dict], width: float, height: float = 190) -> Drawing:
    """Grouped credit/debit bars per month with an average-balance strip underneath."""
    d = Drawing(width, height)
    d.add(Rect(0, 0, width, height, rx=8, ry=8, fillColor=white, strokeColor=theme.border, strokeWidth=0.8))
    d.add(String(width / 2, height - 16, "Monthly Credits / Debits & Average Daily Balance",
                 fontName=theme.font_bold, fontSize=8, fillColor=theme.ink, textAnchor="middle"))
    months = monthly[-18:]
    credits = [float(m["total_credits"]) for m in months]
    debits = [float(m["total_debits"]) for m in months]
    top = _nice_max(max(credits + debits + [1.0]))

    chart = VerticalBarChart()
    chart.x, chart.y = 52, 48
    chart.width, chart.height = width - 70, height - 88
    chart.data = [credits, debits]
    chart.groupSpacing = 6
    chart.barSpacing = 1
    chart.bars[0].fillColor = theme.positive
    chart.bars[1].fillColor = theme.negative
    chart.bars.strokeColor = None
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = top
    chart.valueAxis.valueStep = top / 4
    chart.valueAxis.labels.fontName = theme.font
    chart.valueAxis.labels.fontSize = 6
    chart.valueAxis.labels.fillColor = theme.muted
    chart.valueAxis.labelTextFormat = lambda v: inr_compact(Decimal(str(v)), theme.rupee)
    chart.valueAxis.strokeColor = theme.border
    chart.valueAxis.gridStrokeColor = theme.border
    chart.valueAxis.visibleGrid = True
    chart.categoryAxis.categoryNames = [m["month"][2:] for m in months]  # "2025-10" -> "25-10"
    chart.categoryAxis.labels.fontName = theme.font
    chart.categoryAxis.labels.fontSize = 5.8
    chart.categoryAxis.labels.fillColor = theme.muted
    chart.categoryAxis.labels.angle = 30 if len(months) > 9 else 0
    chart.categoryAxis.labels.dy = -6
    chart.categoryAxis.strokeColor = theme.border
    d.add(chart)

    # Average-balance strip (one value per month), as in the reference layout.
    strip_y = 10
    d.add(Rect(chart.x, strip_y, chart.width, 14, rx=3, ry=3, fillColor=tint(theme.accent, 0.92), strokeColor=None))
    step = chart.width / max(1, len(months))
    for i, m in enumerate(months):
        adb = m.get("average_daily_balance")
        d.add(String(chart.x + step * (i + 0.5), strip_y + 4.5, inr_compact(adb, theme.rupee, na="–"),
                     fontName=theme.font_bold, fontSize=5.4, fillColor=theme.navy, textAnchor="middle"))
    d.add(String(chart.x - 4, strip_y + 4.5, "ADB", fontName=theme.font_bold, fontSize=5.6,
                 fillColor=theme.navy, textAnchor="end"))
    # Legend
    lx = width - 170
    for i, (label, color) in enumerate((("Credits", theme.positive), ("Debits", theme.negative))):
        d.add(Rect(lx + i * 60, height - 32, 7, 7, fillColor=color, strokeColor=None))
        d.add(String(lx + i * 60 + 10, height - 31, label, fontName=theme.font, fontSize=6.5, fillColor=theme.ink))
    return d


def eod_balance_chart(theme: Theme, series: list[dict], width: float, height: float = 140) -> Drawing:
    """End-of-day balance line over the statement period."""
    d = Drawing(width, height)
    d.add(Rect(0, 0, width, height, rx=8, ry=8, fillColor=white, strokeColor=theme.border, strokeWidth=0.8))
    d.add(String(14, height - 15, "End-of-day balance", fontName=theme.font_bold, fontSize=7.5, fillColor=theme.ink))
    if len(series) < 2:
        d.add(String(width / 2, height / 2, "Insufficient data", fontName=theme.font, fontSize=7,
                     fillColor=theme.muted, textAnchor="middle"))
        return d
    start = series[0]["date"]
    if isinstance(start, str):
        start = date.fromisoformat(start)
    points = []
    for i, p in enumerate(series):
        points.append((i, float(p["balance"])))
    lo = min(v for _, v in points)
    hi = max(v for _, v in points)
    plot = LinePlot()
    plot.x, plot.y = 52, 22
    plot.width, plot.height = width - 68, height - 46
    plot.data = [points]
    plot.lines[0].strokeColor = theme.accent
    plot.lines[0].strokeWidth = 1.1
    plot.xValueAxis.valueMin = 0
    plot.xValueAxis.valueMax = len(points) - 1
    plot.xValueAxis.visibleLabels = False
    plot.xValueAxis.strokeColor = theme.border
    plot.yValueAxis.valueMin = min(0.0, lo)
    plot.yValueAxis.valueMax = _nice_max(hi)
    plot.yValueAxis.valueStep = (plot.yValueAxis.valueMax - plot.yValueAxis.valueMin) / 4
    plot.yValueAxis.labels.fontName = theme.font
    plot.yValueAxis.labels.fontSize = 6
    plot.yValueAxis.labels.fillColor = theme.muted
    plot.yValueAxis.labelTextFormat = lambda v: inr_compact(Decimal(str(v)), theme.rupee)
    plot.yValueAxis.strokeColor = theme.border
    plot.yValueAxis.visibleGrid = True
    plot.yValueAxis.gridStrokeColor = theme.border
    d.add(plot)
    # Month ticks
    n = len(points)
    last_month = None
    for i, p in enumerate(series):
        day = p["date"] if not isinstance(p["date"], str) else date.fromisoformat(p["date"])
        key = (day.year, day.month)
        if key != last_month and day.day <= 3:
            x = plot.x + plot.width * i / (n - 1)
            d.add(Line(x, plot.y, x, plot.y - 3, strokeColor=theme.border))
            d.add(String(x, plot.y - 10, day.strftime("%b %y"), fontName=theme.font, fontSize=5.5,
                         fillColor=theme.muted, textAnchor="middle"))
        last_month = key
    return d
