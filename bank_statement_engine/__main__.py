"""Command-line interface.

    python -m bank_statement_engine analyze statement.pdf --pdf report.pdf --json result.json
    python -m bank_statement_engine analyze statement.pdf --config config.json --password secret
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys
from pathlib import Path

from . import BankStatementAnalyzer, BankStatementError, __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bank_statement_engine", description="Analyse Indian bank statement PDFs.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    a = sub.add_parser("analyze", help="Analyse a statement PDF")
    a.add_argument("statement", type=Path)
    a.add_argument("--pdf", type=Path, help="Write the analysis report PDF here")
    a.add_argument("--json", type=Path, help="Write the JSON result here")
    a.add_argument("--no-transactions", action="store_true", help="Omit the transaction list from the JSON")
    a.add_argument("--config", type=Path, help="JSON file with EngineConfig sections")
    a.add_argument("--password", action="store_true", help="Prompt for the statement password")
    a.add_argument("--holder-name", action="append", default=[], help="Account holder name (repeatable)")
    a.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    config = json.loads(args.config.read_text(encoding="utf-8")) if args.config else None
    analyzer = BankStatementAnalyzer(config)
    password = getpass.getpass("Statement password: ") if args.password else None
    try:
        result = analyzer.analyze(file_path=args.statement, password=password, holder_names=args.holder_name)
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(result.to_json(include_transactions=not args.no_transactions), encoding="utf-8")
        if args.pdf:
            analyzer.generate_pdf(result, args.pdf)
    except BankStatementError as exc:
        print(f"error [{exc.code}]: {exc.safe_message}", file=sys.stderr)
        return 2
    summary = result.summary()
    print(json.dumps({"report_id": result.report_id, "status": result.status, "summary": summary,
                      "validation_issues": len(result.validation.issues)}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
