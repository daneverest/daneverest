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
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

import docx

HEADER_RE = re.compile(r"^\t([A-Za-z]+)\t(\d+)\tClass\t(.+)$")
STOPS_HEADER_PREFIX = "\tArrive\tP\tDepart\tP\tCode\tRoute\tCoupled\tLeg\tMileage"


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


def _split_row(line: str) -> list[str]:
    parts = line.split("\t")
    if len(parts) < 12:
        parts += [""] * (12 - len(parts))
    return parts


def parse_stop_row(line: str) -> Stop:
    p = _split_row(line)
    return Stop(
        location=p[2].strip(),
        arrive_raw=p[3].strip(),
        arrive_platform=p[4].strip(),
        depart_raw=p[5].strip(),
        depart_platform=p[6].strip(),
        code=p[7].strip(),
        route=p[8].strip(),
        coupled=p[9].strip(),
        leg_mileage=parse_float(p[10]),
        cum_mileage=parse_float(p[11]),
    )


def parse_paragraphs(paragraphs: list[str]) -> list[Diagram]:
    diagrams: list[Diagram] = []
    current: Diagram | None = None

    for raw_line in paragraphs:
        line = raw_line.rstrip("\n")

        m = HEADER_RE.match(line)
        if m:
            current = Diagram(depot_code=m.group(1), number=m.group(2), unit_class=m.group(3))
            diagrams.append(current)
            continue

        if current is None:
            continue  # preamble before the first diagram

        if not line.strip():
            continue

        if line.startswith("\tDays\t\t"):
            current.days = line.split("\t", 3)[-1].strip()
        elif line.startswith("\tFrom\t\t"):
            current.valid_from = line.split("\t", 3)[-1].strip()
        elif line.startswith("\tTo\t\t"):
            current.valid_to = line.split("\t", 3)[-1].strip()
        elif line.startswith("\tType\t\t"):
            current.diagram_type = line.split("\t", 3)[-1].strip()
        elif line.startswith(STOPS_HEADER_PREFIX):
            continue  # the stop-table column header row, nothing to record
        elif line.startswith("\tLoaded\t"):
            current.loaded = parse_float(line.split("\t")[2] if len(line.split("\t")) > 2 else "")
        elif line.startswith("\tEmpty\t"):
            current.empty = parse_float(line.split("\t")[2] if len(line.split("\t")) > 2 else "")
        elif line.startswith("\tTotal\t"):
            current.total = parse_float(line.split("\t")[2] if len(line.split("\t")) > 2 else "")
        elif line.startswith("\t\t"):
            current.stops.append(parse_stop_row(line))
        elif line.startswith("\t"):
            # A stray single-tab line between "Type" and the stop table that
            # isn't one of the recognised keywords is a free-text note, e.g.
            # "DO NOT USE - maintenance unit".
            text = line.strip()
            if text:
                current.notes.append(text)

    return diagrams


def parse_docx(path: str | Path) -> list[Diagram]:
    document = docx.Document(str(path))
    paragraphs = [p.text for p in document.paragraphs]
    return parse_paragraphs(paragraphs)
