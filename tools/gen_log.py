"""Generate deterministic ADF-1 logs for tests and performance checks.

"""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Sequence, TextIO

# Weighted failure captures used by the synthetic generator.
FAIL_CAPTURES: Final[tuple[tuple[str, int], ...]] = (
    ("flip", 70),  # strong opposite level -> SA0/SA1 candidate
    ("X", 12),  # floating / contention
    ("Z", 10),  # open / tri-state
    ("L", 4),  # weak drive
    ("H", 4),  # weak drive
)

DEFAULT_PERIOD: Final = 1000  # native timescale units between cycles


@dataclass(frozen=True, slots=True)
class InjectedFailure:
    """One planted failure used as the parser test oracle."""

    block: str
    occurrence: int
    vector: int
    time: int
    pin: str
    expected: str
    actual: str


def _pin_names(pins: int) -> tuple[tuple[str, str], ...]:
    """Create CLK, RST_N, and enough DQ pins to reach ``pins``."""
    if pins < 3:
        raise ValueError("need at least 3 pins (CLK, RST_N and one DQ)")
    names: list[tuple[str, str]] = [("CLK", "IN"), ("RST_N", "IN")]
    names += [(f"DQ{i}", "IO") for i in range(pins - 2)]
    return tuple(names)


def _choose_capture(rng: random.Random, expected: str) -> str:
    """Pick a captured state that genuinely disagrees with ``expected``."""
    kinds = [k for k, _ in FAIL_CAPTURES]
    weights = [w for _, w in FAIL_CAPTURES]
    kind = rng.choices(kinds, weights=weights, k=1)[0]
    if kind == "flip":
        return "0" if expected == "1" else "1"
    return kind


def generate(
    out: TextIO,
    *,
    cycles: int,
    pins: int,
    fail_rate: float,
    seed: int,
    blocks: int = 1,
    period: int = DEFAULT_PERIOD,
    start_vector: int = 1,
) -> tuple[InjectedFailure, ...]:
    """Write a valid log and return the failures added to it."""
    if not 0.0 <= fail_rate <= 1.0:
        raise ValueError("fail-rate must be in [0, 1]")
    if blocks < 1 or cycles < blocks:
        raise ValueError("need at least one cycle per block")
    if period <= 0:
        raise ValueError("period must be positive (strictly increasing T=)")
    if start_vector < 0:
        raise ValueError("start-vector must be non-negative")

    rng = random.Random(seed)
    pin_defs = _pin_names(pins)
    dq_pins = [name for name, direction in pin_defs if direction == "IO"]

    out.write("#ATELOG v1.0\n")
    out.write(f"LOT: SYNTH-{seed:06d}\n")
    out.write("WAFER: 1\n")
    out.write("DEVICE: SYNTHETIC_DUT\n")
    out.write("TESTER: GEN-LOG\n")
    out.write(f"PROGRAM: synth_c{cycles}_p{pins}_s{seed}\n")
    out.write("DATE: 2026-07-11T00:00:00\n")
    out.write("TIMESCALE: 1ns\n\n")
    for name, direction in pin_defs:
        out.write(f"PINDEF {name} {direction}\n")
    out.write("\n")

    injected: list[InjectedFailure] = []
    per_block = cycles // blocks

    for block_index in range(blocks):
        # Reuse the name to exercise occurrence-based block identity.
        block_name = "synth_pattern"
        occurrence = block_index + 1
        n_cycles = per_block if block_index < blocks - 1 else cycles - per_block * (
            blocks - 1
        )

        out.write(f"TESTBLOCK {block_name}\n")

        block_fail_count = 0
        fail_vectors: list[int] = []

        for i in range(n_cycles):
            # Vector and time values restart for each block invocation.
            vector = start_vector + i
            time = i * period
            out.write(f"CYCLE {vector} T={time}\n")
            out.write(f"PIN CLK DRV {i & 1}\n")
            out.write("PIN RST_N DRV 1\n")

            vector_failed = False
            for pin_index, pin in enumerate(dq_pins):
                # The pin index is stable across processes; hash(pin) is not.
                expected = "1" if (i + pin_index) & 1 else "0"
                if rng.random() < fail_rate:
                    actual = _choose_capture(rng, expected)
                    out.write(
                        f"PIN {pin} EXP {expected} GOT {actual} FAIL\n"
                    )
                    injected.append(
                        InjectedFailure(
                            block=block_name,
                            occurrence=occurrence,
                            vector=vector,
                            time=time,
                            pin=pin,
                            expected=expected,
                            actual=actual,
                        )
                    )
                    block_fail_count += 1
                    vector_failed = True
                else:
                    out.write(
                        f"PIN {pin} EXP {expected} GOT {expected} PASS\n"
                    )
            if vector_failed:
                fail_vectors.append(vector)
            out.write("END CYCLE\n")

        # A passing block omits FAILSUMMARY; its vector list cannot be empty.
        if block_fail_count:
            vectors = ",".join(str(v) for v in fail_vectors)
            out.write(f"FAILSUMMARY {block_fail_count} VECTORS {vectors}\n")
        out.write("END TESTBLOCK\n")

    out.write("END LOG\n")
    return tuple(injected)


def write_log(
    path: Path,
    *,
    cycles: int,
    pins: int,
    fail_rate: float,
    seed: int,
    blocks: int = 1,
    period: int = DEFAULT_PERIOD,
    start_vector: int = 1,
) -> tuple[InjectedFailure, ...]:
    """Write a generated log with stable LF line endings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        return generate(
            handle,
            cycles=cycles,
            pins=pins,
            fail_rate=fail_rate,
            seed=seed,
            blocks=blocks,
            period=period,
            start_vector=start_vector,
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gen_log",
        description="Generate a deterministic synthetic ADF-1 datalog.",
    )
    parser.add_argument("--cycles", type=int, default=5000)
    parser.add_argument("--pins", type=int, default=8)
    parser.add_argument("--fail-rate", type=float, default=0.001)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--blocks", type=int, default=1)
    parser.add_argument("--period", type=int, default=DEFAULT_PERIOD)
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="output path; omit to write to stdout",
    )
    args = parser.parse_args(argv)

    if args.output is None:
        injected = generate(
            sys.stdout,
            cycles=args.cycles,
            pins=args.pins,
            fail_rate=args.fail_rate,
            seed=args.seed,
            blocks=args.blocks,
            period=args.period,
        )
    else:
        injected = write_log(
            args.output,
            cycles=args.cycles,
            pins=args.pins,
            fail_rate=args.fail_rate,
            seed=args.seed,
            blocks=args.blocks,
            period=args.period,
        )
        size_mb = args.output.stat().st_size / 1_048_576
        print(
            f"{args.output}: {args.cycles} cycles, {args.pins} pins, "
            f"{len(injected)} injected failures, {size_mb:.1f} MiB "
            f"(seed {args.seed})",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
