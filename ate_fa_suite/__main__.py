"""Command-line entry point and future GUI composition root."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ate_fa_suite.model.entities import ParseComplete

BANNER = """\
ATE Test Log Visualizer & Diagnostics Suite v{version}

  GUI not yet implemented - see docs/ROADMAP.md Phase 3.

  Working today:
    - ADF-1 Lark grammar (multi-start, LALR/contextual)  ate_fa_suite/parsing/grammar/atelog.lark
    - STDF V4-2007 reader (stdlib struct only)           python -m ate_fa_suite --stdf FILE
    - Domain entities (frozen, slotted, tuple-backed)    ate_fa_suite/model/entities.py
    - Synthetic log generator                            python tools/gen_log.py --help
    - Release builder (wheelhouse + .pyz zipapp)         python tools/build_release.py

  Run the test suite with:  pytest
"""

USAGE = "usage: python -m ate_fa_suite [--stdf FILE]"

#: How many failures the text summary lists before it stops.
SUMMARY_FAIL_LIMIT = 20

#: How many ranked pins the text summary lists.
SUMMARY_PIN_LIMIT = 10


def main(argv: list[str] | None = None) -> int:
    """Print the current project status and return an exit code."""
    args = sys.argv[1:] if argv is None else argv

    if args and args[0] == "--stdf":
        if len(args) < 2:
            print(USAGE, file=sys.stderr)
            return 2
        return summarize_stdf(Path(args[1]))

    from ate_fa_suite import __version__

    print(BANNER.format(version=__version__))

    if args:
        print(f"  Requested log: {args[0]}")
        print("  (loading is Phase 3 work; the parser lands in Phase 1.)")

    return 0


def summarize_stdf(path: Path) -> int:
    """Parse one STDF file and print a plain-text FA summary."""
    from ate_fa_suite.model.entities import ParseFailed
    from ate_fa_suite.parsing.stdf import StdfParser

    result = StdfParser().parse(path, job_id=0)
    if isinstance(result, ParseFailed):
        print(f"{path}: {result.message}", file=sys.stderr)
        if result.context:
            print(f"  {result.context}", file=sys.stderr)
        return 1

    for line in format_summary(path, result):
        print(line)
    return 0


def format_summary(path: Path, result: ParseComplete) -> list[str]:
    """Render a ``ParseComplete`` as the lines of a text FA summary."""
    run = result.run
    header = run.header
    lines = [
        f"FA summary: {path.name}",
        f"  source format   {run.source_format}",
        f"  parsed in       {result.elapsed_s * 1000:.1f} ms",
        "",
        f"  lot             {header.lot or '-'}",
        f"  device          {header.device or '-'}",
        f"  tester          {header.tester or '-'}",
        f"  program         {header.program or '-'}",
        f"  date            {header.date or '-'}",
        f"  time domain     {header.time_domain}",
        "",
        f"  pins            {len(run.pins)}",
        f"  blocks          {len(run.blocks)}",
        f"  failures        {len(run.failures)}",
        f"  expected waves  {len(run.expected_waves)}",
        f"  captured waves  {len(run.captured_waves)}",
        f"  driven waves    {len(run.driven_waves)} "
        "(a production log carries no programmed stimulus)",
    ]

    if run.blocks:
        lines += ["", "Blocks"]
        for block in run.blocks:
            lines.append(
                f"  {block.id.label():<28} cycles "
                f"{block.first_vector}..{block.last_vector}  "
                f"fails {block.fail_count}"
            )

    if run.failures:
        counts: dict[str, int] = {}
        for failure in run.failures:
            counts[failure.pin] = counts.get(failure.pin, 0) + 1
        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        lines += ["", "Failing pins by count"]
        for pin, count in ranked[:SUMMARY_PIN_LIMIT]:
            share = 100.0 * count / len(run.failures)
            lines.append(f"  {pin:<28} {count:>7}  {share:5.1f}%")
        if len(ranked) > SUMMARY_PIN_LIMIT:
            lines.append(f"  ... {len(ranked) - SUMMARY_PIN_LIMIT} more pins")

        lines += ["", "Failures (expected vs captured, per pin per cycle)"]
        lines.append(
            f"  {'cycle':>12}  {'pin':<20} exp got  category"
        )
        for failure in run.failures[:SUMMARY_FAIL_LIMIT]:
            lines.append(
                f"  {failure.location.vector:>12}  {failure.pin:<20} "
                f"{failure.expected.value:^3} {failure.actual.value:^3}  "
                f"{failure.category.value}"
            )
        if len(run.failures) > SUMMARY_FAIL_LIMIT:
            remaining = len(run.failures) - SUMMARY_FAIL_LIMIT
            lines.append(f"  ... {remaining} more failures")

    if run.warnings:
        lines += ["", f"Warnings ({len(run.warnings)})"]
        lines += [f"  {text}" for text in run.warnings]

    return lines


if __name__ == "__main__":
    raise SystemExit(main())
