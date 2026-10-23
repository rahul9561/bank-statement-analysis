"""Exception hierarchy for the bank statement engine.

Every exception carries a ``code`` and a ``safe_message``. The safe message
never contains file paths, statement content or stack traces and can be
returned to API clients as-is. Diagnostic detail belongs in server logs only.
"""

from __future__ import annotations


class BankStatementError(Exception):
    """Base class for all engine errors."""

    code = "BANK_STATEMENT_ERROR"
    default_message = "The bank statement could not be processed."
    http_status = 422

    def __init__(self, message: str | None = None, *, details: dict | None = None):
        self.safe_message = message or self.default_message
        # ``details`` must only hold non-sensitive diagnostic values (counts, page numbers).
        self.details = details or {}
        super().__init__(self.safe_message)

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.safe_message, "details": self.details}


class InvalidPDFError(BankStatementError):
    code = "INVALID_PDF"
    default_message = "The uploaded file is not a valid PDF document."
    http_status = 400


class FileTooLargeError(InvalidPDFError):
    code = "FILE_TOO_LARGE"
    default_message = "The file exceeds the maximum permitted size."
    http_status = 413


class TooManyPagesError(InvalidPDFError):
    code = "TOO_MANY_PAGES"
    default_message = "The PDF exceeds the maximum permitted number of pages."
    http_status = 413


class EncryptedPDFError(BankStatementError):
    code = "ENCRYPTED_PDF"
    default_message = (
        "The PDF is password protected. Supply the statement password or upload an unprotected copy."
    )
    http_status = 400


class UnsupportedPDFError(BankStatementError):
    code = "UNSUPPORTED_PDF"
    default_message = "The PDF uses a structure that is not supported."


class NoExtractableTextError(BankStatementError):
    code = "NO_EXTRACTABLE_TEXT"
    default_message = (
        "No readable text was found in the PDF. If it is a scanned statement, enable OCR."
    )


class OCRUnavailableError(BankStatementError):
    code = "OCR_UNAVAILABLE"
    default_message = (
        "The statement appears to be scanned, but OCR is not available on this server."
    )
    http_status = 503


class NoTransactionsFoundError(BankStatementError):
    code = "NO_TRANSACTIONS_FOUND"
    default_message = "No transactions could be detected in the statement."


class UnsupportedStatementFormatError(BankStatementError):
    code = "UNSUPPORTED_STATEMENT_FORMAT"
    default_message = (
        "The statement layout was not recognised. No transaction table header was found."
    )


class InvalidTransactionDataError(BankStatementError):
    code = "INVALID_TRANSACTION_DATA"
    default_message = "The extracted transaction data is invalid or inconsistent."


class ReconciliationError(BankStatementError):
    code = "RECONCILIATION_FAILED"
    default_message = "The extracted transactions do not reconcile with the statement balances."


class ReportGenerationError(BankStatementError):
    code = "REPORT_GENERATION_FAILED"
    default_message = "The analysis report could not be generated."
    http_status = 500


class ProcessingTimeoutError(BankStatementError):
    code = "PROCESSING_TIMEOUT"
    default_message = "Processing the statement exceeded the permitted time."
    http_status = 504
