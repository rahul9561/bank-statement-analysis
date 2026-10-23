"""Reusable, framework-independent analysis engine for Indian bank statement PDFs.

Quick start::

    from bank_statement_engine import BankStatementAnalyzer

    analyzer = BankStatementAnalyzer()
    result = analyzer.analyze(file_path="statement.pdf")
    analyzer.generate_pdf(result, output_path="analysis_report.pdf")
"""

__version__ = "1.0.0"

from .config import AnalysisConfig, EngineConfig, LimitsConfig, OCRConfig, ReportConfig  # noqa: E402
from .exceptions import (  # noqa: E402
    BankStatementError,
    EncryptedPDFError,
    FileTooLargeError,
    InvalidPDFError,
    InvalidTransactionDataError,
    NoExtractableTextError,
    NoTransactionsFoundError,
    OCRUnavailableError,
    ProcessingTimeoutError,
    ReconciliationError,
    ReportGenerationError,
    TooManyPagesError,
    UnsupportedPDFError,
    UnsupportedStatementFormatError,
)
from .pipeline import BankStatementAnalyzer  # noqa: E402
from .schemas import AnalysisResult, RiskIndicator, Transaction, ValidationReport  # noqa: E402

__all__ = [
    "__version__",
    "BankStatementAnalyzer",
    "AnalysisResult",
    "Transaction",
    "ValidationReport",
    "RiskIndicator",
    "EngineConfig",
    "OCRConfig",
    "LimitsConfig",
    "AnalysisConfig",
    "ReportConfig",
    "BankStatementError",
    "InvalidPDFError",
    "FileTooLargeError",
    "TooManyPagesError",
    "EncryptedPDFError",
    "UnsupportedPDFError",
    "NoExtractableTextError",
    "OCRUnavailableError",
    "NoTransactionsFoundError",
    "UnsupportedStatementFormatError",
    "InvalidTransactionDataError",
    "ReconciliationError",
    "ReportGenerationError",
    "ProcessingTimeoutError",
]
