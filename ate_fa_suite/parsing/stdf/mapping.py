"""Map STDF concepts onto the existing intermediate representation.

Nothing here invents a new model object.  ``BlockId``, ``VectorLocation``,
``FailureEvent``, ``WaveformSegment`` and ``FailCategory`` are reused as they
stand, and block numbering goes through the existing ``assign_block_ids()``.

The mapping is genuinely lossy in a few places, and each one is handled
explicitly rather than defaulted silently:

* **No timescale.**  STDF's axis is cycles, so ``timescale_ns`` is 1.0 with a
  warning and ``LogHeader.time_domain`` is ``"cycle"``.
* **Vector and time collapse** into one number, and ``cycle_period`` is 1
  rather than 0, because 0 would collapse the §6.2 mismatch band to zero
  width.  This is the defining Tier B coordinate convention.
* **``src_line`` is a record ordinal**, flagged by
  ``TestRun.source_format == "STDF-V4-2007"``.
* **A state that was never logged does not get classified.**
  ``FailCategory.classify(UNKNOWN, X, failed=True)`` returns ``INCONSISTENT``,
  which is right for ADF-1 (a masked compare cannot fail) and wrong here (the
  expectation was simply never written).  Those failures get
  ``FailCategory.OTHER``, whose docstring already reads "genuine fail, no
  canonical bucket".
* **A failure on a pin set in ``MASK_MAP`` is kept**, with a warning naming the
  pin.  Two witnesses disagreeing is the analogue of ADF-1's ``INCONSISTENT``
  and a fact the FA engineer needs, not a contradiction to resolve away.
* **``driven_waves`` is always empty.**  A production log carries no programmed
  stimulus, and synthesizing one would be a drawn lie.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Final

from ate_fa_suite.model.entities import (
    BlockId,
    FailCategory,
    FailureEvent,
    LogHeader,
    LogicState,
    PinDef,
    PinDirection,
    TestBlockResult,
    TestRun,
    VectorLocation,
    WaveformSegment,
    WaveformSeries,
)
from ate_fa_suite.parsing.stdf.codec import DecodedRecord
from ate_fa_suite.parsing.stdf.scan import ScanDataSet, warning
from ate_fa_suite.parsing.transformer import assign_block_ids

#: STDF has no time base, so one cycle is one unit on the axis.
CYCLE_TIMESCALE_NS: Final = 1.0

#: Not 0: a zero period would collapse the §6.2 mismatch band to no width.
CYCLE_PERIOD: Final = 1

#: What ``TestRun.source_format`` reads for a run this reader produced.
SOURCE_FORMAT: Final = "STDF-V4-2007"

#: What ``LogHeader.time_domain`` reads, so the Phase 4 ruler counts cycles
#: instead of labelling them nanoseconds.
TIME_DOMAIN: Final = "cycle"


@dataclass
class StdfContext:
    """What the record stream yielded, ready to become a ``TestRun``."""

    mir: DecodedRecord | None = None
    #: PMR_INDX -> resolved pin name.
    pins: dict[int, str] = field(default_factory=dict)
    #: PSR_INDX -> block name.
    psr_names: dict[int, str] = field(default_factory=dict)
    data_sets: list[ScanDataSet] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def pmr_pin_name(record: DecodedRecord, pmr_indx: int) -> str:
    """Resolve a pin name from a PMR, most specific name first."""
    for name in ("LOG_NAM", "PHY_NAM", "CHAN_NAM"):
        value = record.get_str(name)
        if value:
            return value
    return f"PMR{pmr_indx}"


def psr_block_name(record: DecodedRecord, psr_indx: int) -> str:
    """Resolve a block name from a PSR, most specific name first."""
    symbolic = record.get_str("PSR_NAM")
    if symbolic:
        return symbolic
    files = record.get_strs("PAT_FILE")
    if files:
        return files[0]
    return f"PSR{psr_indx}"


def build_header(mir: DecodedRecord | None) -> tuple[LogHeader, list[str]]:
    """Build the run header from the MIR, and say what had to be defaulted."""
    warnings = [
        "STDF measures its axis in tester cycles, not time; timescale is "
        "1 unit per cycle and the time ruler reads cycles"
    ]

    def text(name: str) -> str:
        return (mir.get_str(name) or "") if mir is not None else ""

    stamp = 0
    if mir is not None:
        stamp = mir.get_int("START_T") or mir.get_int("SETUP_T") or 0
    date = ""
    if stamp:
        date = datetime.fromtimestamp(stamp, timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )

    if mir is None:
        warnings.append("no MIR record; run header fields are empty")

    return (
        LogHeader(
            lot=text("LOT_ID"),
            # No WIR in this record subset, so wafer is genuinely unknown.
            wafer="",
            device=text("PART_TYP"),
            tester=text("NODE_NAM") or text("TSTR_TYP"),
            program=text("JOB_NAM"),
            date=date,
            timescale_ns=CYCLE_TIMESCALE_NS,
            time_domain=TIME_DOMAIN,
        ),
        warnings,
    )


def _fail_pin_name(
    context: StdfContext, pmr_indx: int | None, chain: int | None
) -> str | None:
    """Name the failing pin, or ``None`` if it cannot be named honestly."""
    if pmr_indx is not None:
        return context.pins.get(pmr_indx, f"PMR{pmr_indx}")
    if chain is not None:
        return f"chain_{chain}"  # PMR_INDX was absent; never invent a pin name
    return None


def _category(
    expected: LogicState | None, captured: LogicState | None
) -> FailCategory:
    """Classify a logged failure, or decline to when a state is missing."""
    if expected is None or captured is None:
        # classify() would read a missing expectation as a masked compare and
        # return INCONSISTENT.  Nothing here is inconsistent: it was not logged.
        return FailCategory.OTHER
    category = FailCategory.classify(expected, captured, failed=True)
    return category if category is not None else FailCategory.OTHER


def _coalesce(
    points: list[tuple[int, LogicState]],
) -> tuple[WaveformSegment, ...]:
    """Turn observed (cycle, state) points into disjoint segments.

    A run is extended only across **strictly consecutive** cycles carrying the
    same state, because every cycle in such a run was actually observed.  A gap
    ends the run: the renderer's existing retention-gap path then hatches it as
    no-data, which is what makes the fail-capture strip fall out for free.
    """
    segments: list[WaveformSegment] = []
    start, state = points[0]
    previous = start
    for cycle, next_state in points[1:]:
        if cycle == previous + 1 and next_state is state:
            previous = cycle
            continue
        segments.append(WaveformSegment(start, previous, (start,), (state,)))
        start, previous, state = cycle, cycle, next_state
    segments.append(WaveformSegment(start, previous, (start,), (state,)))
    return tuple(segments)


def _series(
    points: dict[tuple[BlockId, str], dict[int, LogicState]],
) -> tuple[WaveformSeries, ...]:
    """Build the sorted, unique-by-WaveKey tuple ``TestRun`` requires."""
    return tuple(
        WaveformSeries(
            block=block,
            pin=pin,
            segments=_coalesce(sorted(by_cycle.items())),
        )
        for (block, pin), by_cycle in sorted(points.items())
        if by_cycle
    )


def build_run(context: StdfContext) -> TestRun:
    """Assemble one ``TestRun`` from the collected STDF records."""
    header, warnings = build_header(context.mir)
    warnings = list(context.warnings) + warnings

    block_names = [
        context.psr_names.get(
            data_set.key.psr_ref,
            data_set.test_txt or f"test_{data_set.key.test_num}",
        )
        for data_set in context.data_sets
    ]
    block_ids = assign_block_ids(block_names)

    failures: list[FailureEvent] = []
    blocks: list[TestBlockResult] = []
    expected_points: dict[tuple[BlockId, str], dict[int, LogicState]] = {}
    captured_points: dict[tuple[BlockId, str], dict[int, LogicState]] = {}
    pin_names: dict[str, None] = {}

    for block_id, data_set in zip(block_ids, context.data_sets):
        warnings.extend(data_set.warnings)
        masked_reported: set[str] = set()
        unnamed = 0
        cycles: list[int] = []

        for scan_fail in data_set.fails:
            pin = _fail_pin_name(context, scan_fail.pmr_indx, scan_fail.chain)
            if pin is None:
                unnamed += 1
                continue

            if (
                scan_fail.pmr_indx is not None
                and data_set.mask_map is not None
                # The spec's 1st bit is PMR index 1, so the bit index is one less.
                and data_set.mask_map.is_set(scan_fail.pmr_indx - 1)
                and pin not in masked_reported
            ):
                masked_reported.add(pin)
                warnings.append(
                    warning(
                        data_set.ordinal,
                        f"pin {pin!r} is set in MASK_MAP yet has logged "
                        "failures; both witnesses kept",
                    )
                )

            pin_names.setdefault(pin, None)
            cycles.append(scan_fail.cycle)
            location = VectorLocation(
                block=block_id, vector=scan_fail.cycle, time=scan_fail.cycle
            )
            failures.append(
                FailureEvent(
                    location=location,
                    pin=pin,
                    # Vector and time are the same number on a cycle axis.
                    strobe_time=scan_fail.cycle,
                    cycle_period=CYCLE_PERIOD,
                    expected=scan_fail.expected or LogicState.UNKNOWN,
                    actual=scan_fail.captured or LogicState.UNKNOWN,
                    category=_category(scan_fail.expected, scan_fail.captured),
                    src_line=data_set.ordinal,
                    strobe_window=None,
                )
            )

            if scan_fail.expected is not None:
                expected_points.setdefault((block_id, pin), {}).setdefault(
                    scan_fail.cycle, scan_fail.expected
                )
            if scan_fail.captured is not None:
                captured_points.setdefault((block_id, pin), {}).setdefault(
                    scan_fail.cycle, scan_fail.captured
                )

        if unnamed:
            warnings.append(
                warning(
                    data_set.ordinal,
                    f"{unnamed} logged failures carry neither PMR_INDX nor "
                    "CHN_NUM and were skipped rather than given a made-up pin",
                )
            )

        blocks.append(
            TestBlockResult(
                id=block_id,
                first_vector=min(cycles) if cycles else 0,
                last_vector=max(cycles) if cycles else 0,
                fail_count=len(cycles),
                # STDF declares no per-vector fail list; TOTF/TOTL are the
                # cross-check and are already reported as warnings.
                declared_fail_vectors=(),
            )
        )

    return TestRun(
        header=header,
        # PMR carries no signal direction, so IO matches the convention the
        # ADF-1 validator already uses for an undeclared pin.
        pins=tuple(
            PinDef(name=pin, direction=PinDirection.IO) for pin in pin_names
        ),
        blocks=tuple(blocks),
        failures=tuple(failures),
        driven_waves=(),  # never synthesized; see this module's docstring
        expected_waves=_series(expected_points),
        captured_waves=_series(captured_points),
        timing_sets=(),
        warnings=tuple(warnings),
        source_format=SOURCE_FORMAT,
    )


__all__ = [
    "CYCLE_PERIOD",
    "CYCLE_TIMESCALE_NS",
    "SOURCE_FORMAT",
    "TIME_DOMAIN",
    "StdfContext",
    "build_header",
    "build_run",
    "pmr_pin_name",
    "psr_block_name",
]
