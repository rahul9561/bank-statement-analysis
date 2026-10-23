"""Loading and validating PDF input from paths, bytes or file-like objects."""

from __future__ import annotations

import hashlib
import os
from typing import BinaryIO, Union

from ..exceptions import FileTooLargeError, InvalidPDFError

PdfSource = Union[str, "os.PathLike[str]", bytes, bytearray, memoryview, BinaryIO]

PDF_MAGIC = b"%PDF-"


def read_source(source: PdfSource, max_bytes: int) -> bytes:
    """Read a PDF source fully into memory, enforcing ``max_bytes``.

    Statements are typically a few MB; holding the bytes lets PyMuPDF open the
    document without leaving extra temporary files on disk.
    """
    if isinstance(source, (bytes, bytearray, memoryview)):
        data = bytes(source)
    elif isinstance(source, (str, os.PathLike)):
        path = os.fspath(source)
        if not os.path.isfile(path):
            raise InvalidPDFError("The statement file could not be found.")
        size = os.path.getsize(path)
        if size > max_bytes:
            raise FileTooLargeError(details={"max_bytes": max_bytes})
        with open(path, "rb") as fh:
            data = fh.read()
    elif hasattr(source, "read"):
        data = source.read(max_bytes + 1)
        if isinstance(data, str):
            raise InvalidPDFError("File-like sources must be opened in binary mode.")
    else:
        raise InvalidPDFError("Unsupported input type; supply a path, bytes or a binary file object.")

    if len(data) > max_bytes:
        raise FileTooLargeError(details={"max_bytes": max_bytes})
    check_pdf_signature(data)
    return data


def check_pdf_signature(data: bytes) -> None:
    # The PDF spec allows the header within the first 1024 bytes.
    if not data or PDF_MAGIC not in data[:1024]:
        raise InvalidPDFError()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
