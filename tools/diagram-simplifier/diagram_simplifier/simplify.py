"""Reduces parsed :class:`~diagram_simplifier.parser.Diagram` objects down to
the condensed "Simplifier" summary row format used in the reference
spreadsheet (Origin / Yard Depart / Service Depart / Day Stable / Stabling
Location / PM Arr Time / Mileage / Notes).

A diagram number can have several *variants* in the source document -- one
per distinct day-of-week pattern (e.g. ``MTWO``, ``ThO``, ``FO``). The
Simplifier shows one row per diagram number, so we pick the variant that
covers the most days of the week as the "primary" row and fold any other
variants into the Notes column as call-outs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import time

from .coupling import build_coupling_spans, summarize_coupling
from .parser import Diagram, Stop

_DAY_TOKENS = ["Th", "Sa", "Su", "M", "T", "W", "F"]

# Signal/reference locations are timing points on the running line (used to
# regulate an empty-stock move), not a stabling facility -- a stop there
# should never be picked as the "Day Stable" location, however long the
# recorded dwell looks. Some depots spell these out ("Stratford Sig L261",
# "Crown Point Sig CO1498"); others drop the word and just give the bare
# signal/berth code ("Ipswich CO822", "Bury St Eds BY19") -- both are
# caught here, the second via its telltale letters-then-digits shape
# (matched case-sensitively so an ordinary capitalised place name doesn't
# false-positive).
_SIGNAL_WORD_RE = re.compile(r"\bSig(nal)?\b", re.IGNORECASE)
_SIGNAL_CODE_RE = re.compile(r"\b[A-Z]{1,3}\d{2,4}\b")

# Locations that look like an actual depot/siding facility rather than an
# ordinary running-line station. Preferred over a plain station when more
# than one candidate dwell exists.
_DEPOT_RE = re.compile(
    r"\b(EMUD|TMD|T&RSMD|CS|CSD|CSN|CHS|C\.H\.S\.|Sidings|Sdgs|Yard|Depot|"
    r"Recep(tion)?(\s+Rd)?|Loop|Lp|Wash\s+Road|Stabling)\b",
    re.IGNORECASE,
)


def _is_signal_location(location: str) -> bool:
    return bool(_SIGNAL_WORD_RE.search(location) or _SIGNAL_CODE_RE.search(location))


def _is_depot_like(location: str) -> bool:
    return bool(_DEPOT_RE.search(location))


def day_coverage(days_code: str) -> int:
    """Rough count of how many weekdays a ``Days`` code covers, used only to
    pick which day-variant of a diagram is the "primary" one to headline."""
    code = days_code.strip()
    if code in ("SX", "MTWTF", "MTWTFO"):
        return 5
    found = []
    i = 0
    while i < len(code):
        for tok in _DAY_TOKENS:
            if code[i : i + len(tok)] == tok:
                found.append(tok)
                i += len(tok)
                break
        else:
            i += 1
    return len(found) if found else 1


def _fmt_time(t: time | None) -> str:
    return t.strftime("%H:%M") if t else "?"


@dataclass
class Dwell:
    location: str
    arrive: time | None
    depart: time | None
    marker: str | None = None

    @property
    def duration_minutes(self) -> int:
        if self.arrive is None or self.depart is None:
            return 0
        a = self.arrive.hour * 60 + self.arrive.minute
        d = self.depart.hour * 60 + self.depart.minute
        diff = d - a
        return diff if diff >= 0 else diff + 24 * 60


@dataclass
class SimplifiedRow:
    diagram_no: str
    unit_class: str
    days: str
    origin: str
    yard_depart: time | None
    service_depart: time | None
    day_stable: str
    arr_time: time | None
    dep_time: time | None
    stabling_location: str
    pm_arr_time: time | None
    mileage: float | None
    coupling: str
    coupling_pattern: str
    notes: str
    is_ecs_only: bool
    flagged_do_not_use: bool
    variant_count: int


def _reduce_variant(d: Diagram) -> dict:
    """Core reduction of a single diagram variant into the Simplifier
    columns, using headcode conventions to tell empty-stock (depot) legs
    from passenger-carrying legs:

    * A headcode beginning with ``5`` is an empty-stock/positioning leg.
    * The first stop is always a depot departure (Yard Depart).
    * The first subsequent stop whose own departing leg is *not* empty
      stock is where public/passenger service begins (Service Depart).
    * A stop strictly before the last one is a candidate "Day Stable" dwell
      if *both* the leg arriving there and the leg leaving it are
      empty-stock legs (i.e. the unit goes off to a depot mid-diagram and
      comes back out), as opposed to simply calling at a station between
      passenger workings.
    * Candidate dwells at a signal/reference location (e.g. "Stratford Sig
      L261") are discarded outright -- that's the unit regulating on the
      running line, not stabling. Among the remaining candidates, the one
      at an actual depot/siding-type location (EMUD, CS, Sidings, Loop...)
      is preferred over a plain station, and ties are broken by the longest
      dwell -- a fleeting reversal is far less likely to be the "relevant"
      stabling than a long sit at a proper facility.
    * The final stop (no further departure) is the overnight Stabling
      Location.
    """
    stops = d.stops
    result = {
        "origin": "",
        "yard_depart": None,
        "service_depart": None,
        "day_stable": "",
        "arr_time": None,
        "dep_time": None,
        "day_stable_marker": None,
        "stabling_location": "",
        "pm_arr_time": None,
        "extra_dwells": [],
    }
    if not stops:
        return result

    result["origin"] = stops[0].location
    result["yard_depart"] = stops[0].depart_time

    n = len(stops)
    for i in range(1, n):
        code = stops[i].code
        if code and not code.startswith("5") and stops[i].depart_time:
            result["service_depart"] = stops[i].depart_time
            break

    dwells: list[Dwell] = []
    for i in range(0, n - 1):
        incoming = stops[i].code
        outgoing = stops[i + 1].code
        if (
            incoming
            and incoming.startswith("5")
            and outgoing
            and outgoing.startswith("5")
            and stops[i + 1].depart_time
        ):
            dwells.append(
                Dwell(
                    stops[i + 1].location,
                    stops[i + 1].arrive_time,
                    stops[i + 1].depart_time,
                    marker=stops[i + 1].marker,
                )
            )

    candidates = [dw for dw in dwells if not _is_signal_location(dw.location)]
    if candidates:
        # An explicit marker (e.g. "FUEL", "STANDBY UNIT") is definitive
        # evidence of a genuine servicing dwell, so it outranks a location
        # merely looking depot-like; duration is still the final tie-break.
        primary = max(
            candidates,
            key=lambda dw: (dw.marker is not None, _is_depot_like(dw.location), dw.duration_minutes),
        )
        result["day_stable"] = primary.location
        result["arr_time"] = primary.arrive
        result["dep_time"] = primary.depart
        result["day_stable_marker"] = primary.marker
        result["extra_dwells"] = [dw for dw in candidates if dw is not primary]

    last = stops[-1]
    result["stabling_location"] = last.location
    result["pm_arr_time"] = last.arrive_time

    return result


def _describe_dwell(dw: Dwell, *, primary: bool) -> str:
    span = f"{dw.location} {_fmt_time(dw.arrive)}-{_fmt_time(dw.depart)}"
    if dw.marker:
        return f"{dw.marker.capitalize()} at {span}"
    return span if primary else f"also stables {span}"


def _describe_dwells(dwells: list[Dwell]) -> str:
    return "; ".join(_describe_dwell(dw, primary=False) for dw in dwells)


def _row_differs(a: dict, b: dict) -> bool:
    keys = ("origin", "stabling_location", "day_stable")
    if any(a[k] != b[k] for k in keys):
        return True
    for k in ("yard_depart", "service_depart", "pm_arr_time"):
        if a[k] != b[k]:
            return True
    return False


def build_simplified_rows(diagrams: list[Diagram]) -> list[SimplifiedRow]:
    groups: dict[tuple[str, str], list[Diagram]] = {}
    for d in diagrams:
        groups.setdefault((d.depot_code, d.number), []).append(d)

    def sort_key(item):
        (depot, number), _ = item
        try:
            return (depot, int(number))
        except ValueError:
            return (depot, number)

    rows: list[SimplifiedRow] = []
    for (_depot, _number), variants in sorted(groups.items(), key=sort_key):
        variants_sorted = sorted(variants, key=lambda d: day_coverage(d.days), reverse=True)
        primary = variants_sorted[0]
        primary_reduced = _reduce_variant(primary)

        notes: list[str] = []
        for n in primary.notes:
            if n not in notes:
                notes.append(n)
        if primary_reduced["day_stable_marker"]:
            notes.append(
                _describe_dwell(
                    Dwell(
                        primary_reduced["day_stable"],
                        primary_reduced["arr_time"],
                        primary_reduced["dep_time"],
                        marker=primary_reduced["day_stable_marker"],
                    ),
                    primary=True,
                )
            )
        if primary_reduced["extra_dwells"]:
            notes.append(_describe_dwells(primary_reduced["extra_dwells"]))

        for other in variants_sorted[1:]:
            for n in other.notes:
                tagged = f"{other.days}: {n}"
                if tagged not in notes:
                    notes.append(tagged)
            other_reduced = _reduce_variant(other)
            if _row_differs(primary_reduced, other_reduced):
                notes.append(
                    f"{other.days} differs: origin {other_reduced['origin']} "
                    f"({_fmt_time(other_reduced['yard_depart'])}), "
                    f"stables {other_reduced['stabling_location']} "
                    f"({_fmt_time(other_reduced['pm_arr_time'])})"
                )

        flagged = any("DO NOT USE" in n.upper() for n in primary.notes) or any(
            "DO NOT USE" in n.upper() for v in variants for n in v.notes
        )

        coupling_text, coupling_pattern = summarize_coupling(build_coupling_spans(primary))

        rows.append(
            SimplifiedRow(
                diagram_no=primary.diagram_no,
                unit_class=primary.unit_class,
                days=primary.days,
                origin=primary_reduced["origin"],
                yard_depart=primary_reduced["yard_depart"],
                service_depart=primary_reduced["service_depart"],
                day_stable=primary_reduced["day_stable"],
                arr_time=primary_reduced["arr_time"],
                dep_time=primary_reduced["dep_time"],
                stabling_location=primary_reduced["stabling_location"],
                pm_arr_time=primary_reduced["pm_arr_time"],
                mileage=primary.total,
                coupling=coupling_text,
                coupling_pattern=coupling_pattern,
                notes="; ".join(notes),
                is_ecs_only=primary.is_ecs_only,
                flagged_do_not_use=flagged,
                variant_count=len(variants),
            )
        )

    return rows
