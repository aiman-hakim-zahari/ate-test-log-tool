"""Load the shared grammar and expose strict and indexed parse paths."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import replace
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable, Final, cast

from lark import Lark
from lark.exceptions import (
    UnexpectedCharacters,
    UnexpectedEOF,
    UnexpectedInput,
    UnexpectedToken,
    VisitError,
)

from ate_fa_suite.model.entities import (
    ParseComplete,
    ParseFailed,
    ParseProgress,
    PinDef,
)
from ate_fa_suite.model.waveform import DEFAULT_RETENTION_W
from ate_fa_suite.parsing.chunked_reader import (
    FrameKind,
    InvalidUtf8Error,
    LogFrame,
    scan_frames,
)
from ate_fa_suite.parsing.transformer import (
    AteLogTransformer,
    LocatedCompare,
    LocatedDrive,
    ParsedBlock,
    ParsedCycle,
    ParsedDocument,
    ParsedHeader,
    assemble_run,
)
from ate_fa_suite.parsing.validator import (
    SUPPORTED_MAJOR_VERSIONS,
    ValidationError,
    validate,
)

GRAMMAR_PACKAGE: Final = "ate_fa_suite.parsing.grammar"
GRAMMAR_FILENAME: Final = "atelog.lark"
STARTS: Final[list[str]] = [
    "document",
    "prologue",
    "testblock_header",
    "cycle_batch",
    "block_trailer",
    "end_log",
]

ProgressCallback = Callable[[ParseProgress], None]


def load_grammar_text() -> str:
    """Read the grammar from installed package data."""
    return (
        files(GRAMMAR_PACKAGE).joinpath(GRAMMAR_FILENAME).read_text(encoding="utf-8")
    )


@lru_cache(maxsize=1)
def build_parser() -> Lark:
    """Build the shared LALR parser once."""
    return Lark(
        load_grammar_text(),
        parser="lalr",
        lexer="contextual",
        start=STARTS,
        propagate_positions=True,
        maybe_placeholders=False,
        cache=True,
    )


def source_line(text: str, line: int) -> str:
    """Return one 1-based source line, or an empty string."""
    if line < 1:
        return ""
    lines = text.splitlines()
    if line > len(lines):
        return ""
    return lines[line - 1].rstrip("\r")


def describe_error(error: UnexpectedInput) -> str:
    """Turn a Lark error into a useful message."""
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


class _FrameParseError(Exception):
    def __init__(self, result: ParseFailed) -> None:
        self.result = result


def _frame_context(frame: LogFrame, relative_line: int) -> str:
    try:
        return source_line(frame.text(), relative_line)
    except UnicodeDecodeError:
        return ""


def _parse_frame(frame: LogFrame, job_id: int) -> Any:
    """Parse one frame and convert relative positions to file positions."""
    try:
        text = frame.text()
    except UnicodeDecodeError as error:
        raise _FrameParseError(
            ParseFailed(
                job_id=job_id,
                line=frame.start_line,
                column=0,
                message=f"log is not valid UTF-8 (byte {error.start}: {error.reason})",
                context="",
            )
        ) from None

    try:
        tree = build_parser().parse(text, start=cast(str, frame.kind.start_rule))
        return AteLogTransformer().transform(tree)
    except UnexpectedInput as error:
        relative_line = getattr(error, "line", 0) or 0
        absolute_line = frame.start_line + max(relative_line, 1) - 1
        raise _FrameParseError(
            ParseFailed(
                job_id=job_id,
                line=absolute_line,
                column=getattr(error, "column", 0) or 0,
                message=describe_error(error),
                context=_frame_context(frame, relative_line),
            )
        ) from None
    except VisitError as wrapper:
        if isinstance(wrapper.orig_exc, ValidationError):
            validation_error = wrapper.orig_exc
            raise _FrameParseError(
                ParseFailed(
                    job_id=job_id,
                    line=frame.start_line + validation_error.line - 1,
                    column=validation_error.column,
                    message=validation_error.message,
                    context=_frame_context(frame, validation_error.line),
                )
            ) from None
        raise


def _rebase_header(header: ParsedHeader, offset: int) -> ParsedHeader:
    return replace(header, src_line=header.src_line + offset)


def _rebase_cycle(parsed: ParsedCycle, offset: int) -> ParsedCycle:
    events = tuple(
        replace(item, src_line=item.src_line + offset) for item in parsed.events
    )
    return replace(
        parsed,
        cycle=replace(parsed.cycle, src_line=parsed.cycle.src_line + offset),
        compare_lines=tuple(line + offset for line in parsed.compare_lines),
        events=events,
    )


def _combined_cycle_frame(frames: list[LogFrame]) -> LogFrame:
    first = frames[0]
    return LogFrame(
        kind=FrameKind.CYCLE_BATCH,
        data=b"".join(frame.data for frame in frames),
        start_line=first.start_line,
        start_byte=first.start_byte,
        block_name=first.block_name,
    )


class LogParser:
    """Parse a path into a typed success or failure message."""

    def __init__(
        self,
        *,
        chunked: bool = True,
        retention_w: int = DEFAULT_RETENTION_W,
    ) -> None:
        if retention_w < 0:
            raise ValueError("retention_w cannot be negative")
        self._chunked = chunked
        self._retention_w = retention_w

    def parse(
        self,
        path: Path,
        job_id: int,
        on_progress: ProgressCallback | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> ParseComplete | ParseFailed:
        """Parse a UTF-8 log file."""
        if self._chunked:
            return self._parse_indexed(
                path, job_id, on_progress=on_progress, is_cancelled=is_cancelled
            )
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            return ParseFailed(
                job_id=job_id,
                line=0,
                column=0,
                message=(
                    f"{path.name} is not valid UTF-8 "
                    f"(byte {error.start}: {error.reason})"
                ),
                context="",
            )
        except OSError as error:
            return ParseFailed(
                job_id=job_id,
                line=0,
                column=0,
                message=f"cannot read {path}: {error.strerror or error}",
                context="",
            )
        return self.parse_text(text, job_id)

    def parse_text(self, text: str, job_id: int) -> ParseComplete | ParseFailed:
        """Use the strict whole-file path, mainly for tests and auditing."""
        started = time.perf_counter()
        try:
            tree = build_parser().parse(text, start="document")
            try:
                document = AteLogTransformer().transform(tree)
            except VisitError as wrapper:
                if isinstance(wrapper.orig_exc, ValidationError):
                    raise wrapper.orig_exc from None
                raise
            report = validate(document)
            run = assemble_run(report.document, warnings=report.warnings)
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

    def _parse_indexed(
        self,
        path: Path,
        job_id: int,
        *,
        on_progress: ProgressCallback | None,
        is_cancelled: Callable[[], bool] | None,
    ) -> ParseComplete | ParseFailed:
        """Index every cycle, then parse only failure retention windows."""
        started = time.perf_counter()
        try:
            total_bytes = path.stat().st_size
            source = path.open("rb")
        except OSError as error:
            return ParseFailed(
                job_id=job_id,
                line=0,
                column=0,
                message=f"cannot read {path}: {error.strerror or error}",
                context="",
            )

        prologue: tuple[ParsedHeader, tuple[PinDef, ...], tuple[int, ...]] | None = None
        blocks: list[ParsedBlock] = []
        warnings: list[str] = []

        block_name: str | None = None
        block_line = 0
        first_vector: int | None = None
        last_vector: int | None = None
        previous_vector: int | None = None
        previous_time: int | None = None
        declared_count: int | None = None
        declared_vectors: tuple[int, ...] = ()
        summary_line: int | None = None
        recent: deque[LogFrame] = deque()
        retained: list[LogFrame] = []
        retained_offsets: set[int] = set()
        retain_until: int | None = None
        cycle_total = 0
        fail_total = 0
        progress_byte = 0
        saw_end_log = False
        saw_truncated_tail = False

        def keep(frame: LogFrame) -> None:
            if frame.start_byte not in retained_offsets:
                retained.append(frame)
                retained_offsets.add(frame.start_byte)

        def reset_block() -> None:
            nonlocal block_name, block_line, first_vector, last_vector
            nonlocal previous_vector, previous_time, declared_count
            nonlocal declared_vectors, summary_line, retain_until
            block_name = None
            block_line = 0
            first_vector = None
            last_vector = None
            previous_vector = None
            previous_time = None
            declared_count = None
            declared_vectors = ()
            summary_line = None
            retain_until = None
            recent.clear()
            retained.clear()
            retained_offsets.clear()

        def finish_block() -> None:
            if block_name is None:
                return
            parsed_cycles: list[ParsedCycle] = []
            retained.sort(key=lambda frame: frame.start_byte)
            groups: list[list[LogFrame]] = []
            for frame in retained:
                if (
                    groups
                    and groups[-1][-1].start_byte + len(groups[-1][-1].data)
                    == frame.start_byte
                ):
                    groups[-1].append(frame)
                else:
                    groups.append([frame])
            for group in groups:
                combined = _combined_cycle_frame(group)
                parsed = cast(tuple[ParsedCycle, ...], _parse_frame(combined, job_id))
                offset = combined.start_line - 1
                parsed_cycles.extend(_rebase_cycle(cycle, offset) for cycle in parsed)

            blocks.append(
                ParsedBlock(
                    name=block_name,
                    src_line=block_line,
                    cycles=tuple(parsed_cycles),
                    declared_fail_count=declared_count,
                    declared_fail_vectors=declared_vectors,
                    failsummary_line=summary_line,
                    indexed_first_vector=first_vector,
                    indexed_last_vector=last_vector,
                )
            )
            reset_block()

        try:
            with source:
                for frame in scan_frames(source, batch_cycles=1):
                    if is_cancelled is not None and is_cancelled():
                        return ParseFailed(
                            job_id=job_id,
                            line=frame.start_line,
                            column=0,
                            message="parse cancelled",
                            context="",
                        )

                    if frame.kind is FrameKind.PROLOGUE:
                        raw = cast(
                            tuple[ParsedHeader, tuple[PinDef, ...], tuple[int, ...]],
                            _parse_frame(frame, job_id),
                        )
                        offset = frame.start_line - 1
                        prologue = (
                            _rebase_header(raw[0], offset),
                            raw[1],
                            tuple(line + offset for line in raw[2]),
                        )
                    elif frame.kind is FrameKind.BLOCK_START:
                        if block_name is not None:
                            raise _FrameParseError(
                                ParseFailed(
                                    job_id=job_id,
                                    line=frame.start_line,
                                    column=0,
                                    message=(
                                        "new TESTBLOCK started before "
                                        "END TESTBLOCK"
                                    ),
                                    context=_frame_context(frame, 1),
                                )
                            )
                        name, relative_line = cast(
                            tuple[str, int], _parse_frame(frame, job_id)
                        )
                        block_name = name
                        block_line = frame.start_line + relative_line - 1
                    elif frame.kind is FrameKind.CYCLE_BATCH:
                        if block_name is None:
                            _parse_frame(frame, job_id)
                            raise ValidationError(
                                "cycle found outside a test block", frame.start_line
                            )
                        vector = frame.cycle_vectors[0]
                        cycle_time = frame.cycle_times[0]
                        if previous_vector is not None and vector <= previous_vector:
                            raise _FrameParseError(
                                ParseFailed(
                                    job_id=job_id,
                                    line=frame.start_line,
                                    column=0,
                                    message=(
                                        "cycle vectors must be strictly "
                                        "increasing within a block"
                                    ),
                                    context=_frame_context(frame, 1),
                                )
                            )
                        if previous_time is not None and cycle_time <= previous_time:
                            raise _FrameParseError(
                                ParseFailed(
                                    job_id=job_id,
                                    line=frame.start_line,
                                    column=0,
                                    message=(
                                        "cycle times must be strictly "
                                        "increasing within a block"
                                    ),
                                    context=_frame_context(frame, 1),
                                )
                            )
                        previous_vector = vector
                        previous_time = cycle_time
                        first_vector = (
                            vector if first_vector is None else first_vector
                        )
                        last_vector = vector
                        cycle_total += 1
                        fail_total += frame.fail_count

                        recent.append(frame)
                        while (
                            recent
                            and vector - recent[0].cycle_vectors[0]
                            > self._retention_w
                        ):
                            recent.popleft()

                        if frame.fail_count:
                            for nearby in recent:
                                keep(nearby)
                            retain_until = max(
                                retain_until or vector,
                                vector + self._retention_w,
                            )
                        if retain_until is not None and vector <= retain_until:
                            keep(frame)
                    elif frame.kind is FrameKind.BLOCK_TRAILER:
                        raw_count, raw_vectors, raw_line = cast(
                            tuple[int | None, tuple[int, ...], int | None],
                            _parse_frame(frame, job_id),
                        )
                        declared_count = raw_count
                        declared_vectors = raw_vectors
                        summary_line = (
                            frame.start_line + raw_line - 1
                            if raw_line is not None
                            else None
                        )
                        finish_block()
                    elif frame.kind is FrameKind.END_LOG:
                        if block_name is not None:
                            raise _FrameParseError(
                                ParseFailed(
                                    job_id=job_id,
                                    line=frame.start_line,
                                    column=0,
                                    message="END LOG found before END TESTBLOCK",
                                    context=_frame_context(frame, 1),
                                )
                            )
                        _parse_frame(frame, job_id)
                        saw_end_log = True
                    elif frame.kind is FrameKind.TRUNCATED_TAIL:
                        saw_truncated_tail = True
                        if block_name is not None:
                            finish_block()
                        warnings.append(
                            f"line {frame.start_line}: truncated input; "
                            "kept complete cycles before this line"
                        )

                    bytes_done = frame.start_byte + len(frame.data)
                    if (
                        on_progress is not None
                        and (
                            bytes_done - progress_byte >= 1_048_576
                            or bytes_done >= total_bytes
                        )
                    ):
                        on_progress(
                            ParseProgress(
                                job_id=job_id,
                                bytes_done=bytes_done,
                                bytes_total=total_bytes,
                                cycles=cycle_total,
                                fails=fail_total,
                            )
                        )
                        progress_byte = bytes_done

            if prologue is None:
                return ParseFailed(
                    job_id=job_id,
                    line=1,
                    column=0,
                    message="missing or incomplete log prologue",
                    context="",
                )
            if saw_end_log and not blocks:
                return ParseFailed(
                    job_id=job_id,
                    line=1,
                    column=0,
                    message="log must contain at least one TESTBLOCK",
                    context="",
                )
            if not saw_end_log and not saw_truncated_tail:
                return ParseFailed(
                    job_id=job_id,
                    line=0,
                    column=0,
                    message="missing END LOG",
                    context="",
                )
            header, pins, pin_lines = prologue
            document = ParsedDocument(
                header=header,
                pins=pins,
                blocks=tuple(blocks),
                pin_lines=pin_lines,
            )
            report = validate(document)
            run = assemble_run(
                report.document,
                warnings=tuple(warnings) + report.warnings,
            )
        except _FrameParseError as error:
            return error.result
        except InvalidUtf8Error as error:
            return ParseFailed(
                job_id=job_id,
                line=error.line,
                column=0,
                message=(
                    f"{path.name} is not valid UTF-8 "
                    f"(byte {error.byte}: {error.reason})"
                ),
                context="",
            )
        except ValidationError as error:
            return ParseFailed(
                job_id=job_id,
                line=error.line,
                column=error.column,
                message=error.message,
                context="",
            )
        except OSError as error:
            return ParseFailed(
                job_id=job_id,
                line=0,
                column=0,
                message=f"cannot read {path}: {error.strerror or error}",
                context="",
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
