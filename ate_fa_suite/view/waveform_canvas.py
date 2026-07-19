"""Custom Canvas waveform renderer (Phase 4, §6.2 / §6.3).

World coordinates are *time in native timescale units*; the view is fully
described by two numbers, ``t_left`` and ``ppu``.

The honesty rules this renderer must obey (§6.3) — a tester never observes a
continuous waveform, and drawing one silently claims measurements nobody made:

* **Strobe ticks** at compare instants — the only ground truth.  Held line
  between strobes renders dimmed, so it reads as inference, not measurement.
* **Retention gaps render as no-data hatch** — never an interpolated line.  A
  gap and a held state are different facts and look different on screen.
* **Stimulus is not observation** — ``driven_waves`` (solid) and
  ``captured_waves`` are separate series and never merge into one "actual".
* **The mismatch strip is derived from ``FailureEvent``s, never from waveform
  XOR** — it inherits the §4 authority policy, so masked compares and
  ``INCONSISTENT`` records behave correctly, and it stays valid where retention
  has gaps.  The renderer evaluates **no** timing chain: ``strobe_time``,
  ``cycle_period`` and ``strobe_window`` were all resolved at assembly.
* The optional interval-XOR overlay is *inference*, hatched amber,
  legend-labeled, and **off by default**.
"""

from __future__ import annotations

import tkinter as tk
from typing import Final

from ate_fa_suite.model.entities import (
    FailureEvent,
    LogicState,
    WaveformSeries,
)

MARGIN: Final = 24
LANE_H: Final = 40
GAP: Final = 8
PAD: Final = 6

#: Minimum width, in pixels, of a mismatch band so it stays visible when zoomed
#: far out (§6.2).
MIN_BAND_PX: Final = 2


class Viewport:
    """The float half of the §6.2 float/int boundary.

    Zoom math needs floats; the model API is integer-only (native timescale
    units).  The *view* owns the conversion — ``floor(t0)``/``ceil(t1)`` widen
    the query so edge data is never lost, and all sub-unit precision lives in
    pixel space via ``x_of``.  Segments never hold float times, and
    ``mypy --strict`` is what enforces that boundary.
    """

    def __init__(self, t_left: float, ppu: float) -> None:
        self.t_left = t_left  # world time at canvas x=0
        self.ppu = ppu  # pixels per time-unit (zoom level)

    def x_of(self, t: float) -> int:
        return round((t - self.t_left) * self.ppu)

    def zoom(self, factor: float, x_mouse: int) -> None:
        # keep the time under the cursor stationary (anchor-point zoom).  Naive
        # zoom that rescales about x=0 makes the failure fly off-screen — this
        # is the difference between a tool that feels professional and one that
        # does not.
        t_anchor = self.t_left + x_mouse / self.ppu
        self.ppu *= factor
        self.t_left = t_anchor - x_mouse / self.ppu


def lane_top(index: int) -> int:
    return MARGIN + index * (LANE_H + GAP)


class WaveformCanvas(tk.Canvas):
    """Multi-lane differential waveform view for exactly one test block.

    **Block-scoped viewport:** ``t_left``/``ppu`` are block-local coordinates
    (§3.1).  Selecting a failure in another block swaps the series set via its
    ``VectorLocation`` and ``(block, pin)`` wave keys rather than scrolling —
    cross-block time is deliberately not a single axis, because no such axis
    exists in the log.
    """

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, background="#101418", highlightthickness=0)
        self.viewport = Viewport(t_left=0.0, ppu=1.0)
        self.canvas_w = 1
        #: §6.2 / Phase 4 M9: inference, therefore off by default.
        self.show_inferred_disagreement = False

    def y_of(self, lane: int, state: LogicState) -> int:
        """Logic-1 at ``lane_top + PAD``, logic-0 at ``lane_top + LANE_H - PAD``."""
        raise NotImplementedError("Phase 4 M1 — see docs/ROADMAP.md")

    def render(self, series: WaveformSeries, lane: int) -> None:
        """Draw one lane: clipped retained segments, coverage-edge anchoring,
        both-sides clamping, sub-pixel coalescing, no-data hatch elsewhere."""
        raise NotImplementedError("Phase 4 M1 — see docs/ROADMAP.md")

    def hatch(self, x0: int, x1: int, lane: int) -> None:
        """Paint a no-data region.  Emits **no** ``create_line`` call — an
        interpolated line across a retention gap would be a fabricated claim."""
        raise NotImplementedError("Phase 4 M1 — see docs/ROADMAP.md")

    def mark_busy(self, x: int, lane: int) -> None:
        """Activity block for a pixel column that coalesced several transitions
        (the GTKWave trick), while the polyline still exits at the column's
        **final** state — dropping later transitions would leave the wave at a
        stale level, which is wrong data, not just an aliasing artifact."""
        raise NotImplementedError("Phase 4 M6 — see docs/ROADMAP.md")

    def draw_mismatch_bands(self, failures: tuple[FailureEvent, ...]) -> None:
        """One narrow red band per failing compare, centred on the
        assembly-resolved ``strobe_time``; width = ``strobe_window`` when the
        resolved timing was window-strobed, else a fixed fraction of
        ``cycle_period``, minimum ``MIN_BAND_PX``.  Drawn behind both lanes."""
        raise NotImplementedError("Phase 4 M2 — see docs/ROADMAP.md")

    def redraw(self) -> None:
        """Full ``delete("wave")`` + redraw, throttled through ``after_idle`` so
        zoom event storms collapse into one repaint."""
        raise NotImplementedError("Phase 4 M6 — see docs/ROADMAP.md")
