"""Observable presentation state and application commands."""

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

        # Increment on load or cancel so late worker messages can be ignored.
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
        """Select a failure by its full block, vector, and time location."""
        raise NotImplementedError("Phase 3 M4 — see docs/ROADMAP.md")
