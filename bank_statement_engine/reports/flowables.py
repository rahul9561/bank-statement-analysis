"""Custom ReportLab flowables for the analysis report: branded header band, section bars,
money tiles, KPI cards, navigation chips, pills, horizontal bars and call-out boxes.

All canvas text goes through ``fonts.draw_string`` so characters missing from the chosen
font (e.g. "→" in Roboto) are drawn with the fallback font instead of an empty box.
"""

from __future__ import annotations

from reportlab.lib.colors import Color, white
from reportlab.platypus import Flowable

from .assets import Logo
from .fonts import draw_string, fit, string_width
from .theme import Theme, tint


def _gradient_round_rect(canv, x, y, w, h, r, c0: Color, c1: Color) -> None:
    canv.saveState()
    path = canv.beginPath()
    path.roundRect(x, y, w, h, r)
    canv.clipPath(path, stroke=0, fill=0)
    canv.linearGradient(x, y, x + w, y + h, (c0, c1), extend=False)
    canv.restoreState()


def draw_logo(canv, logo: Logo, x: float, y: float, max_width: float, max_height: float) -> float:
    """Draw ``logo`` inside the box at (x, y) keeping its aspect ratio. Returns the drawn width."""
    width, height = logo.size_within(max_width, max_height)
    canv.drawImage(logo.reader, x, y + (max_height - height) / 2, width=width, height=height, mask="auto")
    return width


class HeaderBand(Flowable):
    """Branded banner: logo and report title on the left, reconciliation status on the right."""

    LOGO_HEIGHT = 26

    def __init__(self, theme: Theme, title: str, subtitle: str, lines: list[str], status_label: str,
                 status_caption: str, status_color: Color, org: str = "", logo: Logo | None = None, width: float = 0):
        super().__init__()
        self.t, self.title, self.subtitle, self.lines = theme, title, subtitle, lines
        self.status_label, self.status_caption, self.status_color, self.org = status_label, status_caption, status_color, org
        self.logo = logo
        self.width, self.height = width, 104

    def wrap(self, aw, ah):
        self.width = self.width or aw
        return self.width, self.height

    def draw(self):
        c, t, w, h = self.canv, self.t, self.width, self.height
        _gradient_round_rect(c, 0, 0, w, h, 10, t.navy, t.navy_light)
        x = 18
        badge_w = 128
        text_w = w - x - badge_w - 34
        if self.logo is not None:
            draw_logo(c, self.logo, x, h - 18 - self.LOGO_HEIGHT, text_w, self.LOGO_HEIGHT)
        c.setFillColor(white)
        title = self.title.upper()
        size = 14
        while size > 10 and string_width(title, t.font_heading, size) > text_w:
            size -= 0.5
        draw_string(c, x, 46, title, t.font_heading, size)
        if self.org:
            tw = string_width(title, t.font_heading, size)
            pw = string_width(self.org.upper(), t.font_heading_semi, 6.5) + 12
            if x + tw + 8 + pw <= x + text_w:
                c.setFillColor(Color(1, 1, 1, alpha=0.18))
                c.roundRect(x + tw + 8, 44, pw, 12, 6, stroke=0, fill=1)
                c.setFillColor(white)
                draw_string(c, x + tw + 14, 47.5, self.org.upper(), t.font_heading_semi, 6.5)
        c.setFillColor(Color(1, 1, 1, alpha=0.86))
        for i, line in enumerate([self.subtitle.upper()] + self.lines[:2]):
            draw_string(c, x, 32 - i * 11, fit(line, t.font, 6.8, text_w), t.font, 6.8)
        # Reconciliation status badge (deliberately not a score).
        bh = 56
        bx, by = w - badge_w - 16, (h - bh) / 2
        c.setFillColor(Color(1, 1, 1, alpha=0.12))
        c.setStrokeColor(Color(1, 1, 1, alpha=0.35))
        c.roundRect(bx, by, badge_w, bh, 8, stroke=1, fill=1)
        c.setFillColor(self.status_color)
        c.circle(bx + 12, by + bh - 15, 3.2, stroke=0, fill=1)
        c.setFillColor(white)
        draw_string(c, bx + 20, by + bh - 18.5, fit(self.status_label, t.font_heading, 9.5, badge_w - 26),
                    t.font_heading, 9.5)
        c.setFillColor(Color(1, 1, 1, alpha=0.85))
        for i, line in enumerate(self.status_caption.split("\n")[:3]):
            draw_string(c, bx + 10, by + bh - 31 - i * 8.5, fit(line, t.font, 6.2, badge_w - 16), t.font, 6.2)


