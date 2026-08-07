"""Phase 5 Step 8: shared-plan round trips, goldens, fuzzing, and perf."""

from __future__ import annotations

import io
import random
from dataclasses import fields
from pathlib import Path

import pytest

from ate_fa_suite.model.entities import (
    BlockId,
    FailureEvent,
    LogicState,
    ParseComplete,
    ParseFailed,
    TestRun,
    VectorLocation,
)
from ate_fa_suite.parsing.parser import LogParser
from ate_fa_suite.parsing.stdf import StdfParser
from ate_fa_suite.parsing.stdf.codec import (
    BIG_ENDIAN,
    LITTLE_ENDIAN,
    DecodedRecord,
    decode_record,
)
from ate_fa_suite.parsing.stdf.reader import detect_endianness, iter_records
from ate_fa_suite.parsing.stdf.records import SPECS, STR
from tools.gen_log import plan_run, write_adf1
from tools.perf_stdf import (
    FailureProjection,
    measure,
    project_plan,
    project_run,
)
from tools.stdf_writer import (
    StdfWriteOptions,
    encode_stdf,
    write_golden_corpus,
)

pytestmark = pytest.mark.phase5

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS = REPO_ROOT / "sample_logs" / "stdf"
CORPUS_NAMES = {
    "golden_le.stdf",
    "golden_be.stdf",
    "continuation.stdf",
    "data_bit_1.stdf",
    "data_bit_2.stdf",
    "data_bit_4.stdf",
    "data_bit_8.stdf",
    "nonzero_cyc_base.stdf",
    "mask_map_9pin.stdf",
    "missing_exp_data.stdf",
    "fail_memory_truncated.stdf",
    "truncated.stdf",
}


def _parse_stdf(path: Path, job_id: int = 1) -> TestRun:
    result = StdfParser().parse(path, job_id=job_id)
    assert isinstance(result, ParseComplete), getattr(result, "message", result)
    return result.run


def _str_records(path: Path) -> tuple[DecodedRecord, ...]:
    header = path.read_bytes()[:4]
    endian = detect_endianness(header)
    assert endian is not None, f"{path.name} has no FAR header"
    decoded: list[DecodedRecord] = []
    with path.open("rb") as source:
        for raw in iter_records(source, endian):
            spec = SPECS.get(raw.key)
            if spec is STR:
                decoded.append(decode_record(STR, raw.payload, endian))
    return tuple(decoded)


@pytest.mark.parametrize(("seed", "blocks"), [(1, 1), (42, 3), (1337, 4)])
def test_one_plan_is_the_oracle_for_both_encodings(
    tmp_path: Path, seed: int, blocks: int
) -> None:
    """Neither encoder may make new random choices after planning."""
    plan = plan_run(
        cycles=96,
        pins=9,
        fail_rate=0.15,
        seed=seed,
        blocks=blocks,
        period=7,
        start_vector=1_000_003,
    )
    # A passing block could be omitted by a production STDF file and therefore
    # renumber later occurrences.  This high-rate property corpus deliberately
    # gives every invocation a witness so block identity is compared too.
    assert all(block.failures for block in plan.blocks)

    adf = io.StringIO()
    write_adf1(adf, plan)
    adf_result = LogParser().parse_text(adf.getvalue(), job_id=11)
    assert isinstance(adf_result, ParseComplete), adf_result

    stdf_path = tmp_path / f"roundtrip-{seed}-{blocks}.stdf"
    stdf_path.write_bytes(encode_stdf(plan))
    stdf_result = StdfParser().parse(stdf_path, job_id=12)
    assert isinstance(stdf_result, ParseComplete), stdf_result

    oracle = project_plan(plan)
    assert oracle
    assert project_run(adf_result.run) == oracle
    assert project_run(stdf_result.run) == oracle
    assert len(adf_result.run.failures) == len(stdf_result.run.failures)


def test_pass_only_blocks_keep_later_occurrence_identity(tmp_path: Path) -> None:
    """An empty STR keeps ``synth_pattern#2`` from collapsing to ``#1``."""
    plan = plan_run(
        cycles=3,
        pins=3,
        fail_rate=0.4,
        seed=9,
        blocks=3,
    )
    assert [len(block.failures) for block in plan.blocks] == [0, 1, 0]

    adf = io.StringIO()
    write_adf1(adf, plan)
    adf_result = LogParser().parse_text(adf.getvalue(), job_id=21)
    assert isinstance(adf_result, ParseComplete), adf_result

    stdf_path = tmp_path / "pass-only-blocks.stdf"
    stdf_path.write_bytes(encode_stdf(plan))
    stdf_result = StdfParser().parse(stdf_path, job_id=22)
    assert isinstance(stdf_result, ParseComplete), stdf_result

    expected_ids = [BlockId("synth_pattern", index) for index in (1, 2, 3)]
    assert [block.id for block in adf_result.run.blocks] == expected_ids
    assert [block.id for block in stdf_result.run.blocks] == expected_ids
    assert project_run(adf_result.run) == project_plan(plan)
    assert project_run(stdf_result.run) == project_plan(plan)
    assert stdf_result.run.failures[0].location.block.label() == "synth_pattern#2"


