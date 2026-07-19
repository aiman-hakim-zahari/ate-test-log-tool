"""Phase 1 tests.

M3 (transformer), M4 (generator oracle) and M5 (error mapping) are **active** as
of Step 1.  M6 (raw-byte framing scanner, truncation salvage) and M7 (two-tier
semantic validation) remain skipped stubs encoding the Step 2 gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ate_fa_suite.model.entities import (
    BlockId,
    FailCategory,
    LogicState,
    ParseComplete,
    ParseFailed,
    PinDef,
    PinDirection,
    PinTiming,
    TestRun,
    TimingSet,
)
from ate_fa_suite.model.waveform import resolve_timing
from ate_fa_suite.parsing.parser import LogParser
from ate_fa_suite.parsing.validator import ValidationError, parse_timescale
from tools.gen_log import generate

pytestmark = pytest.mark.phase1

SAMPLE_LOGS = Path(__file__).resolve().parent.parent / "sample_logs"


def parse_golden(name: str) -> TestRun:
    result = LogParser().parse(SAMPLE_LOGS / f"{name}.atelog", job_id=1)
    assert isinstance(result, ParseComplete), result
    return result.run


@pytest.fixture(scope="module")
def multi_fail_run() -> TestRun:
    return parse_golden("multi_fail")


@pytest.fixture(scope="module")
def multi_fail_lines() -> list[str]:
    return (SAMPLE_LOGS / "multi_fail.atelog").read_text(
        encoding="utf-8"
    ).splitlines()


# =============================================================================
# M3 — transformer to dataclasses
# =============================================================================


def test_transformer_populates_every_dataclass_field(
    multi_fail_run: TestRun,
) -> None:
    header = multi_fail_run.header
    assert header.wafer == "14"
    assert header.device == "S32K344_QFN48"
    assert header.tester == "UFLEX-BLR-22"
    assert header.program == "s32k_prod_r3.1"
    assert header.date == "2026-07-11T03:14:22"
    assert header.timescale_ns == 1.0

    assert [p.name for p in multi_fail_run.pins] == [
        "CLK", "RST_N", "DQ0", "DQ1", "DQ2", "DQ3", "TDO",
    ]
    assert multi_fail_run.pins[0].direction is PinDirection.IN
    assert multi_fail_run.pins[-1].direction is PinDirection.OUT

    # No FailureEvent field may be left at a default by accident.
    for failure in multi_fail_run.failures:
        assert failure.pin
        assert failure.src_line > 0
        assert failure.location.vector > 0
        assert failure.category is not FailCategory.INCONSISTENT
        assert failure.cycle_period > 0


def test_metadata_value_keeps_literal_double_slash(
    multi_fail_run: TestRun,
) -> None:
    """On metadata lines `//` is value text, not a comment — so the LOT value
    keeps it.  This is the observable consequence of carrying comments on the
    NEWLINE terminal instead of `%ignore COMMENT`."""
    assert "//" in multi_fail_run.header.lot
    assert multi_fail_run.header.lot.startswith("K78842A-07B")


def test_transformer_assigns_block_occurrence_in_document_order(
    multi_fail_run: TestRun,
) -> None:
    """The golden re-runs `mbist_march_c`; the two invocations must stay
    separate, because block NAMES are not unique run-wide."""
    assert [b.id for b in multi_fail_run.blocks] == [
        BlockId("mbist_march_c", 1),
        BlockId("mbist_march_c", 2),
        BlockId("scan_chain_a", 1),
    ]
    assert [b.id.label() for b in multi_fail_run.blocks] == [
        "mbist_march_c", "mbist_march_c#2", "scan_chain_a",
    ]


def test_failures_are_attributed_to_the_right_invocation(
    multi_fail_run: TestRun,
) -> None:
    """Vector 1200 exists in BOTH invocations. Keying on the bare vector would
    merge them; keying on VectorLocation does not."""
    at_1200 = [f for f in multi_fail_run.failures if f.location.vector == 1200]
    occurrences = {f.location.block.occurrence for f in at_1200}
    assert occurrences == {1, 2}
    assert len({f.location for f in at_1200}) == 2


def test_src_line_round_trips_to_the_raw_log_line(
    multi_fail_run: TestRun, multi_fail_lines: list[str]
) -> None:
    """propagate_positions=True exists so a table row links back to the raw
    line.  Every failure's src_line must land on ITS OWN compare record — not
    on the enclosing CYCLE header."""
    for failure in multi_fail_run.failures:
        raw = multi_fail_lines[failure.src_line - 1]
        assert raw.lstrip().startswith("PIN "), raw
        assert failure.pin in raw
        assert "FAIL" in raw


def test_block_results_carry_declared_failsummary_vectors(
    multi_fail_run: TestRun,
) -> None:
    first, second, third = multi_fail_run.blocks
    assert first.declared_fail_vectors == (1200, 1201, 1202, 4400, 4401)
    assert second.declared_fail_vectors == (1200, 1201)
    assert third.declared_fail_vectors == (3,)
    # fail_count is OBSERVED, and is pin-granular like the declared count.
    assert (first.fail_count, second.fail_count, third.fail_count) == (10, 2, 1)


def test_block_vector_ranges(multi_fail_run: TestRun) -> None:
    first = multi_fail_run.blocks[0]
    assert (first.first_vector, first.last_vector) == (1200, 4401)


def test_clean_pass_round_trips_with_zero_failures() -> None:
    run = parse_golden("clean_pass")
    assert run.failures == ()
    assert len(run.blocks) == 1
    # The zero-failure block omits FAILSUMMARY entirely.
    assert run.blocks[0].declared_fail_vectors == ()
    assert run.blocks[0].fail_count == 0


@pytest.mark.parametrize("golden", ["clean_pass", "multi_fail"])
def test_crlf_golden_round_trips_through_the_strict_path(golden: str) -> None:
    text = (SAMPLE_LOGS / f"{golden}.atelog").read_text(encoding="utf-8")
    lf = LogParser().parse_text(text, job_id=1)
    crlf = LogParser().parse_text(text.replace("\n", "\r\n"), job_id=1)
    assert isinstance(lf, ParseComplete) and isinstance(crlf, ParseComplete)
    assert lf.run.failures == crlf.run.failures
    assert lf.run.header == crlf.run.header


def test_every_fail_category_is_exercised_by_the_golden(
    multi_fail_run: TestRun,
) -> None:
    found = {f.category for f in multi_fail_run.failures}
    assert found == {
        FailCategory.SA0_CANDIDATE,
        FailCategory.SA1_CANDIDATE,
        FailCategory.FLOATING,
        FailCategory.OPEN_TRISTATE,
        FailCategory.WEAK_DRIVE,
        FailCategory.OTHER,
    }


def test_passing_compares_never_become_failures(multi_fail_run: TestRun) -> None:
    """classify() returns None for a passing compare; the FAIL flag alone
    decides membership."""
    for failure in multi_fail_run.failures:
        assert failure.expected is not failure.actual


def test_timescale_is_converted_to_nanoseconds() -> None:
    assert parse_timescale("1ns") == 1.0
    assert parse_timescale("10ps") == pytest.approx(0.01)
    assert parse_timescale("2us") == 2000.0
    assert parse_timescale("1ms") == 1_000_000.0


# --- assembly-time timing resolution (§6.3) ---------------------------------


def test_strobe_time_equals_cycle_time_under_nrz_idealization(
    multi_fail_run: TestRun,
) -> None:
    """ADF-1 v1 emits no TIMESET, so the chain terminates at NRZ and the
    resolved strobe is the cycle instant itself."""
    for failure in multi_fail_run.failures:
        assert failure.strobe_time == failure.location.time
        assert failure.strobe_window is None


def test_cycle_period_is_resolved_from_neighbouring_cycles(
    multi_fail_run: TestRun,
) -> None:
    for failure in multi_fail_run.failures:
        assert failure.cycle_period == 1000


def test_cycle_period_ignores_a_gap_between_capture_regions(
    multi_fail_run: TestRun,
) -> None:
    """The golden jumps 1202 -> 4400.  A naive forward delta would hand the
    cycle at the gap edge a period of the whole jump, and §6.2 sizes the
    mismatch band as a fraction of cycle_period — painting a band thousands of
    times too wide.  A gap between capture regions is not a clock period."""
    edge = [
        f for f in multi_fail_run.failures
        if f.location.vector in (1202, 4400) and f.location.block.occurrence == 1
    ]
    assert edge, "expected failures on both sides of the gap"
    for failure in edge:
        assert failure.cycle_period == 1000


def test_resolve_timing_walks_the_full_chain() -> None:
    """Cycle.timeset -> TimingSet entry -> PinDef.timing -> None (NRZ)."""
    from_timeset = PinTiming(strobe=40)
    from_pindef = PinTiming(strobe=10)
    timing_sets = (TimingSet("fast", (("DQ0", from_timeset),)),)
    pindef = PinDef("DQ0", PinDirection.IO, timing=from_pindef)

    # 1. the named timeset wins
    assert resolve_timing("DQ0", "fast", timing_sets, pindef) is from_timeset
    # 2. timeset named but silent on this pin -> fall through to the pin default
    assert resolve_timing("DQ1", "fast", timing_sets, PinDef(
        "DQ1", PinDirection.IO, timing=from_pindef
    )) is from_pindef
    # 3. no timeset -> pin default
    assert resolve_timing("DQ0", None, timing_sets, pindef) is from_pindef
    # 4. nothing anywhere -> NRZ idealization
    assert resolve_timing("DQ0", None, (), PinDef("DQ0", PinDirection.IO)) is None
    # 5. undeclared pin (M7 auto-declares IO) -> no declaration to fall back to
    assert resolve_timing("DQ9", None, (), None) is None


def test_wave_collections_are_empty_until_phase_2(
    multi_fail_run: TestRun,
) -> None:
    """Honest emptiness, not a placeholder: building them is Phase 2 M3."""
    assert multi_fail_run.driven_waves == ()
    assert multi_fail_run.expected_waves == ()
    assert multi_fail_run.captured_waves == ()


# =============================================================================
# M4 — the generator is the property-test oracle
# =============================================================================


@pytest.mark.parametrize("seed", [1, 42, 1337])
@pytest.mark.parametrize("blocks", [1, 3])
def test_generated_failures_round_trip_exactly(seed: int, blocks: int) -> None:
    """Generator emits N seeded failures -> parse -> exactly those N
    FailureEvents, with matching block invocation, vector, pin and states.

    This is the check no hand-written golden can give you: the oracle knows the
    ground truth independently of the parser.
    """
    import io

    buffer = io.StringIO()
    injected = generate(
        buffer, cycles=400, pins=8, fail_rate=0.02, seed=seed, blocks=blocks
    )
    result = LogParser().parse_text(buffer.getvalue(), job_id=7)
    assert isinstance(result, ParseComplete), result

    expected = {
        (f.occurrence, f.vector, f.pin, f.expected, f.actual) for f in injected
    }
    actual = {
        (
            f.location.block.occurrence,
            f.location.vector,
            f.pin,
            f.expected.value,
            f.actual.value,
        )
        for f in result.run.failures
    }
    assert len(injected) == len(result.run.failures)
    assert actual == expected


def test_generated_block_invocations_stay_separate() -> None:
    """gen_log reuses ONE pattern name across blocks on purpose, so anything
    keying on the bare name collapses invocations that must stay apart."""
    import io

    buffer = io.StringIO()
    generate(buffer, cycles=100, pins=6, fail_rate=0.05, seed=5, blocks=3)
    result = LogParser().parse_text(buffer.getvalue(), job_id=1)
    assert isinstance(result, ParseComplete)

    ids = [b.id for b in result.run.blocks]
    assert len({b.name for b in ids}) == 1  # one name...
    assert [b.occurrence for b in ids] == [1, 2, 3]  # ...three invocations
    assert len(set(ids)) == 3


def test_declared_failsummary_matches_observed_on_generated_logs() -> None:
    """The generator emits a self-consistent FAILSUMMARY; if it did not, M7's
    cross-check would fire on a log we control and the fixture would be
    useless."""
    import io

    buffer = io.StringIO()
    generate(buffer, cycles=300, pins=8, fail_rate=0.02, seed=11)
    result = LogParser().parse_text(buffer.getvalue(), job_id=1)
    assert isinstance(result, ParseComplete)

    for block in result.run.blocks:
        observed_vectors = sorted(
            {
                f.location.vector
                for f in result.run.failures
                if f.location.block == block.id
            }
        )
        assert list(block.declared_fail_vectors) == observed_vectors


@pytest.mark.perf
def test_parse_throughput_baseline_is_recorded() -> None:
    """Deselected by default (`-m "not perf"`); run with `pytest -m perf`.

    Records rather than asserts a rate: hardware varies, and a throughput
    assertion would be a flaky test rather than a baseline.  The recorded
    figures live in perf_baseline.json via tools/perf_baseline.py.
    """
    import io
    import time

    buffer = io.StringIO()
    injected = generate(buffer, cycles=2000, pins=16, fail_rate=0.0005, seed=42)
    text = buffer.getvalue()
    size_mib = len(text.encode("utf-8")) / 1_048_576

    started = time.perf_counter()
    result = LogParser().parse_text(text, job_id=1)
    elapsed = time.perf_counter() - started

    assert isinstance(result, ParseComplete)
    assert len(result.run.failures) == len(injected)
    print(
        f"\nparse throughput: {size_mib / elapsed:.2f} MiB/s "
        f"({size_mib:.2f} MiB in {elapsed:.2f}s)"
    )


# =============================================================================
# M5 — error mapping to ParseFailed
# =============================================================================


def test_syntax_error_becomes_positioned_parse_failed() -> None:
    text = (
        "#ATELOG v1.0\nLOT: L\nWAFER: 1\nDEVICE: D\nTESTER: T\nPROGRAM: P\n"
        "DATE: 2026-07-11T00:00:00\nTIMESCALE: 1ns\nPINDEF CLK IN\n"
        "TESTBLOCK blk\nCYCLE 1 T=0\nPIN CLK DRV @\nEND CYCLE\n"
        "END TESTBLOCK\nEND LOG\n"
    )
    result = LogParser().parse_text(text, job_id=3)
    assert isinstance(result, ParseFailed)
    assert result.job_id == 3
    assert result.line == 12
    assert result.column > 0
    assert result.context == "PIN CLK DRV @"
    assert result.message


def test_truncated_golden_maps_to_parse_failed_at_the_break() -> None:
    """The strict path rejects truncated input BY DESIGN; salvage is M6."""
    path = SAMPLE_LOGS / "truncated.atelog"
    result = LogParser().parse(path, job_id=9)
    assert isinstance(result, ParseFailed)
    assert result.job_id == 9

    lines = path.read_text(encoding="utf-8").splitlines()
    assert result.line == len(lines)
    assert result.context == lines[result.line - 1]
    assert "GOT" in result.context


def test_parse_failed_carries_the_offending_source_line() -> None:
    """FA engineers always want the raw line, so it rides on the message rather
    than requiring the UI to re-read the file."""
    result = LogParser().parse(SAMPLE_LOGS / "truncated.atelog", job_id=1)
    assert isinstance(result, ParseFailed)
    assert result.context.strip().startswith("PIN ")


@pytest.mark.parametrize(
    ("mutation", "expected_fragment"),
    [
        ("TIMESCALE: 1fortnight", "unit"),
        ("TIMESCALE: banana", "unit"),
        ("TIMESCALE: 0ns", "positive"),
    ],
)
def test_bad_timescale_is_fatal(mutation: str, expected_fragment: str) -> None:
    text = (SAMPLE_LOGS / "clean_pass.atelog").read_text(encoding="utf-8")
    result = LogParser().parse_text(
        text.replace("TIMESCALE: 1ns", mutation), job_id=1
    )
    assert isinstance(result, ParseFailed)
    assert expected_fragment in result.message
    assert result.line == 8


def test_missing_metadata_key_is_fatal() -> None:
    text = (SAMPLE_LOGS / "clean_pass.atelog").read_text(encoding="utf-8")
    result = LogParser().parse_text(
        text.replace("WAFER: 14\n", ""), job_id=1
    )
    assert isinstance(result, ParseFailed)
    assert "WAFER" in result.message


def test_duplicate_metadata_key_is_fatal() -> None:
    text = (SAMPLE_LOGS / "clean_pass.atelog").read_text(encoding="utf-8")
    result = LogParser().parse_text(
        text.replace("WAFER: 14\n", "WAFER: 14\nWAFER: 15\n"), job_id=1
    )
    assert isinstance(result, ParseFailed)
    assert "duplicate" in result.message
    assert "WAFER" in result.message


def test_validation_error_carries_its_line() -> None:
    error = ValidationError("boom", line=42, column=3)
    assert (error.line, error.column, error.message) == (42, 3, "boom")


def test_parse_never_raises_for_malformed_input() -> None:
    """Truncate a golden at many offsets: every one must come back as a typed
    message, never an exception.  A crash on a half-written tester dump is the
    failure mode this whole design exists to avoid."""
    text = (SAMPLE_LOGS / "multi_fail.atelog").read_text(encoding="utf-8")
    parser = LogParser()
    for cut in range(0, len(text), 97):
        result = parser.parse_text(text[:cut], job_id=1)
        assert isinstance(result, (ParseComplete, ParseFailed))


def test_empty_input_is_a_clean_parse_failed() -> None:
    result = LogParser().parse_text("", job_id=1)
    assert isinstance(result, ParseFailed)
    assert result.context == ""


def test_job_id_is_echoed_on_both_message_types() -> None:
    """Every worker message carries its job-generation ID, or the pump cannot
    discard superseded jobs (§2.3)."""
    ok = LogParser().parse(SAMPLE_LOGS / "clean_pass.atelog", job_id=101)
    bad = LogParser().parse(SAMPLE_LOGS / "truncated.atelog", job_id=202)
    assert ok.job_id == 101
    assert bad.job_id == 202


def test_parse_complete_records_elapsed_time() -> None:
    result = LogParser().parse(SAMPLE_LOGS / "clean_pass.atelog", job_id=1)
    assert isinstance(result, ParseComplete)
    assert result.elapsed_s >= 0.0


def test_chunked_path_is_not_yet_wired() -> None:
    """Guards the Step 1/Step 2 boundary: asking for the chunked path must fail
    loudly rather than silently falling back to the strict one."""
    with pytest.raises(NotImplementedError):
        LogParser(chunked=True).parse(
            SAMPLE_LOGS / "clean_pass.atelog", job_id=1
        )


# =============================================================================
# M6 — raw-byte framing scanner and truncation salvage (Step 2)
# =============================================================================

step2 = pytest.mark.skip(reason="Phase 1 M6/M7 — Step 2; see docs/ROADMAP.md")


@step2
def test_frames_reassemble_byte_for_byte() -> None:
    """The lossless-framing property test: concatenating every emitted frame's
    raw bytes reproduces the input EXACTLY, original line endings included.
    This is what proves framing is total — every line in exactly one frame."""


@step2
def test_every_frame_parses_independently_with_its_fragment_rule() -> None:
    """Each frame is self-contained and maps 1:1 to a fragment start rule of
    the multi-start grammar."""


@step2
def test_crlf_golden_survives_the_chunked_path() -> None:
    """Frames are untouched byte slices; normalization exists only in the
    classification view."""


@step2
def test_comment_containing_end_cycle_does_not_split_a_frame() -> None:
    """Frame markers are recognized by LEADING TOKEN only, so comment or value
    text can never fake a boundary."""


@step2
def test_trivia_at_a_batch_boundary_attaches_to_the_preceding_frame() -> None:
    """Blank/comment lines immediately before and after a ~5k-cycle boundary:
    a fragment must start with its marker token, so leading trivia would lex to
    an orphan NEWLINE."""


@step2
def test_truncated_golden_salvages_exactly_three_cycles() -> None:
    """Every complete cycle before the break is recovered, block identity and
    the FAILSUMMARY cross-check are preserved, and the tail becomes a
    TestRun.warnings entry with its ABSOLUTE line number — delivered as a
    partial ParseComplete, never a crash."""


@step2
def test_frame_relative_error_positions_are_rebased_to_absolute() -> None:
    """Lark reports positions within the frame it was handed; all user-facing
    reporting stays absolute in the source file."""


# =============================================================================
# M7 — two-tier semantic validation (Step 2)
# =============================================================================


@step2
@pytest.mark.parametrize(
    "rule",
    [
        "unsupported_major_version",
        "non_increasing_vector_within_block",
        "non_increasing_time_within_block",
    ],
)
def test_fatal_tier_yields_parse_failed(rule: str) -> None:
    """One malformed golden per rule, asserting tier, message and source line.

    Note the metadata rules (missing/duplicate key, unparseable TIMESCALE) are
    already active above — they landed in Step 1 because LogHeader cannot be
    built without them.
    """


@step2
@pytest.mark.parametrize(
    "rule",
    [
        "duplicate_pindef_first_wins",
        "undeclared_pin_auto_declared_io",
        "duplicate_pin_event_in_cycle_first_wins",
        "reserved_word_as_identifier",
        "failsummary_count_mismatch",
        "failsummary_vector_list_mismatch",
    ],
)
def test_recoverable_tier_yields_warning_and_deterministic_rule(rule: str) -> None:
    """Warning text carries the source line, and the recovery rule is
    deterministic — not "whatever the dict happened to keep"."""


@step2
def test_failsummary_count_is_pin_granular_not_vector_granular() -> None:
    """multi_fail block 1 declares `FAILSUMMARY 10 VECTORS 1200,1201,1202,4400,
    4401`: 10 failing COMPARE LINES across 5 distinct vectors.  A validator that
    conflated the two would warn on a correct log — so assert the well-formed
    golden produces NO FAILSUMMARY warning."""


@step2
def test_failsummary_checks_are_reported_as_separate_warnings() -> None:
    """A log with a right count and a wrong VECTORS list must warn about the
    vector list ONLY, naming which witness disagreed."""


@step2
def test_model_invariants_are_not_delegated_to_the_validator() -> None:
    """WaveformSegment/WaveformSeries/TimingSet invariants live in the model as
    __post_init__ ValueErrors: `assert` vanishes under `python -O`, and
    builder-side checks do not guard alternate construction paths."""
