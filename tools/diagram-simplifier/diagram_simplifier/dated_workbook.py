"""Writes Simplifier sheets named for one specific calendar date, one sheet
per fleet (e.g. "745s Thu 31st Dec", "755s Thu 31st Dec") -- the layout
used for a one-off period (a Christmas STP book, an engineering-blockade
week) rather than the generic recurring Mon-Fri/Saturday/Sunday sheets
`workbook.py` produces, matching a hand-built "Diagram Simplifier" workbook
for such a period.

The 745 fleet's reference sheet carries four extra columns the generic
schema doesn't have: "Norwich Stn Arr" (the last passenger-carrying arrival
at plain "Norwich" before the unit goes off to a stable/depot -- derived
here from the raw diagram, since it isn't part of the generic Origin /
Stabling Location / Day Stable model) and "CET" / "Sand" / "Pot Water"
(servicing needs -- toilet tank, sanding gear, potable water -- which
aren't recorded in the diagram book at all and are left blank, ready for
manual annotation exactly as the reference was).
"""

from __future__ import annotations

from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from .parser import Diagram
from .simplify import SimplifiedRow, build_simplified_rows, primary_diagram_for
from .workbook import (
    BORDER,
    ECS_FILL,
    FLAG_FILL,
    MIXED_COUPLING_FILL,
    add_summary_sheet,
    group_by_class,
    write_header,
)

_ORDINAL_SUFFIX = {1: "st", 2: "nd", 3: "rd"}


def ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{_ORDINAL_SUFFIX.get(n % 10, 'th')}"


def fleet_of(unit_class: str) -> str:
    """"745/1" -> "745", "720/5" -> "720"."""
    return unit_class.split("/")[0]


def sheet_name_for(target_date: date, fleet: str) -> str:
    return f"{fleet}s {target_date.strftime('%a')} {ordinal(target_date.day)} {target_date.strftime('%b')}"


def norwich_station_arrival(diagram: Diagram):
    """Last *passenger-carrying* arrival at plain "Norwich" (the mainline
    station -- not "Norwich Victoria"/"Jubilee"/"Crown Point"/etc, which
    are depot sidings): i.e. the point revenue service ends and the unit
    goes off to a depot for the day, matching the reference's own pattern
    of this sitting *before* any Day Stable dwell. A stop reached by an
    empty-stock leg (headcode starting "5") -- e.g. Norwich only being
    passed through again later while positioning to the overnight stabling
    point -- doesn't count. None if the diagram never calls there in
    passenger service."""
    stops = diagram.stops
    best = None
    for i in range(1, len(stops)):
        s = stops[i]
        if s.location.strip() != "Norwich":
            continue
        incoming_code = stops[i - 1].code
        if incoming_code and incoming_code.startswith("5"):
            continue
        best = s.arrive_time
    return best


NORWICH_COLUMNS = [
    ("Diagram No", 13),
    ("Origin", 20),
    ("Yard Depart", 12),
    ("Service Depart", 14),
    ("Day Stable", 18),
    ("Arr Time", 10),
    ("Dep Time", 10),
    ("Norwich Stn Arr", 13),
    ("Stabling Location", 20),
    ("PM Arr Time", 12),
    ("Mileage", 10),
    ("CET", 8),
    ("Sand", 8),
    ("Pot Water", 10),
    ("Coupling", 55),
    ("Coupling Pattern", 16),
    ("Notes", 70),
]


def add_norwich_style_sheet(
    wb: Workbook, sheet_name: str, rows: list[SimplifiedRow], diagrams: list[Diagram]
) -> None:
    """The 745-style sheet: same core columns as the generic Simplifier
    plus Norwich Stn Arr (derived) and CET/Sand/Pot Water (left blank for
    manual annotation, matching the reference)."""
    ws = wb.create_sheet(sheet_name)
    primary_by_no = primary_diagram_for(diagrams)
    groups = group_by_class(rows)
    show_class_labels = len(groups) > 1

    r = 1
    for cls, group_rows in groups:
        header_row = r
        write_header(ws, NORWICH_COLUMNS, header_row, cls if show_class_labels else None)
        if header_row == 1:
            ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
        r += 1

        first_data_row = r
        for row in group_rows:
            diagram = primary_by_no.get(row.diagram_no)
            norwich_arr = norwich_station_arrival(diagram) if diagram else None
            values = [
                row.diagram_no,
                row.origin,
                row.yard_depart,
                row.service_depart,
                row.day_stable,
                row.arr_time,
                row.dep_time,
                norwich_arr,
                row.stabling_location,
                row.pm_arr_time,
                row.mileage,
                None,
                None,
                None,
                row.coupling,
                row.coupling_pattern,
                row.notes,
            ]
            for c, value in enumerate(values, start=1):
                cell = ws.cell(row=r, column=c, value=value)
                cell.border = BORDER
                if c in (3, 4, 6, 7, 8, 10):
                    cell.number_format = "hh:mm"
                if c == 11:
                    cell.number_format = "0.00"
            ws.cell(row=r, column=1).font = Font(bold=True)
            if row.flagged_do_not_use:
                for c in range(1, len(NORWICH_COLUMNS) + 1):
                    ws.cell(row=r, column=c).fill = FLAG_FILL
            elif row.is_ecs_only:
                for c in range(1, len(NORWICH_COLUMNS) + 1):
                    ws.cell(row=r, column=c).fill = ECS_FILL
            if row.coupling_pattern == "Mixed":
                ws.cell(row=r, column=16).fill = MIXED_COUPLING_FILL
            r += 1
        last_data_row = r - 1

        total_row = r
        ws.cell(row=total_row, column=10, value="Total").font = Font(bold=True)
        ws.cell(row=total_row, column=11, value=f"=SUM(K{first_data_row}:K{last_data_row})").font = Font(bold=True)
        ws.cell(row=total_row, column=11).number_format = "0.00"
        r = total_row + 2

    if not show_class_labels:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(NORWICH_COLUMNS))}{r - 2}"


def build_dated_workbook(
    diagrams: list[Diagram],
    target_date: date,
    norwich_fleets: frozenset[str] = frozenset({"745"}),
) -> Workbook:
    """One sheet per fleet (the digits before the "/" in the unit class),
    named for `target_date`, containing only the diagram variants actually
    live on that date."""
    wb = Workbook()
    wb.remove(wb.active)

    by_fleet: dict[str, list[Diagram]] = {}
    for d in diagrams:
        by_fleet.setdefault(fleet_of(d.unit_class), []).append(d)

    for fleet in sorted(by_fleet):
        fleet_diagrams = by_fleet[fleet]
        rows = build_simplified_rows(fleet_diagrams, target_date=target_date)
        if not rows:
            continue
        sheet_name = sheet_name_for(target_date, fleet)
        if fleet in norwich_fleets:
            add_norwich_style_sheet(wb, sheet_name, rows, fleet_diagrams)
        else:
            add_summary_sheet(wb, sheet_name, rows)

    return wb
