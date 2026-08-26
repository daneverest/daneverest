"""Writes the reduced Simplifier rows out to a styled .xlsx workbook, in the
same spirit as the reference "Diagram Simplifier" spreadsheets: one sheet per
day-type, diagrams grouped into a "CLASS X" block per unit class (mirroring
the reference's "CLASS 755/4" / "CLASS 755/3" sub-headers -- some depots
diagram more than one class of unit), bold header rows, HH:MM time
formatting, and a running mileage total per class. A "Full Detail" sheet
carries every parsed stop for every diagram so the summary can be audited
against the source diagram book.
"""

from __future__ import annotations

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from .parser import Diagram
from .simplify import SimplifiedRow

HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(name="Calibri", bold=True, color="FFFFFF")
FLAG_FILL = PatternFill("solid", fgColor="FFC7CE")
ECS_FILL = PatternFill("solid", fgColor="E2EFDA")
THIN = Side(style="thin", color="B7B7B7")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

SUMMARY_COLUMNS = [
    ("Diagram No", 13),
    ("Origin", 20),
    ("Yard Depart", 12),
    ("Service Depart", 14),
    ("Day Stable", 18),
    ("Arr Time", 10),
    ("Dep Time", 10),
    ("Stabling Location", 20),
    ("PM Arr Time", 12),
    ("Mileage", 10),
    ("Notes", 70),
]
CLASS_LABEL_COLUMN = len(SUMMARY_COLUMNS) + 1

DETAIL_COLUMNS = [
    ("Day Type", 10),
    ("Diagram No", 12),
    ("Class", 10),
    ("Days", 10),
    ("Valid From", 11),
    ("Valid To", 11),
    ("Leg", 5),
    ("Location", 22),
    ("Arrive", 9),
    ("Arr Plat", 8),
    ("Depart", 9),
    ("Dep Plat", 8),
    ("Code", 8),
    ("Route", 10),
    ("Leg Mileage", 11),
    ("Cum Mileage", 11),
]


def _write_header_cell(ws: Worksheet, row: int, col: int, title: str, width: float) -> None:
    cell = ws.cell(row=row, column=col, value=title)
    cell.font = HEADER_FONT
    cell.fill = HEADER_FILL
    cell.border = BORDER
    cell.alignment = Alignment(vertical="center", wrap_text=False)
    ws.column_dimensions[get_column_letter(col)].width = max(
        width, ws.column_dimensions[get_column_letter(col)].width or 0
    )


def _write_header(ws: Worksheet, columns: list[tuple[str, int]], row: int, class_label: str | None = None) -> None:
    for idx, (title, width) in enumerate(columns, start=1):
        _write_header_cell(ws, row, idx, title, width)
    if class_label:
        _write_header_cell(ws, row, CLASS_LABEL_COLUMN, f"CLASS {class_label}", 14)


def _group_by_class(rows: list[SimplifiedRow]) -> list[tuple[str, list[SimplifiedRow]]]:
    order: list[str] = []
    groups: dict[str, list[SimplifiedRow]] = {}
    for row in rows:
        if row.unit_class not in groups:
            groups[row.unit_class] = []
            order.append(row.unit_class)
        groups[row.unit_class].append(row)
    return [(cls, groups[cls]) for cls in order]


def add_summary_sheet(wb: Workbook, sheet_name: str, rows: list[SimplifiedRow]) -> None:
    ws = wb.create_sheet(sheet_name)
    groups = _group_by_class(rows)
    show_class_labels = len(groups) > 1

    r = 1
    for cls, group_rows in groups:
        header_row = r
        _write_header(ws, SUMMARY_COLUMNS, header_row, cls if show_class_labels else None)
        if header_row == 1:
            ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
        r += 1

        first_data_row = r
        for row in group_rows:
            values = [
                row.diagram_no,
                row.origin,
                row.yard_depart,
                row.service_depart,
                row.day_stable,
                row.arr_time,
                row.dep_time,
                row.stabling_location,
                row.pm_arr_time,
                row.mileage,
                row.notes,
            ]
            for c, value in enumerate(values, start=1):
                cell = ws.cell(row=r, column=c, value=value)
                cell.border = BORDER
                if c in (3, 4, 6, 7, 9):
                    cell.number_format = "hh:mm"
                if c == 10:
                    cell.number_format = "0.00"
            ws.cell(row=r, column=1).font = Font(bold=True)
            if row.flagged_do_not_use:
                for c in range(1, len(SUMMARY_COLUMNS) + 1):
                    ws.cell(row=r, column=c).fill = FLAG_FILL
            elif row.is_ecs_only:
                for c in range(1, len(SUMMARY_COLUMNS) + 1):
                    ws.cell(row=r, column=c).fill = ECS_FILL
            r += 1
        last_data_row = r - 1

        total_row = r
        ws.cell(row=total_row, column=9, value="Total").font = Font(bold=True)
        ws.cell(row=total_row, column=10, value=f"=SUM(J{first_data_row}:J{last_data_row})").font = Font(bold=True)
        ws.cell(row=total_row, column=10).number_format = "0.00"
        r = total_row + 2  # blank separator row before the next class block

    if not show_class_labels:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(SUMMARY_COLUMNS))}{r - 2}"


def add_detail_sheet(wb: Workbook, entries: list[tuple[str, Diagram]]) -> None:
    ws = wb.create_sheet("Full Detail")
    _write_header(ws, DETAIL_COLUMNS, row=1)

    r = 2
    for day_type, d in entries:
        for leg_no, s in enumerate(d.stops, start=1):
            values = [
                day_type,
                d.diagram_no,
                d.unit_class,
                d.days,
                d.valid_from,
                d.valid_to,
                leg_no,
                s.location,
                s.arrive_time,
                s.arrive_platform,
                s.depart_time,
                s.depart_platform,
                s.code,
                s.route,
                s.leg_mileage,
                s.cum_mileage,
            ]
            for c, value in enumerate(values, start=1):
                cell = ws.cell(row=r, column=c, value=value)
                if c in (9, 11):
                    cell.number_format = "hh:mm"
                if c in (15, 16):
                    cell.number_format = "0.00"
            r += 1

    ws.auto_filter.ref = f"A1:{get_column_letter(len(DETAIL_COLUMNS))}{r - 1}"


def build_workbook(sheets: dict[str, list[SimplifiedRow]], detail_entries: list[tuple[str, Diagram]]) -> Workbook:
    wb = Workbook()
    wb.remove(wb.active)
    for sheet_name, rows in sheets.items():
        add_summary_sheet(wb, sheet_name, rows)
    if detail_entries:
        add_detail_sheet(wb, detail_entries)
    return wb
