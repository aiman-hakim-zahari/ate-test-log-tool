"""``lark.Transformer`` -> ADF-1 dataclasses, plus run assembly (Phase 1 M3).

Two stages live here, and the split matters for Phase 1 M6:

``AteLogTransformer``
    Bottom-up transformation of a parse tree into the frozen dataclasses of §4.
    It is the *only* place Lark ``Tree``/``Token`` objects are allowed to
    escape into.

``assemble_run``
    Turns the transformed document into the ``TestRun`` root aggregate:
    assigns ``BlockId`` occurrences in document order, flattens failing
    compares into ``FailureEvent``s, and resolves the §6.3 timing chain **at
    assembly time** while ``Cycle`` objects still exist.  Cycles are discarded
    afterwards; nothing downstream of assembly ever sees one.

Assembly is deliberately a separate function rather than the transformer's
``document`` method: the chunked path (M6) assembles from *frames*, never from
a whole-document tree, and must reuse this exact logic or the two paths drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from lark import Token, Transformer

from ate_fa_suite.model.entities import (
    BlockId,
    CompareEvent,
    Cycle,
    DriveEvent,
    FailCategory,
    FailureEvent,
    LogHeader,
    LogicState,
    PinDef,
    PinDirection,
    PinTiming,
    TestBlockResult,
    TestRun,
    TimingSet,
    VectorLocation,
)
from ate_fa_suite.model.waveform import resolve_timing
from ate_fa_suite.parsing.validator import (
    REQUIRED_META_KEYS,
    ValidationError,
    parse_timescale,
)

#: Fallback when a block has exactly one cycle, so no neighbouring delta exists
#: to resolve a period from.  The renderer clamps band width to a pixel
#: minimum, so a zero period degrades to the minimum band rather than to
#: something invented.
UNKNOWN_CYCLE_PERIOD: Final = 0


# --- transient carriers ------------------------------------------------------
# These exist only between transformation and assembly.  They are NOT part of
# the §4 IR and never cross the thread boundary.


@dataclass(frozen=True, slots=True)
class LocatedCompare:
    """A ``CompareEvent`` plus the source line it was read from.

    §4's ``CompareEvent`` deliberately carries no ``src_line``, but
    ``FailureEvent`` requires one.  Rather than degrade a failure's line to its
    enclosing ``CYCLE`` header (FA engineers want the raw *compare* line), the
    line rides alongside the spec-shaped ``Cycle`` until assembly builds the
    ``FailureEvent``s, then is discarded with the cycles.
    """

    event: CompareEvent
    src_line: int


@dataclass(frozen=True, slots=True)
class ParsedCycle:
    """A spec-shaped ``Cycle`` plus the per-compare source lines."""

    cycle: Cycle
    compare_lines: tuple[int, ...]  # parallel to cycle.compares


@dataclass(frozen=True, slots=True)
class ParsedBlock:
    name: str
    src_line: int
    cycles: tuple[ParsedCycle, ...]
    declared_fail_count: int | None
    declared_fail_vectors: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ParsedHeader:
    """``LogHeader`` plus the magic-line evidence M7's version check needs.

    ``LogHeader`` (§4) has no version field, so the declared ``#ATELOG`` version
    would otherwise be unrecoverable after transformation.
    """

    header: LogHeader
    magic: str
    major_version: int
    minor_version: int
    src_line: int


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """Everything semantic validation needs, BEFORE assembly discards it.

    Validation runs against this, not against ``TestRun``: assembly deliberately
    drops cycles, passing compares, duplicate ``PINDEF``s and the declared
    ``FAILSUMMARY`` count (§6.3 requires ``Cycle`` objects be discarded), and
    every one of those is evidence some M7 rule depends on.  Retaining them on
    ``TestRun`` instead would violate the discard invariant, so the validation
    pass moves earlier rather than the evidence moving later.
    """

    header: ParsedHeader
    pins: tuple[PinDef, ...]  # ALL declarations, duplicates included
    blocks: tuple[ParsedBlock, ...]


# --- helpers -----------------------------------------------------------------


def _line_of(token: Token) -> int:
    """Source line of a token.  ``propagate_positions=True`` guarantees one."""
    return token.line or 0


def state_of(token: str) -> LogicState:
    """Map a raw ``STATE`` terminal to its ``LogicState`` member."""
    return LogicState(token)


# --- transformer -------------------------------------------------------------
#
# Child lists are typed `list[Any]` on purpose. Lark substitutes each rule's
# transformed value in place bottom-up, so by the time `header` runs its `meta`
# children are already MetaEntry objects while its MAGIC child is still a
# Token. No single precise element type exists; the methods narrow explicitly
# instead.


@dataclass(frozen=True, slots=True)
class MetaEntry:
    key: str
    value: str
    src_line: int


class AteLogTransformer(Transformer[Token, ParsedDocument]):
    """Parse tree -> ``ParsedDocument``.  One method per grammar rule."""

    # -- prologue --

    def meta(self, children: list[Any]) -> MetaEntry:
        key_token, value_token = children[0], children[1]
        # META_KEY carries its colon ("LOT:"); VALUE absorbs the leading space
        # because it out-matches the ignored whitespace at that position.
        return MetaEntry(
            key=str(key_token).rstrip(":"),
            value=str(value_token).strip(),
            src_line=_line_of(key_token),
        )

    def header(self, children: list[Any]) -> ParsedHeader:
        magic = children[0]
        entries = [c for c in children if isinstance(c, MetaEntry)]

        seen: dict[str, MetaEntry] = {}
        for entry in entries:
            if entry.key in seen:
                raise ValidationError(
                    f"duplicate metadata key {entry.key!r}", entry.src_line
                )
            seen[entry.key] = entry

        missing = [k for k in REQUIRED_META_KEYS if k not in seen]
        if missing:
            raise ValidationError(
                f"missing required metadata key(s): {', '.join(missing)}",
                _line_of(magic),
            )

        magic_text = str(magic)
        major, _, minor = magic_text.removeprefix("#ATELOG v").partition(".")

        return ParsedHeader(
            header=LogHeader(
                lot=seen["LOT"].value,
                wafer=seen["WAFER"].value,
                device=seen["DEVICE"].value,
                tester=seen["TESTER"].value,
                program=seen["PROGRAM"].value,
                date=seen["DATE"].value,
                timescale_ns=parse_timescale(
                    seen["TIMESCALE"].value, seen["TIMESCALE"].src_line
                ),
            ),
            magic=magic_text,
            # The MAGIC terminal is /#ATELOG v\d+\.\d+/, so both parts are
            # guaranteed to be digits by the time we get here.
            major_version=int(major),
            minor_version=int(minor),
            src_line=_line_of(magic),
        )

    def pindef(self, children: list[Any]) -> PinDef:
        name_token, direction_token = children[0], children[1]
        return PinDef(
            name=str(name_token),
            direction=PinDirection(str(direction_token)),
        )

    def prologue(
        self, children: list[Any]
    ) -> tuple[ParsedHeader, tuple[PinDef, ...]]:
        header = next(c for c in children if isinstance(c, ParsedHeader))
        # Every declaration is kept, duplicates included: "duplicate PINDEF,
        # first wins" is an M7 warning, and de-duplicating here would destroy
        # the evidence that there was anything to warn about.
        pins = tuple(c for c in children if isinstance(c, PinDef))
        return header, pins

    # -- pin events --

    def drive(self, children: list[Any]) -> DriveEvent:
        name_token, state_token = children[0], children[1]
        return DriveEvent(pin=str(name_token), state=state_of(str(state_token)))

    def compare(self, children: list[Any]) -> LocatedCompare:
        name_token = children[0]
        expected_token, actual_token, result_token = (
            children[1],
            children[2],
            children[3],
        )
        return LocatedCompare(
            event=CompareEvent(
                pin=str(name_token),
                expected=state_of(str(expected_token)),
                actual=state_of(str(actual_token)),
                # The tester's flag is stored VERBATIM, never derived from the
                # states: it decides failure membership (§4 authority policy).
                passed=str(result_token) == "PASS",
            ),
            src_line=_line_of(name_token),
        )

    def pin_event(self, children: list[Any]) -> DriveEvent | LocatedCompare:
        event = children[0]
        assert isinstance(event, (DriveEvent, LocatedCompare))
        return event

    # -- cycles --

    def cycle(self, children: list[Any]) -> ParsedCycle:
        vector_token, time_token = children[0], children[1]
        drives = tuple(c for c in children if isinstance(c, DriveEvent))
        located = [c for c in children if isinstance(c, LocatedCompare)]
        return ParsedCycle(
            cycle=Cycle(
                vector=int(str(vector_token)),
                time=int(str(time_token).removeprefix("T=")),
                drives=drives,
                compares=tuple(lc.event for lc in located),
                src_line=_line_of(vector_token),
                # ADF-1 v1 has no TIMESET record, so the chain always begins at
                # None and terminates in the NRZ idealization. The field exists
                # now so a future FORMAT/TIMESET extension is data, not an IR
                # change (§6.3).
                timeset=None,
            ),
            compare_lines=tuple(lc.src_line for lc in located),
        )

    def cycle_batch(self, children: list[Any]) -> tuple[ParsedCycle, ...]:
        return tuple(c for c in children if isinstance(c, ParsedCycle))

    # -- block framing --

    def testblock_header(self, children: list[Any]) -> tuple[str, int]:
        block_token = children[0]
        return str(block_token), _line_of(block_token)

    def vector_list(self, children: list[Any]) -> tuple[int, ...]:
        return tuple(int(str(c)) for c in children)

    def failsummary(self, children: list[Any]) -> tuple[int, tuple[int, ...]]:
        count_token = children[0]
        vectors = next(c for c in children if isinstance(c, tuple))
        return int(str(count_token)), vectors

    def block_trailer(
        self, children: list[Any]
    ) -> tuple[int | None, tuple[int, ...]]:
        for child in children:
            if isinstance(child, tuple) and len(child) == 2:
                count, vectors = child
                if isinstance(count, int):
                    return count, vectors
        return None, ()  # FAILSUMMARY is optional (a zero-failure block)

    def testblock(self, children: list[Any]) -> ParsedBlock:
        (name, src_line) = children[0]
        cycles = children[1]
        declared_count, declared_vectors = children[2]
        return ParsedBlock(
            name=name,
            src_line=src_line,
            cycles=cycles,
            declared_fail_count=declared_count,
            declared_fail_vectors=declared_vectors,
        )

    def end_log(self, children: list[Any]) -> None:
        return None

    def document(self, children: list[Any]) -> ParsedDocument:
        header, pins = children[0]
        blocks = tuple(c for c in children if isinstance(c, ParsedBlock))
        return ParsedDocument(header=header, pins=pins, blocks=blocks)


# --- assembly ----------------------------------------------------------------


def assign_block_ids(names: list[str]) -> list[BlockId]:
    """Number repeated block names into distinct invocations, document order.

    Real flows legally re-run the same pattern (retest loops, corner re-runs),
    so the name alone is not unique — this is what keeps two invocations of
    ``mbist_march_c`` from collapsing into one.
    """
    counts: dict[str, int] = {}
    ids: list[BlockId] = []
    for name in names:
        counts[name] = counts.get(name, 0) + 1
        ids.append(BlockId(name=name, occurrence=counts[name]))
    return ids


def _cycle_periods(cycles: tuple[ParsedCycle, ...]) -> list[int]:
    """Local period per cycle, from the neighbouring cycles' time deltas.

    Resolved here because ``Cycle`` objects are discarded after assembly and
    the renderer must never need them (§6.2 — ``cycle_period`` drives mismatch
    band width).

    Each neighbouring delta is **normalized by the vector distance** it spans,
    then the smallest candidate wins.  Adjacent cycles span one vector, so the
    normalization is a no-op there and this reduces to the raw delta.

    Normalizing is what makes a *capture gap* safe.  A log that jumps between
    distant failure windows (``multi_fail`` goes 1202 -> 4400) would otherwise
    hand the cycle at the gap edge a "period" of the whole jump, and §6.2 sizes
    the mismatch band as a fraction of ``cycle_period`` — painting a band
    thousands of times too wide.  Taking the smallest *raw* neighbouring delta
    fixes that only while some other cycle sits nearby; with a block holding
    just two captured failures (vectors 1 and 100) BOTH neighbours span the gap
    and the raw minimum is still the whole jump.  Dividing by the vector
    distance removes the dependence on a nearby cycle existing: 100000 ns over
    99 vectors is a 1010 ns period, which is an estimate rather than a
    measurement, but an estimate of the right magnitude.

    A block with a single cycle has no delta at all and gets
    ``UNKNOWN_CYCLE_PERIOD``; the renderer clamps band width to a pixel
    minimum, so that degrades to the minimum band rather than to an invention.
    """
    times = [pc.cycle.time for pc in cycles]
    vectors = [pc.cycle.vector for pc in cycles]
    periods: list[int] = []

    for i in range(len(times)):
        candidates: list[int] = []
        for j in (i - 1, i + 1):
            if not 0 <= j < len(times):
                continue
            time_span = abs(times[j] - times[i])
            vector_span = abs(vectors[j] - vectors[i])
            if time_span > 0 and vector_span > 0:
                candidates.append(time_span // vector_span)
        # A per-vector period that floors to zero is not a usable period.
        usable = [c for c in candidates if c > 0]
        periods.append(min(usable) if usable else UNKNOWN_CYCLE_PERIOD)
    return periods


def assemble_run(
    document: ParsedDocument,
    timing_sets: tuple[TimingSet, ...] = (),
    warnings: tuple[str, ...] = (),
) -> TestRun:
    """``ParsedDocument`` -> ``TestRun``, resolving timing at assembly time.

    The three wave collections are left empty here: building them is Phase 2
    milestone 3.  Everything the failure table and clustering need is populated.
    """
    # First declaration wins, matching M7's deterministic duplicate-PINDEF rule.
    # A plain dict comprehension would silently keep the LAST, quietly
    # disagreeing with the warning the validator emits about the same log.
    pins_by_name: dict[str, PinDef] = {}
    for pin in document.pins:
        pins_by_name.setdefault(pin.name, pin)

    block_ids = assign_block_ids([b.name for b in document.blocks])

    failures: list[FailureEvent] = []
    blocks: list[TestBlockResult] = []

    for block_id, parsed in zip(block_ids, document.blocks):
        periods = _cycle_periods(parsed.cycles)
        block_fail_count = 0

        for period, parsed_cycle in zip(periods, parsed.cycles):
            cycle = parsed_cycle.cycle
            location = VectorLocation(
                block=block_id, vector=cycle.vector, time=cycle.time
            )
            for compare, src_line in zip(
                cycle.compares, parsed_cycle.compare_lines
            ):
                category = FailCategory.classify(
                    compare.expected, compare.actual, not compare.passed
                )
                if category is None:
                    continue  # a passing compare is not a failure

                # §6.3 timing chain, evaluated ONCE, here, while cycles exist.
                # The renderer consumes only the resolved values below.
                timing = resolve_timing(
                    pin=compare.pin,
                    timeset_name=cycle.timeset,
                    timing_sets=timing_sets,
                    pindef=pins_by_name.get(compare.pin),
                )
                failures.append(
                    FailureEvent(
                        location=location,
                        pin=compare.pin,
                        strobe_time=_strobe_time(cycle.time, timing),
                        cycle_period=period,
                        expected=compare.expected,
                        actual=compare.actual,
                        category=category,
                        src_line=src_line,
                        strobe_window=(
                            timing.strobe_window if timing else None
                        ),
                    )
                )
                block_fail_count += 1

        vectors = [pc.cycle.vector for pc in parsed.cycles]
        blocks.append(
            TestBlockResult(
                id=block_id,
                first_vector=vectors[0] if vectors else 0,
                last_vector=vectors[-1] if vectors else 0,
                fail_count=block_fail_count,
                declared_fail_vectors=parsed.declared_fail_vectors,
            )
        )

    return TestRun(
        header=document.header.header,
        pins=document.pins,
        blocks=tuple(blocks),
        failures=tuple(failures),
        # Phase 2 M3 populates these; empty is honest, not a placeholder.
        driven_waves=(),
        expected_waves=(),
        captured_waves=(),
        timing_sets=timing_sets,
        warnings=warnings,
    )


def _strobe_time(cycle_time: int, timing: PinTiming | None) -> int:
    """Resolved strobe instant: cycle start + strobe offset.

    Equals the cycle time exactly when no timing is known — the NRZ
    idealization ADF-1 v1 renders and documents.
    """
    if timing is None or timing.strobe is None:
        return cycle_time
    return cycle_time + timing.strobe