def _assert_projection_fields(
    cls: type[object], included: set[str], excluded: dict[str, str]
) -> None:
    assert included.isdisjoint(excluded)
    assert all(reason.strip() for reason in excluded.values())
    assert {field.name for field in fields(cls)} == included | set(excluded)


def test_round_trip_projection_is_total_over_the_ir() -> None:
    """Adding an IR field forces an include/exclude decision here.

    The projection is intentionally about failure facts.  ADF-1 is a complete
    timed retest log while STDF STR is a sparse cycle-indexed failure log, so a
    whole-``TestRun`` equality assertion would compare facts one format cannot
    represent.
    """
    _assert_projection_fields(
        TestRun,
        {"failures"},
        {
            "header": "format-specific metadata and time-domain conventions",
            "pins": "STDF has no direction and maps only addressable fail pins",
            "blocks": "range and declared-summary semantics differ by format",
            "driven_waves": "production STDF contains no programmed drive data",
            "expected_waves": "STDF is sparse while ADF wave building is Phase 2",
            "captured_waves": "STDF is sparse while ADF wave building is Phase 2",
            "timing_sets": "STR has no ADF-1 timing-set model",
            "warnings": "warnings cite format-specific evidence and positions",
            "source_format": "the two readers must identify different formats",
        },
    )
    _assert_projection_fields(
        FailureEvent,
        {"location", "pin", "expected", "actual", "category"},
        {
            "strobe_time": "ADF-1 resolves time; STDF uses its cycle address",
            "cycle_period": "ADF-1 has a period; STDF cycles are unit width",
            "src_line": "ADF-1 uses lines while STDF uses record ordinals",
            "strobe_window": "STDF STR supplies no ADF-1 timeset window",
        },
    )
    _assert_projection_fields(
        VectorLocation,
        {"block", "vector"},
        {"time": "ADF-1 time and the STDF cycle axis are not equivalent"},
    )
    _assert_projection_fields(BlockId, {"name", "occurrence"}, {})
    assert FailureProjection._fields == (
        "block_name",
        "block_occurrence",
        "vector",
        "pin",
        "expected",
        "actual",
        "category",
    )


def test_committed_golden_corpus_is_reproducible(tmp_path: Path) -> None:
    generated = write_golden_corpus(tmp_path)
    assert {path.name for path in generated} == CORPUS_NAMES
    assert {path.name for path in CORPUS.glob("*.stdf")} == CORPUS_NAMES
    for path in generated:
        assert path.read_bytes() == (CORPUS / path.name).read_bytes(), path.name


def test_committed_little_and_big_endian_goldens_are_ir_equivalent() -> None:
    little_path = CORPUS / "golden_le.stdf"
    big_path = CORPUS / "golden_be.stdf"
    assert little_path.read_bytes() != big_path.read_bytes()
    assert detect_endianness(little_path.read_bytes()[:4]) == LITTLE_ENDIAN
    assert detect_endianness(big_path.read_bytes()[:4]) == BIG_ENDIAN
    assert _parse_stdf(little_path) == _parse_stdf(big_path)


def test_committed_corpus_pins_every_required_writer_feature() -> None:
    continuation = _str_records(CORPUS / "continuation.stdf")
    assert max(record.get_int("REC_TOT") or 0 for record in continuation) >= 3
    assert {record.get_int("CYC_BASE") for record in continuation} == {100}
    continuation_run = _parse_stdf(CORPUS / "continuation.stdf")
    assert [
        failure.location.vector for failure in continuation_run.failures
    ] == [100, 101, 102]

    for data_bit in (1, 2, 4, 8):
        records = _str_records(CORPUS / f"data_bit_{data_bit}.stdf")
        assert records
        assert {record.get_int("DATA_BIT") for record in records} == {data_bit}

    variants_plan = plan_run(
        cycles=4,
        pins=3,
        fail_rate=1.0,
        seed=7,
        start_vector=20,
    )
    expected_captures = tuple(
        (
            failure.block,
            failure.occurrence,
            failure.vector,
            failure.pin,
            LogicState(failure.actual),
        )
        for failure in variants_plan.failures
    )
    for data_bit in (1, 2, 4, 8):
        run = _parse_stdf(CORPUS / f"data_bit_{data_bit}.stdf")
        captures = tuple(
            (
                failure.location.block.name,
                failure.location.block.occurrence,
                failure.location.vector,
                failure.pin,
                failure.actual,
            )
            for failure in run.failures
        )
        assert captures == expected_captures
        if data_bit in (4, 8):
            assert project_run(run) == project_plan(variants_plan)

    based = _str_records(CORPUS / "nonzero_cyc_base.stdf")
    assert any((record.get_int("CYC_BASE") or 0) > 0 for record in based)

    masked = _str_records(CORPUS / "mask_map_9pin.stdf")
    maps = [record.get_map("MASK_MAP") for record in masked]
    nine_pin = [bitmap for bitmap in maps if bitmap is not None and bitmap.bit_count == 9]
    assert nine_pin
    assert all(len(bitmap.data) == 2 for bitmap in nine_pin)
    assert any(8 in bitmap.set_bits() for bitmap in nine_pin)

    missing = _str_records(CORPUS / "missing_exp_data.stdf")
    assert missing
    assert all(record.get_ints("EXP_DATA") is None for record in missing)
    missing_run = _parse_stdf(CORPUS / "missing_exp_data.stdf")
    assert missing_run.failures
    assert all(failure.expected is LogicState.UNKNOWN for failure in missing_run.failures)
    assert any("no EXP_DATA" in warning for warning in missing_run.warnings)

    truncated_memory = _parse_stdf(CORPUS / "fail_memory_truncated.stdf")
    assert any("fail memory truncated" in warning for warning in truncated_memory.warnings)

    truncated_file = _parse_stdf(CORPUS / "truncated.stdf")
    assert truncated_file.failures
    assert any("ends mid-record" in warning for warning in truncated_file.warnings)