class LogoChip(Flowable):
    """The logo on a navy chip, for light backgrounds (the logo artwork is designed for dark ones)."""

    def __init__(self, theme: Theme, logo: Logo, height: float = 26):
        super().__init__()
        self.t, self.logo = theme, logo
        lw, lh = logo.size_for_height(height - 10)
        self.width, self.height = lw + 16, height

    def wrap(self, aw, ah):
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.setFillColor(self.t.navy)
        c.roundRect(0, 0, self.width, self.height, 5, stroke=0, fill=1)
        draw_logo(c, self.logo, 8, 5, self.width - 16, self.height - 10)


class SectionBar(Flowable):
    """Navy section heading bar; registers a PDF bookmark/outline entry named ``key``."""

    def __init__(self, theme: Theme, title: str, index: int, key: str, width: float = 0):
        super().__init__()
        self.t, self.title, self.index, self.key = theme, title, index, key
        self.width, self.height = width, 26
        self.spaceBefore, self.spaceAfter = 10, 6

    def wrap(self, aw, ah):
        self.width = self.width or aw
        return self.width, self.height

    def draw(self):
        c, t, w, h = self.canv, self.t, self.width, self.height
        c.bookmarkPage(self.key)
        c.addOutlineEntry(self.title, self.key, level=0)
        _gradient_round_rect(c, 0, 0, w, h, 6, t.navy, t.navy_light)
        c.setFillColor(t.accent)
        c.rect(0, 0, 3.2, h, stroke=0, fill=1)
        c.setFillColor(Color(1, 1, 1, alpha=0.16))
        c.roundRect(11, 5, 22, 16, 4, stroke=0, fill=1)
        c.setFillColor(white)
        draw_string(c, 22, 10, f"{self.index:02d}", t.font_heading, 7.5, anchor="middle")
        draw_string(c, 42, 9.3, fit(self.title.upper(), t.font_heading, 9, w - 52), t.font_heading, 9)


class SubHeading(Flowable):
    """Small uppercase label with a left accent (e.g. 'TOP INCOME SOURCES')."""

    def __init__(self, theme: Theme, text: str, width: float = 0):
        super().__init__()
        self.t, self.text = theme, text
        self.width, self.height = width, 16
        self.spaceBefore, self.spaceAfter = 6, 3

    def wrap(self, aw, ah):
        self.width = self.width or aw
        return self.width, self.height

    def draw(self):
        c, t = self.canv, self.t
        c.setFillColor(tint(t.accent, 0.9))
        c.rect(0, 0, self.width, self.height, stroke=0, fill=1)
        c.setFillColor(t.accent)
        c.rect(0, 0, 2.5, self.height, stroke=0, fill=1)
        c.setFillColor(t.navy)
        draw_string(c, 9, 5, fit(self.text.upper(), t.font_heading_semi, 7.2, self.width - 14), t.font_heading_semi, 7.2)


class NavChips(Flowable):
    """Rounded chips linking to section bookmarks."""

    def __init__(self, theme: Theme, items: list[tuple[str, str]], width: float = 0):
        super().__init__()
        self.t, self.items = theme, items
        self.width = width
        self._layout: list[tuple[float, float, float, str, str]] = []
        self.height = 0

    def wrap(self, aw, ah):
        self.width = self.width or aw
        x, y, row_h, pad = 10.0, 0.0, 15.0, 9.0
        layout = []
        for label, key in self.items:
            w = string_width(label, self.t.font_medium, 6.8) + 2 * pad
            if x + w > self.width - 10:
                x, y = 10.0, y + row_h + 4
            layout.append((x, y, w, label, key))
            x += w + 5
        self._layout = layout
        self.height = y + row_h + 26
        return self.width, self.height

    def draw(self):
        c, t = self.canv, self.t
        c.setFillColor(t.label_bg)
        c.setStrokeColor(t.border)
        c.roundRect(0, 0, self.width, self.height, 8, stroke=1, fill=1)
        c.setFillColor(t.navy)
        draw_string(c, 10, self.height - 13, "QUICK NAVIGATION", t.font_heading_semi, 6.8)
        top = self.height - 22
        for x, y, w, label, key in self._layout:
            yy = top - y - 15
            c.setFillColor(white)
            c.setStrokeColor(t.border)
            c.roundRect(x, yy, w, 15, 7.5, stroke=1, fill=1)
            c.setFillColor(t.navy)
            draw_string(c, x + w / 2, yy + 5, label, t.font_medium, 6.8, anchor="middle")
            c.linkRect("", key, (x, yy, x + w, yy + 15), relative=1, thickness=0)


