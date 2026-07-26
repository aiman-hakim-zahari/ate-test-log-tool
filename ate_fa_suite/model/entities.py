"""Immutable domain objects shared by the parser, model, and UI."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from enum import Enum


class PinDirection(Enum):
    IN = "IN"
    OUT = "OUT"
    IO = "IO"


class LogicState(Enum):
    """The VCD / IEEE-1364 logic alphabet used by ADF-1."""

    LOW = "0"
    HIGH = "1"
    UNKNOWN = "X"
    HIGH_Z = "Z"
    WEAK_LOW = "L"
    WEAK_HIGH = "H"


class FailCategory(Enum):
    """Failure categories inferred from one compare record."""

    SA0_CANDIDATE = "stuck-at-0 candidate"  # expected 1, captured 0
    SA1_CANDIDATE = "stuck-at-1 candidate"  # expected 0, captured 1
    FLOATING = "floating / contention"  # captured X
    OPEN_TRISTATE = "open / tri-state"  # captured Z
    WEAK_DRIVE = "weak drive"  # captured L or H
    OTHER = "other mismatch"  # genuine fail, no canonical bucket
    INCONSISTENT = "inconsistent record"  # FAIL flag contradicts the states

    @staticmethod
    def classify(
        expected: LogicState, actual: LogicState, failed: bool
    ) -> FailCategory | None:
        """Use the result flag for membership and states for the category."""
        if not failed:
            return None
        if expected is LogicState.UNKNOWN:  # masked compare cannot fail
            return FailCategory.INCONSISTENT
        if expected is actual:  # FAIL flag, yet states agree
            return FailCategory.INCONSISTENT
        if actual is LogicState.UNKNOWN:
            return FailCategory.FLOATING
        if actual is LogicState.HIGH_Z:
            return FailCategory.OPEN_TRISTATE
        if actual in (LogicState.WEAK_LOW, LogicState.WEAK_HIGH):
            return FailCategory.WEAK_DRIVE
        if expected is LogicState.HIGH and actual is LogicState.LOW:
            return FailCategory.SA0_CANDIDATE
        if expected is LogicState.LOW and actual is LogicState.HIGH:
            return FailCategory.SA1_CANDIDATE
        return FailCategory.OTHER  # e.g. expected Z/L/H, captured strong 0/1


@dataclass(frozen=True, slots=True)
class LogHeader:
    lot: str
    wafer: str
    device: str
    tester: str
    program: str
    date: str
    timescale_ns: float


class WaveShape(Enum):
    NRZ = "NRZ"
    RZ = "RZ"
    RO = "RO"
    SBC = "SBC"


@dataclass(frozen=True, slots=True)
class PinTiming:
    """Optional pin timing offsets, measured from the cycle start."""

    shape: WaveShape = WaveShape.NRZ
    drive_on: int | None = None  # d1: drive edge offset
    drive_off: int | None = None  # d2: return edge (RZ/RO/SBC)
    strobe: int | None = None  # compare strobe offset (edge strobe)
    strobe_window: int | None = None  # window-strobe width, if windowed


@dataclass(frozen=True, slots=True)
class PinDef:
    name: str
    direction: PinDirection
    timing: PinTiming | None = None  # run-default; absent => NRZ idealization


@dataclass(frozen=True, slots=True)
class TimingSet:
    """A named, sorted collection of per-pin timing values."""

    name: str
    entries: tuple[tuple[str, PinTiming], ...]  # (pin, timing), sorted

    def __post_init__(self) -> None:
        pins = [p for p, _ in self.entries]
        if pins != sorted(pins) or len(set(pins)) != len(pins):
            raise ValueError("timing entries must be sorted, unique by pin")


@dataclass(frozen=True, slots=True)
class DriveEvent:
    pin: str
    state: LogicState


@dataclass(frozen=True, slots=True)
class CompareEvent:
    pin: str
    expected: LogicState
    actual: LogicState
    passed: bool


@dataclass(frozen=True, slots=True)
class Cycle:
    vector: int
    time: int  # time in native timescale units
    drives: tuple[DriveEvent, ...]
    compares: tuple[CompareEvent, ...]
    src_line: int
    timeset: str | None = None  # TimingSet reference (§6.3 chain)


@dataclass(frozen=True, slots=True, order=True)
class BlockId:
    """A block name plus its 1-based occurrence in the log."""

    name: str
    occurrence: int  # 1-based, document order

    def label(self) -> str:  # UI display: "mbist_march_c" / "mbist_march_c#2"
        return (
            self.name
            if self.occurrence == 1
            else f"{self.name}#{self.occurrence}"
        )


@dataclass(frozen=True, slots=True)
class VectorLocation:
    """A cycle address that remains unique when block values restart."""

    block: BlockId
    vector: int
    time: int  # invocation-local


@dataclass(frozen=True, slots=True)
class FailureEvent:
    """One failure-table row per failing compare."""

    location: VectorLocation
    pin: str
    strobe_time: int  # cycle time plus the resolved strobe offset
    cycle_period: int  # local estimate used for mismatch-band width
    expected: LogicState
    actual: LogicState
    category: FailCategory
    src_line: int
    strobe_window: int | None = None  # resolved PinTiming, if window-strobed


@dataclass(frozen=True, slots=True)
class FailSignature:
    block: BlockId  # buckets only comparable within one invocation
    pin_group: str  # bus-collapsed, e.g. "DQ[*]"
    category: FailCategory
    vector_bucket: int  # vector // SIGNATURE_BUCKET (default 100)


@dataclass(frozen=True, slots=True)
class SignatureCluster:
    signature: FailSignature
    count: int
    share: float
    members: tuple[FailureEvent, ...]


WaveKey = tuple[BlockId, str]
"""(block invocation, pin) — neither a pin name nor a block name alone is
unique run-wide."""


@dataclass(frozen=True, slots=True)
class WaveformSegment:
    """One retained interval of a pin's waveform history."""

    t_start: int  # inclusive, block-local
    t_end: int  # inclusive, block-local
    times: tuple[int, ...]  # ascending; invariant: times[0] == t_start
    states: tuple[LogicState, ...]  # parallel to times

    def __post_init__(self) -> None:
        # Validate here so every construction path follows the same rules.
        if not self.times or len(self.times) != len(self.states):
            raise ValueError("segment arrays empty or not parallel")
        if (
            self.t_start > self.t_end
            or self.times[0] != self.t_start
            or self.times[-1] > self.t_end
        ):
            raise ValueError("segment bounds violated")
        if any(a >= b for a, b in zip(self.times, self.times[1:])):
            raise ValueError("times not strictly ascending")

    def state_at(self, t: int) -> LogicState:
        return self.states[bisect_right(self.times, t) - 1]  # t within bounds

    def clipped(self, t0: int, t1: int) -> WaveformSegment | None:
        """Intersection with ``[t0, t1]``; carry-in state re-anchored so the
        ``times[0] == t_start`` invariant survives clipping."""
        if t1 < self.t_start or t0 > self.t_end:
            return None
        start, end = max(t0, self.t_start), min(t1, self.t_end)
        lo = bisect_right(self.times, start) - 1
        hi = bisect_right(self.times, end)
        return WaveformSegment(
            start,
            end,
            (start,) + self.times[lo + 1 : hi],
            self.states[lo:hi],
        )


