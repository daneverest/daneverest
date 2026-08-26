"""Plans a software-update / maintenance-campaign rollout across a fleet of
diagrams, working shift by shift from the same parsed diagram data the
Simplifier already produces.

This is a scheduling *engine*, not a fixed report: give it a parsed set of
diagrams (any depot, any period -- "holistic... based on any timetable
added" per the brief) plus a :class:`CampaignConfig`, and it plans which
units get updated at which location on which shift, respecting:

- **Where updates can happen**: only at the named campaign sites (depots,
  sidings, stations) where a unit actually stables or dwells long enough,
  derived from the Simplifier's own Origin / Stabling Location / Day Stable
  fields -- never mid-journey.
- **Technician capacity**: a fixed number of technicians per 12-hour shift,
  each capable of one unit per `minutes_per_unit`, net of round-trip travel
  time from Ilford (technicians are based there) and a lunch/personal-needs
  break -- see :class:`CampaignConfig`.
- **Coupling safety**: a diagram that ever couples with another (see
  `coupling.py`) can only be updated once its direct partner(s) -- everyone
  it has *ever* been recorded coupled with -- are updated too, either
  already done or alongside it in the same shift if co-located.
  Spare/never-coupled diagrams go first since they carry no such risk. Note
  this is checked one hop at a time, not transitively: the *full* "ever
  coupled together" graph tends to chain into one component covering most
  of the fleet (a unit that swaps partners across different day patterns
  links into its partners' *other* partners), which would make a real
  rollout impossible. So once a diagram is marked updated, any of *its*
  partners that are still pending is surfaced as a residual-risk warning
  for a controller to judge, rather than forcing the whole chain to move
  as one -- see `residual_partner_warning`.

What this can't know: the *real* travel times between sites, how long a
lunch break actually is, or the true size of the fleet to roll out -- those
are exactly the knobs on :class:`CampaignConfig`, shipped here with clearly
labelled placeholder defaults that must be replaced with real operational
figures before this is used to actually schedule people.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time, timedelta

from .coupling import build_coupling_spans
from .parser import Diagram
from .simplify import SimplifiedRow, build_simplified_rows, is_signal_location

DAY_TYPE_FOR_WEEKDAY = {0: "Mon-Fri", 1: "Mon-Fri", 2: "Mon-Fri", 3: "Mon-Fri", 4: "Mon-Fri", 5: "Saturday", 6: "Sunday"}


def day_type_for_date(d: date) -> str:
    return DAY_TYPE_FOR_WEEKDAY[d.weekday()]


# ---------------------------------------------------------------------------
# Campaign sites
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CampaignSite:
    name: str
    keywords: tuple[str, ...]  # lowercase substrings that identify this site in a raw location string
    travel_minutes_from_ilford: int  # ONE-WAY placeholder -- replace with real figures


# PLACEHOLDER travel times (one-way, minutes, by road from Ilford Depot).
# These are rough real-world estimates, not measured figures -- replace with
# your own before using this for an actual rollout.
DEFAULT_SITES: tuple[CampaignSite, ...] = (
    CampaignSite("Ilford", ("ilford",), 0),
    CampaignSite("Aldersbrook Sidings", ("aldersbrook up c.h.s", "aldersbrook chs", "aldersbrook sidings"), 10),
    CampaignSite("Orient Way", ("orient way",), 25),
    CampaignSite("London Liverpool Street", ("liverpool st",), 35),
    CampaignSite("Hertford", ("hertford east", "hertford"), 50),
    CampaignSite("Bishops Stortford", ("bishops stortford", "b stortford"), 55),
    CampaignSite("Stansted", ("stansted",), 60),
    CampaignSite("Southend", ("southend vic",), 55),
    CampaignSite("Colchester", ("colchester",), 75),
    CampaignSite("Clacton", ("clacton",), 100),
    CampaignSite("Harwich Parkeston Quay", ("harwich", "parkeston"), 110),
    CampaignSite("Cambridge", ("cambridge",), 90),
)


def normalize_site(raw_location: str, sites: tuple[CampaignSite, ...] = DEFAULT_SITES) -> CampaignSite | None:
    """Map a raw Origin/Stabling Location string to one of the campaign
    sites, or None if it isn't one of them (e.g. a running-line station or a
    signal reference -- a unit passing through isn't a unit stabling)."""
    if not raw_location or is_signal_location(raw_location):
        return None
    low = raw_location.lower()
    for site in sites:
        if any(kw in low for kw in site.keywords):
            return site
    return None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class CampaignConfig:
    sites: tuple[CampaignSite, ...] = DEFAULT_SITES
    technicians_per_shift: int = 2
    minutes_per_unit: int = 60
    """How long one update takes. Stated as ~1 hour but varies per campaign."""
    lunch_break_minutes: int = 30
    personal_buffer_minutes: int = 15
    """Washing up, handover, contingency -- on top of the lunch break."""
    shift_hours: int = 12
    unit_class_prefix: str = "720"
    """Only diagrams whose class starts with this are in scope (Class 720 only, per the brief)."""

    def site(self, name: str) -> CampaignSite:
        for s in self.sites:
            if s.name == name:
                return s
        raise KeyError(name)

    def technician_unit_capacity(self, site: CampaignSite) -> int:
        """How many units ONE technician can complete in a single shift at
        `site`, after round-trip travel from Ilford and breaks."""
        shift_minutes = self.shift_hours * 60
        travel_roundtrip = 0 if site.name == "Ilford" else 2 * site.travel_minutes_from_ilford
        overhead = travel_roundtrip + self.lunch_break_minutes + self.personal_buffer_minutes
        available = shift_minutes - overhead
        if available <= 0:
            return 0
        return available // self.minutes_per_unit


