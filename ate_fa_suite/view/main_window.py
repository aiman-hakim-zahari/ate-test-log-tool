"""Main window: layout, menu, status bar, and the queue pump (Phase 3).

The pump is the one place the worker's messages become UI state.  It runs on the
Tk thread via ``root.after(PUMP_INTERVAL_MS, pump)``, drains in bounded batches,
and **discards any message whose ``job_id`` is not the current generation**.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ate_fa_suite.viewmodel.app_viewmodel import AppViewModel


class MainWindow(ttk.Frame):
    """Root layout: menu, failure table, signature panel, waveform canvas."""

    def __init__(self, master: tk.Misc, viewmodel: AppViewModel) -> None:
        super().__init__(master)
        self.viewmodel = viewmodel

    def build(self) -> None:
        """Create widgets and wire ViewModel subscriptions."""
        raise NotImplementedError("Phase 3 M1 — see docs/ROADMAP.md")

    def pump(self) -> None:
        """Drain the worker queue in bounded batches, dropping stale jobs."""
        raise NotImplementedError("Phase 3 M2 — see docs/ROADMAP.md")


def run(argv: list[str]) -> int:
    """Composition root for the GUI: build Tk, wire everything, mainloop."""
    raise NotImplementedError("Phase 3 — see docs/ROADMAP.md")
