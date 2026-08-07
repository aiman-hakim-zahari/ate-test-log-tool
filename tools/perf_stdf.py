"""Record STDF parse throughput against the equivalent ADF-1 workload.

File generation is deliberately outside every timed region.  Both encodings
come from one :class:`~tools.gen_log.RunPlan`, and both parsed projections are
checked against that plan before a measurement is eligible for recording.
"""

from __future__ import annotations

import argparse
import gc
import io
import json
import platform
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import NamedTuple, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent

# Allow direct execution from a checkout without an editable install.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ate_fa_suite.model.entities import (  # noqa: E402
    FailCategory,
    LogicState,
    ParseComplete,
    TestRun,
)
from ate_fa_suite.parsing.parser import LogParser  # noqa: E402
from ate_fa_suite.parsing.stdf import StdfParser  # noqa: E402
from tools.gen_log import RunPlan, plan_run, write_adf1  # noqa: E402
from tools.stdf_writer import encode_stdf  # noqa: E402

DEFAULT_LADDER = (2_000, 8_000, 24_000)
DEFAULT_OUTPUT = REPO_ROOT / "stdf_perf_baseline.json"

class FailureProjection(NamedTuple):
    """The failure fields ADF-1 and STDF can represent identically."""

    block_name: str
    block_occurrence: int
    vector: int
    pin: str
    expected: LogicState
    actual: LogicState
    category: FailCategory


@dataclass(frozen=True, slots=True)
class Sample:
    """One same-plan ADF-1/STDF timing comparison."""

    cycles: int
    pins: int
    failures: int
    adf1_bytes: int
    adf1_strict_s: float
    stdf_bytes: int
    stdf_s: float
    stdf_min_s: float
    stdf_max_s: float
    stdf_batches: int
    stdf_repeats_per_batch: int
    stdf_mib_per_s: float
    stdf_cycles_per_s: float
    elapsed_speedup_vs_adf1_strict: float


def project_plan(plan: RunPlan) -> tuple[FailureProjection, ...]:
    """Return the format-neutral failure facts promised by ``plan``."""
    projected: list[FailureProjection] = []
    for failure in plan.failures:
        expected = LogicState(failure.expected)
        actual = LogicState(failure.actual)
        category = FailCategory.classify(expected, actual, failed=True)
        if category is None:  # impossible for a planted failure
            raise RuntimeError("the run plan contains a non-failure")
        projected.append(
            FailureProjection(
                failure.block,
                failure.occurrence,
                failure.vector,
                failure.pin,
                expected,
                actual,
                category,
            )
        )
    return tuple(projected)


def project_run(run: TestRun) -> tuple[FailureProjection, ...]:
    """Project either reader's IR onto facts both formats preserve."""
    return tuple(
        FailureProjection(
            failure.location.block.name,
            failure.location.block.occurrence,
            failure.location.vector,
            failure.pin,
            failure.expected,
            failure.actual,
            failure.category,
        )
        for failure in run.failures
    )


def _complete(result: object, label: str) -> ParseComplete:
    if not isinstance(result, ParseComplete):
        message = getattr(result, "message", repr(result))
        raise RuntimeError(f"{label} parse failed: {message}")
    return result


