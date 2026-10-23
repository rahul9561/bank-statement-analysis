"""Plain-Python usage of the engine (no Django).

    python examples/analyze_statement.py input/statement.pdf output/analysis.pdf
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run from a source checkout

from bank_statement_engine import BankStatementAnalyzer, BankStatementError, EngineConfig  # noqa: E402


def main(statement: str, report: str) -> int:
    config = EngineConfig.from_dict({
        "ocr": {"mode": "auto"},                                  # OCR only for pages without text
        "analysis": {"low_balance_threshold": "2000"},
        "report": {"organization_name": "", "max_transactions_in_report": 300},
    })
    analyzer = BankStatementAnalyzer(config)
    try:
        result = analyzer.analyze(file_path=statement)
    except BankStatementError as exc:
        print(f"Could not analyse the statement: [{exc.code}] {exc.safe_message}")
        return 1

    print("Status:", result.status)                              # VERIFIED / REQUIRES_REVIEW / FAILED
    print(json.dumps(result.summary(), indent=2))
    for issue in result.validation.issues:
        print(f"  {issue.severity:7s} {issue.code}: {issue.message}")
    for indicator in result.risk_indicators:
        print(f"  [{indicator.severity}] {indicator.title}")

    analyzer.generate_pdf(result, report)
    Path(report).with_suffix(".json").write_text(result.to_json(), encoding="utf-8")
    print("Report written to", report)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
