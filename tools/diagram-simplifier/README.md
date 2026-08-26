# Diagram Simplifier

Turns a raw "Unit Diagrams" Word document (the D40-style diagram book export,
one per day-type: SX/SO/Su) into the condensed "Simplifier" spreadsheet used
for depot planning — one row per diagram number showing where it starts,
when it enters/leaves passenger service, where (if anywhere) it goes back to
a depot during the day, where it finishes up overnight, and its daily
mileage.

This was reverse-engineered from:

- `D40_DRAFT_DEC26_{SX,SO,Su}_Ilford_Unit_Diagrams.docx` — the raw diagram
  books (one diagram number can appear multiple times with different `Days`
  codes, e.g. `MTWO`, `ThO`, `FO`, when the working varies through the week).
- `Draft_Dec_26_Diagram_Simplifier_Fleet_North_08.04.26.xlsx` — an existing,
  hand-built Simplifier for a different fleet/depot, used as the reference
  for the target output format (`Diagram No / Origin / Yard Depart / Service
  Depart / Day Stable / Arr Time / Dep Time / Stabling Location / PM Arr
  Time / Mileage / Notes`).

## How the raw format works

Each diagram is a block of tab-separated paragraphs:

```
	IL	301	Class	720/5

	Days		MTWO
	From		14/12/2026
	To		12/05/2027
	Type		LTP

	Arrive	P	Depart	P	Code	Route	Coupled	Leg	Mileage

		Ilford EMUD			04+40		5H00			8.00	8.00
		Liverpool St	05+04	7	05.12	7	2H00	Lea V		55.65	63.65
		...
	Loaded	657.61
	Empty	8.50
	Total	666.11
```

Key conventions decoded from the data:

- A **headcode beginning with `5`** is an empty-stock/positioning leg; any
  other leading digit (`1`, `2`, ...) is a passenger service.
- `+` is used instead of `.` in a time to mark a working/passing time rather
  than a public time — both mean the same clock time (`05+04` == `05.12` in
  spirit, just not a public stop). A trailing `½` is a 30-second offset.
- The diagram's first stop is always the depot departure ("Yard Depart");
  the first subsequent stop whose *own* departing leg is not empty-stock is
  where public service starts ("Service Depart").
- A stop is treated as a **Day Stable** dwell when both the leg arriving
  there *and* the leg leaving it are empty-stock legs — i.e. the unit goes
  off to a depot/siding mid-diagram and comes back out again, as opposed to
  simply calling at an ordinary station between passenger workings.
- The last stop (no further departure) is the overnight **Stabling
  Location**.
- A free-text line can appear between `Type` and the stop table (e.g. `DO
  NOT USE - maintenance unit`, or a routing caveat) — these are carried
  through verbatim into the Notes column.
- Diagram numbers that repeat with different `Days` codes are folded into a
  single Simplifier row: the variant covering the most weekdays is shown as
  the primary row, and any other variants that differ are summarised in
  Notes (e.g. `ThO differs: origin ... stables ...`).
- A candidate Day Stable dwell at a **signal/reference location** (e.g.
  "Stratford Sig L261", or a bare berth code like "Ipswich CO822") is
  discarded outright — that's the unit regulating on the running line, not
  stabling, however long the recorded dwell looks. Among the remaining
  candidates, one at an actual depot/siding-type location (EMUD, CS,
  Sidings, Loop...) is preferred over a plain station, and an explicit
  in-diagram marker (see below) outranks both; duration is the final
  tie-break.
- Some depots (e.g. Norwich Crown Point) record a servicing dwell — a fuel
  stop, a standby-unit period — as two physical rows for the same location
  (an arrival-only row, then a separate departure-only row) with a bare
  marker line such as `FUEL` or `STANDBY UNIT` in between. These are
  recombined into one logical stop and surfaced as a descriptive note, e.g.
  `Fuel at Colchester CS 20:04-20:45`, matching the reference spreadsheet's
  own "Fuel at NCP 21:56-23:15" convention.
