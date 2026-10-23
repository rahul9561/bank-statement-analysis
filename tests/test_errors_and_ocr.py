"""Invalid input handling and the OCR extraction path (with a fake local OCR provider)."""

import io

import pymupdf
import pytest

import helpers
from bank_statement_engine import (
    BankStatementAnalyzer,
    EncryptedPDFError,
    FileTooLargeError,
    InvalidPDFError,
    NoExtractableTextError,
    OCRUnavailableError,
    TooManyPagesError,
    UnsupportedStatementFormatError,
)
from bank_statement_engine.extractors.base import OCRProvider, Word
from bank_statement_engine.extractors.ocr import TesseractOCRProvider


def test_rejects_non_pdf_bytes(analyzer):
    with pytest.raises(InvalidPDFError):
        analyzer.analyze(b"MZ\x90\x00 definitely not a pdf")


def test_rejects_corrupt_pdf(analyzer):
    with pytest.raises(InvalidPDFError):
        analyzer.analyze(b"%PDF-1.7\n garbage without objects")


def test_missing_file(analyzer, tmp_path):
    with pytest.raises(InvalidPDFError) as exc:
        analyzer.analyze(file_path=tmp_path / "missing.pdf")
    assert str(tmp_path) not in exc.value.safe_message


def test_file_size_limit(known_pdf):
    small = BankStatementAnalyzer({"limits": {"max_file_size_bytes": 1000}, "ocr": {"mode": "off"}})
    with pytest.raises(FileTooLargeError):
        small.analyze(known_pdf)
    with pytest.raises(FileTooLargeError):
        small.analyze(io.BytesIO(known_pdf))


def test_page_limit():
    pdf = helpers.build_statement_pdf(rows_per_page=3)
    limited = BankStatementAnalyzer({"limits": {"max_pages": 2}, "ocr": {"mode": "off"}})
    with pytest.raises(TooManyPagesError):
        limited.analyze(pdf)


def _encrypt(pdf: bytes, password: str) -> bytes:
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    out = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password + "-owner")
    doc.close()
    return out


def test_encrypted_pdf_requires_password(analyzer, known_pdf):
    locked = _encrypt(known_pdf, "s3cret")
    with pytest.raises(EncryptedPDFError):
        analyzer.analyze(locked)
    with pytest.raises(EncryptedPDFError):
        analyzer.analyze(locked, password="wrong")
    result = analyzer.analyze(locked, password="s3cret")
    assert result.status == "VERIFIED"
    assert "s3cret" not in result.to_json()


def test_unrecognised_document(analyzer):
    pdf = helpers.text_pdf(["Quarterly newsletter", "Nothing here looks like a bank statement.", "Thank you"])
    with pytest.raises(UnsupportedStatementFormatError):
        analyzer.analyze(pdf)


def _blank_pdf(pages: int = 1) -> bytes:
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    return doc.tobytes()


def test_no_text_with_ocr_disabled(analyzer):
    with pytest.raises(NoExtractableTextError):
        analyzer.analyze(_blank_pdf())


class UnavailableOCR(OCRProvider):
    def is_available(self):
        return False

    def recognize(self, png_bytes, dpi):  # pragma: no cover
        raise AssertionError("must not be called")


def test_scanned_pdf_without_ocr_reports_unavailable():
    with pytest.raises(OCRUnavailableError):
        BankStatementAnalyzer(ocr_provider=UnavailableOCR()).analyze(_blank_pdf())


def test_tesseract_provider_degrades_gracefully():
    provider = TesseractOCRProvider(tesseract_cmd=None)
    assert provider.is_available() in (True, False)  # never raises


# --------------------------------------------------------------------------- OCR path

def _image_only(pdf: bytes, keep_text_pages: set[int] = frozenset()) -> bytes:
    """Rasterise pages (except ``keep_text_pages``, 1-based) into image-only pages."""
    src = pymupdf.open(stream=pdf, filetype="pdf")
    out = pymupdf.open()
    for i, page in enumerate(src, start=1):
        if i in keep_text_pages:
            out.insert_pdf(src, from_page=i - 1, to_page=i - 1)
            continue
        pix = page.get_pixmap(dpi=100)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, stream=pix.tobytes("png"))
    return out.tobytes()


class FakeOCR(OCRProvider):
    """Returns the true words of the original text PDF, page by page, in PDF points."""

    name = "fake"

    def __init__(self, original_pdf: bytes, pages: list[int]):
        doc = pymupdf.open(stream=original_pdf, filetype="pdf")
        self.queue = [[Word(w[4], w[0], w[1], w[2], w[3]) for w in doc[p - 1].get_text("words")] for p in pages]
        self.calls = 0

    def is_available(self):
        return True

    def recognize(self, png_bytes, dpi):
        assert png_bytes.startswith(b"\x89PNG")
        self.calls += 1
        return self.queue.pop(0)


def test_scanned_statement_uses_ocr_provider():
    original = helpers.build_statement_pdf(rows_per_page=12)
    pages = pymupdf.open(stream=original, filetype="pdf").page_count
    fake = FakeOCR(original, list(range(1, pages + 1)))
    result = BankStatementAnalyzer({"ocr": {"dpi": 100}}, ocr_provider=fake).analyze(_image_only(original))
    assert fake.calls == pages
    assert result.metadata.extraction_method == "ocr"
    assert result.overview["total_credits"] == helpers.EXPECTED_TOTAL_CREDITS
    assert result.status == "VERIFIED"
    assert any("OCR" in limitation for limitation in result.limitations)


def test_mixed_pdf_only_ocrs_image_pages():
    original = helpers.build_statement_pdf(rows_per_page=12)
    fake = FakeOCR(original, [2])
    result = BankStatementAnalyzer({"ocr": {"dpi": 100}}, ocr_provider=fake).analyze(
        _image_only(original, keep_text_pages={1}))
    assert fake.calls == 1
    assert result.metadata.ocr_pages == [2]
    assert result.metadata.extraction_method == "mixed"
    assert result.overview["closing_balance"] == helpers.EXPECTED_CLOSING


def test_ocr_mode_off_never_calls_provider(known_pdf):
    fake = FakeOCR(known_pdf, [])
    BankStatementAnalyzer({"ocr": {"mode": "off"}}, ocr_provider=fake).analyze(known_pdf)
    assert fake.calls == 0
