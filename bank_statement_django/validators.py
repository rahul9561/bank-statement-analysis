"""Upload validation: size, extension, declared MIME type and PDF signature.

The declared content type and file name come from the client and are not
trusted on their own; the PDF signature check on the actual bytes is decisive.
"""

from __future__ import annotations

from django.core.files.uploadedfile import UploadedFile

from bank_statement_engine.exceptions import FileTooLargeError, InvalidPDFError

from . import conf

ALLOWED_CONTENT_TYPES = {"application/pdf", "application/x-pdf", "application/octet-stream", ""}
PDF_MAGIC = b"%PDF-"


def read_validated_upload(upload: UploadedFile) -> bytes:
    """Validate an uploaded statement and return its bytes (bounded by MAX_UPLOAD_BYTES)."""
    max_bytes = int(conf.get("MAX_UPLOAD_BYTES"))
    if upload.size is not None and upload.size > max_bytes:
        raise FileTooLargeError(details={"max_bytes": max_bytes})
    name = (upload.name or "").lower()
    if name and not name.endswith(".pdf"):
        raise InvalidPDFError("Only PDF files are accepted.")
    content_type = (getattr(upload, "content_type", "") or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise InvalidPDFError("Only PDF files are accepted.")

    chunks, total = [], 0
    for chunk in upload.chunks():
        total += len(chunk)
        if total > max_bytes:
            raise FileTooLargeError(details={"max_bytes": max_bytes})
        chunks.append(chunk)
    data = b"".join(chunks)
    if PDF_MAGIC not in data[:1024]:
        raise InvalidPDFError()
    return data
