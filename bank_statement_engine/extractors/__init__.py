"""PDF content extraction (native text and OCR) behind a common interface."""

from .base import OCRProvider, PageContent, Word
from .pdf_text import DocumentExtractor

__all__ = ["DocumentExtractor", "OCRProvider", "PageContent", "Word"]