- Depots don't all export identically: an extra column in the stop table
  (e.g. Crown Point's "Fuel" column, which shifts every field after it),
  meta lines at a different tab depth, or an unused field (`Off`) are all
  handled by reading each diagram's own column-header row and scanning for
  labels by name rather than assuming a fixed layout — see `parser.py`.
- When a depot's diagrams span more than one unit class (Crown Point diagrams
  more than one class per day-type — 745/0, 745/1, 755/3, 755/4), each
  sheet is split into a block per class with its own "CLASS X" sub-header
  and mileage total, mirroring the reference spreadsheet's "CLASS 755/4" /
  "CLASS 755/3" layout. A depot with a single class (e.g. Ilford, all
  720/5) gets one plain table instead.

## Coupling detection

The "Coupled" field on a leg (e.g. `383(1)\407(2)`) isn't a fleet stock
number as it first appears — it's *other diagram numbers*, each tagged with
its position in the formation: `(1)` is the leading portion, the highest
number present is the trailing portion, and anything in between (a 3+ unit
formation) is a middle portion. This was confirmed by cross-referencing:
diagrams 383 and 407 record byte-for-byte identical stops for as long as
they're shown running together, then diverge the instant they part company —
i.e. a diagram in a coupled working always lists itself alongside its
partner(s), so the coupling — and any point where the two swap ends at a
reversal — can be read straight off the diagram book with no outside data.

This surfaces as:

- **Coupling** / **Coupling Pattern** columns on each summary sheet: a
  plain-English account of who a diagram ran attached to, when, and in what
  position (e.g. `Coupled with IL407 (leading) Ilford EMUD 06:33 –
  Liverpool St 09:39`), plus a one-word tag — `Always Leading` / `Always
  Trailing` / `Always Middle` / `Mixed` — for whether it ever swapped ends.
  A `Mixed` diagram is highlighted, since a unit that has to stay at one
  end (a defective coupler, a fault that must be "boxed" between two
  working units) would show up here as a violation the moment it doesn't.
- A **Couplings** sheet: the same thing at full leg-by-leg granularity,
  across every day-of-week variant of every diagram, for auditing.

What this **can't** do is know *why* two units shouldn't mix, or which
specific units carry a software version or a fault restriction — that's
fleet/engineering data the diagram book itself doesn't contain. What it
gives you is the other half of that check: the complete, accurate record of
which diagram actually ran coupled to which, when, and at which end, so it
can be checked against a restriction list or software-campaign roster you
supply separately.

## Usage

```
pip install -r requirements.txt
python -m diagram_simplifier -o Simplifier.xlsx \
    SX=path/to/..._SX_Unit_Diagrams.docx \
    SO=path/to/..._SO_Unit_Diagrams.docx \
    Su=path/to/..._Su_Unit_Diagrams.docx
```

For the standard D40 filenames (containing `_SX_`, `_SO_` or `_Su_`) the
day-type label can be omitted and guessed from the filename:

```
python -m diagram_simplifier -o Simplifier.xlsx *_Unit_Diagrams.docx
```

Output is a workbook with one sheet per day-type (`Mon-Fri` / `Saturday` /
`Sunday`) plus a `Full Detail` sheet listing every parsed leg of every
diagram, for auditing the summary against the source document. Rows are
colour-flagged: red for anything marked `DO NOT USE`, green for diagrams
that are empty-stock/positioning only (no passenger service).

Because the parsing rules above are generic (driven by headcode prefixes
and diagram structure, not depot-specific names), this same tool should work
unmodified on the next iteration of the timetable, or on diagrams for a
different depot — just point it at the new set of three `.docx` files.

## Layout

- `diagram_simplifier/parser.py` — reads the raw `.docx` into structured
  `Diagram`/`Stop` objects.
- `diagram_simplifier/simplify.py` — reduces diagrams (and their day-of-week
  variants) into one Simplifier row each.
- `diagram_simplifier/workbook.py` — writes the styled `.xlsx`.
- `diagram_simplifier/cli.py` — command-line entry point.
