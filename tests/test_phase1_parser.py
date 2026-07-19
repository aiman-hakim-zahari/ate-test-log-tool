"""Phase 1 parser, framing, and validation tests."""

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
from ate_fa_suite.parsing.parser import LogParser, build_parser
from ate_fa_suite.parsing.transformer import (
    UNKNOWN_CYCLE_PERIOD,
    AteLogTransformer,
    ParsedDocument,
    assemble_run,
)
from ate_fa_suite.parsing.validator import (
    ValidationError,
    parse_timescale,
    validate,
)
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
    """Metadata values may contain a literal double slash."""
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
    """Each failure points to its compare line, not its cycle header."""
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


@pytest.mark.parametrize("magnitude", ["NaN", "nan", "1e309", "-1e309", "inf"])
def test_timescale_rejects_non_finite_values(magnitude: str) -> None:
    """Values accepted by float() can still be invalid timescales."""
    with pytest.raises(ValidationError):
        parse_timescale(f"{magnitude}ns")


@pytest.mark.parametrize("magnitude", ["NaN", "1e309"])
def test_non_finite_timescale_is_a_parse_failure_not_a_parse_complete(
    magnitude: str,
) -> None:
    text = (SAMPLE_LOGS / "clean_pass.atelog").read_text(encoding="utf-8")
    result = LogParser().parse_text(
        text.replace("TIMESCALE: 1ns", f"TIMESCALE: {magnitude}ns"), job_id=1
    )
    assert isinstance(result, ParseFailed)
    assert "finite" in result.message


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
    """A discarded vector range is not one long clock period."""
    edge = [
        f for f in multi_fail_run.failures
        if f.location.vector in (1202, 4400) and f.location.block.occurrence == 1
    ]
    assert edge, "expected failures on both sides of the gap"
    for failure in edge:
        assert failure.cycle_period == 1000


def _two_capture_log(v0: int, t0: int, v1: int, t1: int) -> str:
    return (
        "#ATELOG v1.0\nLOT: L\nWAFER: 1\nDEVICE: D\nTESTER: T\nPROGRAM: P\n"
        "DATE: 2026-07-11T00:00:00\nTIMESCALE: 1ns\n\nPINDEF DQ0 IO\n\n"
        "TESTBLOCK blk\n"
        f"CYCLE {v0} T={t0}\nPIN DQ0 EXP 1 GOT 0 FAIL\nEND CYCLE\n"
        f"CYCLE {v1} T={t1}\nPIN DQ0 EXP 1 GOT 0 FAIL\nEND CYCLE\n"
        f"FAILSUMMARY 2 VECTORS {v0},{v1}\nEND TESTBLOCK\nEND LOG\n"
    )


