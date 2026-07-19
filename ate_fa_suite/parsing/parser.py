"""Grammar loading and the strict whole-file parse entry point.

The grammar plumbing in this module is **complete and load-tested** in Step 0 —
``atelog.lark`` is genuinely valid Lark, ships as package data, and is reachable
from a wheel or zipapp.  ``LogParser.parse`` itself (transformer, error mapping,
chunked path) is Phase 1 work; see ``docs/ROADMAP.md``.
"""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Callable, Final

from lark import Lark

from ate_fa_suite.model.entities import ParseComplete, ParseFailed, ParseProgress

GRAMMAR_PACKAGE: Final = "ate_fa_suite.parsing.grammar"
GRAMMAR_FILENAME: Final = "atelog.lark"

#: The six entry points of the multi-start grammar (§3.2).  ``document`` is the
#: strict whole-file rule and is *composed from* the five fragment rules used by
#: the chunked path (§6.1), so the two paths cannot drift apart.
STARTS: Final[list[str]] = [
    "document",
    "prologue",
    "testblock_header",
    "cycle_batch",
    "block_trailer",
    "end_log",
]

#: ADF-1 major versions this build understands.  A mismatch is a *fatal*
#: validation error (Phase 1 milestone 7, two-tier policy).
SUPPORTED_MAJOR_VERSIONS: Final[frozenset[int]] = frozenset({1})


def load_grammar_text() -> str:
    """Read ``atelog.lark`` as package data.

    Deliberately uses ``importlib.resources`` rather than a ``__file__``-relative
    path: that is what makes the grammar survive installation into a wheel and
    bundling into the single-file zipapp (§3.2).
    """
    return (
        files(GRAMMAR_PACKAGE).joinpath(GRAMMAR_FILENAME).read_text(encoding="utf-8")
    )


@lru_cache(maxsize=1)
def build_parser() -> Lark:
    """Build the single shared multi-start LALR parser.

    ``parser="lalr"``      O(n) parsing — mandatory for multi-hundred-MB logs.
    ``lexer="contextual"`` resolves the deliberate terminal collisions per parse
                           state (``STATE`` vs ``INT``, ``VALUE`` vs everything,
                           ``DIRECTION`` vs ``PIN_NAME``).  With the standard
                           lexer this grammar would be unbuildable.
    ``start=STARTS``       one grammar, one instance, six entry points.
    ``propagate_positions`` every dataclass can carry its source line — FA
                           engineers always want the raw log line.
    ``cache=True``         caches the generated LALR table; near-instant startup.
    """
    return Lark(
        load_grammar_text(),
        parser="lalr",
        lexer="contextual",
        start=STARTS,
        propagate_positions=True,
        maybe_placeholders=False,
        cache=True,
    )


ProgressCallback = Callable[[ParseProgress], None]


class LogParser:
    """Path -> ``ParseComplete`` | ``ParseFailed``.

    Never raises for malformed input: a syntax error becomes a positioned
    ``ParseFailed`` message, and a truncated file becomes a *partial*
    ``ParseComplete`` carrying a ``TestRun.warnings`` entry (§6.1 salvage).
    """

    def __init__(self, *, chunked: bool = True) -> None:
        self._chunked = chunked

    def parse(
        self,
        path: Path,
        job_id: int,
        on_progress: ProgressCallback | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> ParseComplete | ParseFailed:
        """Parse ``path`` into a ``TestRun``.

        Phase 1 milestones 3 & 5 (transformer + error mapping); the chunked
        framing path is milestone 6.
        """
        raise NotImplementedError(
            "LogParser.parse — Phase 1, see docs/ROADMAP.md"
        )