# ---------------------------------------------------------------------------
# Coupling partners
# ---------------------------------------------------------------------------
#
# The *transitive* closure of "ever coupled together" turns out to sweep up
# almost the entire fleet into one component (a diagram that swaps partners
# across different day patterns chains into its partners' *other* partners,
# and so on) -- requiring a whole such component to flip in one shift would
# mean re-flashing most of the fleet simultaneously, which isn't how a real
# rollout works. So safety here is checked one hop at a time: a diagram's
# *direct* partners (everyone it has ever actually been recorded coupled
# with) must be already updated, or updated alongside it in the same shift
# if they happen to be co-located. A diagram whose partner has *its own*
# other partner still pending is flagged as a residual-risk note rather than
# blocking the whole chain -- see `_partner_warning`.


def build_direct_partners(diagrams_by_day_type: dict[str, list[Diagram]]) -> dict[str, frozenset[str]]:
    """{diagram_no: every diagram_no it has ever been recorded coupled
    with, across all day-type variants}. Diagrams that never couple map to
    an empty frozenset."""
    partners: dict[str, set[str]] = {}
    for diagrams in diagrams_by_day_type.values():
        for d in diagrams:
            partners.setdefault(d.diagram_no, set())
            for span in build_coupling_spans(d):
                partners[d.diagram_no].update(span.partners)
                for p in span.partners:
                    partners.setdefault(p, set()).add(d.diagram_no)
    return {k: frozenset(v) for k, v in partners.items()}


# ---------------------------------------------------------------------------
# Eligibility: which diagrams are physically present at a site, for how long
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    diagram_no: str
    site: CampaignSite
    window_start: time | None
    window_end: time | None
    is_spare: bool  # a unit that just sits there all shift (never departs) -- zero coupling risk while idle


def _yard_departs_from(rows: list[SimplifiedRow], site: CampaignSite, sites: tuple[CampaignSite, ...]) -> list[time]:
    out = []
    for row in rows:
        if normalize_site(row.origin, sites) is site and row.yard_depart is not None:
            out.append(row.yard_depart)
    return out


def night_shift_candidates(
    rows_today: list[SimplifiedRow],
    rows_tomorrow: list[SimplifiedRow],
    config: CampaignConfig,
) -> list[Candidate]:
    """Units becoming available overnight: those whose Stabling Location
    (tonight's diagram) is a campaign site, available from PM Arr Time until
    the earliest Yard Depart tomorrow morning from that same site (or 06:00
    if nothing departs from there before then)."""
    candidates = []
    for row in rows_today:
        if row.flagged_do_not_use:
            continue
        site = normalize_site(row.stabling_location, config.sites)
        if site is None:
            continue
        is_spare = row.pm_arr_time is None  # never really "arrives" -- it just sits there (e.g. a stabled spare)
        cutoff_candidates = [t for t in _yard_departs_from(rows_tomorrow, site, config.sites) if t < time(6, 0)]
        window_end = min(cutoff_candidates) if cutoff_candidates else time(5, 59)
        candidates.append(
            Candidate(
                diagram_no=row.diagram_no,
                site=site,
                window_start=row.pm_arr_time,
                window_end=window_end,
                is_spare=is_spare,
            )
        )
    return candidates


