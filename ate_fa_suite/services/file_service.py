"""Filesystem helpers for recent files and exports."""

from __future__ import annotations

from pathlib import Path
from typing import Final

#: How many entries the recent-files list keeps.
MAX_RECENT: Final = 8

#: Extensions the Open dialog accepts, in the order they are offered.
#: A tuple rather than a single string because Phase 5 adds STDF ingestion
#: alongside ADF-1; ``parsing.ingest.parser_for`` picks the reader per file.
LOG_SUFFIXES: Final[tuple[str, ...]] = (".atelog", ".stdf", ".std")


def recent_files_path() -> Path:
    """Per-user location of the recent-files list."""
    raise NotImplementedError("Phase 3 M1 — see docs/ROADMAP.md")


def load_recent() -> tuple[Path, ...]:
    raise NotImplementedError("Phase 3 M1 — see docs/ROADMAP.md")


def push_recent(path: Path) -> tuple[Path, ...]:
    """Record ``path`` as most-recent; returns the updated list."""
    raise NotImplementedError("Phase 3 M1 — see docs/ROADMAP.md")


def write_export(path: Path, content: str) -> None:
    raise NotImplementedError("Phase 2 M5 — see docs/ROADMAP.md")
