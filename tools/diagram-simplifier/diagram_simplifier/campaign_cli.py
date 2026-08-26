"""Command-line entry point for the campaign rollout planner.

Usage::

    python -m diagram_simplifier.campaign_cli \\
        --start-date 2026-12-14 --days 28 \\
        -o campaign_plan.xlsx \\
        SX=path/to/..._SX_Unit_Diagrams.docx \\
        SO=path/to/..._SO_Unit_Diagrams.docx \\
        Su=path/to/..._Su_Unit_Diagrams.docx

Reuses the same `[LABEL=]path.docx` input handling as the Simplifier CLI --
one file per day-type (SX/SO/Su), with the label guessable from a standard
D40 filename. Every scheduling assumption (travel times, break lengths,
technicians per shift, minutes per unit, which unit class is in scope) is a
:class:`~diagram_simplifier.campaign.CampaignConfig` field; pass `--config`
with a JSON file to override any of them rather than editing the code.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import date

from .campaign import CampaignConfig, CampaignSite, plan_rollout
from .campaign_workbook import build_campaign_workbook
from .cli import parse_inputs
from .parser import parse_docx


def _load_config(path: str | None) -> CampaignConfig:
    config = CampaignConfig()
    if not path:
        return config
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    sites = config.sites
    if "sites" in data:
        sites = tuple(
            CampaignSite(s["name"], tuple(s["keywords"]), s["travel_minutes_from_ilford"]) for s in data["sites"]
        )

    overrides = {k: v for k, v in data.items() if k != "sites"}
    return replace(config, sites=sites, **overrides)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Plan a Class 720 software-update campaign rollout.")
    ap.add_argument("inputs", nargs="+", help="[LABEL=]path.docx, one per day-type (SX/SO/Su)")
    ap.add_argument("-o", "--output", required=True, help="Output .xlsx path")
    ap.add_argument("--start-date", required=True, help="First day of the rollout, YYYY-MM-DD")
    ap.add_argument("--days", type=int, required=True, help="Number of calendar days to plan")
    ap.add_argument("--config", help="Optional JSON file overriding CampaignConfig defaults")
    args = ap.parse_args(argv)

    config = _load_config(args.config)
    start_date = date.fromisoformat(args.start_date)

    inputs = parse_inputs(args.inputs)
    day_type_labels = {"SX": "Mon-Fri", "SO": "Saturday", "SU": "Sunday"}
    diagrams_by_day_type = {day_type_labels.get(label, label): parse_docx(path) for label, path in inputs}

    rollout = plan_rollout(diagrams_by_day_type, start_date, args.days, config)

    wb = build_campaign_workbook(rollout, config, start_date, args.days)
    wb.save(args.output)

    pct = round(100 * len(rollout.updated) / len(rollout.fleet), 1) if rollout.fleet else 0
    print(
        f"Wrote {args.output}: {len(rollout.updated)}/{len(rollout.fleet)} units updated "
        f"({pct}%) over {args.days} days, {len(rollout.warnings)} residual coupling warning(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