def day_shift_candidates(rows_today: list[SimplifiedRow], config: CampaignConfig) -> list[Candidate]:
    """Units available during the day: a genuine Day Stable dwell at a
    campaign site within 06:00-18:00, or a fully spare/stabled unit sitting
    at its origin all day."""
    candidates = []
    for row in rows_today:
        if row.flagged_do_not_use:
            continue
        if row.yard_depart is None and row.origin:
            # Never moves this day-type -- a resident spare, available all day.
            site = normalize_site(row.origin, config.sites)
            if site is not None:
                candidates.append(
                    Candidate(row.diagram_no, site, time(6, 0), time(18, 0), is_spare=True)
                )
            continue
        if row.day_stable and row.arr_time and row.dep_time:
            site = normalize_site(row.day_stable, config.sites)
            if site is not None and time(6, 0) <= row.arr_time and row.dep_time <= time(18, 0):
                candidates.append(Candidate(row.diagram_no, site, row.arr_time, row.dep_time, is_spare=False))
    return candidates


def _window_minutes(start: time | None, end: time | None) -> int:
    if start is None or end is None:
        return 0
    s = start.hour * 60 + start.minute
    e = end.hour * 60 + end.minute
    return e - s if e >= s else e + 24 * 60 - s


# ---------------------------------------------------------------------------
# Shift planning
# ---------------------------------------------------------------------------


@dataclass
class SiteShiftResult:
    site: CampaignSite
    technicians: int
    capacity: int
    updated: list[str]  # diagram numbers actually updated this shift at this site
    deferred_notes: list[str]  # why some candidates present here couldn't be done this shift


@dataclass
class ShiftPlan:
    shift_date: date
    shift_name: str  # "Day" or "Night"
    day_type: str
    sites: list[SiteShiftResult]


def _min_work_window_ok(candidates_at_site: list[Candidate], config: CampaignConfig) -> list[Candidate]:
    """Drop candidates whose physical dwell isn't even long enough for one
    technician's round trip + one update, regardless of overall capacity."""
    min_minutes_needed = config.minutes_per_unit  # a single unit's own work must fit in its own window
    return [c for c in candidates_at_site if _window_minutes(c.window_start, c.window_end) >= min_minutes_needed]


def plan_shift(
    shift_date: date,
    shift_name: str,
    day_type: str,
    candidates: list[Candidate],
    partners: dict[str, frozenset[str]],
    updated: set[str],
    config: CampaignConfig,
) -> ShiftPlan:
    """Greedily plan one shift: pick which (up to `technicians_per_shift`)
    sites to work, then which diagrams to update across the whole shift
    (not per-site in isolation, since a coupled pair can be split across two
    technicians at two different sites), preferring spares, then
    never-coupled diagrams, then direct-partner groups that can be cleared
    in full this shift."""
    by_site: dict[str, list[Candidate]] = {}
    for c in candidates:
        if c.diagram_no in updated:
            continue
        by_site.setdefault(c.site.name, []).append(c)

    for name in list(by_site):
        by_site[name] = _min_work_window_ok(by_site[name], config)
        if not by_site[name]:
            del by_site[name]

    if not by_site:
        return ShiftPlan(shift_date, shift_name, day_type, [])

    # Rank sites by how much work is waiting there, capped by one technician's
    # capacity (a rough proxy for "worth sending someone").
    ranked = sorted(
        by_site.items(),
        key=lambda kv: min(len(kv[1]), config.technician_unit_capacity(kv[1][0].site)),
        reverse=True,
    )

    if len(ranked) == 1:
        assignments = {ranked[0][0]: (ranked[0][1][0].site, config.technicians_per_shift)}
    else:
        assignments = {name: (cands[0].site, 1) for name, cands in ranked[: config.technicians_per_shift]}

    capacities = {name: techs * config.technician_unit_capacity(site) for name, (site, techs) in assignments.items()}
    pool: dict[str, Candidate] = {}
    for name in assignments:
        for c in by_site[name]:
            pool[c.diagram_no] = c

    chosen_by_site, notes_by_site = _choose_shiftwide(pool, capacities, partners, updated)

    site_results = [
        SiteShiftResult(
            site=site,
            technicians=techs,
            capacity=capacities[name],
            updated=chosen_by_site.get(name, []),
            deferred_notes=notes_by_site.get(name, []),
        )
        for name, (site, techs) in assignments.items()
    ]
    for sr in site_results:
        updated.update(sr.updated)

    return ShiftPlan(shift_date, shift_name, day_type, site_results)


