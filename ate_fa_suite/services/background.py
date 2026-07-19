"""Background parsing and its worker-to-UI message queue."""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Final

from ate_fa_suite.model.entities import (
    ParseComplete,
    ParseFailed,
    ParseProgress,
)

# UI queue polling interval in milliseconds.
PUMP_INTERVAL_MS: Final = 50

# Limit each queue drain so Tk can keep processing events.
PUMP_BATCH_LIMIT: Final = 64

WorkerMessage = ParseProgress | ParseComplete | ParseFailed


class ParserWorker:
    """Runs ``LogParser`` off the Tk thread and reports over a queue."""

    def __init__(self) -> None:
        self.queue: queue.Queue[WorkerMessage] = queue.Queue()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, path: Path, job_id: int) -> None:
        """Begin parsing ``path`` under generation ``job_id``."""
        raise NotImplementedError("Phase 3 M2 — see docs/ROADMAP.md")

    def cancel(self) -> None:
        """Signal cancellation; the worker checks between chunks."""
        raise NotImplementedError("Phase 3 M2 — see docs/ROADMAP.md")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
