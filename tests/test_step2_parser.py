"""Step 2 framing and semantic-validation tests."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest

from ate_fa_suite.model.entities import (
    ParseComplete,
    ParseFailed,
    PinDirection,
    PinTiming,
    TimingSet,
)
from ate_fa_suite.parsing.chunked_reader import (
    DEFAULT_BATCH_CYCLES,
    FrameKind,
    LogFrame,
    scan_frames,
)
from ate_fa_suite.parsing.parser import LogParser, build_parser
from ate_fa_suite.parsing import parser as parser_module

pytestmark = pytest.mark.phase1

SAMPLE_LOGS = Path(__file__).resolve().parent.parent / "sample_logs"
MALFORMED_LOGS = SAMPLE_LOGS / "malformed"


def test_frames_reassemble_byte_for_byte() -> None:
    for name in ("clean_pass", "multi_fail", "truncated"):
        data = (SAMPLE_LOGS / f"{name}.atelog").read_bytes()
        frames = tuple(scan_frames(BytesIO(data), batch_cycles=2))
        assert b"".join(frame.data for frame in frames) == data


def test_every_complete_frame_parses_with_its_fragment_rule() -> None:
    data = (SAMPLE_LOGS / "multi_fail.atelog").read_bytes()
    for frame in scan_frames(BytesIO(data), batch_cycles=2):
        if frame.kind is not FrameKind.TRUNCATED_TAIL:
            build_parser().parse(frame.text(), start=frame.kind.start_rule)


def test_crlf_golden_survives_the_indexed_path(tmp_path: Path) -> None:
    text = (SAMPLE_LOGS / "multi_fail.atelog").read_text(encoding="utf-8")
    path = tmp_path / "crlf.atelog"
    path.write_bytes(text.replace("\n", "\r\n").encode())
    result = LogParser().parse(path, job_id=1)
    assert isinstance(result, ParseComplete)
    assert len(result.run.failures) == 13


def test_comment_marker_text_does_not_split_a_frame() -> None:
    data = (SAMPLE_LOGS / "clean_pass.atelog").read_bytes().replace(
        b"PIN DQ1 EXP 1 GOT 1 PASS\n",
        b"PIN DQ1 EXP 1 GOT 1 PASS\n// END CYCLE is only comment text\n",
        1,
    )
    cycles = [
        frame
        for frame in scan_frames(BytesIO(data), batch_cycles=1)
        if frame.kind is FrameKind.CYCLE_BATCH
    ]
    assert sum(
        sum(line.startswith(b"CYCLE ") for line in frame.data.splitlines())
        for frame in cycles
    ) == 3


def test_boundary_trivia_belongs_to_the_previous_frame() -> None:
    data = (SAMPLE_LOGS / "clean_pass.atelog").read_bytes().replace(
        b"END CYCLE\nCYCLE 101",
        b"END CYCLE\n\n// batch boundary\nCYCLE 101",
    )
    cycles = [
        frame
        for frame in scan_frames(BytesIO(data), batch_cycles=1)
        if frame.kind is FrameKind.CYCLE_BATCH
    ]
    assert cycles[0].data.endswith(b"\n// batch boundary\n")
    assert cycles[1].data.startswith(b"CYCLE 101")
    for frame in cycles:
        build_parser().parse(frame.text(), start="cycle_batch")


def test_truncated_golden_salvages_three_cycles() -> None:
    result = LogParser().parse(SAMPLE_LOGS / "truncated.atelog", job_id=1)
    assert isinstance(result, ParseComplete)
    assert [failure.location.vector for failure in result.run.failures] == [
        1200,
        1201,
        1202,
    ]
    assert result.run.blocks[0].last_vector == 1202
    assert result.run.warnings == (
        "line 45: truncated input; kept complete cycles before this line",
    )


def test_every_emitted_complete_frame_reaches_lark_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines = [
        "#ATELOG v1.0",
        "LOT: L",
        "WAFER: 1",
        "DEVICE: D",
        "TESTER: T",
        "PROGRAM: P",
        "DATE: 2026-07-11T00:00:00",
        "TIMESCALE: 1ns",
        "PINDEF DQ0 IO",
        "TESTBLOCK blk",
    ]
    for vector in range(1, 201):
        flag = "FAIL" if vector == 100 else "PASS"
        actual = "0" if flag == "FAIL" else "1"
        lines.extend(
            [
                f"CYCLE {vector} T={vector * 1000}",
                f"PIN DQ0 EXP 1 GOT {actual} {flag}",
                "END CYCLE",
            ]
        )
    lines.extend(
        [
            "FAILSUMMARY 1 VECTORS 100",
            "END TESTBLOCK",
            "END LOG",
            "",
        ]
    )
    path = tmp_path / "sparse-fail.atelog"
    path.write_text("\n".join(lines), encoding="utf-8")

    expected_frames = [
        (frame.kind, frame.start_byte)
        for frame in scan_frames(BytesIO(path.read_bytes()))
        if frame.kind is not FrameKind.TRUNCATED_TAIL
    ]
    parsed_frames: list[tuple[FrameKind, int]] = []
    parsed_cycle_headers = 0
    real_parse_frame = parser_module._parse_frame

    def counting_parse_frame(frame: object, job_id: int) -> object:
        nonlocal parsed_cycle_headers
        assert isinstance(frame, parser_module.LogFrame)
        parsed_frames.append((frame.kind, frame.start_byte))
        if frame.kind is FrameKind.CYCLE_BATCH:
            parsed_cycle_headers += frame.data.count(b"\nCYCLE ")
            if frame.data.startswith(b"CYCLE "):
                parsed_cycle_headers += 1
        return real_parse_frame(frame, job_id)

    monkeypatch.setattr(parser_module, "_parse_frame", counting_parse_frame)
    result = LogParser(retention_w=2).parse(path, job_id=1)
    assert isinstance(result, ParseComplete)
    assert parsed_frames == expected_frames
    assert parsed_cycle_headers == 200
    assert len(result.run.failures) == 1


def test_malformed_passing_cycle_outside_retention_is_fatal(
    tmp_path: Path,
) -> None:
    lines = [
        "#ATELOG v1.0",
        "LOT: L",
        "WAFER: 1",
        "DEVICE: D",
        "TESTER: T",
        "PROGRAM: P",
        "DATE: 2026-07-11T00:00:00",
        "TIMESCALE: 1ns",
        "PINDEF DQ0 IO",
        "TESTBLOCK blk",
    ]
    for vector in range(1, 21):
        actual = "0" if vector == 18 else "1"
        result = "FAIL" if vector == 18 else "PASS"
        state = "@" if vector == 2 else "1"
        lines.extend(
            [
                f"CYCLE {vector} T={vector * 100}",
                f"PIN DQ0 EXP {state} GOT {actual} {result}",
                "END CYCLE",
            ]
        )
    lines.extend(
        ["FAILSUMMARY 1 VECTORS 18", "END TESTBLOCK", "END LOG", ""]
    )
    path = tmp_path / "bad-unretained-pass.atelog"
    path.write_text("\n".join(lines), encoding="utf-8")

    result = LogParser(retention_w=0).parse(path, job_id=1)
    assert isinstance(result, ParseFailed)
    assert result.line == 15
    assert (
        result.message
        == "unexpected VALUE '@ GOT 1 PASS'; expected one of: STATE"
    )
    assert result.context == "PIN DQ0 EXP @ GOT 1 PASS"


def test_frame_error_line_is_rebased_to_the_file(tmp_path: Path) -> None:
    text = (SAMPLE_LOGS / "multi_fail.atelog").read_text(encoding="utf-8")
    bad_line = next(
        index
        for index, line in enumerate(text.splitlines(), 1)
        if "PIN DQ1 EXP 0 GOT 1 FAIL" in line
    )
    text = text.replace("PIN DQ1 EXP 0 GOT 1 FAIL", "PIN DQ1 EXP @ GOT 1 FAIL", 1)
    path = tmp_path / "bad-frame.atelog"
    path.write_text(text, encoding="utf-8")
    result = LogParser().parse(path, job_id=1)
    assert isinstance(result, ParseFailed)
    assert result.line == bad_line
    assert result.context.startswith("PIN DQ1 EXP @ GOT 1 FAIL")


def test_index_checks_order_even_outside_failure_windows(tmp_path: Path) -> None:
    text = (SAMPLE_LOGS / "clean_pass.atelog").read_text(encoding="utf-8")
    text = text.replace("CYCLE 101 T=101000", "CYCLE 100 T=101000")
    path = tmp_path / "bad-order.atelog"
    path.write_text(text, encoding="utf-8")
    result = LogParser().parse(path, job_id=1)
    assert isinstance(result, ParseFailed)
    assert "vectors" in result.message
    assert result.context.startswith("CYCLE 100 T=101000")


def test_index_rejects_non_utf8_in_a_skipped_cycle(tmp_path: Path) -> None:
    data = (SAMPLE_LOGS / "clean_pass.atelog").read_bytes().replace(
        b"PIN DQ0 EXP 0 GOT 0 PASS",
        b"PIN DQ0 EXP 0 GOT 0 PASS // bad byte: \xff",
        1,
    )
    path = tmp_path / "bad-encoding.atelog"
    path.write_bytes(data)
    result = LogParser().parse(path, job_id=1)
    assert isinstance(result, ParseFailed)
    assert "UTF-8" in result.message


def test_end_log_cannot_replace_end_testblock(tmp_path: Path) -> None:
    text = (SAMPLE_LOGS / "clean_pass.atelog").read_text(encoding="utf-8")
    path = tmp_path / "missing-block-end.atelog"
    path.write_text(text.replace("END TESTBLOCK\n", ""), encoding="utf-8")
    result = LogParser().parse(path, job_id=1)
    assert isinstance(result, ParseFailed)
    assert "END TESTBLOCK" in result.message


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (("#ATELOG v1.0", "#ATELOG v2.0"), "unsupported"),
        (("CYCLE 101 T=101000", "CYCLE 100 T=101000"), "vectors"),
        (("CYCLE 101 T=101000", "CYCLE 101 T=100000"), "times"),
    ],
)
def test_fatal_validation_returns_parse_failed(
    mutation: tuple[str, str], expected: str
) -> None:
    text = (SAMPLE_LOGS / "clean_pass.atelog").read_text(encoding="utf-8")
    result = LogParser().parse_text(text.replace(*mutation), job_id=1)
    assert isinstance(result, ParseFailed)
    assert expected in result.message
    assert result.line > 0


DUPLICATE_LOG = """#ATELOG v1.3
LOT: L
WAFER: 1
DEVICE: D
TESTER: T
PROGRAM: P
DATE: 2026-07-11T00:00:00
TIMESCALE: 1ns
PINDEF DQ0 IO
PINDEF DQ0 IN
PINDEF CLK IN
TESTBLOCK blk
CYCLE 5 T=5000
PIN CLK DRV 1
PIN DQ0 EXP 1 GOT 1 PASS
PIN DQ0 EXP 1 GOT 0 FAIL
END CYCLE
CYCLE 6 T=6000
PIN DQ0 EXP 0 GOT 1 FAIL
END CYCLE
FAILSUMMARY 99 VECTORS 5,6
END TESTBLOCK
END LOG
"""


@pytest.mark.parametrize(
    "rule",
    [
        "duplicate_pindef",
        "undeclared_pin",
        "duplicate_event",
        "reserved_identifier",
        "summary_count",
        "summary_vectors",
    ],
)
def test_recoverable_validation_warns_and_normalizes(rule: str) -> None:
    if rule in {"duplicate_pindef", "duplicate_event"}:
        text = DUPLICATE_LOG
    else:
        text = (SAMPLE_LOGS / "multi_fail.atelog").read_text(encoding="utf-8")

    if rule == "duplicate_pindef":
        expected = "duplicate PINDEF"
    elif rule == "undeclared_pin":
        text = text.replace("PIN DQ1 EXP 0 GOT 1 FAIL", "PIN NEW EXP 0 GOT 1 FAIL", 1)
        expected = "undeclared pin"
    elif rule == "duplicate_event":
        expected = "duplicate event"
    elif rule == "reserved_identifier":
        text = text.replace("PINDEF CLK IN", "PINDEF END IN").replace(
            "PIN CLK DRV", "PIN END DRV"
        )
        expected = "reserved identifier"
    elif rule == "summary_count":
        text = text.replace("FAILSUMMARY 10 VECTORS", "FAILSUMMARY 9 VECTORS", 1)
        expected = "FAILSUMMARY count"
    else:
        text = text.replace(
            "VECTORS 1200,1201,1202,4400,4401",
            "VECTORS 1200,1201",
            1,
        )
        expected = "FAILSUMMARY VECTORS"

    result = LogParser().parse_text(text, job_id=1)
    assert isinstance(result, ParseComplete)
    matching = [warning for warning in result.run.warnings if expected in warning]
    assert matching and matching[0].startswith("line ")

    if rule == "duplicate_pindef":
        dq0 = [pin for pin in result.run.pins if pin.name == "DQ0"]
        assert len(dq0) == 1 and dq0[0].direction is PinDirection.IO
    elif rule == "undeclared_pin":
        new_pin = next(pin for pin in result.run.pins if pin.name == "NEW")
        assert new_pin.direction is PinDirection.IO
    elif rule == "duplicate_event":
        assert all(failure.location.vector != 5 for failure in result.run.failures)


def test_failsummary_checks_are_independent() -> None:
    text = (SAMPLE_LOGS / "multi_fail.atelog").read_text(encoding="utf-8")
    text = text.replace(
        "VECTORS 1200,1201,1202,4400,4401", "VECTORS 1200", 1
    )
    result = LogParser().parse_text(text, job_id=1)
    assert isinstance(result, ParseComplete)
    warnings = [
        warning for warning in result.run.warnings if "FAILSUMMARY" in warning
    ]
    assert len(warnings) == 1
    assert "VECTORS" in warnings[0]


def test_correct_pin_granular_summary_has_no_warning() -> None:
    text = (SAMPLE_LOGS / "multi_fail.atelog").read_text(encoding="utf-8")
    result = LogParser().parse_text(text, job_id=1)
    assert isinstance(result, ParseComplete)
    assert not any("FAILSUMMARY" in warning for warning in result.run.warnings)


def test_model_still_enforces_its_own_invariants() -> None:
    with pytest.raises(ValueError):
        TimingSet(
            name="bad",
            entries=(("B", PinTiming()), ("A", PinTiming())),
        )


class _ShortReadSource:
    def __init__(self, data: bytes, read_size: int) -> None:
        self._source = BytesIO(data)
        self._read_size = read_size

    def read(self, size: int = -1, /) -> bytes:
        if size < 0:
            size = self._read_size
        return self._source.read(min(size, self._read_size))


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize("read_size", [1, 7, 65_536])
def test_framing_is_lossless_across_endings_and_source_read_sizes(
    newline: bytes, read_size: int
) -> None:
    text = (SAMPLE_LOGS / "clean_pass.atelog").read_text(encoding="utf-8")
    text = text.replace(
        "PIN DQ1 EXP 1 GOT 1 PASS\n",
        "PIN DQ1 EXP 1 GOT 1 PASS\n"
        "// TESTBLOCK fake CYCLE 999 END CYCLE FAILSUMMARY END LOG\n",
        1,
    )
    data = newline.join(line.encode() for line in text.splitlines()) + newline
    frames = tuple(
        scan_frames(_ShortReadSource(data, read_size), batch_cycles=2)
    )

    assert b"".join(frame.data for frame in frames) == data
    cursor = 0
    line = 1
    for frame in frames:
        assert frame.start_byte == cursor
        assert frame.start_line == line
        cursor += len(frame.data)
        line += frame.data.count(b"\n")
        if frame.kind is not FrameKind.TRUNCATED_TAIL:
            build_parser().parse(frame.text(), start=frame.kind.start_rule)


def test_default_batch_boundary_is_approximately_five_thousand_cycles() -> None:
    lines = [
        "#ATELOG v1.0",
        "LOT: L",
        "WAFER: 1",
        "DEVICE: D",
        "TESTER: T",
        "PROGRAM: P",
        "DATE: 2026-07-11T00:00:00",
        "TIMESCALE: 1ns",
        "PINDEF DQ0 IO",
        "TESTBLOCK blk",
    ]
    for vector in range(1, DEFAULT_BATCH_CYCLES + 2):
        lines.extend(
            [
                f"CYCLE {vector} T={vector * 10}",
                "PIN DQ0 EXP 1 GOT 1 PASS",
                "END CYCLE",
            ]
        )
        if vector == DEFAULT_BATCH_CYCLES:
            lines.extend(["", "// trivia at the real batch boundary"])
    lines.extend(["END TESTBLOCK", "END LOG", ""])
    data = "\n".join(lines).encode()

    batches = [
        frame
        for frame in scan_frames(BytesIO(data))
        if frame.kind is FrameKind.CYCLE_BATCH
    ]
    assert len(batches) == 2
    assert [
        sum(line.startswith(b"CYCLE ") for line in frame.data.splitlines())
        for frame in batches
    ] == [DEFAULT_BATCH_CYCLES, 1]
    assert batches[0].data.endswith(
        b"END CYCLE\n\n// trivia at the real batch boundary\n"
    )
    for frame in batches:
        build_parser().parse(frame.text(), start="cycle_batch")


def test_complete_cycle_missing_end_is_not_salvaged(tmp_path: Path) -> None:
    text = (SAMPLE_LOGS / "clean_pass.atelog").read_text(encoding="utf-8")
    prefix, suffix = text.rsplit("END CYCLE\n", 1)
    text = prefix + suffix
    path = tmp_path / "malformed-complete-cycle.atelog"
    path.write_text(text, encoding="utf-8")

    result = LogParser().parse(path, job_id=1)
    assert isinstance(result, ParseFailed)
    assert result.line > 0
    assert "truncated" not in result.message


@pytest.mark.parametrize(
    ("name", "line", "message"),
    [
        (
            "unsupported_major",
            1,
            "unsupported ADF major version 2",
        ),
        (
            "missing_metadata",
            1,
            "missing required metadata key(s): LOT",
        ),
        (
            "duplicate_metadata",
            3,
            "duplicate metadata key 'LOT'",
        ),
        (
            "invalid_timescale",
            8,
            "TIMESCALE must be positive, got '0ns'",
        ),
        (
            "non_increasing_vector",
            16,
            "cycle vectors must be strictly increasing within a block",
        ),
        (
            "non_increasing_time",
            16,
            "cycle times must be strictly increasing within a block",
        ),
    ],
)
def test_fatal_malformed_fixtures_have_exact_diagnostics(
    name: str, line: int, message: str
) -> None:
    path = MALFORMED_LOGS / f"{name}.atelog"
    strict = LogParser().parse_text(path.read_text(encoding="utf-8"), job_id=9)
    chunked = LogParser().parse(path, job_id=9)

    for result in (strict, chunked):
        assert isinstance(result, ParseFailed)
        assert result.line == line
        assert result.message == message
        assert result.context == path.read_text(
            encoding="utf-8"
        ).splitlines()[line - 1]


@pytest.mark.parametrize(
    ("name", "warnings"),
    [
        (
            "duplicate_pindef",
            (
                "line 10: duplicate PINDEF 'DQ0'; "
                "keeping the first declaration",
            ),
        ),
        (
            "undeclared_pin",
            ("line 14: undeclared pin 'NEW'; added as IO",),
        ),
        (
            "duplicate_cycle_event",
            (
                "line 15: duplicate event for pin 'DQ0' in "
                "cycle 1; keeping the first",
            ),
        ),
        (
            "reserved_identifier",
            (
                "line 9: reserved identifier 'END' used as a pin",
                "line 14: reserved identifier 'END' used as a pin",
            ),
        ),
        (
            "summary_count_mismatch",
            (
                "line 16: FAILSUMMARY count 2 does not match "
                "1 failing compare lines",
            ),
        ),
        (
            "summary_vector_mismatch",
            (
                "line 16: FAILSUMMARY VECTORS (2,) does not match (1,)",
            ),
        ),
    ],
)
def test_recoverable_malformed_fixtures_have_exact_normalized_results(
    name: str, warnings: tuple[str, ...]
) -> None:
    path = MALFORMED_LOGS / f"{name}.atelog"
    strict = LogParser().parse_text(path.read_text(encoding="utf-8"), job_id=9)
    chunked = LogParser().parse(path, job_id=9)

    assert isinstance(strict, ParseComplete)
    assert isinstance(chunked, ParseComplete)
    assert strict.run == chunked.run
    assert strict.run.warnings == warnings
    assert len(strict.run.failures) == 1

    pins = {pin.name: pin.direction for pin in strict.run.pins}
    if name == "duplicate_pindef":
        assert pins["DQ0"] is PinDirection.IO
        assert len([pin for pin in strict.run.pins if pin.name == "DQ0"]) == 1
    elif name == "undeclared_pin":
        assert pins["NEW"] is PinDirection.IO
    elif name == "duplicate_cycle_event":
        assert strict.run.failures[0].actual.value == "0"


@pytest.mark.parametrize(
    "key",
    ["LOT", "WAFER", "DEVICE", "TESTER", "PROGRAM", "DATE", "TIMESCALE"],
)
def test_removing_each_required_metadata_key_is_fatal(key: str) -> None:
    path = MALFORMED_LOGS / "summary_count_mismatch.atelog"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    text = "".join(line for line in lines if not line.startswith(f"{key}:"))

    result = LogParser().parse_text(text, job_id=1)
    assert isinstance(result, ParseFailed)
    assert result.line == 1
    assert result.message == f"missing required metadata key(s): {key}"


@pytest.mark.parametrize(
    "path",
    [
        SAMPLE_LOGS / "clean_pass.atelog",
        SAMPLE_LOGS / "multi_fail.atelog",
        *sorted(MALFORMED_LOGS.glob("*.atelog")),
    ],
    ids=lambda path: path.stem,
)
def test_strict_and_chunked_paths_agree_on_complete_fixtures(
    path: Path,
) -> None:
    strict = LogParser().parse_text(path.read_text(encoding="utf-8"), job_id=4)
    chunked = LogParser().parse(path, job_id=4)

    assert type(strict) is type(chunked)
    if isinstance(strict, ParseFailed):
        assert isinstance(chunked, ParseFailed)
        assert (
            strict.line,
            strict.column,
            strict.message,
            strict.context,
        ) == (
            chunked.line,
            chunked.column,
            chunked.message,
            chunked.context,
        )
    else:
        assert isinstance(strict, ParseComplete)
        assert isinstance(chunked, ParseComplete)
        assert strict.run == chunked.run


@pytest.mark.perf
def test_one_million_cycle_chunked_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.gen_log import write_log

    path = tmp_path / "generated_1m.atelog"
    injected = write_log(
        path,
        cycles=1_000_000,
        pins=3,
        fail_rate=0.000_02,
        seed=42,
        blocks=2,
    )

    largest_frame = 0
    real_parse_frame = parser_module._parse_frame

    def recording_parse_frame(frame: LogFrame, job_id: int) -> object:
        nonlocal largest_frame
        largest_frame = max(largest_frame, len(frame.data))
        return real_parse_frame(frame, job_id)

    monkeypatch.setattr(parser_module, "_parse_frame", recording_parse_frame)
    result = LogParser().parse(path, job_id=1)

    assert isinstance(result, ParseComplete)
    expected = {
        (failure.occurrence, failure.vector, failure.pin)
        for failure in injected
    }
    actual = {
        (
            failure.location.block.occurrence,
            failure.location.vector,
            failure.pin,
        )
        for failure in result.run.failures
    }
    assert actual == expected
    assert len(result.run.failures) == len(injected)
    assert largest_frame < path.stat().st_size // 50
