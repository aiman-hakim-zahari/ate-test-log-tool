"""Apply semantic rules that are clearer outside the grammar."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

from ate_fa_suite.model.entities import (
    CompareEvent,
    Cycle,
    DriveEvent,
    FailCategory,
    LogicState,
    PinDef,
    PinDirection,
)

if TYPE_CHECKING:
    from ate_fa_suite.parsing.transformer import ParsedDocument

REQUIRED_META_KEYS: Final[tuple[str, ...]] = (
    "LOT",
    "WAFER",
    "DEVICE",
    "TESTER",
    "PROGRAM",
    "DATE",
    "TIMESCALE",
)

TIMESCALE_UNITS_NS: Final[dict[str, float]] = {
    "ps": 0.001,
    "ns": 1.0,
    "us": 1000.0,
    "ms": 1_000_000.0,
}

SUPPORTED_MAJOR_VERSIONS: Final[frozenset[int]] = frozenset({1})

RESERVED_IDENTIFIERS: Final[frozenset[str]] = frozenset(
    {
        "ATELOG",
        "PINDEF",
        "TESTBLOCK",
        "CYCLE",
        "PIN",
        "DRV",
        "EXP",
        "GOT",
        "PASS",
        "FAIL",
        "FAILSUMMARY",
        "VECTORS",
        "END",
        "LOG",
        "IN",
        "OUT",
        "IO",
    }
)


class ValidationError(Exception):
    """A fatal log problem with its source position."""

    def __init__(self, message: str, line: int, column: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """The normalized document and recoverable warnings."""

    document: ParsedDocument
    warnings: tuple[str, ...] = ()


def parse_timescale(value: str, src_line: int = 0) -> float:
    """Convert a timescale such as ``1ns`` to nanoseconds."""
    text = value.strip()
    for suffix, factor in TIMESCALE_UNITS_NS.items():
        if text.endswith(suffix):
            magnitude = text[: -len(suffix)].strip()
            try:
                scale = float(magnitude)
            except ValueError:
                raise ValidationError(
                    f"TIMESCALE magnitude {magnitude!r} is not a number", src_line
                ) from None
            if not math.isfinite(scale):
                raise ValidationError(
                    f"TIMESCALE must be a finite number, got {text!r}", src_line
                )
            if scale <= 0:
                raise ValidationError(
                    f"TIMESCALE must be positive, got {text!r}", src_line
                )
            return scale * factor
    raise ValidationError(
        f"TIMESCALE {text!r} has no recognized unit "
        f"({', '.join(TIMESCALE_UNITS_NS)})",
        src_line,
    )


def _warning(line: int, message: str) -> str:
    return f"line {line}: {message}"


def validate(document: ParsedDocument) -> ValidationReport:
    """Reject fatal problems and normalize recoverable ones."""
    from ate_fa_suite.parsing.transformer import (
        LocatedCompare,
        LocatedDrive,
        ParsedCycle,
    )

    if document.header.major_version not in SUPPORTED_MAJOR_VERSIONS:
        raise ValidationError(
            f"unsupported ADF major version {document.header.major_version}",
            document.header.src_line,
        )

    warnings: list[str] = []
    pins: list[PinDef] = []
    pin_lines: list[int] = []
    declared: set[str] = set()

    for index, pin in enumerate(document.pins):
        line = (
            document.pin_lines[index]
            if index < len(document.pin_lines)
            else document.header.src_line
        )
        if pin.name.upper() in RESERVED_IDENTIFIERS:
            warnings.append(
                _warning(line, f"reserved identifier {pin.name!r} used as a pin")
            )
        if pin.name in declared:
            warnings.append(
                _warning(
                    line,
                    f"duplicate PINDEF {pin.name!r}; keeping the first declaration",
                )
            )
            continue
        declared.add(pin.name)
        pins.append(pin)
        pin_lines.append(line)

    normalized_blocks = []
    for block in document.blocks:
        previous_vector: int | None = None
        previous_time: int | None = None
        normalized_cycles: list[ParsedCycle] = []

        for parsed_cycle in block.cycles:
            cycle = parsed_cycle.cycle
            if previous_vector is not None and cycle.vector <= previous_vector:
                raise ValidationError(
                    "cycle vectors must be strictly increasing within a block",
                    cycle.src_line,
                )
            if previous_time is not None and cycle.time <= previous_time:
                raise ValidationError(
                    "cycle times must be strictly increasing within a block",
                    cycle.src_line,
                )
            previous_vector = cycle.vector
            previous_time = cycle.time

            seen_events: set[str] = set()
            kept_events = []
            drives: list[DriveEvent] = []
            compares: list[CompareEvent] = []
            compare_lines: list[int] = []

            for located in parsed_cycle.events:
                event = located.event
                line = located.src_line

                if event.pin.upper() in RESERVED_IDENTIFIERS:
                    warnings.append(
                        _warning(
                            line,
                            f"reserved identifier {event.pin!r} used as a pin",
                        )
                    )
                if event.pin not in declared:
                    declared.add(event.pin)
                    pins.append(PinDef(event.pin, PinDirection.IO))
                    pin_lines.append(line)
                    warnings.append(
                        _warning(
                            line,
                            f"undeclared pin {event.pin!r}; added as IO",
                        )
                    )
                if event.pin in seen_events:
                    warnings.append(
                        _warning(
                            line,
                            f"duplicate event for pin {event.pin!r} in "
                            f"cycle {cycle.vector}; keeping the first",
                        )
                    )
                    continue

                seen_events.add(event.pin)
                kept_events.append(located)
                if isinstance(located, LocatedDrive):
                    drives.append(located.event)
                elif isinstance(located, LocatedCompare):
                    compare = located.event
                    compares.append(compare)
                    compare_lines.append(line)
                    category = FailCategory.classify(
                        compare.expected, compare.actual, not compare.passed
                    )
                    if category is FailCategory.INCONSISTENT:
                        warnings.append(
                            _warning(
                                line,
                                "FAIL flag contradicts the compare states",
                            )
                        )
                    elif (
                        compare.passed
                        and compare.expected is not LogicState.UNKNOWN
                        and compare.expected is not compare.actual
                    ):
                        warnings.append(
                            _warning(
                                line,
                                "PASS flag disagrees with the compare states",
                            )
                        )

            normalized_cycles.append(
                ParsedCycle(
                    cycle=Cycle(
                        vector=cycle.vector,
                        time=cycle.time,
                        drives=tuple(drives),
                        compares=tuple(compares),
                        src_line=cycle.src_line,
                        timeset=cycle.timeset,
                    ),
                    compare_lines=tuple(compare_lines),
                    events=tuple(kept_events),
                )
            )

        observed_count = 0
        observed_vectors: set[int] = set()
        for parsed_cycle in normalized_cycles:
            for compare in parsed_cycle.cycle.compares:
                if not compare.passed:
                    observed_count += 1
                    observed_vectors.add(parsed_cycle.cycle.vector)

        if block.declared_fail_count is not None:
            line = block.failsummary_line or block.src_line
            if block.declared_fail_count != observed_count:
                warnings.append(
                    _warning(
                        line,
                        "FAILSUMMARY count "
                        f"{block.declared_fail_count} does not match "
                        f"{observed_count} failing compare lines",
                    )
                )
            declared_vectors = set(block.declared_fail_vectors)
            if declared_vectors != observed_vectors:
                warnings.append(
                    _warning(
                        line,
                        "FAILSUMMARY VECTORS "
                        f"{tuple(sorted(declared_vectors))} does not match "
                        f"{tuple(sorted(observed_vectors))}",
                    )
                )

        normalized_blocks.append(
            replace(block, cycles=tuple(normalized_cycles))
        )

    normalized = replace(
        document,
        pins=tuple(pins),
        pin_lines=tuple(pin_lines),
        blocks=tuple(normalized_blocks),
    )
    return ValidationReport(document=normalized, warnings=tuple(warnings))
