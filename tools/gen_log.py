"""Synthetic ADF-1 log generator — the perf corpus and property-test oracle.

Two jobs, and the second is the important one:

1. Produce a deterministic multi-hundred-MB corpus for the parse-throughput
   baseline::

       python tools/gen_log.py --cycles 1000000 --pins 16 --fail-rate 0.0005 \\
           --seed 42 -o sample_logs/perf_1m.atelog

2. Act as the **oracle** for the Phase 1 property test.  ``generate()`` returns
   the exact set of failures it injected, so the test can assert that parsing
   the emitted log yields precisely those ``FailureEvent``s with matching
   vectors — a round-trip check no hand-written golden can give you.

Determinism: everything derives from a single ``random.Random(seed)``, and the
log is streamed to the output handle rather than accumulated, so a 10**6-cycle
corpus costs constant memory.
"""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Sequence, TextIO

#: States a failing capture may take.  Weighted towards strong-level mismatches
#: because that is what real silicon defects mostly look like; the exotic
#: captures still appear often enough to exercise every FailCategory branch.
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
    """One failure the generator deliberately planted.

    The property test compares these against the parsed ``FailureEvent``s.
    """

    block: str
    occurrence: int
    vector: int
    time: int
    pin: str
    expected: str
    actual: str


def _pin_names(pins: int) -> tuple[tuple[str, str], ...]:
    """``(name, direction)`` for ``pins`` pins: CLK, RST_N, then a DQ bus.

    The DQ bus is what makes bus collapsing (``DQ3`` -> ``DQ[*]``) testable.
    """
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
    """Write a well-formed ADF-1 log to ``out``; return the injected failures.

    ``fail_rate`` is the per-compare probability of a failure, so the expected
    failure count is roughly ``cycles * (pins - 2) * fail_rate``.
    """
    if not 0.0 <= fail_rate <= 1.0:
        raise ValueError("fail-rate must be in [0, 1]")
    if blocks < 1 or cycles < blocks:
        raise ValueError("need at least one cycle per block")
    # period == 0 emits every cycle at T=0 and a negative period emits negative
    # timestamps the grammar cannot lex - either way this function would break
    # its promise to produce a well-formed ADF-1 log, and both would trip the
    # monotonic-time fatal check once M7 lands.
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
        # Deliberately reuse ONE pattern name across every block, so that any
        # consumer keying on the bare name collapses invocations that must stay
        # separate. BlockId occurrence is the thing that keeps them apart.
        block_name = "synth_pattern"
        occurrence = block_index + 1
        n_cycles = per_block if block_index < blocks - 1 else cycles - per_block * (
            blocks - 1
        )

        out.write(f"TESTBLOCK {block_name}\n")

        block_fail_count = 0
        fail_vectors: list[int] = []

        for i in range(n_cycles):
            # Vector numbers and timestamps restart per block invocation - that
            # is the ADF-1 coordinate rule (§3.1), not an oversight.
            vector = start_vector + i
            time = i * period
            out.write(f"CYCLE {vector} T={time}\n")
            out.write(f"PIN CLK DRV {i & 1}\n")
            out.write("PIN RST_N DRV 1\n")

            vector_failed = False
            for pin_index, pin in enumerate(dq_pins):
                # Deliberately NOT hash(pin): str hashing is salted by
                # PYTHONHASHSEED, which would make "deterministic" a lie across
                # processes. The pin's index is stable everywhere.
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

        # Omit FAILSUMMARY entirely when the block passed: `vector_list`
        # requires at least one vector, so there is no "zero" spelling.
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
    """Convenience wrapper: ``generate()`` straight to a file.

    ``newline="\\n"`` is explicit so the corpus is byte-identical on Windows and
    Linux — CRLF handling is something the tests inject deliberately, never
    something the platform does behind our back.
    """
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
