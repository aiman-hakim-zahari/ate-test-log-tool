"""Grammar loading and the strict whole-file parse entry point.

The grammar plumbing in this module is **complete and load-tested** in Step 0 —
``atelog.lark`` is genuinely valid Lark, ships as package data, and is reachable
from a wheel or zipapp.  ``LogParser.parse`` itself (transformer, error mapping,
chunked path) is Phase 1 work; see ``docs/ROADMAP.md``.
"""

from __future__ import annotations

import time
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Callable, Final

from lark import Lark
from lark.exceptions import (
    UnexpectedCharacters,
    UnexpectedEOF,
    UnexpectedInput,
    UnexpectedToken,
    VisitError,
)

from ate_fa_suite.model.entities import ParseComplete, ParseFailed, ParseProgress
from ate_fa_suite.parsing.transformer import (
    AteLogTransformer,
    ParsedDocument,
    assemble_run,
)
from ate_fa_suite.parsing.validator import ValidationError

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


def source_line(text: str, line: int) -> str:
    """The raw source line ``line`` (1-based), or ``""`` if out of range.

    FA engineers always want the raw log line next to an error, so it is
    carried on ``ParseFailed.context`` rather than left for the UI to re-read
    the file for.
    """
    if line < 1:
        return ""
    lines = text.splitlines()
    if line > len(lines):
        return ""
    return lines[line - 1].rstrip("\r")


def describe_error(error: UnexpectedInput) -> str:
    """A human-readable message for a Lark syntax error.

    Kept deliberately concrete — "unexpected NEWLINE, expected STATE" is
    actionable at a tester console; "parse error" is not.
    """
    if isinstance(error, UnexpectedToken):
        expected = ", ".join(sorted(error.expected))
        return (
            f"unexpected {error.token.type} {str(error.token)!r}; "
            f"expected one of: {expected}"
        )
    if isinstance(error, UnexpectedCharacters):
        return f"unexpected character {error.char!r}"
    if isinstance(error, UnexpectedEOF):
        return "unexpected end of file (log is truncated or missing END LOG)"
    return "syntax error"


class LogParser:
    """Path -> ``ParseComplete`` | ``ParseFailed``.

    Never raises for malformed input: a syntax error becomes a positioned
    ``ParseFailed`` message, and (from M6) a truncated file becomes a *partial*
    ``ParseComplete`` carrying a ``TestRun.warnings`` entry (§6.1 salvage).
    """

    def __init__(self, *, chunked: bool = False) -> None:
        # Defaults to the strict whole-file path until the framing scanner
        # lands in M6, at which point this flips to True.
        self._chunked = chunked

    def parse(
        self,
        path: Path,
        job_id: int,
        on_progress: ProgressCallback | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> ParseComplete | ParseFailed:
        """Parse ``path`` into a ``TestRun``."""
        if self._chunked:
            raise NotImplementedError(
                "chunked path — Phase 1 M6, see docs/ROADMAP.md"
            )
        text = path.read_text(encoding="utf-8")
        return self.parse_text(text, job_id)

    def parse_text(self, text: str, job_id: int) -> ParseComplete | ParseFailed:
        """Strict whole-file parse of ``text``.

        Separate from ``parse`` so tests and the property-test oracle can drive
        the parser without touching the filesystem.
        """
        started = time.perf_counter()
        try:
            tree = build_parser().parse(text, start="document")
            try:
                document = AteLogTransformer().transform(tree)
            except VisitError as wrapper:
                # Lark wraps ANY exception raised inside a transformer method in
                # VisitError, so a ValidationError from `header` would otherwise
                # escape as an opaque crash instead of a positioned ParseFailed.
                # Unwrap ours; let a genuine bug keep its traceback.
                if isinstance(wrapper.orig_exc, ValidationError):
                    raise wrapper.orig_exc from None
                raise
            run = assemble_run(document)
        except UnexpectedInput as error:
            line = getattr(error, "line", 0) or 0
            return ParseFailed(
                job_id=job_id,
                line=line,
                column=getattr(error, "column", 0) or 0,
                message=describe_error(error),
                context=source_line(text, line),
            )
        except ValidationError as error:
            return ParseFailed(
                job_id=job_id,
                line=error.line,
                column=error.column,
                message=error.message,
                context=source_line(text, error.line),
            )

        return ParseComplete(
            job_id=job_id,
            run=run,
            elapsed_s=time.perf_counter() - started,
        )


__all__ = [
    "GRAMMAR_FILENAME",
    "GRAMMAR_PACKAGE",
    "STARTS",
    "SUPPORTED_MAJOR_VERSIONS",
    "LogParser",
    "ParsedDocument",
    "build_parser",
    "describe_error",
    "load_grammar_text",
    "source_line",
]