def test_oversized_str_payload_is_split_into_continuations() -> None:
    plan = plan_run(
        cycles=9_000,
        pins=3,
        fail_rate=1.0,
        seed=7,
    )
    data = encode_stdf(
        plan,
        StdfWriteOptions(data_bit=8, max_failures_per_record=9_000),
    )
    with io.BytesIO(data) as source:
        raw_str = [
            raw
            for raw in iter_records(source, LITTLE_ENDIAN)
            if raw.key == STR.key
        ]
    assert len(raw_str) == 2
    assert all(len(raw.payload) <= 0xFFFF for raw in raw_str)
    decoded = tuple(
        decode_record(STR, raw.payload, LITTLE_ENDIAN) for raw in raw_str
    )
    assert {record.get_int("REC_TOT") for record in decoded} == {2}


def _prefix_cut_points(data: bytes, endian: str) -> tuple[int, ...]:
    """Cover the whole file plus every framing boundary and its neighbours."""
    cuts = {0, 1, 2, 3, 4, max(0, len(data) - 1), len(data)}
    offset = 0
    while offset + 4 <= len(data):
        payload_size = int.from_bytes(
            data[offset : offset + 2],
            byteorder="little" if endian == LITTLE_ENDIAN else "big",
        )
        next_offset = offset + 4 + payload_size
        for pivot in (offset, offset + 4, next_offset):
            cuts.update(
                cut for cut in (pivot - 1, pivot, pivot + 1) if 0 <= cut <= len(data)
            )
        if next_offset <= offset or next_offset > len(data):
            break
        offset = next_offset

    # Exhaust short files.  For larger files, deterministic uniform and random
    # cuts retain whole-file coverage without making the test quadratic.
    if len(data) <= 1_024:
        cuts.update(range(len(data) + 1))
    else:
        cuts.update(round(index * len(data) / 512) for index in range(513))
        rng = random.Random(0x5ADF2007)
        cuts.update(rng.randrange(len(data) + 1) for _ in range(256))
    return tuple(sorted(cuts))


@pytest.mark.parametrize(
    ("name", "endian"),
    [("golden_le.stdf", LITTLE_ENDIAN), ("golden_be.stdf", BIG_ENDIAN)],
)
def test_every_selected_prefix_returns_a_typed_result(
    tmp_path: Path, name: str, endian: str
) -> None:
    """Deterministic prefix fuzzing spans both complete byte orders."""
    data = (CORPUS / name).read_bytes()
    cuts = _prefix_cut_points(data, endian)
    assert cuts[0] == 0 and cuts[-1] == len(data)
    assert len(cuts) >= min(len(data) + 1, 256)

    path = tmp_path / name
    for cut in cuts:
        path.write_bytes(data[:cut])
        result = StdfParser().parse(path, job_id=cut)
        assert isinstance(result, (ParseComplete, ParseFailed)), (name, cut, result)
        assert result.job_id == cut


@pytest.mark.perf
def test_stdf_perf_measurement_checks_the_same_plan_before_reporting(
    tmp_path: Path,
) -> None:
    sample = measure(
        cycles=120,
        pins=8,
        fail_rate=0.2,
        seed=91,
        blocks=3,
        period=17,
        start_vector=70_001,
        work_dir=tmp_path,
        stdf_batches=2,
        stdf_repeats_per_batch=3,
    )
    assert sample.failures > 0
    assert sample.adf1_bytes > sample.stdf_bytes > 0
    assert sample.adf1_strict_s > 0
    assert sample.stdf_s > 0
    assert 0 < sample.stdf_min_s <= sample.stdf_s <= sample.stdf_max_s
    assert sample.stdf_batches == 2
    assert sample.stdf_repeats_per_batch == 3
    assert sample.stdf_mib_per_s > 0
    assert sample.stdf_cycles_per_s > 0
    assert sample.elapsed_speedup_vs_adf1_strict > 0