def measure(
    *,
    cycles: int,
    pins: int,
    fail_rate: float,
    seed: int,
    blocks: int = 1,
    period: int = 1_000,
    start_vector: int = 1,
    work_dir: Path | None = None,
    stdf_batches: int = 5,
    stdf_repeats_per_batch: int = 25,
) -> Sample:
    """Measure both public parse paths for one logical run.

    The comparison is elapsed time for the same logical workload.  STDF is a
    sparse failure log, so its MiB/s alone is not comparable to ADF-1's MiB/s;
    cycles/s and the same-plan elapsed speedup are recorded as well.
    """
    if stdf_batches < 1 or stdf_repeats_per_batch < 1:
        raise ValueError("STDF timing batches and repeats must be positive")

    plan = plan_run(
        cycles=cycles,
        pins=pins,
        fail_rate=fail_rate,
        seed=seed,
        blocks=blocks,
        period=period,
        start_vector=start_vector,
    )
    oracle = project_plan(plan)

    adf_buffer = io.StringIO()
    write_adf1(adf_buffer, plan)
    adf_text = adf_buffer.getvalue()
    adf_bytes = len(adf_text.encode("utf-8"))
    del adf_buffer

    stdf_data = encode_stdf(plan)

    # Warm the cached Lark parser before timing the strict ADF-1 path.
    LogParser().parse_text("", job_id=-1)
    gc.collect()
    started = time.perf_counter()
    adf_result = LogParser().parse_text(adf_text, job_id=1)
    adf_elapsed = time.perf_counter() - started
    adf_complete = _complete(adf_result, "ADF-1 strict")
    if project_run(adf_complete.run) != oracle:
        raise RuntimeError("ADF-1 projection disagrees with the run plan")
    del adf_complete, adf_result, adf_text

    selected_work_dir = (
        REPO_ROOT / "build" / "perf_stdf"
        if work_dir is None
        else work_dir
    )
    selected_work_dir.mkdir(parents=True, exist_ok=True)
    stdf_path = selected_work_dir / (
        f"workload-c{cycles}-p{pins}-s{seed}-b{blocks}.stdf"
    )
    stdf_path.write_bytes(stdf_data)

    # Validate once outside the timed region.  The timing batches below still
    # assert the public parser's result type, but do not redo the projection.
    checked = _complete(StdfParser().parse(stdf_path, job_id=2), "STDF")
    if project_run(checked.run) != oracle:
        raise RuntimeError("STDF projection disagrees with the run plan")
    if len(checked.run.failures) != len(plan.failures):
        raise RuntimeError("STDF failure count disagrees with the run plan")
    del checked

    per_parse_samples: list[float] = []
    for batch in range(stdf_batches):
        gc.collect()
        started = time.perf_counter()
        for repeat in range(stdf_repeats_per_batch):
            result = StdfParser().parse(
                stdf_path,
                job_id=3 + batch * stdf_repeats_per_batch + repeat,
            )
            _complete(result, "timed STDF")
        batch_elapsed = time.perf_counter() - started
        per_parse_samples.append(batch_elapsed / stdf_repeats_per_batch)

    stdf_elapsed = statistics.median(per_parse_samples)

    stdf_mib = len(stdf_data) / 1_048_576
    return Sample(
        cycles=cycles,
        pins=pins,
        failures=len(plan.failures),
        adf1_bytes=adf_bytes,
        adf1_strict_s=adf_elapsed,
        stdf_bytes=len(stdf_data),
        stdf_s=stdf_elapsed,
        stdf_min_s=min(per_parse_samples),
        stdf_max_s=max(per_parse_samples),
        stdf_batches=stdf_batches,
        stdf_repeats_per_batch=stdf_repeats_per_batch,
        stdf_mib_per_s=stdf_mib / stdf_elapsed,
        stdf_cycles_per_s=cycles / stdf_elapsed,
        elapsed_speedup_vs_adf1_strict=adf_elapsed / stdf_elapsed,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="perf_stdf",
        description=(
            "Record public STDF parser throughput and the elapsed speedup "
            "over strict ADF-1 for the same generated plan."
        ),
    )
    parser.add_argument(
        "--cycles", type=int, nargs="+", default=list(DEFAULT_LADDER)
    )
    parser.add_argument("--pins", type=int, default=16)
    parser.add_argument("--fail-rate", type=float, default=0.0005)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--blocks", type=int, default=1)
    parser.add_argument("--period", type=int, default=1_000)
    parser.add_argument("--start-vector", type=int, default=1)
    parser.add_argument("--stdf-batches", type=int, default=5)
    parser.add_argument("--stdf-repeats", type=int, default=25)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    samples: list[Sample] = []
    header = (
        f"{'cycles':>8} {'fails':>7} {'ADF MiB':>8} {'ADF s':>8} "
        f"{'STDF KiB':>9} {'STDF s':>8} {'MiB/s':>8} "
        f"{'cycles/s':>11} {'speedup':>8}"
    )
    print(header)
    print("-" * len(header))
    for cycles in args.cycles:
        sample = measure(
            cycles=cycles,
            pins=args.pins,
            fail_rate=args.fail_rate,
            seed=args.seed,
            blocks=args.blocks,
            period=args.period,
            start_vector=args.start_vector,
            stdf_batches=args.stdf_batches,
            stdf_repeats_per_batch=args.stdf_repeats,
        )
        samples.append(sample)
        print(
            f"{sample.cycles:>8} {sample.failures:>7} "
            f"{sample.adf1_bytes/1_048_576:>8.2f} "
            f"{sample.adf1_strict_s:>8.3f} "
            f"{sample.stdf_bytes/1_024:>9.2f} {sample.stdf_s:>8.4f} "
            f"{sample.stdf_mib_per_s:>8.2f} "
            f"{sample.stdf_cycles_per_s:>11,.0f} "
            f"{sample.elapsed_speedup_vs_adf1_strict:>7.1f}x"
        )

    payload = {
        "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "processor": platform.processor(),
        "workload": {
            "pins": args.pins,
            "fail_rate": args.fail_rate,
            "seed": args.seed,
            "blocks": args.blocks,
            "period": args.period,
            "start_vector": args.start_vector,
        },
        "stdf_path": "StdfParser.parse (public file path, little-endian)",
        "comparison_path": "LogParser.parse_text (strict whole-file)",
        "timing_scope": "parse only; generation and correctness checks excluded",
        "stdf_statistic": (
            "median per-parse time across repeated timing batches"
        ),
        "samples": [asdict(sample) for sample in samples],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nrecorded -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
