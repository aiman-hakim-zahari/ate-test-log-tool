"""Open / recent-files / export services (Phase 3 milestone 1, Phase 2 M5).

Pure filesystem work with no Tk dependency — the *dialog* lives in the view, the
policy lives here, which keeps recent-file handling and export testable headless.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

#: How many entries the recent-files list keeps.
MAX_RECENT: Final = 8

#: The ADF-1 file extension.
LOG_SUFFIX: Final = ".atelog"


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
