"""Tesseract OCR provider (via pytesseract).

Installation is optional. When ``pytesseract`` or the ``tesseract`` binary is
missing, ``is_available()`` returns False (``unavailable_reason`` says why) and the
pipeline reports ``OCRUnavailableError`` only if a page actually needs OCR.

The binary is located in this order:

1. ``tesseract_cmd`` passed to the provider (``EngineConfig.ocr.tesseract_cmd``);
2. the ``TESSERACT_CMD`` / ``BSA_TESSERACT_CMD`` environment variables;
3. ``tesseract`` on ``PATH``;
4. the standard install locations (UB Mannheim installer on Windows,
   ``/usr/bin`` / ``/usr/local/bin`` / Homebrew elsewhere).

An explicitly configured path that does not work is reported as unavailable; it is
never silently replaced by another binary.
"""

from __future__ import annotations

import io
import logging
import os
import re
import shutil
import sys
import threading
from pathlib import Path

from .base import OCRProvider, Word

logger = logging.getLogger(__name__)

ENV_VARS = ("TESSERACT_CMD", "BSA_TESSERACT_CMD")

#: Characters that table borders turn into when OCR reads a ruled cell edge.
_BORDER_EDGE = "|¦"
_BRACKET_EDGE = "[]{}"
_BORDER_ONLY = re.compile(r"^[|¦_=~\[\]{}—–]+$")

# pytesseract keeps the binary path in a module global; guard updates to it.
_CMD_LOCK = threading.Lock()


class OCRError(Exception):
    """OCR of a page failed (engine crash, timeout, bad image). Message is safe to log."""


def _candidate_paths() -> list[Path]:
    if sys.platform.startswith("win"):
        roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramW6432"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs") if os.environ.get("LOCALAPPDATA") else None,
                 r"C:\Program Files", r"C:\Program Files (x86)"]
        seen: list[Path] = []
        for root in roots:
            if root:
                path = Path(root) / "Tesseract-OCR" / "tesseract.exe"
                if path not in seen:
                    seen.append(path)
        return seen
    return [Path(p) for p in ("/usr/bin/tesseract", "/usr/local/bin/tesseract",
                               "/opt/homebrew/bin/tesseract", "/opt/local/bin/tesseract")]


def resolve_tesseract_cmd(configured: str | None = None) -> tuple[str | None, str]:
    """Return ``(path_or_None, source)`` for the tesseract binary.

    ``source`` is one of ``config``, ``env:<NAME>``, ``path``, ``default-location`` or
    ``not-found``. A configured or environment value is returned as given (after
    resolving a bare command name through ``PATH``) even if it does not exist, so the
    availability check can report the misconfiguration instead of hiding it.
    """
    explicit: list[tuple[str, str]] = []
    if configured and str(configured).strip():
        explicit.append((str(configured).strip().strip('"'), "config"))
    for name in ENV_VARS:
        value = os.environ.get(name, "").strip().strip('"')
        if value:
            explicit.append((value, f"env:{name}"))
    if explicit:
        value, source = explicit[0]
        return shutil.which(value) or value, source
    found = shutil.which("tesseract")
    if found:
        return found, "path"
    for candidate in _candidate_paths():
        if candidate.is_file():
            return str(candidate), "default-location"
    return None, "not-found"