def test_isolated_capture_gap_is_not_treated_as_a_cycle_period() -> None:
    """Period estimation divides time gaps by their vector distance."""
    result = LogParser().parse_text(
        _two_capture_log(1, 0, 100, 100_000), job_id=1
    )
    assert isinstance(result, ParseComplete)
    periods = {f.cycle_period for f in result.run.failures}
    assert periods == {100_000 // 99}  # 1010 ns/vector, not 100000


def test_adjacent_cycles_are_unaffected_by_the_normalization() -> None:
    """Vector distance 1 makes the normalization a no-op, so contiguous logs
    keep the exact raw delta."""
    result = LogParser().parse_text(_two_capture_log(7, 7000, 8, 8000), job_id=1)
    assert isinstance(result, ParseComplete)
    assert {f.cycle_period for f in result.run.failures} == {1000}


def test_single_cycle_block_reports_the_unknown_period_sentinel() -> None:
    """No neighbour, therefore no delta.  The renderer clamps band width to a
    pixel minimum, so this degrades to the minimum band, not to an invention."""
    text = (
        "#ATELOG v1.0\nLOT: L\nWAFER: 1\nDEVICE: D\nTESTER: T\nPROGRAM: P\n"
        "DATE: 2026-07-11T00:00:00\nTIMESCALE: 1ns\n\nPINDEF DQ0 IO\n\n"
        "TESTBLOCK blk\nCYCLE 1 T=0\nPIN DQ0 EXP 1 GOT 0 FAIL\nEND CYCLE\n"
        "FAILSUMMARY 1 VECTORS 1\nEND TESTBLOCK\nEND LOG\n"
    )
    result = LogParser().parse_text(text, job_id=1)
    assert isinstance(result, ParseComplete)
    assert result.run.failures[0].cycle_period == UNKNOWN_CYCLE_PERIOD


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
    """Parsed failures must match the generator's independent record."""
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
    """Record a rate without asserting hardware-dependent performance."""
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
    result = LogParser(chunked=False).parse(path, job_id=9)
    assert isinstance(result, ParseFailed)
    assert result.job_id == 9

    lines = path.read_text(encoding="utf-8").splitlines()
    assert result.line == len(lines)
    assert result.context == lines[result.line - 1]
    assert "GOT" in result.context


def test_parse_failed_carries_the_offending_source_line() -> None:
    """FA engineers always want the raw line, so it rides on the message rather
    than requiring the UI to re-read the file."""
    result = LogParser(chunked=False).parse(
        SAMPLE_LOGS / "truncated.atelog", job_id=1
    )
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
    """Many truncation points must all return typed results."""
    text = (SAMPLE_LOGS / "multi_fail.atelog").read_text(encoding="utf-8")
    parser = LogParser()
    for cut in range(0, len(text), 97):
        result = parser.parse_text(text[:cut], job_id=1)
        assert isinstance(result, (ParseComplete, ParseFailed))


def test_missing_file_becomes_parse_failed_not_an_exception(
    tmp_path: Path,
) -> None:
    """The worker MUST emit a typed message: an exception escaping the parse
    thread leaves the GUI's job pending forever, with the progress bar stuck."""
    result = LogParser().parse(tmp_path / "does_not_exist.atelog", job_id=5)
    assert isinstance(result, ParseFailed)
    assert result.job_id == 5
    assert "cannot read" in result.message


def test_non_utf8_file_becomes_parse_failed(tmp_path: Path) -> None:
    """Real tester output is not always clean UTF-8 — Latin-1 operator
    comments, stray control bytes from an aborted transfer."""
    path = tmp_path / "latin1.atelog"
    path.write_bytes(b"#ATELOG v1.0\nLOT: caf\xe9 crash\n")
    result = LogParser().parse(path, job_id=6)
    assert isinstance(result, ParseFailed)
    assert result.job_id == 6
    assert "UTF-8" in result.message


def test_directory_instead_of_file_becomes_parse_failed(tmp_path: Path) -> None:
    result = LogParser().parse(tmp_path, job_id=1)
    assert isinstance(result, ParseFailed)


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


def test_chunked_path_is_the_default() -> None:
    result = LogParser().parse(SAMPLE_LOGS / "clean_pass.atelog", job_id=1)
    assert isinstance(result, ParseComplete)


# =============================================================================
# Validation handoff — the evidence M7 needs must survive to the validator
# =============================================================================


def transform_only(text: str) -> ParsedDocument:
    """Transform without assembling, so tests can inspect the evidence."""
    tree = build_parser().parse(text, start="document")
    return AteLogTransformer().transform(tree)


DUPLICATE_PINDEF_LOG = """#ATELOG v2.3
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


def test_parsed_document_retains_the_declared_version() -> None:
    """LogHeader has no version field, so without this the `#ATELOG` version is
    unrecoverable and M7's version check is unimplementable."""
    document = transform_only(DUPLICATE_PINDEF_LOG)
    assert document.header.magic == "#ATELOG v2.3"
    assert (document.header.major_version, document.header.minor_version) == (2, 3)
    assert document.header.src_line == 1


def test_parsed_document_retains_duplicate_pindefs() -> None:
    """De-duplicating at transform time would destroy the evidence that there
    was anything to warn about."""
    document = transform_only(DUPLICATE_PINDEF_LOG)
    assert [p.name for p in document.pins] == ["DQ0", "DQ0", "CLK"]


def test_duplicate_pindef_resolves_first_wins_at_assembly() -> None:
    """M7's deterministic rule is FIRST wins; a plain dict comprehension keeps
    the LAST, which would quietly disagree with the warning the validator emits
    about the very same log."""
    document = transform_only(DUPLICATE_PINDEF_LOG)
    run = assemble_run(document)
    assert run.pins[0].direction is PinDirection.IO  # the first declaration
    # ...and the run still carries both declarations for the validator.
    assert len([p for p in run.pins if p.name == "DQ0"]) == 2


def test_parsed_document_retains_cycles_with_source_lines() -> None:
    """Monotonic vector/time and duplicate-pin-event checks all need cycles,
    which assembly discards (§6.3)."""
    document = transform_only(DUPLICATE_PINDEF_LOG)
    cycles = document.blocks[0].cycles
    assert [pc.cycle.vector for pc in cycles] == [5, 6]
    assert [pc.cycle.time for pc in cycles] == [5000, 6000]
    assert all(pc.cycle.src_line > 0 for pc in cycles)


def test_parsed_document_retains_passing_compares() -> None:
    """A non-masked PASS with disagreeing states is an M7 warning, so passing
    compares cannot be dropped before validation."""
    document = transform_only(DUPLICATE_PINDEF_LOG)
    first_cycle = document.blocks[0].cycles[0].cycle
    assert [c.passed for c in first_cycle.compares] == [True, False]
    assert [d.pin for d in first_cycle.drives] == ["CLK"]


def test_parsed_document_retains_declared_failsummary_count() -> None:
    """TestBlockResult has no field for the DECLARED count — only the observed
    one and the declared vectors — so the count check needs this."""
    document = transform_only(DUPLICATE_PINDEF_LOG)
    block = document.blocks[0]
    assert block.declared_fail_count == 99  # deliberately wrong in the fixture
    assert block.declared_fail_vectors == (5, 6)
    # ...and the observed count genuinely differs, so M7 has something to catch.
    assert assemble_run(document).blocks[0].fail_count == 2


def test_validate_accepts_a_parsed_document_not_a_test_run() -> None:
    document = transform_only(
        (SAMPLE_LOGS / "clean_pass.atelog").read_text(encoding="utf-8")
    )
    report = validate(document)
    assert report.document == document
    assert report.warnings == ()
