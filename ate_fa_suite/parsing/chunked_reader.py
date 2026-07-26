"""Split a log into lossless structural frames."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final, Iterator, Protocol

DEFAULT_BATCH_CYCLES: Final = 5000
READ_SIZE: Final = 64 * 1024

class FrameKind(Enum):
    PROLOGUE = "prologue"
    BLOCK_START = "testblock_header"
    CYCLE_BATCH = "cycle_batch"
    BLOCK_TRAILER = "block_trailer"
    END_LOG = "end_log"
    TRUNCATED_TAIL = ""

    @property
    def start_rule(self) -> str | None:
        return self.value or None


@dataclass(frozen=True, slots=True)
class LogFrame:
    kind: FrameKind
    data: bytes
    start_line: int
    start_byte: int

    def text(self) -> str:
        return self.data.decode("utf-8")


class ByteSource(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


class InvalidUtf8Error(Exception):
    """UTF-8 error with an absolute source line and byte offset."""

    def __init__(self, line: int, byte: int, reason: str) -> None:
        super().__init__(reason)
        self.line = line
        self.byte = byte
        self.reason = reason


def _lines(source: ByteSource) -> Iterator[bytes]:
    """Yield byte lines without changing their line endings."""
    pending = b""
    while chunk := source.read(READ_SIZE):
        parts = (pending + chunk).split(b"\n")
        pending = parts.pop()
        for part in parts:
            yield part + b"\n"
    if pending:
        yield pending


def _record(line: bytes) -> bytes:
    """Return the leading record text used only for classification."""
    return line.rstrip(b"\r\n").lstrip(b" \t")


def _marker(line: bytes) -> str:
    value = _record(line)
    if not value or value.startswith(b"//"):
        return "trivia"
    fields = value.split(None, 2)
    if fields[0] == b"TESTBLOCK":
        return "block"
    if fields[0] == b"CYCLE":
        return "cycle"
    if fields[:2] == [b"END", b"CYCLE"]:
        return "cycle_end"
    if fields[0] == b"FAILSUMMARY":
        return "summary"
    if fields[:2] == [b"END", b"TESTBLOCK"]:
        return "block_end"
    if fields[:2] == [b"END", b"LOG"]:
        return "log_end"
    return "other"


def scan_frames(
    source: ByteSource, batch_cycles: int = DEFAULT_BATCH_CYCLES
) -> Iterator[LogFrame]:
    """Stream untouched bytes using leading record tokens for boundaries only."""
    if batch_cycles < 1:
        raise ValueError("batch_cycles must be positive")

    kind = FrameKind.PROLOGUE
    buffer = bytearray()
    frame_line = 1
    frame_byte = 0
    line_no = 1
    byte_no = 0

    in_cycle = False
    partial_cycle_offset = 0
    partial_cycle_line = 0
    partial_cycle_byte = 0
    completed_cycles = 0
    saw_closing_marker_in_cycle = False

    def reset_frame(
        new_kind: FrameKind, start_line: int, start_byte: int
    ) -> None:
        nonlocal kind, frame_line, frame_byte, completed_cycles
        kind = new_kind
        frame_line = start_line
        frame_byte = start_byte
        buffer.clear()
        completed_cycles = 0

    for line in _lines(source):
        try:
            line.decode("utf-8")
        except UnicodeDecodeError as error:
            raise InvalidUtf8Error(
                line=line_no,
                byte=byte_no + error.start,
                reason=error.reason,
            ) from None
        marker = _marker(line)

        boundary: FrameKind | None = None
        if marker == "block":
            boundary = FrameKind.BLOCK_START
        elif marker == "cycle" and not in_cycle:
            if (
                kind is not FrameKind.CYCLE_BATCH
                or completed_cycles >= batch_cycles
            ):
                boundary = FrameKind.CYCLE_BATCH
        elif (
            marker in ("summary", "block_end")
            and not in_cycle
            and kind is not FrameKind.BLOCK_TRAILER
        ):
            boundary = FrameKind.BLOCK_TRAILER
        elif marker == "log_end" and not in_cycle:
            boundary = FrameKind.END_LOG

        if boundary is not None and buffer:
            yield LogFrame(kind, bytes(buffer), frame_line, frame_byte)
            reset_frame(boundary, line_no, byte_no)
        elif boundary is not None and not buffer:
            reset_frame(boundary, line_no, byte_no)

        if boundary is FrameKind.BLOCK_START and in_cycle:
            in_cycle = False
            saw_closing_marker_in_cycle = False

        if marker == "cycle" and not in_cycle:
            in_cycle = True
            partial_cycle_offset = len(buffer)
            partial_cycle_line = line_no
            partial_cycle_byte = byte_no
        elif marker == "cycle_end" and in_cycle:
            in_cycle = False
            completed_cycles += 1
            saw_closing_marker_in_cycle = False
        elif in_cycle and marker in ("summary", "block_end", "log_end"):
            # A later structural closer means this is a malformed complete
            # record, not an incomplete EOF tail eligible for salvage.
            saw_closing_marker_in_cycle = True

        buffer.extend(line)
        byte_no += len(line)
        line_no += 1

    if not buffer:
        return

    if in_cycle and not saw_closing_marker_in_cycle:
        complete = bytes(buffer[:partial_cycle_offset])
        if complete:
            yield LogFrame(
                FrameKind.CYCLE_BATCH, complete, frame_line, frame_byte
            )
        yield LogFrame(
            kind=FrameKind.TRUNCATED_TAIL,
            data=bytes(buffer[partial_cycle_offset:]),
            start_line=partial_cycle_line,
            start_byte=partial_cycle_byte,
        )
        return

    yield LogFrame(kind, bytes(buffer), frame_line, frame_byte)
    if kind is not FrameKind.END_LOG:
        yield LogFrame(
            kind=FrameKind.TRUNCATED_TAIL,
            data=b"",
            start_line=line_no,
            start_byte=byte_no,
        )


def rebase_error_line(frame: LogFrame, frame_relative_line: int) -> int:
    return frame.start_line + frame_relative_line - 1
