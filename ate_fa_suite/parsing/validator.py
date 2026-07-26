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
    from ate_fa_suite.parsing.transformer import (
        ParsedBlock,
        ParsedCycle,
        ParsedDocument,
        ParsedHeader,
    )

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


class StatefulValidator:
    """Normalize and validate one document in source order."""

    def __init__(self) -> None:
        self._warnings: list[str] = []
        self._pins: list[PinDef] = []
        self._pin_lines: list[int] = []
        self._declared: set[str] = set()
        self._block_active = False
        self._previous_vector: int | None = None
        self._previous_time: int | None = None
        self._observed_fail_count = 0
        self._observed_fail_vectors: set[int] = set()

    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(self._warnings)

    @property
    def pins(self) -> tuple[PinDef, ...]:
        return tuple(self._pins)

    @property
    def pin_lines(self) -> tuple[int, ...]:
        return tuple(self._pin_lines)

    def normalize_prologue(
        self,
        header: ParsedHeader,
        pins: tuple[PinDef, ...],
        pin_lines: tuple[int, ...],
    ) -> None:
        """Validate the header and apply first-wins PINDEF normalization."""
        if header.major_version not in SUPPORTED_MAJOR_VERSIONS:
            raise ValidationError(
                f"unsupported ADF major version {header.major_version}",
                header.src_line,
            )

        for index, pin in enumerate(pins):
            line = (
                pin_lines[index]
                if index < len(pin_lines)
                else header.src_line
            )
            if pin.name.upper() in RESERVED_IDENTIFIERS:
                self._warnings.append(
                    _warning(
                        line,
                        f"reserved identifier {pin.name!r} used as a pin",
                    )
                )
            if pin.name in self._declared:
                self._warnings.append(
                    _warning(
                        line,
                        f"duplicate PINDEF {pin.name!r}; "
                        "keeping the first declaration",
                    )
                )
                continue
            self._declared.add(pin.name)
            self._pins.append(pin)
            self._pin_lines.append(line)

    def begin_block(self) -> None:
        """Start a block invocation and reset its monotonic/summary state."""
        self._block_active = True
        self._previous_vector = None
        self._previous_time = None
        self._observed_fail_count = 0
        self._observed_fail_vectors.clear()

    def validate_cycle(self, parsed_cycle: ParsedCycle) -> ParsedCycle:
        """Validate and normalize one cycle without retaining prior cycles."""
        if not self._block_active:
            raise ValidationError(
                "cycle found outside a test block",
                parsed_cycle.cycle.src_line,
            )

        cycle = parsed_cycle.cycle
        if (
            self._previous_vector is not None
            and cycle.vector <= self._previous_vector
        ):
            raise ValidationError(
                "cycle vectors must be strictly increasing within a block",
                cycle.src_line,
            )
        if (
            self._previous_time is not None
            and cycle.time <= self._previous_time
        ):
            raise ValidationError(
                "cycle times must be strictly increasing within a block",
                cycle.src_line,
            )
        self._previous_vector = cycle.vector
        self._previous_time = cycle.time

        seen_events: set[str] = set()
        kept_events = []
        drives: list[DriveEvent] = []
        compares: list[CompareEvent] = []
        compare_lines: list[int] = []

        from ate_fa_suite.parsing.transformer import (
            LocatedCompare,
            LocatedDrive,
            ParsedCycle,
        )

        for located in parsed_cycle.events:
            event = located.event
            line = located.src_line

            if event.pin.upper() in RESERVED_IDENTIFIERS:
                self._warnings.append(
                    _warning(
                        line,
                        f"reserved identifier {event.pin!r} used as a pin",
                    )
                )
            if event.pin not in self._declared:
                self._declared.add(event.pin)
                self._pins.append(PinDef(event.pin, PinDirection.IO))
                self._pin_lines.append(line)
                self._warnings.append(
                    _warning(
                        line,
                        f"undeclared pin {event.pin!r}; added as IO",
                    )
                )
            if event.pin in seen_events:
                self._warnings.append(
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
                    self._warnings.append(
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
                    self._warnings.append(
                        _warning(
                            line,
                            "PASS flag disagrees with the compare states",
                        )
                    )

        normalized = ParsedCycle(
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
        failed = sum(not compare.passed for compare in compares)
        self._observed_fail_count += failed
        if failed:
            self._observed_fail_vectors.add(cycle.vector)
        return normalized

    def finalize_block(self, block: ParsedBlock) -> ParsedBlock:
        """Check both FAILSUMMARY claims and finish the block invocation."""
        if block.declared_fail_count is not None:
            line = block.failsummary_line or block.src_line
            if block.declared_fail_count != self._observed_fail_count:
                self._warnings.append(
                    _warning(
                        line,
                        "FAILSUMMARY count "
                        f"{block.declared_fail_count} does not match "
                        f"{self._observed_fail_count} failing compare lines",
                    )
                )
            declared_vectors = set(block.declared_fail_vectors)
            if declared_vectors != self._observed_fail_vectors:
                self._warnings.append(
                    _warning(
                        line,
                        "FAILSUMMARY VECTORS "
                        f"{tuple(sorted(declared_vectors))} does not match "
                        f"{tuple(sorted(self._observed_fail_vectors))}",
                    )
                )
        self._block_active = False
        return block


def validate(document: ParsedDocument) -> ValidationReport:
    """Adapt whole-document validation to the streaming validator."""
    from ate_fa_suite.parsing.transformer import (
        ParsedDocument,
    )

    validator = StatefulValidator()
    validator.normalize_prologue(
        document.header, document.pins, document.pin_lines
    )
    normalized_blocks = []
    for block in document.blocks:
        validator.begin_block()
        normalized_cycles = tuple(
            validator.validate_cycle(cycle) for cycle in block.cycles
        )
        normalized_blocks.append(
            validator.finalize_block(
                replace(block, cycles=normalized_cycles)
            )
        )

    normalized = replace(
        document,
        pins=validator.pins,
        pin_lines=validator.pin_lines,
        blocks=tuple(normalized_blocks),
    )
    return ValidationReport(document=normalized, warnings=validator.warnings)
