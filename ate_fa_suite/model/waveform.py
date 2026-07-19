"""Waveform lookup and the Phase 2 waveform builder."""

from __future__ import annotations

from bisect import bisect_left
from typing import Final

from ate_fa_suite.model.entities import (
    BlockId,
    Cycle,
    FailureEvent,
    PinDef,
    PinTiming,
    TimingSet,
    WaveformSeries,
)

# Default number of vectors kept on each side of a failure.
DEFAULT_RETENTION_W: Final = 32


def wave_for(
    waves: tuple[WaveformSeries, ...], block: BlockId, pin: str
) -> WaveformSeries | None:
    """Find one series in a tuple sorted by ``(block, pin)``."""
    key = (block, pin)
    i = bisect_left(waves, key, key=lambda w: (w.block, w.pin))
    if i < len(waves) and (waves[i].block, waves[i].pin) == key:
        return waves[i]
    return None


def resolve_timing(
    pin: str,
    timeset_name: str | None,
    timing_sets: tuple[TimingSet, ...],
    pindef: PinDef | None,
) -> PinTiming | None:
    """Resolve timeset timing first, then fall back to the pin default."""
    if timeset_name is not None:
        for timing_set in timing_sets:
            if timing_set.name != timeset_name:
                continue
            for entry_pin, timing in timing_set.entries:
                if entry_pin == pin:
                    return timing
            break  # This timeset has no value for the pin; use its default.
    return pindef.timing if pindef is not None else None


def build_waves(
    block: BlockId,
    cycles: tuple[Cycle, ...],
    pins: tuple[PinDef, ...],
    failures: tuple[FailureEvent, ...],
    timing_sets: tuple[TimingSet, ...] = (),
    retention_w: int = DEFAULT_RETENTION_W,
) -> tuple[
    tuple[WaveformSeries, ...],
    tuple[WaveformSeries, ...],
    tuple[WaveformSeries, ...],
]:
    """Build the driven, expected, and captured series for one block."""
    raise NotImplementedError("Phase 2 M3 — see docs/ROADMAP.md")
