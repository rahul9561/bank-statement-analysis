import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bank_statement_engine import BankStatementAnalyzer  # noqa: E402

import helpers  # noqa: E402


@pytest.fixture(scope="session")
def known_pdf() -> bytes:
    return helpers.build_statement_pdf()


@pytest.fixture(scope="session")
def analyzer() -> BankStatementAnalyzer:
    return BankStatementAnalyzer({"ocr": {"mode": "off"}})


@pytest.fixture(scope="session")
def known_result(analyzer, known_pdf):
    return analyzer.analyze(known_pdf, holder_names=[helpers.HOLDER])


INPUT_DIR = ROOT / "input"
