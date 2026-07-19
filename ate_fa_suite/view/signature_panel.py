"""Ranked fail-signature panel (Phase 3 milestone 4).

Shows clusters in 8D-report language — "83% of failures: SA0-candidate on
DQ[7:0], vectors 1200-1299" — and clicking one applies the corresponding filter
to the failure table.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ate_fa_suite.model.entities import SignatureCluster
from ate_fa_suite.viewmodel.app_viewmodel import AppViewModel


class SignaturePanel(ttk.Frame):
    """Ranked cluster summary with click-to-filter."""

    def __init__(self, master: tk.Misc, viewmodel: AppViewModel) -> None:
        super().__init__(master)
        self.viewmodel = viewmodel

    def build(self) -> None:
        raise NotImplementedError("Phase 3 M4 — see docs/ROADMAP.md")

    def set_clusters(self, clusters: tuple[SignatureCluster, ...]) -> None:
        raise NotImplementedError("Phase 3 M4 — see docs/ROADMAP.md")
