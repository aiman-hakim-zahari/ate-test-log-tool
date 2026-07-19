"""Raw-byte framing scanner for large logs (§6.1 item 2, Phase 1 milestone 6).

This is a small **state machine, not a substring search**.  A naive
``str.find("END CYCLE\\n")`` would miss CRLF records, false-match inside trailing
``//`` comment text, and lose block context.

Design invariants — each one is load-bearing and separately tested:

* The scanner streams **raw bytes**, splitting on ``\\n`` (the byte common to LF
  and CRLF endings).  Every frame is an **untouched slice of the original byte
  stream**, original line endings included.  That raw fidelity is what makes
  true byte offsets and byte-for-byte reassembly possible; frames are *never*
  normalized.
* Normalization exists only in the **classification view**: a decoded throwaway
  copy of each line (trailing ``\\r`` tolerated) is inspected for its **leading
  token only**, so the frame markers can never be faked by comment or value
  text.
* Frames are handed to Lark decoded but with their original endings intact — the
  grammar's ``NEWLINE`` terminal accepts both, so the parser never needed
  normalization in the first place.
* **Frame-boundary ownership:** comment and blank lines attach to the
  *preceding* frame — the scanner cuts a batch immediately before the next
  marker line — because the segment grammars cannot accept leading trivia (a
  frame must start with its marker token; a leading comment line would lex to an
  orphan ``NEWLINE``).
* Every input line belongs to **exactly one** frame, and every frame type maps
  1:1 to a fragment start rule of the multi-start grammar (§3.2).  Two tests pin
  this down: byte-for-byte reassembly proves totality, and an independent parse
  of each emitted frame with its fragment rule proves the frames are
  self-contained.
* The scanner does **no real parsing** — Lark remains the single syntactic
  authority.

On truncation the scanner emits an explicit truncated-tail frame: every complete
cycle before the break is recovered, block identity and ``FAILSUMMARY``
cross-checks are preserved, and the tail becomes a ``TestRun.warnings`` entry on
a partial result delivered as ``ParseComplete`` — never a crash.  The strict
whole-file ``document`` grammar deliberately rejects truncated input instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final, Iterator, Protocol

#: Cycles per ``cycle_batch`` frame.  Bounds peak memory and sets the
#: granularity of progress ticks and cancellation checks.
DEFAULT_BATCH_CYCLES: Final = 5000


class FrameKind(Enum):
    """Frame type -> its fragment start rule in the multi-start grammar.

    The value *is* the Lark start rule name, so the mapping cannot drift.
    ``TRUNCATED_TAIL`` is the one kind with no start rule: it is never handed to
    Lark, it becomes a salvage warning.
    """

    PROLOGUE = "prologue"
    BLOCK_START = "testblock_header"
    CYCLE_BATCH = "cycle_batch"
    BLOCK_TRAILER = "block_trailer"
    END_LOG = "end_log"
    TRUNCATED_TAIL = ""

    @property
    def start_rule(self) -> str | None:
        """The fragment start rule, or ``None`` for the truncated tail."""
        return self.value or None


@dataclass(frozen=True, slots=True)
class LogFrame:
    """One self-contained slice of the input.

    ``data`` is an untouched slice of the original bytes — concatenating every
    emitted frame's ``data`` in order reproduces the input exactly.
    """

    kind: FrameKind
    data: bytes
    start_line: int  # 1-based, absolute in the source file
    start_byte: int  # 0-based, absolute in the source file
    block_name: str | None = None  # enclosing TESTBLOCK name, when known
    cycle_count: int = 0  # complete cycles in a CYCLE_BATCH frame

    def text(self) -> str:
        """Decode for Lark, preserving the original line endings."""
        return self.data.decode("utf-8")


class ByteSource(Protocol):
    """Anything yielding the raw bytes of a log — a file, a socket, a test."""

    def read(self, size: int = -1, /) -> bytes: ...


def scan_frames(
    source: ByteSource, batch_cycles: int = DEFAULT_BATCH_CYCLES
) -> Iterator[LogFrame]:
    """Stream ``source`` as typed frames.

    Yields, in document order: one ``PROLOGUE`` frame; then per test block a
    ``BLOCK_START`` frame (where the assembler assigns the ``BlockId``
    occurrence), one or more ``CYCLE_BATCH`` frames, and a ``BLOCK_TRAILER``
    frame; then ``END_LOG``.  If EOF arrives mid-record, the final frame is
    ``TRUNCATED_TAIL``.
    """
    raise NotImplementedError("Phase 1 M6 — see docs/ROADMAP.md")


def rebase_error_line(frame: LogFrame, frame_relative_line: int) -> int:
    """Lark reports positions relative to the frame it was handed; all user-
    facing reporting must be absolute in the source file."""
    return frame.start_line + frame_relative_line - 1
