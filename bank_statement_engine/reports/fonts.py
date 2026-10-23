"""Central font registration and glyph-safe text helpers for the PDF report.

Roles
-----
* headings / section titles : Montserrat (fallback Poppins)
* display figures (KPI tiles): Poppins (fallback Montserrat)
* body, tables, transactions : Roboto
* glyph fallback             : DejaVu Sans (bundled; covers ₹ • → ● and other symbols)

Only static TTF files are registered (ReportLab cannot use variable fonts). Every family is
optional: a missing or unreadable file falls back to the next weight, then the next family,
then DejaVu, then Helvetica. TrueType fonts are embedded (subset) in the PDF by ReportLab.

ReportLab draws each string with a single font, so a character missing from that font would
render as an empty box. ``markup`` (for Paragraphs) and ``draw_string`` / ``string_width``
(for canvas drawing) switch to the fallback font for exactly those characters.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from .assets import resolve_asset

logger = logging.getLogger(__name__)

BUNDLED_FONTS = Path(__file__).resolve().parent / "fonts"
FALLBACK, FALLBACK_BOLD = "DejaVu", "DejaVu-Bold"
WEIGHTS = ("Regular", "Medium", "SemiBold", "Bold")
FAMILY_FILES = {
    "Montserrat": "Montserrat/static/Montserrat-{w}.ttf",
    "Poppins": "Poppins/Poppins-{w}.ttf",
    "Roboto": "Roboto/static/Roboto-{w}.ttf",
}
WEIGHT_PREFERENCE = {
    "Regular": ("Regular", "Medium"),
    "Medium": ("Medium", "Regular", "SemiBold"),
    "SemiBold": ("SemiBold", "Bold", "Medium"),
    "Bold": ("Bold", "SemiBold", "Medium"),
}


@dataclass(frozen=True)
class FontSet:
    heading: str          # section titles, header title
    heading_semi: str     # sub-headings, table header labels
    display: str          # large figures in summary tiles
    body: str
    body_medium: str
    body_bold: str
    fallback: str
    fallback_bold: str
    registered: tuple[str, ...]  # successfully registered family-weight names
    rupee: str


_REGISTRY: dict[str, FontSet] = {}


def _register(name: str, path: Path) -> bool:
    if name in pdfmetrics.getRegisteredFontNames():
        return True
    try:
        pdfmetrics.registerFont(TTFont(name, str(path)))
        return True
    except Exception as exc:  # corrupt / unsupported file
        logger.warning("Font %s could not be registered (%s); using a fallback.", name, type(exc).__name__)
        return False


def register_fonts(assets_dir: str | os.PathLike | None = None) -> FontSet:
    """Register all available report fonts once per asset directory and return the role mapping."""
    key = str(assets_dir or "")
    if key in _REGISTRY:
        return _REGISTRY[key]

    fallback_ok = _register(FALLBACK, BUNDLED_FONTS / "DejaVuSans.ttf")
    fallback_bold_ok = _register(FALLBACK_BOLD, BUNDLED_FONTS / "DejaVuSans-Bold.ttf")
    fb = FALLBACK if fallback_ok else "Helvetica"
    fbb = FALLBACK_BOLD if fallback_bold_ok else ("Helvetica-Bold" if not fallback_ok else fb)
    if fallback_ok:
        pdfmetrics.registerFontFamily(FALLBACK, normal=FALLBACK, bold=fbb, italic=FALLBACK, boldItalic=fbb)

    registered: list[str] = []
    for family, pattern in FAMILY_FILES.items():
        for weight in WEIGHTS:
            path = resolve_asset(pattern.format(w=weight), assets_dir)
            if path is not None and _register(f"{family}-{weight}", path):
                registered.append(f"{family}-{weight}")

    def pick(families: tuple[str, ...], weight: str, default: str) -> str:
        for family in families:
            for w in WEIGHT_PREFERENCE[weight]:
                if f"{family}-{w}" in registered:
                    return f"{family}-{w}"
        return default

    for family in FAMILY_FILES:
        normal = pick((family,), "Regular", "")
        bold = pick((family,), "Bold", "")
        if normal or bold:
            normal, bold = normal or bold, bold or normal
            pdfmetrics.registerFontFamily(family, normal=normal, bold=bold, italic=normal, boldItalic=bold)
            # Paragraph <b> resolves through the regular face's name.
            pdfmetrics.registerFontFamily(normal, normal=normal, bold=bold, italic=normal, boldItalic=bold)

    fonts = FontSet(
        heading=pick(("Montserrat", "Poppins"), "Bold", fbb),
        heading_semi=pick(("Montserrat", "Poppins"), "SemiBold", fbb),
        display=pick(("Poppins", "Montserrat"), "SemiBold", fbb),
        body=pick(("Roboto",), "Regular", fb),
        body_medium=pick(("Roboto",), "Medium", fb),
        body_bold=pick(("Roboto",), "Bold", fbb),
        fallback=fb,
        fallback_bold=fbb,
        registered=tuple(registered),
        rupee="₹" if fallback_ok or any(has_glyph(f, "₹") for f in registered) else "Rs.",
    )
    missing = sorted({f"{fam}-{w}" for fam in FAMILY_FILES for w in WEIGHTS} - set(registered))
    if missing:
        logger.info("Report fonts not found, using fallbacks: %s", ", ".join(missing))
    _REGISTRY[key] = fonts
    return fonts


def has_glyph(font: str, char: str) -> bool:
    try:
        face = pdfmetrics.getFont(font).face
    except Exception:
        return False
    cmap = getattr(face, "charToGlyph", None)
    if cmap is None:  # standard Type 1 fonts: Latin-1 only
        return ord(char) < 256
    return ord(char) in cmap


def _fallback_for(font: str) -> str:
    bold = any(tag in font for tag in ("Bold", "SemiBold", "Black"))
    preferred = FALLBACK_BOLD if bold else FALLBACK
    if preferred in pdfmetrics.getRegisteredFontNames():
        return preferred
    return "Helvetica-Bold" if bold else "Helvetica"


def runs(text: str, font: str) -> list[tuple[str, str]]:
    """Split ``text`` into (segment, font) runs, using the fallback for unsupported characters."""
    out: list[tuple[str, str]] = []
    fallback = _fallback_for(font)
    for ch in text:
        use = font if ch.isascii() or has_glyph(font, ch) else fallback
        if out and out[-1][1] == use:
            out[-1] = (out[-1][0] + ch, use)
        else:
            out.append((ch, use))
    return out


def string_width(text: str, font: str, size: float) -> float:
    return sum(pdfmetrics.stringWidth(seg, f, size) for seg, f in runs(text, font))


def draw_string(canv, x: float, y: float, text: str, font: str, size: float, anchor: str = "start") -> None:
    """Glyph-safe replacement for ``drawString`` / ``drawCentredString`` / ``drawRightString``."""
    width = string_width(text, font, size)
    if anchor == "middle":
        x -= width / 2
    elif anchor == "end":
        x -= width
    for seg, f in runs(text, font):
        canv.setFont(f, size)
        canv.drawString(x, y, seg)
        x += pdfmetrics.stringWidth(seg, f, size)


def fit(text: str, font: str, size: float, width: float) -> str:
    """Truncate with an ellipsis so the text fits ``width``."""
    if string_width(text, font, size) <= width:
        return text
    while text and string_width(text + "…", font, size) > width:
        text = text[:-1]
    return text + "…"


def markup(text: str, font: str) -> str:
    """Wrap characters ``font`` lacks in ``<font name=fallback>`` for Paragraph markup.

    Markup tags and entities are ASCII and therefore never touched.
    """
    if text.isascii():
        return text
    fallback = _fallback_for(font)
    out = []
    for ch in text:
        if ch.isascii() or has_glyph(font, ch):
            out.append(ch)
        else:
            out.append(f'<font name="{fallback}">{ch}</font>')
    return "".join(out)
