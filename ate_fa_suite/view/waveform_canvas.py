"""Custom Canvas renderer for retained waveform data."""

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

# Minimum visible width of a mismatch band.
MIN_BAND_PX: Final = 2


class Viewport:
    """Convert floating-point view coordinates to canvas pixels."""

    def __init__(self, t_left: float, ppu: float) -> None:
        self.t_left = t_left  # world time at canvas x=0
        self.ppu = ppu  # pixels per time-unit (zoom level)

    def x_of(self, t: float) -> int:
        return round((t - self.t_left) * self.ppu)

    def zoom(self, factor: float, x_mouse: int) -> None:
        # Keep the time under the mouse stationary while zooming.
        t_anchor = self.t_left + x_mouse / self.ppu
        self.ppu *= factor
        self.t_left = t_anchor - x_mouse / self.ppu


def lane_top(index: int) -> int:
    return MARGIN + index * (LANE_H + GAP)


class WaveformCanvas(tk.Canvas):
    """Multi-lane waveform view for one test-block invocation."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, background="#101418", highlightthickness=0)
        self.viewport = Viewport(t_left=0.0, ppu=1.0)
        self.canvas_w = 1
        # Inferred disagreements are optional and start disabled.
        self.show_inferred_disagreement = False

    def y_of(self, lane: int, state: LogicState) -> int:
        """Map a logic state to its vertical lane position."""
        raise NotImplementedError("Phase 4 M1 — see docs/ROADMAP.md")

    def render(self, series: WaveformSeries, lane: int) -> None:
        """Draw retained segments and hatch missing regions."""
        raise NotImplementedError("Phase 4 M1 — see docs/ROADMAP.md")

    def hatch(self, x0: int, x1: int, lane: int) -> None:
        """Paint a region where waveform data was not retained."""
        raise NotImplementedError("Phase 4 M1 — see docs/ROADMAP.md")

    def mark_busy(self, x: int, lane: int) -> None:
        """Mark several transitions that share one pixel column."""
        raise NotImplementedError("Phase 4 M6 — see docs/ROADMAP.md")

    def draw_mismatch_bands(self, failures: tuple[FailureEvent, ...]) -> None:
        """Draw one band at each failed compare's resolved strobe time."""
        raise NotImplementedError("Phase 4 M2 — see docs/ROADMAP.md")

    def redraw(self) -> None:
        """Schedule a complete waveform redraw."""
        raise NotImplementedError("Phase 4 M6 — see docs/ROADMAP.md")
