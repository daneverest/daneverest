"""Writes a campaign :class:`~diagram_simplifier.campaign.Rollout` out to a
styled .xlsx with two audiences in mind, per the brief:

- **Technician Plan**: how many units to update, where, and when -- counts
  per location, never specific unit numbers, since technicians work
  whatever is physically in front of them.
- **Controller Plan**: which specific diagrams have been updated (so
  controllers know which units are now "new software") and any coupling
  caveats to hold in mind when allocating units to diagrams.

Plus a Rollout Progress sheet, a Coupling Reference sheet, and an
Assumptions sheet spelling out every configurable figure this plan was
built on -- travel times, break lengths, shift definitions -- since those
are placeholders that must be checked against real operational knowledge.
"""

from __future__ import annotations

from datetime import timedelta

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from .campaign import CampaignConfig, Rollout

HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(name="Calibri", bold=True, color="FFFFFF")
WARN_FILL = PatternFill("solid", fgColor="FFD966")
THIN = Side(style="thin", color="B7B7B7")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

TECHNICIAN_COLUMNS = [
    ("Date", 12),
    ("Shift", 8),
    ("Location", 20),
    ("Technicians Assigned", 12),
    ("Units To Update", 12),
    ("One-Way Travel (min)", 12),
    ("Technician-Hours Used", 12),
    ("Site Capacity (units)", 12),
    ("Utilisation", 11),
    ("Blocked This Shift", 15),
]

DEFERRED_COLUMNS = [
    ("Date", 12),
    ("Shift", 8),
    ("Location", 20),
    ("Reason", 90),
]

CONTROLLER_COLUMNS = [
    ("Date", 12),
    ("Shift", 8),
    ("Location", 20),
    ("Diagram No", 12),
    ("New Status", 22),
    ("Coupling Note", 70),
]

PROGRESS_COLUMNS = [
    ("Date", 12),
    ("Shift", 8),
    ("Units Updated This Shift", 14),
    ("Cumulative Updated", 14),
    ("Fleet Size", 11),
    ("% Complete", 11),
]

PARTNERS_COLUMNS = [
    ("Diagram No", 12),
    ("Direct Coupling Partners", 60),
    ("Updated?", 10),
]


def _write_header(ws: Worksheet, columns: list[tuple[str, int]]) -> None:
    for idx, (title, width) in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=idx, value=title)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.border = BORDER
        cell.alignment = Alignment(vertical="center", wrap_text=False)
        ws.column_dimensions[get_column_letter(idx)].width = width
    ws.freeze_panes = "A2"


def add_technician_plan(wb: Workbook, rollout: Rollout, config: CampaignConfig) -> None:
    ws = wb.create_sheet("Technician Plan")
    _write_header(ws, TECHNICIAN_COLUMNS)

    r = 2
    for shift in rollout.shifts:
        if not shift.sites:
            continue
        for sr in shift.sites:
            units = len(sr.updated)
            tech_hours = round(units * config.minutes_per_unit / 60, 1)
            utilisation = f"{round(100 * units / sr.capacity)}%" if sr.capacity else "n/a"
            blocked = (
                f"{len(sr.deferred_notes)} unit(s) -- see Deferred - Blocked sheet" if sr.deferred_notes else ""
            )
            values = [
                shift.shift_date,
                shift.shift_name,
                sr.site.name,
                sr.technicians,
                units,
                sr.site.travel_minutes_from_ilford,
                tech_hours,
                sr.capacity,
                utilisation,
                blocked,
            ]
            for c, value in enumerate(values, start=1):
                cell = ws.cell(row=r, column=c, value=value)
                cell.border = BORDER
                if c == 1:
                    cell.number_format = "yyyy-mm-dd"
            if sr.deferred_notes:
                ws.cell(row=r, column=10).fill = WARN_FILL
            r += 1

    if r > 2:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(TECHNICIAN_COLUMNS))}{r - 1}"


def add_deferred_sheet(wb: Workbook, rollout: Rollout) -> None:
    """The full per-diagram reasoning behind every "Blocked This Shift"
    count on the Technician Plan -- kept separate so that sheet stays
    countable at a glance."""
    ws = wb.create_sheet("Deferred - Blocked")
    _write_header(ws, DEFERRED_COLUMNS)

    r = 2
    for shift in rollout.shifts:
        for sr in shift.sites:
            for note in sr.deferred_notes:
                values = [shift.shift_date, shift.shift_name, sr.site.name, note]
                for c, value in enumerate(values, start=1):
                    cell = ws.cell(row=r, column=c, value=value)
                    cell.border = BORDER
                    if c == 1:
                        cell.number_format = "yyyy-mm-dd"
                r += 1

    if r > 2:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(DEFERRED_COLUMNS))}{r - 1}"


def add_controller_plan(wb: Workbook, rollout: Rollout) -> None:
    ws = wb.create_sheet("Controller Plan")
    _write_header(ws, CONTROLLER_COLUMNS)

    r = 2
    updated_so_far: set[str] = set()
    for shift in rollout.shifts:
        # Add everything updated in this shift (across all its sites) before
        # writing any notes for it -- otherwise a pair updated together in
        # the same shift (e.g. two mutually-coupled diagrams) would show
        # each other as "not yet updated" purely due to list order.
        updated_so_far.update(no for sr in shift.sites for no in sr.updated)
        for sr in shift.sites:
            for diagram_no in sr.updated:
                partners = sorted(rollout.partners.get(diagram_no, ()))
                if not partners:
                    note = "Never coupled -- no restriction."
                elif all(p in updated_so_far for p in partners):
                    note = f"Couples with {', '.join(partners)} -- also updated. Safe to pair."
                else:
                    pending = [p for p in partners if p not in updated_so_far]
                    note = (
                        f"Couples with {', '.join(partners)} -- {', '.join(pending)} NOT yet updated. "
                        "Avoid pairing with them until they are."
                    )
                values = [shift.shift_date, shift.shift_name, sr.site.name, diagram_no, "Updated (New Software)", note]
                for c, value in enumerate(values, start=1):
                    cell = ws.cell(row=r, column=c, value=value)
                    cell.border = BORDER
                    if c == 1:
                        cell.number_format = "yyyy-mm-dd"
                if "NOT yet updated" in note:
                    ws.cell(row=r, column=6).fill = WARN_FILL
                r += 1

    if r > 2:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(CONTROLLER_COLUMNS))}{r - 1}"


