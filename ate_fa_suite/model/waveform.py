"""``WaveformSeries`` assembly and lookup (Phase 2 milestone 3).

The builder turns transient ``Cycle`` objects into three
**provenance-separated** series kinds per ``WaveKey`` — driven (from ``DRV``),
expected and captured (from compares) — which are *never merged*.  §6.3: a
``DRV`` record is programmed tester stimulus, continuously known by definition;
a ``GOT`` capture is a comparator observation that exists only at its strobe.
Carrying a captured value through a drive interval, or presenting stimulus as a
DUT observation, would fabricate data.

Retention: full fidelity only within +-W vectors of a failure.  Overlapping or
adjacent windows are merged into single ``WaveformSegment``s, and the gaps
between segments are **structural** — ``state_at`` returns ``None`` there and
the renderer hatches it.  A gap and a held state are different facts.

The §6.3 timing chain (``Cycle.timeset`` -> ``TimingSet`` entry -> ``PinDef.timing``
-> NRZ idealization) is resolved **here, at assembly time**, while ``Cycle``
objects still exist: drive edges and strobe placement are baked into transition
times and each failure's resolved strobe is stamped into
``FailureEvent.strobe_time``.  Cycles are then discarded and the renderer
evaluates no chain at all.
"""

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

#: Retention half-width, in vectors, around each failure (§6.1 item 3b).
#:
#: This is a DEFAULT ONLY. ``W`` is a parameter of ``build_waves``; nothing
#: inside the builder may read this global directly, or ``W`` stops being
#: tunable per call and the retention tests lose their ability to drive it.
DEFAULT_RETENTION_W: Final = 32


def wave_for(
    waves: tuple[WaveformSeries, ...], block: BlockId, pin: str
) -> WaveformSeries | None:
    """Bisect lookup into one of the sorted, tuple-backed wave collections.

    The collections on ``TestRun`` are tuples sorted by ``WaveKey``, not dicts —
    a frozen dataclass holding a dict is only shallowly immutable, and
    ``MappingProxyType`` is unpicklable.  ``BlockId`` is ``order=True``, so the
    keys compare naturally and this is O(log n).
    """
    key = (block, pin)
    i = bisect_left(waves, key, key=lambda w: (w.block, w.pin))
    if i < len(waves) and (waves[i].block, waves[i].pin) == key:
        return waves[i]
    return None


def resolve_timing(
    pin: str,
    timeset_name: str | None,
    timing_sets: tuple[TimingSet, ...],
    pindef: PinDef,
) -> PinTiming | None:
    """The §6.3 resolution chain, evaluated once at assembly time.

    ``Cycle.timeset`` -> the ``TimingSet``'s entry for this pin ->
    ``PinDef.timing`` -> ``None`` (the NRZ idealization).
    """
    raise NotImplementedError("Phase 2 M3 — see docs/ROADMAP.md")


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
    """Assemble ``(driven_waves, expected_waves, captured_waves)`` for a block.

    ``retention_w`` is the +-W half-width in vectors.  It is a parameter, not a
    module global read from inside — callers and tests must be able to drive it.

    Each returned tuple is sorted and unique by ``WaveKey``, as
    ``TestRun.__post_init__`` requires.
    """
    raise NotImplementedError("Phase 2 M3 — see docs/ROADMAP.md")