@dataclass(frozen=True, slots=True)
class WaveformSeries:
    """A pin's sorted, non-overlapping retained waveform segments."""

    block: BlockId
    pin: str
    segments: tuple[WaveformSegment, ...]

    def __post_init__(self) -> None:
        for a, b in zip(self.segments, self.segments[1:]):
            if a.t_end >= b.t_start:
                raise ValueError("segments must be sorted and disjoint")

    def state_at(self, t: int) -> LogicState | None:
        """None => not retained at ``t``: render as no-data, never extrapolate."""
        i = bisect_right(self.segments, t, key=lambda s: s.t_start) - 1
        if i < 0 or t > self.segments[i].t_end:
            return None
        return self.segments[i].state_at(t)

    def window(self, t0: int, t1: int) -> tuple[WaveformSegment, ...]:
        # Bisect narrows the search before clipping matching segments.
        lo = bisect_left(self.segments, t0, key=lambda s: s.t_end)
        hi = bisect_right(self.segments, t1, key=lambda s: s.t_start)
        return tuple(
            c
            for c in (s.clipped(t0, t1) for s in self.segments[lo:hi])
            if c is not None
        )  # overlap guaranteed; filter for typing


@dataclass(frozen=True, slots=True)
class TestBlockResult:
    id: BlockId
    first_vector: int
    last_vector: int
    fail_count: int
    declared_fail_vectors: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class TestRun:
    """Root aggregate handed from the parser thread to the ViewModel."""

    header: LogHeader
    pins: tuple[PinDef, ...]
    blocks: tuple[TestBlockResult, ...]
    failures: tuple[FailureEvent, ...]

    # Keep programmed, expected, and captured signals separate. Each tuple is
    # sorted by (block, pin) for binary-search lookup.
    driven_waves: tuple[WaveformSeries, ...]  # programmed stimulus (DRV)
    expected_waves: tuple[WaveformSeries, ...]  # what the program demanded
    captured_waves: tuple[WaveformSeries, ...]  # comparator captures (GOT)

    timing_sets: tuple[TimingSet, ...] = ()  # per-cycle timing (§6.3)
    warnings: tuple[str, ...] = ()
    """Log inconsistencies, each with source line: truncation salvage
    (Ph1 M6), FAILSUMMARY count mismatches, INCONSISTENT records, non-masked
    PASS with disagreeing states."""

    def __post_init__(self) -> None:
        for waves in (
            self.driven_waves,
            self.expected_waves,
            self.captured_waves,
        ):
            keys = [(w.block, w.pin) for w in waves]
            if keys != sorted(keys) or len(set(keys)) != len(keys):
                raise ValueError(
                    "wave tuples must be sorted, unique by WaveKey"
                )
        names = [ts.name for ts in self.timing_sets]
        if len(set(names)) != len(names):
            raise ValueError("timing-set names must be unique")


# The UI ignores queue messages whose job_id belongs to an older load.


@dataclass(frozen=True, slots=True)
class ParseProgress:
    job_id: int
    bytes_done: int
    bytes_total: int
    cycles: int
    fails: int


@dataclass(frozen=True, slots=True)
class ParseComplete:
    job_id: int
    run: TestRun
    elapsed_s: float


@dataclass(frozen=True, slots=True)
class ParseFailed:
    job_id: int
    line: int
    column: int
    message: str
    context: str
