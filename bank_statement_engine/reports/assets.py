"""Locating branding assets (logo, fonts) without depending on the working directory.

Search order for a relative asset path such as ``"av-logo.png"``:

1. ``ReportConfig.assets_dir`` (explicit configuration);
2. the ``BSA_ASSETS_DIR`` environment variable;
3. ``<project root>/assets`` when running from a source checkout;
4. the copy bundled inside the package (``bank_statement_engine/reports/assets``), so an
   installed engine in another Django project is branded without extra setup.

Nothing here raises for a missing or unreadable asset: callers get ``None`` and the report is
rendered without it.
"""

from __future__ import annotations

import io
import logging
import os
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

PACKAGE_ASSETS = Path(__file__).resolve().parent / "assets"
SOURCE_ASSETS = Path(__file__).resolve().parents[2] / "assets"
ENV_VAR = "BSA_ASSETS_DIR"
LOGO_FILE = "av-logo.png"
#: Logos are downscaled to this width before embedding (keeps PDFs small; ample for print).
LOGO_MAX_WIDTH_PX = 1200


def asset_dirs(override: str | os.PathLike | None = None) -> list[Path]:
    dirs: list[Path] = []
    if override:
        dirs.append(Path(override))
    if os.environ.get(ENV_VAR):
        dirs.append(Path(os.environ[ENV_VAR]))
    dirs += [SOURCE_ASSETS, PACKAGE_ASSETS]
    return dirs


def resolve_asset(relative: str, override: str | os.PathLike | None = None) -> Path | None:
    for base in asset_dirs(override):
        candidate = base / relative
        if candidate.is_file():
            return candidate
    return None


class Logo:
    """A decoded logo ready for ReportLab, with its natural aspect ratio."""

    def __init__(self, reader, aspect: float, source: Path):
        self.reader = reader          # reportlab.lib.utils.ImageReader
        self.aspect = aspect          # width / height
        self.source = source

    def size_for_height(self, height: float) -> tuple[float, float]:
        return height * self.aspect, height

    def size_within(self, max_width: float, max_height: float) -> tuple[float, float]:
        """Largest size fitting the box without changing the aspect ratio."""
        width = min(max_width, max_height * self.aspect)
        return width, width / self.aspect


@lru_cache(maxsize=8)
def _load(path_str: str, mtime: float) -> Logo | None:
    from PIL import Image
    from reportlab.lib.utils import ImageReader

    path = Path(path_str)
    try:
        with Image.open(path) as img:
            img.load()
            image = img.convert("RGBA")
        if image.width < 1 or image.height < 1:
            raise ValueError("empty image")
        if image.width > LOGO_MAX_WIDTH_PX:
            height = max(1, round(image.height * LOGO_MAX_WIDTH_PX / image.width))
            image = image.resize((LOGO_MAX_WIDTH_PX, height), Image.LANCZOS)
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        buf.seek(0)
        return Logo(ImageReader(buf), image.width / image.height, path)
    except Exception as exc:  # unreadable/corrupt image: render the report without a logo
        logger.warning("Report logo could not be loaded (%s); continuing without it.", type(exc).__name__)
        return None


def load_logo(logo_path: str | os.PathLike | None = None, assets_dir: str | os.PathLike | None = None) -> Logo | None:
    """Load the configured logo, or the default ``av-logo.png`` from the asset search path.

    An explicit ``logo_path`` that does not exist yields ``None`` (no silent substitution).
    """
    if logo_path:
        path = Path(logo_path)
        if not path.is_file():
            logger.warning("Configured report logo not found; continuing without a logo.")
            return None
    else:
        path = resolve_asset(LOGO_FILE, assets_dir)
        if path is None:
            logger.info("No report logo found in asset directories; continuing without a logo.")
            return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    return _load(str(path.resolve()), mtime)