class TesseractOCRProvider(OCRProvider):
    name = "tesseract"

    def __init__(
        self,
        language: str = "eng",
        tesseract_cmd: str | None = None,
        timeout: int = 60,
        tessdata_dir: str | None = None,
        psm: int = 6,
    ):
        self.language = language
        self.tesseract_cmd = tesseract_cmd
        self.timeout = timeout
        self.tessdata_dir = tessdata_dir
        self.psm = psm
        self._available: bool | None = None
        #: Why OCR cannot be used (no statement content, no paths); None when available.
        self.unavailable_reason: str | None = None
        #: Resolved binary path and where it came from (for diagnostics / logs).
        self.resolved_cmd: str | None = None
        self.cmd_source: str | None = None
        self.version: str | None = None

    # ------------------------------------------------------------------ availability
    def _module(self):
        import pytesseract  # optional dependency

        if self.resolved_cmd:
            with _CMD_LOCK:
                pytesseract.pytesseract.tesseract_cmd = self.resolved_cmd
        return pytesseract

    def _config_flags(self) -> str:
        flags = f"--psm {self.psm} -c preserve_interword_spaces=1"
        if self.tessdata_dir:
            # Quotes keep paths with spaces intact; pytesseract splits flags with shlex.
            flags = f'--tessdata-dir "{Path(self.tessdata_dir).as_posix()}" ' + flags
        return flags

    def is_available(self) -> bool:
        if self._available is None:
            self._available = self._check()
            if self._available:
                logger.info("Tesseract OCR available (version %s, found via %s)", self.version, self.cmd_source)
            else:
                # Server-side diagnostics only; the API error stays generic.
                logger.warning("Tesseract OCR unavailable: %s", self.unavailable_reason)
        return self._available

    def _check(self) -> bool:
        try:
            import pytesseract  # noqa: F401
        except ImportError:
            self.unavailable_reason = "pytesseract is not installed (pip install pytesseract Pillow)"
            return False
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.unavailable_reason = "Pillow is not installed (pip install Pillow)"
            return False

        cmd, source = resolve_tesseract_cmd(self.tesseract_cmd)
        self.cmd_source = source
        if cmd is None:
            self.unavailable_reason = (
                "tesseract executable not found on PATH or in the standard install locations; "
                "set ocr.tesseract_cmd or the TESSERACT_CMD environment variable"
            )
            return False
        if not Path(cmd).is_file():
            self.unavailable_reason = f"tesseract executable configured via {source} does not exist: {cmd}"
            return False
        self.resolved_cmd = cmd
        module = self._module()
        try:
            self.version = str(module.get_tesseract_version())
        except Exception as exc:  # TesseractNotFoundError, OSError, permission errors
            self.unavailable_reason = f"tesseract executable ({source}) could not be run: {type(exc).__name__}"
            return False
        try:
            installed = set(module.get_languages(config=f'--tessdata-dir "{Path(self.tessdata_dir).as_posix()}"'
                                                 if self.tessdata_dir else ""))
        except Exception as exc:
            self.unavailable_reason = f"tesseract language data could not be listed: {type(exc).__name__}"
            return False
        missing = [lang for lang in self.language.split("+") if lang and lang not in installed]
        if missing:
            self.unavailable_reason = (
                f"tesseract language data missing for {'+'.join(missing)}; install the traineddata "
                "files or set ocr.tessdata_dir / TESSDATA_PREFIX"
            )
            return False
        self.unavailable_reason = None
        return True

    # ------------------------------------------------------------------ recognition
    def recognize(self, png_bytes: bytes, dpi: int) -> list[Word]:
        from PIL import Image

        pytesseract = self._module()
        try:
            image = Image.open(io.BytesIO(png_bytes))
            # psm 6 = assume a uniform block of text; preserves table rows well.
            data = pytesseract.image_to_data(
                image,
                lang=self.language,
                config=self._config_flags(),
                output_type=pytesseract.Output.DICT,
                timeout=self.timeout,
            )
        except RuntimeError as exc:  # pytesseract raises RuntimeError on timeout
            raise OCRError(f"tesseract timed out after {self.timeout}s") from exc
        except Exception as exc:  # TesseractError, OSError, PIL errors
            raise OCRError(f"tesseract failed: {type(exc).__name__}") from exc
        scale = 72.0 / dpi
        words: list[Word] = []
        for i, text in enumerate(data["text"]):
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                conf = -1
            if conf < 0:
                continue
            text = clean_ocr_token(text)
            if not text:
                continue
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            words.append(Word(text, x * scale, y * scale, (x + w) * scale, (y + h) * scale))
        return words


def clean_ocr_token(text: str | None) -> str:
    """Remove table-border artefacts that OCR glues onto tokens (``|03/04/2026``, ``[500.00``).

    Pipes are never meaningful at a token edge in a statement table. Brackets are removed
    only around numeric tokens, so narration text such as ``[REF]`` is kept. Tokens that
    consist only of border glyphs are dropped.
    """
    text = (text or "").strip()
    if not text or _BORDER_ONLY.match(text):
        return ""
    text = text.strip(_BORDER_EDGE).strip()
    core = text.strip(_BRACKET_EDGE + _BORDER_EDGE)
    if core and core[0].isdigit() and core[-1].isdigit():
        text = core
    return "" if _BORDER_ONLY.match(text or "|") else text
