"""Measure strict-parser throughput on a practical size ladder."""

from __future__ import annotations

import argparse
import gc
import io
import json
import platform
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent

# Add the repository root when this file is run directly.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ate_fa_suite.parsing.parser import build_parser  # noqa: E402
from ate_fa_suite.parsing.transformer import (  # noqa: E402
    AteLogTransformer,
    assemble_run,
)
from tools.gen_log import generate  # noqa: E402

DEFAULT_LADDER = (2_000, 8_000, 24_000)

# Target corpus size used for extrapolation.
NOMINAL_CYCLES = 1_000_000


@dataclass(frozen=True, slots=True)
class Sample:
    cycles: int
    pins: int
    bytes_: int
    lark_s: float
    transform_s: float
    assemble_s: float
    total_s: float
    mib_per_s: float
    failures: int


def measure(cycles: int, pins: int, fail_rate: float, seed: int) -> Sample:
    buffer = io.StringIO()
    injected = generate(
        buffer, cycles=cycles, pins=pins, fail_rate=fail_rate, seed=seed
    )
    text = buffer.getvalue()
    size = len(text.encode("utf-8"))
    del buffer
    gc.collect()

    parser = build_parser()
    t0 = time.perf_counter()
    tree = parser.parse(text, start="document")
    t1 = time.perf_counter()
    document = AteLogTransformer().transform(tree)
    t2 = time.perf_counter()
    run = assemble_run(document)
    t3 = time.perf_counter()

    # Verify correctness before recording the timing.
    if len(run.failures) != len(injected):
        raise SystemExit(
            f"baseline aborted: parsed {len(run.failures)} failures, "
            f"generator injected {len(injected)}"
        )

    total = t3 - t0
    return Sample(
        cycles=cycles,
        pins=pins,
        bytes_=size,
        lark_s=t1 - t0,
        transform_s=t2 - t1,
        assemble_s=t3 - t2,
        total_s=total,
        mib_per_s=(size / 1_048_576) / total,
        failures=len(injected),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="perf_baseline")
    parser.add_argument("--cycles", type=int, nargs="+", default=list(DEFAULT_LADDER))
    parser.add_argument("--pins", type=int, default=16)
    parser.add_argument("--fail-rate", type=float, default=0.0005)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("-o", "--output", type=Path, default=REPO_ROOT / "perf_baseline.json")
    args = parser.parse_args(argv)

    build_parser()  # warm the LALR table cache out of the measurement

    samples: list[Sample] = []
    header = (
        f"{'cycles':>8} {'MiB':>7} {'lark s':>8} {'xform s':>8} "
        f"{'asm s':>7} {'total s':>8} {'MiB/s':>7} {'fails':>6}"
    )
    print(header)
    print("-" * len(header))
    for cycles in args.cycles:
        sample = measure(cycles, args.pins, args.fail_rate, args.seed)
        samples.append(sample)
        print(
            f"{sample.cycles:>8} {sample.bytes_/1_048_576:>7.2f} "
            f"{sample.lark_s:>8.2f} {sample.transform_s:>8.2f} "
            f"{sample.assemble_s:>7.2f} {sample.total_s:>8.2f} "
            f"{sample.mib_per_s:>7.2f} {sample.failures:>6}"
        )
        gc.collect()

    slowest = min(s.mib_per_s for s in samples)
    biggest = max(samples, key=lambda s: s.cycles)
    bytes_per_cycle = biggest.bytes_ / biggest.cycles
    projected_mib = NOMINAL_CYCLES * bytes_per_cycle / 1_048_576
    projected_s = projected_mib / slowest

    print()
    print(f"stage split (largest sample): lark {biggest.lark_s / biggest.total_s:.0%}, "
          f"transform {biggest.transform_s / biggest.total_s:.0%}, "
          f"assemble {biggest.assemble_s / biggest.total_s:.1%}")
    print(
        f"EXTRAPOLATION (not measured): {NOMINAL_CYCLES:,} cycles x {args.pins} pins "
        f"~= {projected_mib:,.0f} MiB -> ~{projected_s/60:,.0f} min on the strict path"
    )

    payload = {
        "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "processor": platform.processor(),
        "pins": args.pins,
        "fail_rate": args.fail_rate,
        "seed": args.seed,
        "path": "strict whole-file (document start rule)",
        "samples": [asdict(s) for s in samples],
        "extrapolation": {
            "note": "computed, NOT measured - see module docstring",
            "cycles": NOMINAL_CYCLES,
            "projected_mib": round(projected_mib, 1),
            "projected_seconds": round(projected_s, 1),
        },
    }
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nrecorded -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