def _choose_shiftwide(
    pool: dict[str, Candidate],
    capacities: dict[str, int],
    partners: dict[str, frozenset[str]],
    updated: set[str],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    present = set(pool)
    remaining = dict(capacities)
    chosen_by_site: dict[str, list[str]] = {}
    notes_by_site: dict[str, list[str]] = {}

    def site_of(no: str) -> str:
        return pool[no].site.name

    chosen_ids: set[str] = set()

    def take(no: str) -> bool:
        site = site_of(no)
        if remaining.get(site, 0) <= 0:
            return False
        remaining[site] -= 1
        chosen_by_site.setdefault(site, []).append(no)
        chosen_ids.add(no)
        return True

    spares = [no for no, c in pool.items() if c.is_spare and not partners.get(no)]
    isolated = [no for no, c in pool.items() if not c.is_spare and not partners.get(no)]
    for no in spares + isolated:
        take(no)

    handled: set[str] = set()
    grouped = [no for no, c in pool.items() if partners.get(no)]
    for no in grouped:
        # Already decided (taken as someone else's partner, or already
        # deferred/handled from an earlier iteration this shift).
        if no in handled or no in chosen_ids:
            continue
        # Partners already updated *or already chosen earlier this same
        # shift* (e.g. IL382 partnering with both IL381 and IL427 only
        # needs updating once) don't need to be asked for again.
        pending_partners = partners.get(no, frozenset()) - updated - chosen_ids
        need = {no} | pending_partners
        handled.update(need)

        missing = [m for m in need if m not in present]
        if missing:
            notes_by_site.setdefault(site_of(no), []).append(
                f"{no}: deferred -- still needs {', '.join(sorted(pending_partners))} updated first "
                f"({', '.join(sorted(missing))} not present this shift)"
            )
            continue

        per_site_need: dict[str, int] = {}
        for m in need:
            per_site_need[site_of(m)] = per_site_need.get(site_of(m), 0) + 1
        if all(remaining.get(s, 0) >= n for s, n in per_site_need.items()):
            for m in need:
                take(m)
        else:
            notes_by_site.setdefault(site_of(no), []).append(
                f"{no}: deferred -- coupling group {sorted(need)} present but exceeds remaining capacity this shift"
            )

    return chosen_by_site, notes_by_site


def residual_partner_warning(diagram_no: str, partners: dict[str, frozenset[str]], updated: set[str]) -> str | None:
    """After marking `diagram_no` updated, flag any of its direct partners
    that are still not updated -- a residual mixed-coupling risk this
    planner resolves by note rather than by force-including the whole
    chain (see module docstring)."""
    still_pending = sorted(p for p in partners.get(diagram_no, ()) if p not in updated)
    if not still_pending:
        return None
    return f"{diagram_no} is updated but still shares a coupling history with not-yet-updated {', '.join(still_pending)} -- avoid pairing them until that's also done."


# ---------------------------------------------------------------------------
# Top-level rollout
# ---------------------------------------------------------------------------


@dataclass
class Rollout:
    shifts: list[ShiftPlan]
    partners: dict[str, frozenset[str]]
    fleet: set[str]
    updated: set[str]
    warnings: list[str]


def plan_rollout(
    diagrams_by_day_type: dict[str, list[Diagram]],
    start_date: date,
    num_days: int,
    config: CampaignConfig | None = None,
    already_updated: set[str] | None = None,
) -> Rollout:
    config = config or CampaignConfig()

    filtered = {
        day_type: [d for d in diagrams if d.unit_class.startswith(config.unit_class_prefix)]
        for day_type, diagrams in diagrams_by_day_type.items()
    }
    partners = build_direct_partners(filtered)

    rows_by_day_type = {day_type: build_simplified_rows(diagrams) for day_type, diagrams in filtered.items()}
    fleet = {row.diagram_no for rows in rows_by_day_type.values() for row in rows if not row.flagged_do_not_use}

    updated = set(already_updated or ())
    shifts: list[ShiftPlan] = []
    warnings: list[str] = []

    for i in range(num_days):
        d = start_date + timedelta(days=i)
        day_type = day_type_for_date(d)
        rows_today = rows_by_day_type.get(day_type, [])

        day_candidates = day_shift_candidates(rows_today, config)
        day_shift = plan_shift(d, "Day", day_type, day_candidates, partners, updated, config)
        shifts.append(day_shift)

        tomorrow_type = day_type_for_date(d + timedelta(days=1))
        rows_tomorrow = rows_by_day_type.get(tomorrow_type, [])
        night_candidates = night_shift_candidates(rows_today, rows_tomorrow, config)
        night_shift = plan_shift(d, "Night", day_type, night_candidates, partners, updated, config)
        shifts.append(night_shift)

        for shift in (day_shift, night_shift):
            for sr in shift.sites:
                for no in sr.updated:
                    warning = residual_partner_warning(no, partners, updated)
                    if warning:
                        warnings.append(f"{d.isoformat()} {shift.shift_name}: {warning}")

    return Rollout(shifts=shifts, partners=partners, fleet=fleet, updated=updated, warnings=warnings)
