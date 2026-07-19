"""Split a log into lossless frames and index its cycle headers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Final, Iterator, Protocol

DEFAULT_BATCH_CYCLES: Final = 5000
READ_SIZE: Final = 64 * 1024

_CYCLE_RE = re.compile(rb"^CYCLE[ \t]+(\d+)[ \t]+T=(\d+)(?:[ \t]|$)")


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
    block_name: str | None = None
    cycle_count: int = 0
    cycle_vectors: tuple[int, ...] = ()
    cycle_times: tuple[int, ...] = ()
    fail_count: int = 0
    fail_vectors: tuple[int, ...] = ()

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
    if value.startswith(b"TESTBLOCK") and value[9:10] in (b" ", b"\t"):
        return "block"
    if _CYCLE_RE.match(value):
        return "cycle"
    if value.startswith(b"END CYCLE"):
        return "cycle_end"
    if value.startswith(b"FAILSUMMARY"):
        return "summary"
    if value.startswith(b"END TESTBLOCK"):
        return "block_end"
    if value.startswith(b"END LOG"):
        return "log_end"
    return "other"


def _block_name(line: bytes) -> str | None:
    fields = _record(line).split()
    if len(fields) >= 2:
        return fields[1].decode("ascii", errors="replace")
    return None


def _is_failed_compare(line: bytes) -> bool:
    value = _record(line)
    if not value.startswith(b"PIN"):
        return False
    value = value.split(b"//", 1)[0]
    fields = value.split()
    return (
        len(fields) == 7
        and fields[0] == b"PIN"
        and fields[2] == b"EXP"
        and fields[4] == b"GOT"
        and fields[6] == b"FAIL"
    )


def scan_frames(
    source: ByteSource, batch_cycles: int = DEFAULT_BATCH_CYCLES
) -> Iterator[LogFrame]:
    """Stream the input as prologue, block, cycle, trailer, and end frames."""
    if batch_cycles < 1:
        raise ValueError("batch_cycles must be positive")

    kind = FrameKind.PROLOGUE
    block: str | None = None
    buffer = bytearray()
    frame_line = 1
    frame_byte = 0
    line_no = 1
    byte_no = 0

    in_cycle = False
    current_vector: int | None = None
    current_time: int | None = None
    current_fail_count = 0
    partial_cycle_offset = 0
    partial_cycle_line = 0
    partial_cycle_byte = 0

    vectors: list[int] = []
    times: list[int] = []
    fail_vectors: list[int] = []
    fail_count = 0

    def make_frame(
        frame_kind: FrameKind,
        data: bytes,
        start_line: int,
        start_byte: int,
        *,
        cycle_limit: int | None = None,
    ) -> LogFrame:
        count = len(vectors) if cycle_limit is None else cycle_limit
        return LogFrame(
            kind=frame_kind,
            data=data,
            start_line=start_line,
            start_byte=start_byte,
            block_name=block,
            cycle_count=count if frame_kind is FrameKind.CYCLE_BATCH else 0,
            cycle_vectors=tuple(vectors[:count]),
            cycle_times=tuple(times[:count]),
            fail_count=fail_count if frame_kind is FrameKind.CYCLE_BATCH else 0,
            fail_vectors=tuple(fail_vectors)
            if frame_kind is FrameKind.CYCLE_BATCH
            else (),
        )

    def reset_frame(
        new_kind: FrameKind, start_line: int, start_byte: int
    ) -> None:
        nonlocal kind, frame_line, frame_byte, fail_count
        kind = new_kind
        frame_line = start_line
        frame_byte = start_byte
        buffer.clear()
        vectors.clear()
        times.clear()
        fail_vectors.clear()
        fail_count = 0

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
            if kind is not FrameKind.CYCLE_BATCH or len(vectors) >= batch_cycles:
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
            yield make_frame(kind, bytes(buffer), frame_line, frame_byte)
            reset_frame(boundary, line_no, byte_no)
            if boundary is FrameKind.BLOCK_START:
                block = _block_name(line)
            elif boundary is FrameKind.END_LOG:
                block = None
        elif boundary is not None and not buffer:
            reset_frame(boundary, line_no, byte_no)
            if boundary is FrameKind.BLOCK_START:
                block = _block_name(line)
            elif boundary is FrameKind.END_LOG:
                block = None

        if marker == "cycle" and not in_cycle:
            match = _CYCLE_RE.match(_record(line))
            if match is not None:
                current_vector = int(match.group(1))
                current_time = int(match.group(2))
            in_cycle = True
            current_fail_count = 0
            partial_cycle_offset = len(buffer)
            partial_cycle_line = line_no
            partial_cycle_byte = byte_no
        elif in_cycle and _is_failed_compare(line):
            current_fail_count += 1
        elif marker == "cycle_end" and in_cycle:
            in_cycle = False
            if current_vector is not None and current_time is not None:
                vectors.append(current_vector)
                times.append(current_time)
                if current_fail_count:
                    fail_vectors.append(current_vector)
                    fail_count += current_fail_count
            current_vector = None
            current_time = None

        buffer.extend(line)
        byte_no += len(line)
        line_no += 1

    if not buffer:
        return

    if in_cycle:
        complete = bytes(buffer[:partial_cycle_offset])
        if complete:
            yield make_frame(
                FrameKind.CYCLE_BATCH,
                complete,
                frame_line,
                frame_byte,
                cycle_limit=len(vectors),
            )
        yield LogFrame(
            kind=FrameKind.TRUNCATED_TAIL,
            data=bytes(buffer[partial_cycle_offset:]),
            start_line=partial_cycle_line,
            start_byte=partial_cycle_byte,
            block_name=block,
        )
        return

    yield make_frame(kind, bytes(buffer), frame_line, frame_byte)
    if kind is not FrameKind.END_LOG:
        yield LogFrame(
            kind=FrameKind.TRUNCATED_TAIL,
            data=b"",
            start_line=line_no,
            start_byte=byte_no,
            block_name=block,
        )


def rebase_error_line(frame: LogFrame, frame_relative_line: int) -> int:
    return frame.start_line + frame_relative_line - 1
