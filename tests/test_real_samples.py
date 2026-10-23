"""Integration tests on the real sample statements in ``input/`` (skipped when absent).

Assertions use only aggregate, non-identifying values (counts, statuses, bank codes).
"""

import pytest

from conftest import INPUT_DIR

SAMPLES = sorted(INPUT_DIR.glob("*.pdf")) if INPUT_DIR.is_dir() else []

# Exact file name -> (bank code identified from content, exact transaction count verified by
# independent cross-checks). Files not listed here must still reconcile, but have no count pin.
EXPECTED = {
    "axisbankstatement_bank_statement_5cea5ce073ec.pdf": ("AXIS", 1773),
    "bankofindiastatement_bank_statement_0f370ad79eb3.pdf": ("BOI", 1022),
    "equitasstatement_bank_statement_9c07e56d8521.pdf": ("EQUITAS", 2424),
    "hdfcbankstatement_bank_statement_51459828854b.pdf": ("HDFC", 237),
    "icicbankstatement_bank_statement_b78c0d98b8e4.pdf": ("ICICI", 266),
    "idfcefirstbankstatement_bank_statement_95805517d055.pdf": ("IDFC", 40),
    "kotakstatement_bank_statement_46393f6c2ba8.pdf": ("KOTAK", 1062),
    "punjabnationsbank.pdf": ("PNB", 69),
    "statement_sbibank_statement_947af53e5752.pdf": ("SBI", 842),
    "utkarsh_small_financestatement_bank_statement_e8a13fe07212.pdf": ("UTKARSH", 1209),
    "aniket.pdf": ("KOTAK", 1481),
    "newreporticic.pdf": ("ICICI", 154),
}


def _expected(path):
    return EXPECTED.get(path.name, (None, None))


@pytest.mark.skipif(not SAMPLES, reason="no sample statements in input/")
@pytest.mark.parametrize("path", SAMPLES, ids=lambda p: p.stem[:20])
def test_sample_statement_reconciles(analyzer, path):
    original = path.read_bytes()
    result = analyzer.analyze(file_path=path)
    bank, expected_count = _expected(path)
    assert result.status == "VERIFIED", [(i.code, i.message) for i in result.validation.issues if i.severity == "ERROR"]
    assert result.validation.balance_chain_breaks == 0
    if expected_count is not None:
        assert len(result.transactions) == expected_count
    if bank:
        assert result.metadata.bank_code == bank
    assert path.read_bytes() == original  # input files are never modified