class MoneyTiles(Flowable):
    """Grid of account totals: (label, amount text, source/caption, accent colour).

    Amounts are rupee values shown in full; there is deliberately no score or scale.
    """

    def __init__(self, theme: Theme, tiles: list[tuple[str, str, str, Color]], columns: int = 3, width: float = 0):
        super().__init__()
        self.t, self.tiles, self.columns = theme, tiles, columns
        self.width = width
        self.tile_h, self.gap = 50, 6
        rows = (len(tiles) + columns - 1) // columns
        self.height = rows * self.tile_h + (rows - 1) * self.gap
        self.spaceBefore, self.spaceAfter = 4, 6

    def wrap(self, aw, ah):
        self.width = self.width or aw
        return self.width, self.height

    def draw(self):
        c, t = self.canv, self.t
        cw = (self.width - self.gap * (self.columns - 1)) / self.columns
        for i, (label, value, caption, color) in enumerate(self.tiles):
            row, col = divmod(i, self.columns)
            x = col * (cw + self.gap)
            y = self.height - (row + 1) * self.tile_h - row * self.gap
            c.setFillColor(white)
            c.setStrokeColor(t.border)
            c.roundRect(x, y, cw, self.tile_h, 6, stroke=1, fill=1)
            c.setFillColor(color)
            c.roundRect(x, y, 3.5, self.tile_h, 1.5, stroke=0, fill=1)
            c.setFillColor(t.muted)
            draw_string(c, x + 11, y + self.tile_h - 13, fit(label.upper(), t.font_heading_semi, 6.2, cw - 16),
                        t.font_heading_semi, 6.2)
            size = 13
            while size > 8 and string_width(value, t.font_display, size) > cw - 18:
                size -= 0.5
            c.setFillColor(t.ink)
            draw_string(c, x + 11, y + self.tile_h - 31, value, t.font_display, size)
            c.setFillColor(t.muted)
            draw_string(c, x + 11, y + 8, fit(caption, t.font, 6, cw - 16), t.font, 6)


class KpiCards(Flowable):
    """Row of analytical indicator cards: (label, value, caption, colour)."""

    def __init__(self, theme: Theme, cards: list[tuple[str, str, str, Color]], width: float = 0):
        super().__init__()
        self.t, self.cards = theme, cards
        self.width, self.height = width, 54
        self.spaceBefore, self.spaceAfter = 6, 4

    def wrap(self, aw, ah):
        self.width = self.width or aw
        return self.width, self.height

    def draw(self):
        c, t = self.canv, self.t
        n = len(self.cards)
        gap = 6
        cw = (self.width - gap * (n - 1)) / n
        for i, (label, value, caption, color) in enumerate(self.cards):
            x = i * (cw + gap)
            c.setFillColor(white)
            c.setStrokeColor(t.border)
            c.roundRect(x, 0, cw, self.height, 6, stroke=1, fill=1)
            c.setFillColor(color)
            c.roundRect(x, 0, 3.5, self.height, 1.5, stroke=0, fill=1)
            c.setFillColor(t.muted)
            draw_string(c, x + 10, self.height - 13, fit(label.upper(), t.font_heading_semi, 5.8, cw - 14),
                        t.font_heading_semi, 5.8)
            size = 12
            while size > 7.5 and string_width(value, t.font_display, size) > cw - 14:
                size -= 0.5
            c.setFillColor(t.ink)
            draw_string(c, x + 10, self.height - 31, value, t.font_display, size)
            c.setFillColor(t.muted)
            draw_string(c, x + 10, 9, fit(caption, t.font, 5.8, cw - 14), t.font, 5.8)


