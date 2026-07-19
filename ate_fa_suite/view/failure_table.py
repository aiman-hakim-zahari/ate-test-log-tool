"""Searchable failure table (Phase 3 milestone 3).

UI-side hygiene from §6.1 item 4: batched inserts (``INSERT_BATCH`` rows per
tick), paging above ``PAGE_THRESHOLD`` rows, and a debounced search box — the
table never receives 100k rows in one tick.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Final

from ate_fa_suite.model.entities import FailureEvent
from ate_fa_suite.viewmodel.app_viewmodel import AppViewModel

#: Rows inserted per tick, so the Tk event loop stays responsive.
INSERT_BATCH: Final = 500

#: Above this many rows the table pages instead of materializing everything.
PAGE_THRESHOLD: Final = 10_000

#: Search-box debounce, milliseconds.
SEARCH_DEBOUNCE_MS: Final = 300

COLUMNS: Final[tuple[str, ...]] = (
    "block",
    "vector",
    "time",
    "pin",
    "expected",
    "actual",
    "category",
    "line",
)


class FailureTable(ttk.Frame):
    """``ttk.Treeview`` over ``TestRun.failures`` with sort, search, paging."""

    def __init__(self, master: tk.Misc, viewmodel: AppViewModel) -> None:
        super().__init__(master)
        self.viewmodel = viewmodel

    def build(self) -> None:
        raise NotImplementedError("Phase 3 M3 — see docs/ROADMAP.md")

    def set_rows(self, failures: tuple[FailureEvent, ...]) -> None:
        """Replace the contents, inserting in batches of ``INSERT_BATCH``."""
        raise NotImplementedError("Phase 3 M3 — see docs/ROADMAP.md")