def add_progress_sheet(wb: Workbook, rollout: Rollout) -> None:
    ws = wb.create_sheet("Rollout Progress")
    _write_header(ws, PROGRESS_COLUMNS)

    r = 2
    cumulative = 0
    fleet_size = len(rollout.fleet)
    for shift in rollout.shifts:
        this_shift = sum(len(sr.updated) for sr in shift.sites)
        cumulative += this_shift
        pct = round(100 * cumulative / fleet_size, 1) if fleet_size else 0
        values = [shift.shift_date, shift.shift_name, this_shift, cumulative, fleet_size, pct]
        for c, value in enumerate(values, start=1):
            cell = ws.cell(row=r, column=c, value=value)
            cell.border = BORDER
            if c == 1:
                cell.number_format = "yyyy-mm-dd"
            if c == 6:
                cell.number_format = "0.0"
        r += 1

    if r > 2:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(PROGRESS_COLUMNS))}{r - 1}"


def add_partners_sheet(wb: Workbook, rollout: Rollout) -> None:
    ws = wb.create_sheet("Coupling Reference")
    _write_header(ws, PARTNERS_COLUMNS)

    r = 2
    for diagram_no in sorted(rollout.fleet):
        partners = sorted(rollout.partners.get(diagram_no, ()))
        if not partners:
            continue
        values = [diagram_no, ", ".join(partners), "Yes" if diagram_no in rollout.updated else "No"]
        for c, value in enumerate(values, start=1):
            cell = ws.cell(row=r, column=c, value=value)
            cell.border = BORDER
        r += 1

    if r > 2:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(PARTNERS_COLUMNS))}{r - 1}"


def add_warnings_sheet(wb: Workbook, rollout: Rollout) -> None:
    ws = wb.create_sheet("Coupling Warnings")
    ws.cell(row=1, column=1, value="Residual coupling risk").font = HEADER_FONT
    ws.cell(row=1, column=1).fill = HEADER_FILL
    ws.column_dimensions["A"].width = 120
    r = 2
    if not rollout.warnings:
        ws.cell(row=r, column=1, value="None -- every updated diagram's direct partners were also updated.")
    else:
        for w in rollout.warnings:
            ws.cell(row=r, column=1, value=w).alignment = Alignment(wrap_text=True)
            r += 1


def add_assumptions_sheet(wb: Workbook, config: CampaignConfig, start_date, num_days: int) -> None:
    ws = wb.create_sheet("Assumptions")
    ws.cell(
        row=1,
        column=1,
        value="REPLACE THE FIGURES BELOW WITH REAL OPERATIONAL DATA BEFORE USING THIS PLAN",
    ).font = Font(bold=True, color="C00000", size=13)
    ws.column_dimensions["A"].width = 40
    ws.column_dimensions["B"].width = 60

    end_date = start_date + timedelta(days=num_days - 1)
    r = 3
    rows = [
        ("Unit class in scope", f"{config.unit_class_prefix} (prefix match)"),
        ("Rollout window", f"{start_date.isoformat()} to {end_date.isoformat()} ({num_days} days)"),
        ("Day shift", "06:00 - 18:00"),
        ("Night shift", "18:00 - 06:00 (next day)"),
        ("Technicians per shift", config.technicians_per_shift),
        ("Minutes per unit update", config.minutes_per_unit),
        ("Lunch break (minutes)", config.lunch_break_minutes),
        ("Personal-needs buffer (minutes)", config.personal_buffer_minutes),
    ]
    for label, value in rows:
        ws.cell(row=r, column=1, value=label)
        ws.cell(row=r, column=2, value=value)
        r += 1

    r += 1
    ws.cell(row=r, column=1, value="Campaign sites & one-way travel time from Ilford (PLACEHOLDER, minutes)").font = Font(
        bold=True
    )
    r += 1

    for site in config.sites:
        ws.cell(row=r, column=1, value=f"  {site.name}")
        ws.cell(row=r, column=2, value=site.travel_minutes_from_ilford)
        r += 1

    r += 1
    ws.cell(
        row=r,
        column=1,
        value=(
            "Eligibility model: a unit is only considered available for update while it is stabled "
            "overnight, or during a genuine daytime dwell, at one of the sites above -- never mid-journey. "
            "Coupling safety: a diagram is only updated once every diagram it has ever run coupled with is "
            "either already updated or updated in the same shift (see the Coupling Reference and Coupling "
            "Warnings sheets for the residual risk this can't fully resolve automatically)."
        ),
    ).alignment = Alignment(wrap_text=True)
    ws.row_dimensions[r].height = 60


def build_campaign_workbook(rollout: Rollout, config: CampaignConfig, start_date, num_days: int) -> Workbook:
    wb = Workbook()
    wb.remove(wb.active)
    add_technician_plan(wb, rollout, config)
    add_deferred_sheet(wb, rollout)
    add_controller_plan(wb, rollout)
    add_progress_sheet(wb, rollout)
    add_partners_sheet(wb, rollout)
    add_warnings_sheet(wb, rollout)
    add_assumptions_sheet(wb, config, start_date, num_days)
    return wb