class HBarList(Flowable):
    """Horizontal bars: label | bar | value text."""

    def __init__(self, theme: Theme, rows: list[tuple[str, float, str]], width: float = 0):
        super().__init__()
        self.t, self.rows = theme, rows
        self.width = width
        self.row_h = 13
        self.height = self.row_h * len(rows) + 4

    def wrap(self, aw, ah):
        self.width = self.width or aw
        return self.width, self.height

    def draw(self):
        c, t = self.canv, self.t
        label_w, value_w = 120, 128
        bar_w = self.width - label_w - value_w - 12
        peak = max((r[1] for r in self.rows), default=0) or 1
        for i, (label, fraction, value) in enumerate(self.rows):
            y = self.height - (i + 1) * self.row_h
            c.setFillColor(t.ink)
            draw_string(c, label_w - 6, y + 3.5, fit(label, t.font_medium, 6.8, label_w - 10), t.font_medium, 6.8,
                        anchor="end")
            c.setFillColor(t.label_bg)
            c.setStrokeColor(t.border)
            c.roundRect(label_w, y + 2, bar_w, 7, 3.5, stroke=1, fill=1)
            fw = max(2.5, bar_w * (fraction / peak))
            c.setFillColor(t.bars[i % len(t.bars)])
            c.roundRect(label_w, y + 2, fw, 7, 3.5, stroke=0, fill=1)
            c.setFillColor(t.ink)
            draw_string(c, label_w + bar_w + 8, y + 3.5, fit(value, t.font, 6.8, value_w - 6), t.font, 6.8)


class Pill(Flowable):
    """Small rounded badge used inside table cells."""

    def __init__(self, theme: Theme, text: str, color: Color, align: str = "CENTER"):
        super().__init__()
        self.t, self.text, self.color, self.hAlign = theme, text.upper(), color, align
        self.width = string_width(self.text, theme.font_bold, 5.6) + 12
        self.height = 10

    def wrap(self, aw, ah):
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.setFillColor(tint(self.color, 0.88))
        c.setStrokeColor(tint(self.color, 0.55))
        c.roundRect(0, 0, self.width, self.height, 5, stroke=1, fill=1)
        c.setFillColor(self.color)
        draw_string(c, self.width / 2, 3, self.text, self.t.font_bold, 5.6, anchor="middle")


class Dot(Flowable):
    """Traffic-light dot in a soft pill (indicator level)."""

    def __init__(self, color: Color):
        super().__init__()
        self.color = color
        self.width, self.height = 22, 10
        self.hAlign = "CENTER"

    def wrap(self, aw, ah):
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.setFillColor(tint(self.color, 0.85))
        c.setStrokeColor(tint(self.color, 0.5))
        c.roundRect(0, 0, self.width, self.height, 5, stroke=1, fill=1)
        c.setFillColor(self.color)
        c.circle(self.width / 2, self.height / 2, 2.6, stroke=0, fill=1)


class Callout(Flowable):
    """Tinted box with a coloured left edge wrapping a Paragraph."""

    def __init__(self, theme: Theme, paragraph, color: Color, width: float = 0):
        super().__init__()
        self.t, self.para, self.color = theme, paragraph, color
        self.width = width
        self.pad = 7
        self.spaceBefore, self.spaceAfter = 4, 4

    def wrap(self, aw, ah):
        self.width = self.width or aw
        _, ph = self.para.wrap(self.width - 2 * self.pad - 6, ah)
        self.height = ph + 2 * self.pad
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.setFillColor(tint(self.color, 0.92))
        c.roundRect(0, 0, self.width, self.height, 5, stroke=0, fill=1)
        c.setFillColor(self.color)
        c.rect(0, 0, 3, self.height, stroke=0, fill=1)
        self.para.drawOn(c, self.pad + 6, self.pad)


class StatusBox(Flowable):
    """Large centred status panel summarising indicator levels (not a lending verdict)."""

    def __init__(self, theme: Theme, title: str, counts_line: str, body, color: Color, width: float = 0):
        super().__init__()
        self.t, self.title, self.counts_line, self.body, self.color = theme, title, counts_line, body, color
        self.width = width
        self.spaceBefore, self.spaceAfter = 8, 8

    def wrap(self, aw, ah):
        self.width = self.width or aw
        _, bh = self.body.wrap(self.width - 40, ah)
        self.height = 52 + bh
        return self.width, self.height

    def draw(self):
        c, t = self.canv, self.t
        c.setFillColor(tint(self.color, 0.9))
        c.setStrokeColor(tint(self.color, 0.45))
        c.setLineWidth(1)
        c.roundRect(0, 0, self.width, self.height, 9, stroke=1, fill=1)
        c.setFillColor(self.color)
        c.rect(12, self.height - 2.5, self.width - 24, 2.5, stroke=0, fill=1)
        draw_string(c, self.width / 2, self.height - 22, fit(self.title, t.font_heading, 12, self.width - 30),
                    t.font_heading, 12, anchor="middle")
        c.setFillColor(t.ink)
        draw_string(c, self.width / 2, self.height - 35, self.counts_line, t.font, 7, anchor="middle")
        self.body.drawOn(c, 20, 10)
