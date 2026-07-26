"""Pick the right reader for a file: suffix first, magic bytes as tiebreak.

Phase 3 M1 wires this into the Open dialog, which is why file dispatch is
written once here rather than twice.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Final, Protocol, runtime_checkable

from ate_fa_suite.model.entities import (
    ParseComplete,
    ParseFailed,
    ParseProgress,
)
from ate_fa_suite.parsing.parser import LogParser
from ate_fa_suite.parsing.stdf.parser import StdfParser
from ate_fa_suite.parsing.stdf.reader import (
    FAR_HEADER_BIG,
    FAR_HEADER_LITTLE,
    HEADER_SIZE,
    detect_endianness,
)

ProgressCallback = Callable[[ParseProgress], None]

#: Suffixes each reader claims, lowercase.
ATELOG_SUFFIXES: Final[tuple[str, ...]] = (".atelog",)
STDF_SUFFIXES: Final[tuple[str, ...]] = (".stdf", ".std")


@runtime_checkable
class LogSourceParser(Protocol):
    """What every reader offers.  ``background.py`` needs only this."""

    def parse(
        self,
        path: Path,
        job_id: int,
        on_progress: ProgressCallback | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> ParseComplete | ParseFailed: ...


def looks_like_stdf(path: Path) -> bool:
    """Whether the file opens with a FAR header, in either byte order."""
    try:
        with path.open("rb") as source:
            return detect_endianness(source.read(HEADER_SIZE)) is not None
    except OSError:
        return False


def parser_for(path: Path) -> LogSourceParser:
    """Return the reader for ``path``.

    The suffix decides, because that is what the user chose in the dialog.  A
    magic-byte sniff only breaks the tie for an unfamiliar suffix, so a
    misnamed ``.stdf`` still parses rather than failing on a text grammar.
    """
    suffix = path.suffix.lower()
    if suffix in STDF_SUFFIXES:
        return StdfParser()
    if suffix in ATELOG_SUFFIXES:
        return LogParser()
    return StdfParser() if looks_like_stdf(path) else LogParser()


__all__ = [
    "ATELOG_SUFFIXES",
    "FAR_HEADER_BIG",
    "FAR_HEADER_LITTLE",
    "STDF_SUFFIXES",
    "LogSourceParser",
    "looks_like_stdf",
    "parser_for",
]
