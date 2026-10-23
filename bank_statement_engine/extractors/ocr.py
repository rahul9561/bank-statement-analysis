"""Tesseract OCR provider (via pytesseract).

Installation is optional. When ``pytesseract`` or the ``tesseract`` binary is
missing, ``is_available()`` returns False and the pipeline reports
``OCRUnavailableError`` only if a page actually needs OCR.
"""

from __future__ import annotations

import io
import logging
import shutil

from .base import OCRProvider, Word

logger = logging.getLogger(__name__)


class TesseractOCRProvider(OCRProvider):
    name = "tesseract"

    def __init__(self, language: str = "eng", tesseract_cmd: str | None = None, timeout: int = 60):
        self.language = language
        self.tesseract_cmd = tesseract_cmd
        self.timeout = timeout
        self._available: bool | None = None

    def _module(self):
        import pytesseract  # optional dependency

        if self.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = self.tesseract_cmd
        return pytesseract

    def is_available(self) -> bool:
        if self._available is None:
            try:
                module = self._module()
                cmd = self.tesseract_cmd or module.pytesseract.tesseract_cmd
                if shutil.which(cmd) is None and not self.tesseract_cmd:
                    self._available = False
                else:
                    module.get_tesseract_version()
                    self._available = True
            except Exception:  # ImportError, TesseractNotFoundError, OSError
                self._available = False
        return self._available

    def recognize(self, png_bytes: bytes, dpi: int) -> list[Word]:
        from PIL import Image

        pytesseract = self._module()
        image = Image.open(io.BytesIO(png_bytes))
        # psm 6 = assume a uniform block of text; preserves table rows well.
        data = pytesseract.image_to_data(
            image,
            lang=self.language,
            config="--psm 6 -c preserve_interword_spaces=1",
            output_type=pytesseract.Output.DICT,
            timeout=self.timeout,
        )
        scale = 72.0 / dpi
        words: list[Word] = []
        for i, text in enumerate(data["text"]):
            text = (text or "").strip()
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                conf = -1
            if not text or conf < 0:
                continue
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            words.append(Word(text, x * scale, y * scale, (x + w) * scale, (y + h) * scale))
        return words
