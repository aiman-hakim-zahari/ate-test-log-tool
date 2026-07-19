"""Phase 3 stubs — pub-sub, ViewModel, worker pump, table, panel, status bar.

Skipped by design in Step 0.  Gate for Step 4: headless ViewModel tests plus
one Tk smoke test.
"""

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.phase3,
    pytest.mark.skip(reason="Phase 3 — Step 4; see docs/ROADMAP.md"),
]


# --- headless: no Tk required ------------------------------------------------


def test_event_subscribe_emit_unsubscribe() -> None: ...


def test_unsubscribe_during_emit_does_not_skip_a_listener() -> None:
    """Mutating the listener list while iterating it is the classic pub-sub
    bug; emit must iterate a snapshot."""


def test_viewmodel_is_importable_without_a_display() -> None:
    """The whole point of the firewall: CI has no display."""


def test_pump_discards_messages_from_a_superseded_job() -> None:
    """The stale-ParseComplete-after-cancel/reload race.  Message-passing alone
    does not remove it — the job-generation ID does."""


def test_opening_a_second_file_mid_parse_supersedes_the_first() -> None: ...


def test_cancel_is_observable_within_200ms() -> None:
    """The worker checks the threading.Event between chunks."""


def test_worker_never_touches_tk() -> None:
    """Not even root.after — Tkinter/Tcl is not thread-safe."""


def test_pump_drains_in_bounded_batches() -> None:
    """A message storm must not starve the Tk event loop."""


def test_parse_failed_surfaces_the_offending_line() -> None: ...


def test_status_bar_reports_inconsistency_warnings() -> None: ...


# --- one real Tk smoke test --------------------------------------------------


def test_tk_smoke_loads_golden_and_matches_row_count() -> None:
    """Instantiate a real Tk root, load multi_fail.atelog, assert the table row
    count equals the failure count.  Skipped automatically where no display is
    available."""


def test_table_inserts_are_batched() -> None:
    """500 rows per tick; the table never receives 100k rows in one tick."""


def test_search_box_is_debounced() -> None:
    """300 ms."""
