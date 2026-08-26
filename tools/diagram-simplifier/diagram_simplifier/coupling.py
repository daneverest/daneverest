"""Detects when a diagram runs physically coupled to another diagram --
which diagram(s), for how long, and in what position.

The "Coupled" field recorded on a leg (e.g. ``383(1)\\407(2)``) turns out
not to be fleet stock numbers but *other diagram numbers*, each tagged with
its position in the formation: ``(1)`` is the leading portion, the highest
number present is the trailing portion, and anything in between (a 3+ unit
formation) is a middle portion. This was confirmed by cross-referencing:
diagrams 383 and 407 in the Ilford SX book record byte-for-byte identical
stops (same times, same headcodes) for as long as they're shown coupled,
then diverge the moment they part company -- i.e. each diagram in a coupled
working carries its own full copy of the shared leg, self included, so a
diagram's own number always appears somewhere in its own "Coupled" field
alongside its partner(s).

This lets us reconstruct, purely from the diagram book, exactly when a unit
ran attached to another one and in which position -- the data an engineer
needs to check a diagram against a known restriction (a defective coupler
that must stay at one end, a fault that has to be "boxed" between two
working units, or units that must not be mixed during a software campaign)
even though the restriction itself isn't something these diagrams record.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import time

from .parser import Diagram

_COUPLED_ENTRY_RE = re.compile(r"(\d+)\((\d+)\)")


def parse_coupled(raw: str) -> list[tuple[str, int]]:
    """Parse a raw Coupled field like ``383(1)\\407(2)`` into
    ``[("383", 1), ("407", 2)]``."""
    return [(num, int(pos)) for num, pos in _COUPLED_ENTRY_RE.findall(raw or "")]


def _role(position: int, all_positions: list[int]) -> str:
    if len(all_positions) <= 1:
        return "alone"
    if position == min(all_positions):
        return "leading"
    if position == max(all_positions):
        return "trailing"
    return "middle"


def _role_for(entries: list[tuple[str, int]], self_number: str) -> str:
    self_positions = [pos for num, pos in entries if num == self_number]
    if not self_positions:
        return "unknown"
    return _role(self_positions[0], [pos for _, pos in entries])


def _partner_key(entries: list[tuple[str, int]], self_number: str, depot: str) -> tuple[str, ...]:
    return tuple(sorted(f"{depot}{num}" for num, _pos in entries if num != self_number))


def _fmt(t: time | None) -> str:
    return t.strftime("%H:%M") if t else "?"


@dataclass
class CouplingSpan:
    partners: tuple[str, ...]  # depot-prefixed diagram numbers, e.g. ("IL407",)
    start_location: str
    start_time: time | None
    end_location: str
    end_time: time | None
    roles: tuple[str, ...]  # this diagram's role on each leg of the span

    @property
    def role_summary(self) -> str:
        distinct = list(dict.fromkeys(self.roles))
        if len(distinct) == 1:
            return distinct[0]
        return "mixed: " + "→".join(distinct)

    @property
    def partner_text(self) -> str:
        return " + ".join(self.partners)

    @property
    def description(self) -> str:
        return (
            f"Coupled with {self.partner_text} ({self.role_summary}) "
            f"{self.start_location} {_fmt(self.start_time)} – "
            f"{self.end_location} {_fmt(self.end_time)}"
        )


def build_coupling_spans(diagram: Diagram) -> list[CouplingSpan]:
    """Walk a single diagram's stops and collapse consecutive legs that
    share the same coupling partner(s) into spans."""
    stops = diagram.stops
    depot = diagram.depot_code
    n = len(stops)
    spans: list[CouplingSpan] = []

    i = 0
    while i < n - 1:
        entries_i = parse_coupled(stops[i].coupled)
        partners_i = _partner_key(entries_i, diagram.number, depot)
        if not partners_i:
            i += 1
            continue

        roles = [_role_for(entries_i, diagram.number)]
        j = i + 1
        while j < n - 1:
            entries_j = parse_coupled(stops[j].coupled)
            if _partner_key(entries_j, diagram.number, depot) != partners_i:
                break
            roles.append(_role_for(entries_j, diagram.number))
            j += 1

        spans.append(
            CouplingSpan(
                partners=partners_i,
                start_location=stops[i].location,
                start_time=stops[i].depart_time,
                end_location=stops[j].location,
                end_time=stops[j].arrive_time,
                roles=tuple(roles),
            )
        )
        i = j

    return spans


def summarize_coupling(spans: list[CouplingSpan]) -> tuple[str, str]:
    """Condense a diagram's coupling spans into (free-text note, short
    position-pattern tag). The pattern tag is the quick answer to "does
    this diagram always run at the same end when coupled" -- the question
    that matters for a one-end-only restriction."""
    if not spans:
        return "", ""

    text = "; ".join(span.description for span in spans)

    all_roles = [role for span in spans for role in span.roles]
    distinct = set(all_roles)
    if distinct == {"leading"}:
        pattern = "Always Leading"
    elif distinct == {"trailing"}:
        pattern = "Always Trailing"
    elif distinct == {"middle"}:
        pattern = "Always Middle"
    elif distinct == {"alone"}:
        pattern = ""
    else:
        pattern = "Mixed"

    return text, pattern
