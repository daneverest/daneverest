"""Command-line entry point for a specific-calendar-date Simplifier, e.g. a
Christmas-week STP diagram book covering several dates/depots in one
document.

Usage::

    python -m diagram_simplifier.dated_cli \\
        --date 2026-12-31 -o Xmas_31_Dec.xlsx \\
        GAUnitsWk39ThODRAFT.docx

Unlike the main CLI, inputs here don't need an SX/SO/Su label -- one or
more `.docx` files are parsed and pooled together, then for each diagram
number only the variant actually live on `--date` (matching its Days code
and From/To range) is kept. Output is one sheet per fleet, named for that
date (e.g. "745s Thu 31st Dec"), matching a hand-built Simplifier for the
same kind of one-off period.
"""

from __future__ import annotations

import argparse
from datetime import date

from .dated_workbook import build_dated_workbook
from .parser import parse_docx


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build a Simplifier for one specific calendar date.")
    ap.add_argument("inputs", nargs="+", help="One or more .docx diagram books to pool together")
    ap.add_argument("-o", "--output", required=True, help="Output .xlsx path")
    ap.add_argument("--date", required=True, help="Target calendar date, YYYY-MM-DD")
    args = ap.parse_args(argv)

    target_date = date.fromisoformat(args.date)

    diagrams = []
    for path in args.inputs:
        diagrams.extend(parse_docx(path))

    wb = build_dated_workbook(diagrams, target_date)
    wb.save(args.output)

    print(f"Wrote {args.output}: sheets {wb.sheetnames} for {target_date.isoformat()} ({target_date.strftime('%A')}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
