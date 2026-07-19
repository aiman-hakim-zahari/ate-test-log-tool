"""``ParserWorker`` thread + message queue (Phase 3 milestone 2).

The threading contract (§2.3), in full:

* Only the main thread touches Tk widgets — Tkinter/Tcl is not thread-safe, and
  the worker never calls Tk, **not even** ``root.after``.
* The worker communicates *exclusively* by putting frozen dataclass messages
  (``ParseProgress`` / ``ParseComplete`` / ``ParseFailed``) on a ``queue.Queue``,
  drained by a ``root.after(50, pump)`` loop in bounded batches.
* Cancellation via ``threading.Event``, checked between chunks (< 200 ms).
* **Every message carries a job-generation ID** and the pump discards messages
  from superseded jobs.  Message-passing alone does not remove the
  stale-``ParseComplete``-after-cancel/reload race; the generation ID does.

Because the payloads are picklable tuple-backed frozen dataclasses, the *message
schema* is reusable as-is under a future ``multiprocessing`` worker — but the
transport and lifecycle change (``multiprocessing.Queue``, process start/join,
cancellation via a sentinel or event proxy instead of a shared
``threading.Event``).
"""

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

#: Pump interval in milliseconds — ``root.after(PUMP_INTERVAL_MS, pump)``.
PUMP_INTERVAL_MS: Final = 50

#: Upper bound on messages drained per pump tick, so a message storm can never
#: starve the Tk event loop.
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
