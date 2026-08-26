"""Parses raw "Unit Diagram" Word documents (e.g. D40 diagram books) into
structured data.

The source documents are exported from a diagramming system as a flat list
of tab-separated paragraphs. Each diagram is a block that looks like::

    \tIL\t301\tClass\t720/5

    \tDays\t\tMTWO
    \tFrom\t\t14/12/2026
    \tTo\t\t12/05/2027
    \tType\t\tLTP

    \tArrive\tP\tDepart\tP\tCode\tRoute\tCoupled\tLeg\tMileage

    \t\tIlford EMUD\t\t\t04+40\t\t5H00\t\t\t8.00\t8.00
    \t\tLiverpool St\t05+04\t7\t05.12\t7\t2H00\tLea V\t\t55.65\t63.65
    ...
    \tLoaded\t657.61
    \tEmpty\t8.50
    \tTotal\t666.11

A diagram number (e.g. "IL 301") can repeat with different ``Days`` codes
when the working varies by day of the week -- each repeat is parsed as a
separate :class:`Diagram` "variant".

Different depots' exports aren't quite identical: Norwich Crown Point's, for
example, has an extra "Fuel" column in the stop table (shifting every field
after it), an unused "Off" field, and the "Loaded"/"Empty"/"Total" summary
lines sit at a different tab depth (there's even a stray "Works" label
sharing the "Empty" line). None of that is depot-specific as such -- it's
just export-format variation -- so rather than hardcode column positions,
the parser reads the stop-table's own header row to learn the column layout
for whichever document it's given, and finds each summary field by scanning
for its label token rather than assuming a fixed tab prefix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

import re

import docx

HEADER_RE = re.compile(r"^\t([A-Za-z]+)\t(\d+)\tClass\t(.+)$")

# Labels that can appear as a standalone tab-delimited field within the
# "meta" portion of a diagram block (Days/From/To/Type/Loaded/Empty/Total),
# plus a couple of fields seen in some depots' exports that carry no data we
# currently model (Off, Works) but which must still be recognised so they
# aren't mistaken for a free-text note.
_VALUE_LABELS = {"Days", "From", "To", "Type", "Loaded", "Empty", "Total"}
_IGNORED_LABELS = {"Off", "Works"}
_ALL_LABELS = _VALUE_LABELS | _IGNORED_LABELS

_IGNORED_LINES = {"- End of report -"}

_DIAGRAM_ATTR = {"Days": "days", "From": "valid_from", "To": "valid_to", "Type": "diagram_type"}


def parse_time(raw: str) -> time | None:
    """Convert a diagram time like ``05.12``, ``04+40`` or ``11.15½`` into a
    :class:`datetime.time`.

    ``+`` is used in the source in place of ``.`` to mark a passing/working
    time rather than a public time -- for our purposes both denote the same
    clock time. A trailing ``½`` marks a half-minute (30 second) offset.
    """
    raw = raw.strip()
    if not raw:
        return None
    half = raw.endswith("½")
    if half:
        raw = raw[:-1]
    raw = raw.replace("+", ".")
    if "." not in raw:
        return None
    hh_s, mm_s = raw.split(".", 1)
    try:
        hh = int(hh_s) % 24
        mm = int(mm_s)
    except ValueError:
        return None
    return time(hh, mm, 30 if half else 0)


def parse_float(raw: str) -> float | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


@dataclass
class Stop:
    location: str
    arrive_raw: str
    arrive_platform: str
    depart_raw: str
    depart_platform: str
    code: str
    route: str
    coupled: str
    leg_mileage: float | None
    cum_mileage: float | None
    marker: str | None = None
    """Text of a bare annotation row (e.g. "FUEL", "STANDBY UNIT") that sat
    between this stop's split arrival/departure halves, if any."""

    @property
    def arrive_time(self) -> time | None:
        return parse_time(self.arrive_raw)

    @property
    def depart_time(self) -> time | None:
        return parse_time(self.depart_raw)


@dataclass
class Diagram:
    depot_code: str
    number: str
    unit_class: str
    days: str = ""
    valid_from: str = ""
    valid_to: str = ""
    diagram_type: str = ""
    notes: list[str] = field(default_factory=list)
    stops: list[Stop] = field(default_factory=list)
    loaded: float | None = None
    empty: float | None = None
    total: float | None = None

    @property
    def diagram_no(self) -> str:
        return f"{self.depot_code}{self.number}"

    @property
    def is_ecs_only(self) -> bool:
        """True when every leg is empty-stock (headcode starts with 5) --
        i.e. no passenger service is worked on this diagram."""
        return bool(self.stops) and (self.loaded in (0, 0.0, None)) and all(
            not s.code or s.code[0] == "5" for s in self.stops if s.code
        )


def _find_label_value(fields: list[str], label: str) -> str | None:
    """Find `label` as an exact tab-field and return the next non-empty
    field after it, wherever in the line it sits."""
    for i, f in enumerate(fields):
        if f.strip() == label:
            for later in fields[i + 1 :]:
                if later.strip():
                    return later.strip()
            return ""
    return None


def _default_stop_columns() -> list[str]:
    return ["Arrive", "P", "Depart", "P", "Code", "Route", "Coupled", "Leg", "Mileage"]


