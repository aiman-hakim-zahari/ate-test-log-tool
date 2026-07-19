"""Observable application state + commands (Phase 3).

The ViewModel owns *all* presentation state — current filter, selected failure,
zoom window — and exposes it through the ``events`` pub-sub.  It holds the
immutable ``TestRun`` handed up from the parser thread and never mutates it.

Everything here is testable headless; ``tkinter`` is not importable from this
layer (§2.3).
"""

from __future__ import annotations

from pathlib import Path

from ate_fa_suite.model.entities import (
    FailureEvent,
    SignatureCluster,
    TestRun,
    VectorLocation,
)
from ate_fa_suite.model.signature import FailurePredicate
from ate_fa_suite.viewmodel.events import Event


class AppViewModel:
    """Presentation state for the whole application."""

    def __init__(self) -> None:
        self.run_loaded: Event[TestRun] = Event()
        self.failures_changed: Event[tuple[FailureEvent, ...]] = Event()
        self.clusters_changed: Event[tuple[SignatureCluster, ...]] = Event()
        self.selection_changed: Event[VectorLocation | None] = Event()
        self.status_changed: Event[str] = Event()

        self._run: TestRun | None = None
        self._selected: VectorLocation | None = None

        #: Monotonic job-generation ID (§2.3).  Incremented on every load or
        #: cancel; the pump discards worker messages from superseded jobs,
        #: which is what kills the stale-``ParseComplete``-after-cancel race.
        self._job_generation = 0

    @property
    def run(self) -> TestRun | None:
        return self._run

    def load(self, path: Path) -> None:
        """Start a background parse, superseding any job in flight."""
        raise NotImplementedError("Phase 3 M2 — see docs/ROADMAP.md")

    def cancel(self) -> None:
        """Cancel the in-flight parse; must be observable in < 200 ms."""
        raise NotImplementedError("Phase 3 M2 — see docs/ROADMAP.md")

    def apply_filter(self, predicate: FailurePredicate | None) -> None:
        """Filter the failure table; ``None`` clears the filter."""
        raise NotImplementedError("Phase 3 M3 — see docs/ROADMAP.md")

    def select(self, location: VectorLocation | None) -> None:
        """Select a failure.  Carries the full ``VectorLocation`` triple — a
        bare vector or timestamp is not a run-wide address (§3.1)."""
        raise NotImplementedError("Phase 3 M4 — see docs/ROADMAP.md")
