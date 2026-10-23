"""Common extraction data model.

Every extractor (native PDF text, Tesseract OCR, or a future provider) produces
``PageContent`` objects holding positioned words in PDF points with the origin at
the top-left. Parsers depend only on this model, never on a specific library.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def xc(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass
class PageContent:
    number: int  # 1-based
    width: float
    height: float
    words: list[Word] = field(default_factory=list)
    #: y positions of horizontal ruling lines / row rectangles spanning a large part of the page.
    separators: list[float] = field(default_factory=list)
    method: str = "text"  # "text" | "ocr" | "none"
    char_count: int = 0
    image_coverage: float = 0.0
    garbled: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


class OCRProvider(ABC):
    """Interface for OCR engines. Implementations must run locally by default."""

    name = "ocr"

    @abstractmethod
    def is_available(self) -> bool:
        """Return True when the engine (binaries, models) can be used."""

    @abstractmethod
    def recognize(self, png_bytes: bytes, dpi: int) -> list[Word]:
        """Recognise words in a rendered page image.

        Coordinates in the returned words must be converted to PDF points
        (``pixels * 72 / dpi``) with the origin at the top-left.
        """
