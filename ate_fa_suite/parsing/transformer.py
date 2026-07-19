"""Transform Lark trees into validated application dataclasses."""

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

# A single retained cycle has no measurable period.
UNKNOWN_CYCLE_PERIOD: Final = 0


# These wrappers keep source lines available until validation finishes.


@dataclass(frozen=True, slots=True)
class LocatedPinDef:
    definition: PinDef
    src_line: int


@dataclass(frozen=True, slots=True)
class LocatedDrive:
    event: DriveEvent
    src_line: int


@dataclass(frozen=True, slots=True)
class LocatedCompare:
    event: CompareEvent
    src_line: int


LocatedEvent = LocatedDrive | LocatedCompare


@dataclass(frozen=True, slots=True)
class ParsedCycle:
    cycle: Cycle
    compare_lines: tuple[int, ...]  # parallel to cycle.compares
    events: tuple[LocatedEvent, ...]  # original order, used by validation


@dataclass(frozen=True, slots=True)
class ParsedBlock:
    name: str
    src_line: int
    cycles: tuple[ParsedCycle, ...]
    declared_fail_count: int | None
    declared_fail_vectors: tuple[int, ...]
    failsummary_line: int | None = None
    indexed_first_vector: int | None = None
    indexed_last_vector: int | None = None


@dataclass(frozen=True, slots=True)
class ParsedHeader:
    header: LogHeader
    magic: str
    major_version: int
    minor_version: int
    src_line: int


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    header: ParsedHeader
    pins: tuple[PinDef, ...]
    blocks: tuple[ParsedBlock, ...]
    pin_lines: tuple[int, ...] = ()  # parallel to pins


# --- helpers -----------------------------------------------------------------


def _line_of(token: Token) -> int:
    """Return the source line recorded by Lark."""
    return token.line or 0


def state_of(token: str) -> LogicState:
    """Map a raw ``STATE`` terminal to its ``LogicState`` member."""
    return LogicState(token)


# --- transformer -------------------------------------------------------------
#
# Child lists contain both tokens and values from earlier transformer methods.


@dataclass(frozen=True, slots=True)
class MetaEntry:
    key: str
    value: str
    src_line: int


@dataclass(frozen=True, slots=True)
class ParsedFailSummary:
    count: int
    vectors: tuple[int, ...]
    src_line: int


