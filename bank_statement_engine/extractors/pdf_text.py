"""Native PDF text extraction with PyMuPDF, with per-page OCR fallback."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections import deque
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

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
from .ocr import OCRError

logger = logging.getLogger(__name__)

_READABLE = re.compile(r"[A-Za-z0-9]")
_DAMAGED = "The PDF file is damaged and cannot be read."

# MuPDF prints parser diagnostics to stderr; for damaged files these can echo fragments of
# the document. Errors still surface as exceptions, which the engine converts to safe errors.
pymupdf.TOOLS.mupdf_display_errors(False)
pymupdf.TOOLS.mupdf_display_warnings(False)
# MuPDF's warning store is process-global; serialise reset -> extract -> check across threads.
_MUPDF_DIAGNOSTICS_LOCK = threading.Lock()

#: Minimum length (points) of a stroke treated as a table rule; glyph strokes are shorter.
_MIN_RULE_LENGTH = 8.0
#: Maximum thickness (points) of a filled rectangle treated as a drawn line.
_MAX_RULE_THICKNESS = 2.5

Rect = tuple[float, float, float, float]


class _OCRJob:
    """A rendered page waiting for (or running) OCR."""

    __slots__ = ("png", "dpi", "future")

    def __init__(self, png: bytes, dpi: int):
        self.png = png
        self.dpi = dpi
        self.future: Future | None = None


class DocumentExtractor:
    """Opens a PDF from bytes and yields ``PageContent`` page by page.

    Pages are processed lazily so large statements are never fully expanded
    in memory. OCR is attempted only on pages whose native text is missing or
    unreadable (``ocr.mode == "auto"``), on every page (``"force"``) or never (``"off"``).
    OCR of upcoming pages runs in a small thread pool (``ocr.workers``) while pages
    are still yielded strictly in order.
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
        #: Pages where OCR was attempted but the engine failed (crash, timeout).
        self.ocr_failed_pages: list[int] = []
        self.unreadable_pages: list[int] = []
        self.blank_pages: list[int] = []
        self.damaged_pages: list[int] = []
        self._ocr_attempts = 0
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
        workers = self._ocr_workers()
        executor: ThreadPoolExecutor | None = None
        pending: deque[tuple[PageContent, _OCRJob | None]] = deque()
        try:
            for index in range(self.doc.page_count):
                self._check_deadline(index + 1)
                try:
                    content, job = self._page(index, force_ocr)
                except BankStatementError:
                    raise
                except Exception as exc:  # damaged page tree / content stream inside an otherwise valid file
                    logger.warning("Unreadable PDF structure on page %s (%s)", index + 1, type(exc).__name__)
                    raise InvalidPDFError(_DAMAGED, details={"page": index + 1}) from exc
                if job is not None and workers > 1:
                    if executor is None:
                        executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bsa-ocr")
                    job.future = executor.submit(self.ocr_provider.recognize, job.png, job.dpi)
                pending.append((content, job))
                # Yield in page order; keep at most ``workers`` OCR pages in flight.
                while pending and (pending[0][1] is None or len(pending) > workers):
                    yield self._finish(*pending.popleft())
            while pending:
                yield self._finish(*pending.popleft())
        finally:
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)

    # ------------------------------------------------------------------ internals
    def _ocr_workers(self) -> int:
        configured = self.config.ocr.workers
        if configured and configured > 0:
            return configured
        return max(1, min(4, (os.cpu_count() or 2) // 2))

    def _check_deadline(self, page_no: int) -> None:
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise ProcessingTimeoutError(details={"page": page_no})

    def _page(self, index: int, force_ocr: bool) -> tuple[PageContent, _OCRJob | None]:
        page = self.doc[index]
        rect = page.rect
        content = PageContent(number=index + 1, width=rect.width, height=rect.height)
        with _MUPDF_DIAGNOSTICS_LOCK:
            pymupdf.TOOLS.reset_mupdf_warnings()
            raw_words = page.get_text("words")
            image_coverage = _image_coverage(page)
            separators, rules = _drawing_geometry(page)
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
            return content, self._ocr_job(page, content, rules)
        if content.char_count == 0 or content.garbled:
            self._mark_textless(content, "Page has no readable text and OCR is disabled.")
        return content, None

    def _mark_textless(self, content: PageContent, reason: str) -> None:
        """Record a page without text: genuinely blank, or content we could not read."""
        content.method = "none"
        if content.char_count == 0 and content.image_coverage < 0.01 and not content.separators:
            self.blank_pages.append(content.number)
        else:
            self.unreadable_pages.append(content.number)
            content.warnings.append(reason)

    def _ocr_job(self, page: "pymupdf.Page", content: PageContent, rules: list[Rect]) -> _OCRJob | None:
        """Render the page for OCR, or record why it cannot be OCR'd."""
        provider = self.ocr_provider
        if provider is None or not provider.is_available():
            if content.char_count == 0 or content.garbled:
                self._mark_textless(content, "Page has no text and OCR is unavailable.")
                if self.config.ocr.mode == "force":
                    raise OCRUnavailableError()
            return None
        if self._ocr_attempts >= self.config.ocr.max_pages:
            if content.char_count == 0 or content.garbled:
                self._mark_textless(content, "OCR page limit reached; page not OCR'd.")
            return None
        self._ocr_attempts += 1
        dpi = self.config.ocr.dpi
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
        if self.config.ocr.mask_ruling_lines and rules and not page.rotation:
            _mask_rules(pix, rules, dpi)
        return _OCRJob(pix.tobytes("png"), dpi)

    def _finish(self, content: PageContent, job: _OCRJob | None) -> PageContent:
        if job is None:
            return content
        try:
            if job.future is not None:
                remaining = None if self.deadline is None else max(0.0, self.deadline - time.monotonic())
                words = job.future.result(timeout=remaining)
            else:
                self._check_deadline(content.number)
                words = self.ocr_provider.recognize(job.png, job.dpi)
        except FutureTimeout as exc:
            raise ProcessingTimeoutError(details={"page": content.number}) from exc
        except BankStatementError:
            raise
        except Exception as exc:  # OCR engine crash / per-page timeout: the page is unreadable, not the PDF
            reason = str(exc) if isinstance(exc, OCRError) else type(exc).__name__
            logger.warning("OCR failed on page %s (%s)", content.number, reason)
            self.ocr_failed_pages.append(content.number)
            if content.char_count == 0 or content.garbled:
                self._mark_textless(content, "OCR failed on this page; its transactions may be missing.")
            return content
        finally:
            job.png = b""  # release the image as soon as it is consumed
        logger.debug("OCR page %s produced %s words", content.number, len(words))
        content.words = words
        content.char_count = sum(len(w.text) for w in words)
        content.method = "ocr"
        # Vector ruling lines (if the page has any) share the OCR words' coordinate system, so they
        # still delimit rows. Scanned images have none, and rows are then segmented by gaps.
        self.ocr_pages.append(content.number)
        return content


def _image_coverage(page: "pymupdf.Page") -> float:
    area = page.rect.width * page.rect.height or 1
    covered = 0.0
    for info in page.get_image_info():
        x0, y0, x1, y1 = info["bbox"]
        covered += max(0.0, x1 - x0) * max(0.0, y1 - y0)
    return min(1.0, covered / area)


def _separators(page: "pymupdf.Page") -> list[float]:
    """y positions of horizontal rules / row rectangles spanning >= 30% of the page width."""
    return _drawing_geometry(page)[0]


def _drawing_geometry(page: "pymupdf.Page") -> tuple[list[float], list[Rect]]:
    """Return ``(separators, rules)`` from the page's vector drawings.

    ``separators``: y positions of horizontal rules / row rectangles spanning >= 30% of
    the page width (used to delimit table rows).
    ``rules``: bounding boxes (points) of straight table lines of any orientation, used
    to blank borders out of the page image before OCR. Filled glyph outlines are not rules.
    """
    min_width = page.rect.width * 0.3
    ys: list[float] = []
    rules: list[Rect] = []
    try:
        drawings = page.get_drawings()
    except Exception:  # pragma: no cover - defensive; malformed content streams
        return [], []
    for drawing in drawings:
        stroked = "s" in (drawing.get("type") or "")
        half = max(float(drawing.get("width") or 1.0), 0.5) / 2
        items = drawing.get("items", [])
        for item in items:
            kind = item[0]
            if kind == "l":
                a, b = item[1], item[2]
                if abs(a.y - b.y) < 1.0 and abs(a.x - b.x) >= min_width:
                    ys.append(a.y)
                if stroked:
                    _add_segment(rules, a.x, a.y, b.x, b.y, half)
            elif kind == "re":
                r = item[1]
                if r.width >= min_width:
                    ys.append(r.y0)
                    if r.height >= 2:
                        ys.append(r.y1)
                if stroked:
                    for x0, y0, x1, y1 in ((r.x0, r.y0, r.x1, r.y0), (r.x0, r.y1, r.x1, r.y1),
                                           (r.x0, r.y0, r.x0, r.y1), (r.x1, r.y0, r.x1, r.y1)):
                        _add_segment(rules, x0, y0, x1, y1, half)
                elif len(items) == 1 and min(r.width, r.height) <= _MAX_RULE_THICKNESS \
                        and max(r.width, r.height) >= _MIN_RULE_LENGTH:
                    rules.append((r.x0, r.y0, r.x1, r.y1))  # a line drawn as a thin filled box
    ys.sort()
    merged: list[float] = []
    for y in ys:
        if not merged or y - merged[-1] > 1.5:
            merged.append(y)
    return merged, rules


def _add_segment(rules: list[Rect], x0: float, y0: float, x1: float, y1: float, half: float) -> None:
    horizontal = abs(y0 - y1) < 1.0 and abs(x1 - x0) >= _MIN_RULE_LENGTH
    vertical = abs(x0 - x1) < 1.0 and abs(y1 - y0) >= _MIN_RULE_LENGTH
    if horizontal or vertical:
        rules.append((min(x0, x1) - half, min(y0, y1) - half, max(x0, x1) + half, max(y0, y1) + half))


def _mask_rules(pix: "pymupdf.Pixmap", rules: list[Rect], dpi: int) -> None:
    """Paint table rules white in a rendered page image (coordinates in points)."""
    scale = dpi / 72.0
    pad = 0.6  # points; covers anti-aliasing around the stroke
    white = (255,) * pix.n
    bounds = pymupdf.IRect(0, 0, pix.width, pix.height)
    for x0, y0, x1, y1 in rules:
        irect = pymupdf.IRect(
            int((x0 - pad) * scale), int((y0 - pad) * scale),
            int((x1 + pad) * scale) + 1, int((y1 + pad) * scale) + 1,
        ) & bounds
        if not irect.is_empty:
            pix.set_rect(irect, white)
