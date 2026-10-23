"""Native PDF text extraction with PyMuPDF, with per-page OCR fallback."""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Iterator

import pymupdf

from ..config import EngineConfig
from ..exceptions import (
    BankStatementError,
    EncryptedPDFError,
    InvalidPDFError,
    OCRUnavailableError,
    ProcessingTimeoutError,
    TooManyPagesError,
)
from .base import OCRProvider, PageContent, Word

logger = logging.getLogger(__name__)

_READABLE = re.compile(r"[A-Za-z0-9]")
_DAMAGED = "The PDF file is damaged and cannot be read."

# MuPDF prints parser diagnostics to stderr; for damaged files these can echo fragments of
# the document. Errors still surface as exceptions, which the engine converts to safe errors.
pymupdf.TOOLS.mupdf_display_errors(False)
pymupdf.TOOLS.mupdf_display_warnings(False)
# MuPDF's warning store is process-global; serialise reset -> extract -> check across threads.
_MUPDF_DIAGNOSTICS_LOCK = threading.Lock()


class DocumentExtractor:
    """Opens a PDF from bytes and yields ``PageContent`` page by page.

    Pages are processed lazily so large statements are never fully expanded
    in memory. OCR is attempted only on pages whose native text is missing or
    unreadable (``ocr.mode == "auto"``), on every page (``"force"``) or never (``"off"``).
    """

    def __init__(
        self,
        data: bytes,
        config: EngineConfig,
        ocr_provider: OCRProvider | None = None,
        password: str | None = None,
        deadline: float | None = None,
    ):
        self.config = config
        self.ocr_provider = ocr_provider
        self.deadline = deadline
        self.ocr_pages: list[int] = []
        self.unreadable_pages: list[int] = []
        self.blank_pages: list[int] = []
        self.damaged_pages: list[int] = []
        try:
            self.doc = pymupdf.open(stream=data, filetype="pdf")
            needs_pass = self.doc.needs_pass
        except Exception as exc:  # pymupdf raises a variety of errors for corrupt files
            raise InvalidPDFError() from exc
        if needs_pass:
            if not password or not self.doc.authenticate(password):
                self.doc.close()
                raise EncryptedPDFError()
        try:
            count = self.doc.page_count
        except Exception as exc:
            self.doc.close()
            raise InvalidPDFError(_DAMAGED) from exc
        if count == 0:
            self.doc.close()
            raise InvalidPDFError("The PDF contains no pages.")
        if count > config.limits.max_pages:
            self.doc.close()
            raise TooManyPagesError(details={"pages": count, "max_pages": config.limits.max_pages})

    @property
    def page_count(self) -> int:
        return self.doc.page_count

    def close(self) -> None:
        self.doc.close()

    def __enter__(self) -> "DocumentExtractor":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def pages(self, force_ocr: bool = False) -> Iterator[PageContent]:
        for index in range(self.doc.page_count):
            if self.deadline is not None and time.monotonic() > self.deadline:
                raise ProcessingTimeoutError(details={"page": index + 1})
            try:
                page = self._page(index, force_ocr)
            except BankStatementError:
                raise
            except Exception as exc:  # damaged page tree / content stream inside an otherwise valid file
                logger.warning("Unreadable PDF structure on page %s (%s)", index + 1, type(exc).__name__)
                raise InvalidPDFError(_DAMAGED, details={"page": index + 1}) from exc
            yield page

    # ------------------------------------------------------------------ internals
    def _page(self, index: int, force_ocr: bool) -> PageContent:
        page = self.doc[index]
        rect = page.rect
        content = PageContent(number=index + 1, width=rect.width, height=rect.height)
        with _MUPDF_DIAGNOSTICS_LOCK:
            pymupdf.TOOLS.reset_mupdf_warnings()
            raw_words = page.get_text("words")
            image_coverage = _image_coverage(page)
            separators = _separators(page)
            # MuPDF records (silently) any error decoding this page's content, e.g. a corrupt stream.
            # Such a page may be partly or entirely missing even though no exception was raised.
            damaged = bool(pymupdf.TOOLS.mupdf_warnings())
        content.words = [Word(w[4], w[0], w[1], w[2], w[3]) for w in raw_words if w[4].strip()]
        text = "".join(w.text for w in content.words)
        content.char_count = len(text)
        content.image_coverage = image_coverage
        content.separators = separators
        if damaged:
            self.damaged_pages.append(content.number)
            content.warnings.append("Page content stream is damaged; text may be incomplete.")

        readable = len(_READABLE.findall(text))
        # Text that is mostly non-alphanumeric is undecodable (missing font maps, damaged streams).
        content.garbled = content.char_count >= self.config.ocr.min_text_chars and readable < 0.5 * content.char_count
        needs_ocr = (
            self.config.ocr.mode == "force"
            or force_ocr
            or (
                self.config.ocr.mode == "auto"
                and (content.char_count < self.config.ocr.min_text_chars or readable < 0.5 * max(1, content.char_count))
            )
        )
        if needs_ocr and self.config.ocr.mode != "off":
            self._ocr(page, content)
        elif content.char_count == 0 or content.garbled:
            self._mark_textless(content, "Page has no readable text and OCR is disabled.")
        return content

    def _mark_textless(self, content: PageContent, reason: str) -> None:
        """Record a page without text: genuinely blank, or content we could not read."""
        content.method = "none"
        if content.char_count == 0 and content.image_coverage < 0.01 and not content.separators:
            self.blank_pages.append(content.number)
        else:
            self.unreadable_pages.append(content.number)
            content.warnings.append(reason)

    def _ocr(self, page: "pymupdf.Page", content: PageContent) -> None:
        provider = self.ocr_provider
        if provider is None or not provider.is_available():
            if content.char_count == 0 or content.garbled:
                self._mark_textless(content, "Page has no text and OCR is unavailable.")
                if self.config.ocr.mode == "force":
                    raise OCRUnavailableError()
            return
        if len(self.ocr_pages) >= self.config.ocr.max_pages:
            if content.char_count == 0 or content.garbled:
                self._mark_textless(content, "OCR page limit reached; page not OCR'd.")
            return
        dpi = self.config.ocr.dpi
        png = page.get_pixmap(dpi=dpi).tobytes("png")
        words = provider.recognize(png, dpi)
        logger.debug("OCR page %s produced %s words", content.number, len(words))
        content.words = words
        content.char_count = sum(len(w.text) for w in words)
        content.method = "ocr"
        # Ruling lines are not reliable for scanned pages; rows are segmented by gaps instead.
        content.separators = []
        self.ocr_pages.append(content.number)


def _image_coverage(page: "pymupdf.Page") -> float:
    area = page.rect.width * page.rect.height or 1
    covered = 0.0
    for info in page.get_image_info():
        x0, y0, x1, y1 = info["bbox"]
        covered += max(0.0, x1 - x0) * max(0.0, y1 - y0)
    return min(1.0, covered / area)


def _separators(page: "pymupdf.Page") -> list[float]:
    """y positions of horizontal rules / row rectangles spanning >= 30% of the page width."""
    min_width = page.rect.width * 0.3
    ys: list[float] = []
    try:
        drawings = page.get_drawings()
    except Exception:  # pragma: no cover - defensive; malformed content streams
        return []
    for drawing in drawings:
        for item in drawing.get("items", []):
            kind = item[0]
            if kind == "l":
                a, b = item[1], item[2]
                if abs(a.y - b.y) < 1.0 and abs(a.x - b.x) >= min_width:
                    ys.append(a.y)
            elif kind == "re":
                r = item[1]
                if r.width >= min_width:
                    ys.append(r.y0)
                    if r.height >= 2:
                        ys.append(r.y1)
    ys.sort()
    merged: list[float] = []
    for y in ys:
        if not merged or y - merged[-1] > 1.5:
            merged.append(y)
    return merged
