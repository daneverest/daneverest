"""Command-line entry point.

Usage::

    python -m diagram_simplifier -o Simplifier.xlsx \\
        SX=path/to/..._SX_Unit_Diagrams.docx \\
        SO=path/to/..._SO_Unit_Diagrams.docx \\
        Su=path/to/..._Su_Unit_Diagrams.docx

Each input is ``<sheet-label>=<path>``. If the ``<sheet-label>=`` prefix is
omitted, the label is guessed from the filename (looking for ``_SX_``,
``_SO_`` or ``_Su_``), so for the standard D40-style filenames you can just
run::

    python -m diagram_simplifier -o Simplifier.xlsx *_Unit_Diagrams.docx
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .parser import parse_docx
from .simplify import build_simplified_rows
from .workbook import build_workbook

DAY_TYPE_LABELS = {
    "SX": "Mon-Fri",
    "SO": "Saturday",
    "SU": "Sunday",
}

_GUESS_RE = re.compile(r"(?:^|_)(SX|SO|Su)(?:_|$)", re.IGNORECASE)


def guess_label(path: str) -> str:
    m = _GUESS_RE.search(Path(path).stem)
    if not m:
        raise SystemExit(
            f"Could not guess a day-type label (SX/SO/Su) from filename '{path}'. "
            "Pass it explicitly as LABEL=path.docx instead."
        )
    return m.group(1).upper()


def parse_inputs(specs: list[str]) -> list[tuple[str, str]]:
    parsed = []
    for spec in specs:
        if "=" in spec and Path(spec.split("=", 1)[1]).suffix.lower() == ".docx":
            label, path = spec.split("=", 1)
            label = label.upper()
        else:
            path = spec
            label = guess_label(path)
        parsed.append((label, path))
    return parsed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build a Diagram Simplifier workbook from raw Unit Diagram docx files.")
    ap.add_argument("inputs", nargs="+", help="[LABEL=]path.docx, one per day-type (SX/SO/Su)")
    ap.add_argument("-o", "--output", required=True, help="Output .xlsx path")
    ap.add_argument("--no-detail", action="store_true", help="Skip the Full Detail audit sheet")
    args = ap.parse_args(argv)

    inputs = parse_inputs(args.inputs)

    sheets = {}
    detail_entries = []
    for label, path in inputs:
        diagrams = parse_docx(path)
        if not diagrams:
            print(f"warning: no diagrams parsed from {path}", file=sys.stderr)
        sheet_name = DAY_TYPE_LABELS.get(label, label)
        sheets[sheet_name] = build_simplified_rows(diagrams)
        if not args.no_detail:
            detail_entries.extend((sheet_name, d) for d in diagrams)

    wb = build_workbook(sheets, detail_entries)
    wb.save(args.output)

    total_diagrams = sum(len(rows) for rows in sheets.values())
    print(f"Wrote {args.output}: {total_diagrams} diagram(s) across {len(sheets)} sheet(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