def parse_stop_row(line: str, columns: list[str]) -> Stop:
    parts = line.split("\t")
    location = parts[2].strip() if len(parts) > 2 else ""
    values = parts[3 : 3 + len(columns)]
    values += [""] * (len(columns) - len(values))

    def get(name: str, occurrence: int = 0) -> str:
        idx = [i for i, c in enumerate(columns) if c == name]
        if occurrence < len(idx):
            return values[idx[occurrence]].strip()
        return ""

    return Stop(
        location=location,
        arrive_raw=get("Arrive"),
        arrive_platform=get("P", 0),
        depart_raw=get("Depart"),
        depart_platform=get("P", 1),
        code=get("Code"),
        route=get("Route"),
        coupled=get("Coupled"),
        leg_mileage=parse_float(get("Leg")),
        cum_mileage=parse_float(get("Mileage")),
    )


def _merge_split_stops(stops: list[Stop]) -> list[Stop]:
    """Some depots record a stop that has a mid-dwell annotation (e.g. a
    fuel stop) as two consecutive physical rows for the same location: one
    with only an arrival, immediately followed by one with only a
    departure. Recombine those into a single logical :class:`Stop` so
    dwell/Day-Stable detection (which expects one row per stop) still works.
    """
    merged: list[Stop] = []
    i = 0
    n = len(stops)
    while i < n:
        s = stops[i]
        nxt = stops[i + 1] if i + 1 < n else None
        if (
            nxt is not None
            and s.location == nxt.location
            and s.arrive_raw
            and not s.depart_raw
            and not nxt.arrive_raw
            and nxt.depart_raw
        ):
            merged.append(
                Stop(
                    location=s.location,
                    arrive_raw=s.arrive_raw,
                    arrive_platform=s.arrive_platform or nxt.arrive_platform,
                    depart_raw=nxt.depart_raw,
                    depart_platform=nxt.depart_platform,
                    code=nxt.code,
                    route=nxt.route,
                    coupled=nxt.coupled or s.coupled,
                    leg_mileage=nxt.leg_mileage,
                    cum_mileage=nxt.cum_mileage,
                    marker=s.marker or nxt.marker,
                )
            )
            i += 2
        else:
            merged.append(s)
            i += 1
    return merged


def parse_paragraphs(paragraphs: list[str]) -> list[Diagram]:
    diagrams: list[Diagram] = []
    current: Diagram | None = None
    stop_columns = _default_stop_columns()
    last_was_note = False
    pending_marker = None

    for raw_line in paragraphs:
        line = raw_line.rstrip("\n")
        stripped = line.strip()

        if not stripped:
            last_was_note = False
            continue

        if stripped in _IGNORED_LINES:
            continue

        m = HEADER_RE.match(line)
        if m:
            current = Diagram(depot_code=m.group(1), number=m.group(2), unit_class=m.group(3))
            diagrams.append(current)
            stop_columns = _default_stop_columns()
            last_was_note = False
            pending_marker = None
            continue

        if current is None:
            continue  # preamble before the first diagram

        fields = line.split("\t")

        # The stop-table's own column header row -- capture the layout so
        # later stop rows in this diagram are read by column name rather
        # than a hardcoded position (depots differ, e.g. an extra "Fuel"
        # column shifts everything after it).
        if "Arrive" in fields and "Mileage" in fields:
            start = fields.index("Arrive")
            cols = [f.strip() for f in fields[start:]]
            while cols and not cols[-1]:
                cols.pop()
            stop_columns = cols
            last_was_note = False
            pending_marker = None
            continue

        matched_label = False
        for label in _ALL_LABELS:
            value = _find_label_value(fields, label)
            if value is None:
                continue
            matched_label = True
            if label in _VALUE_LABELS:
                if label in _DIAGRAM_ATTR:
                    setattr(current, _DIAGRAM_ATTR[label], value)
                elif label == "Loaded":
                    current.loaded = parse_float(value)
                elif label == "Empty":
                    current.empty = parse_float(value)
                elif label == "Total":
                    current.total = parse_float(value)
        if matched_label:
            last_was_note = False
            pending_marker = None
            continue

        if line.startswith("\t\t"):
            # A marker line (e.g. "FUEL") sits between the arrival and
            # departure halves of a split stop row and shares its double-tab
            # prefix -- catch it before it's mistaken for a station called
            # "FUEL", and tag whichever stop comes next instead of recording
            # a note.
            row_fields = line.split("\t")
            location_field = row_fields[2].strip() if len(row_fields) > 2 else ""
            if location_field and not any(f.strip() for f in row_fields[3:]) and location_field.isupper():
                pending_marker = location_field
                last_was_note = False
                continue
            stop = parse_stop_row(line, stop_columns)
            if pending_marker:
                stop.marker = pending_marker
                pending_marker = None
            current.stops.append(stop)
            last_was_note = False
            continue

        # A stray line that isn't the diagram header, a recognised meta
        # field, or a stop row is free-text commentary, e.g. "DO NOT USE -
        # maintenance unit". Some notes wrap onto a second paragraph with no
        # blank line in between -- merge those back into one note.
        if last_was_note and current.notes:
            current.notes[-1] = f"{current.notes[-1]} {stripped}"
        else:
            current.notes.append(stripped)
        last_was_note = True
        pending_marker = None

    for d in diagrams:
        d.stops = _merge_split_stops(d.stops)

    return diagrams


def parse_docx(path: str | Path) -> list[Diagram]:
    document = docx.Document(str(path))
    paragraphs = [p.text for p in document.paragraphs]
    return parse_paragraphs(paragraphs)