class AteLogTransformer(Transformer[Token, ParsedDocument]):
    """Parse tree -> ``ParsedDocument``.  One method per grammar rule."""

    # -- prologue --

    def meta(self, children: list[Any]) -> MetaEntry:
        key_token, value_token = children[0], children[1]
        # META_KEY includes ":" and VALUE can include leading whitespace.
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
            major_version=int(major),
            minor_version=int(minor),
            src_line=_line_of(magic),
        )

    def pindef(self, children: list[Any]) -> LocatedPinDef:
        name_token, direction_token = children[0], children[1]
        return LocatedPinDef(
            definition=PinDef(
                name=str(name_token),
                direction=PinDirection(str(direction_token)),
            ),
            src_line=_line_of(name_token),
        )

    def prologue(
        self, children: list[Any]
    ) -> tuple[ParsedHeader, tuple[PinDef, ...], tuple[int, ...]]:
        header = next(c for c in children if isinstance(c, ParsedHeader))
        located = [c for c in children if isinstance(c, LocatedPinDef)]
        return (
            header,
            tuple(item.definition for item in located),
            tuple(item.src_line for item in located),
        )

    # -- pin events --

    def drive(self, children: list[Any]) -> LocatedDrive:
        name_token, state_token = children[0], children[1]
        return LocatedDrive(
            event=DriveEvent(
                pin=str(name_token), state=state_of(str(state_token))
            ),
            src_line=_line_of(name_token),
        )

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
                # Keep the tester's result; validation reports contradictions.
                passed=str(result_token) == "PASS",
            ),
            src_line=_line_of(name_token),
        )

    def pin_event(self, children: list[Any]) -> LocatedEvent:
        event = children[0]
        assert isinstance(event, (LocatedDrive, LocatedCompare))
        return event

    # -- cycles --

    def cycle(self, children: list[Any]) -> ParsedCycle:
        vector_token, time_token = children[0], children[1]
        events = tuple(
            c for c in children if isinstance(c, (LocatedDrive, LocatedCompare))
        )
        drives = tuple(
            item.event for item in events if isinstance(item, LocatedDrive)
        )
        compares = tuple(
            item.event for item in events if isinstance(item, LocatedCompare)
        )
        compare_lines = tuple(
            item.src_line for item in events if isinstance(item, LocatedCompare)
        )
        return ParsedCycle(
            cycle=Cycle(
                vector=int(str(vector_token)),
                time=int(str(time_token).removeprefix("T=")),
                drives=drives,
                compares=compares,
                src_line=_line_of(vector_token),
                timeset=None,
            ),
            compare_lines=compare_lines,
            events=events,
        )

    def cycle_batch(self, children: list[Any]) -> tuple[ParsedCycle, ...]:
        return tuple(c for c in children if isinstance(c, ParsedCycle))

    # -- block framing --

    def testblock_header(self, children: list[Any]) -> tuple[str, int]:
        block_token = children[0]
        return str(block_token), _line_of(block_token)

    def vector_list(self, children: list[Any]) -> tuple[int, ...]:
        return tuple(int(str(c)) for c in children)

    def failsummary(self, children: list[Any]) -> ParsedFailSummary:
        count_token = children[0]
        vectors = next(c for c in children if isinstance(c, tuple))
        return ParsedFailSummary(
            count=int(str(count_token)),
            vectors=vectors,
            src_line=_line_of(count_token),
        )

    def block_trailer(
        self, children: list[Any]
    ) -> tuple[int | None, tuple[int, ...], int | None]:
        for child in children:
            if isinstance(child, ParsedFailSummary):
                return child.count, child.vectors, child.src_line
        return None, (), None

    def testblock(self, children: list[Any]) -> ParsedBlock:
        (name, src_line) = children[0]
        cycles = children[1]
        declared_count, declared_vectors, summary_line = children[2]
        return ParsedBlock(
            name=name,
            src_line=src_line,
            cycles=cycles,
            declared_fail_count=declared_count,
            declared_fail_vectors=declared_vectors,
            failsummary_line=summary_line,
        )

    def end_log(self, children: list[Any]) -> None:
        return None

    def document(self, children: list[Any]) -> ParsedDocument:
        header, pins, pin_lines = children[0]
        blocks = tuple(c for c in children if isinstance(c, ParsedBlock))
        return ParsedDocument(
            header=header, pins=pins, blocks=blocks, pin_lines=pin_lines
        )


# --- assembly ----------------------------------------------------------------


def assign_block_ids(names: list[str]) -> list[BlockId]:
    """Number repeated block names in document order."""
    counts: dict[str, int] = {}
    ids: list[BlockId] = []
    for name in names:
        counts[name] = counts.get(name, 0) + 1
        ids.append(BlockId(name=name, occurrence=counts[name]))
    return ids


def _cycle_periods(cycles: tuple[ParsedCycle, ...]) -> list[int]:
    """Estimate each cycle period from its retained neighbours.

    Normalize by vector distance so a discarded range does not look like one
    unusually long cycle.
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
        usable = [c for c in candidates if c > 0]
        periods.append(min(usable) if usable else UNKNOWN_CYCLE_PERIOD)
    return periods


def assemble_run(
    document: ParsedDocument,
    timing_sets: tuple[TimingSet, ...] = (),
    warnings: tuple[str, ...] = (),
) -> TestRun:
    """Build the immutable run while cycle timing is still available."""
    # setdefault keeps the first declaration.
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
        first_vector = (
            parsed.indexed_first_vector
            if parsed.indexed_first_vector is not None
            else (vectors[0] if vectors else 0)
        )
        last_vector = (
            parsed.indexed_last_vector
            if parsed.indexed_last_vector is not None
            else (vectors[-1] if vectors else 0)
        )
        blocks.append(
            TestBlockResult(
                id=block_id,
                first_vector=first_vector,
                last_vector=last_vector,
                fail_count=block_fail_count,
                declared_fail_vectors=parsed.declared_fail_vectors,
            )
        )

    return TestRun(
        header=document.header.header,
        pins=document.pins,
        blocks=tuple(blocks),
        failures=tuple(failures),
        # Waveform construction is implemented in Phase 2.
        driven_waves=(),
        expected_waves=(),
        captured_waves=(),
        timing_sets=timing_sets,
        warnings=warnings,
    )


def _strobe_time(cycle_time: int, timing: PinTiming | None) -> int:
    """Return cycle time plus an optional strobe offset."""
    if timing is None or timing.strobe is None:
        return cycle_time
    return cycle_time + timing.strobe
