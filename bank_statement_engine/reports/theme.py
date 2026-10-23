"""Report template loading, colours and font roles (registration lives in ``fonts.py``)."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.colors import Color
from .fonts import FontSet, register_fonts

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_template(name_or_path: str) -> dict[str, Any]:
    """Load a report template by name (``templates/<name>.json``) or path, merged over the default."""
    default = json.loads((TEMPLATES / "default.json").read_text(encoding="utf-8"))
    if not name_or_path or name_or_path == "default":
        return default
    path = Path(name_or_path)
    if not path.suffix:
        path = TEMPLATES / f"{name_or_path}.json"
    if not path.is_file():
        raise ValueError(f"Report template not found: {name_or_path}")
    return _deep_merge(default, json.loads(path.read_text(encoding="utf-8")))


@dataclass
class Theme:
    navy: Color
    navy_light: Color
    accent: Color
    ink: Color
    muted: Color
    label_bg: Color
    zebra: Color
    border: Color
    positive: Color
    negative: Color
    warning: Color
    info: Color
    chip_bg: Color
    bars: list[Color]
    font: str              # body / tables (Roboto)
    font_bold: str
    font_medium: str
    font_heading: str      # titles (Montserrat)
    font_heading_semi: str
    font_display: str      # summary figures (Poppins)
    rupee: str
    fonts: FontSet | None = None

    @classmethod
    def from_template(cls, template: dict, assets_dir=None) -> "Theme":
        t = template["theme"]
        f = register_fonts(assets_dir)
        c = colors.HexColor
        return cls(
            navy=c(t["navy"]), navy_light=c(t["navy_light"]), accent=c(t["accent"]), ink=c(t["ink"]),
            muted=c(t["muted"]), label_bg=c(t["label_bg"]), zebra=c(t["zebra"]), border=c(t["border"]),
            positive=c(t["positive"]), negative=c(t["negative"]), warning=c(t["warning"]), info=c(t["info"]),
            chip_bg=c(t["chip_bg"]), bars=[c(x) for x in t["bars"]],
            font=f.body, font_bold=f.body_bold, font_medium=f.body_medium,
            font_heading=f.heading, font_heading_semi=f.heading_semi, font_display=f.display,
            rupee=f.rupee, fonts=f,
        )

    def severity_color(self, severity: str) -> Color:
        return {"HIGH": self.negative, "MEDIUM": self.warning, "LOW": self.info, "INFO": self.muted}.get(
            severity, self.muted)

    def status_color(self, status: str) -> Color:
        return {"VERIFIED": self.positive, "REQUIRES_REVIEW": self.warning, "FAILED": self.negative}.get(
            status, self.muted)


def tint(color: Color, amount: float) -> Color:
    """Mix ``color`` with white (amount=0.9 -> very light)."""
    return Color(color.red + (1 - color.red) * amount, color.green + (1 - color.green) * amount,
                 color.blue + (1 - color.blue) * amount)
