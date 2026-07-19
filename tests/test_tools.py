"""Tests for the developer tools.

They are not shipped, but they are load-bearing: ``gen_log`` is the property-test
oracle (a generator that emits a malformed log invalidates every test that uses
it) and ``build_release`` produces the artifacts the offline deployment claims
rest on.
"""

from __future__ import annotations

import io

import pytest

from ate_fa_suite.model.entities import ParseComplete
from ate_fa_suite.parsing.parser import LogParser
from tools import build_release, gen_log


# --- gen_log: the oracle must only ever emit well-formed ADF-1 --------------


@pytest.mark.parametrize("period", [0, -1, -1000])
def test_generate_rejects_non_positive_period(period: int) -> None:
    """`period=0` emits every cycle at T=0 and a negative period emits negative
    timestamps the grammar cannot lex.  Either way the function would break its
    promise to produce a well-formed log, and both would trip the monotonic-time
    fatal check once M7 lands."""
    with pytest.raises(ValueError, match="period"):
        gen_log.generate(
            io.StringIO(), cycles=10, pins=4, fail_rate=0.0, seed=1, period=period
        )


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        (dict(cycles=10, pins=2, fail_rate=0.0, seed=1), "3 pins"),
        (dict(cycles=10, pins=4, fail_rate=1.5, seed=1), "fail-rate"),
        (dict(cycles=10, pins=4, fail_rate=-0.1, seed=1), "fail-rate"),
        (dict(cycles=2, pins=4, fail_rate=0.0, seed=1, blocks=5), "one cycle"),
        (dict(cycles=10, pins=4, fail_rate=0.0, seed=1, blocks=0), "one cycle"),
    ],
)
def test_generate_rejects_invalid_arguments(
    kwargs: dict[str, object], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        gen_log.generate(io.StringIO(), **kwargs)  # type: ignore[arg-type]


def test_generated_timestamps_are_strictly_increasing_within_a_block() -> None:
    """The invariant M7 will make a FATAL rule — the waveform bisects depend on
    sorted transitions, so the oracle must never violate it."""
    buffer = io.StringIO()
    gen_log.generate(
        buffer, cycles=50, pins=6, fail_rate=0.1, seed=3, blocks=2, period=250
    )
    result = LogParser().parse_text(buffer.getvalue(), job_id=1)
    assert isinstance(result, ParseComplete)

    for block in result.run.blocks:
        vectors = [
            f.location.vector
            for f in result.run.failures
            if f.location.block == block.id
        ]
        assert vectors == sorted(vectors)


def test_generated_log_survives_a_non_default_period() -> None:
    buffer = io.StringIO()
    gen_log.generate(buffer, cycles=20, pins=5, fail_rate=0.2, seed=9, period=7)
    result = LogParser().parse_text(buffer.getvalue(), job_id=1)
    assert isinstance(result, ParseComplete)
    assert all(f.cycle_period == 7 for f in result.run.failures)


# --- build_release: a silent no-op must not look like success ---------------


def test_release_modes_are_mutually_exclusive() -> None:
    """Passing both flags previously skipped BOTH builds, printed the
    verification instructions and exited 0 — a no-op indistinguishable from a
    successful build, which is the worst possible outcome for a release tool."""
    with pytest.raises(SystemExit) as exc:
        build_release.main(["--wheelhouse-only", "--zipapp-only"])
    assert exc.value.code == 2  # argparse usage error, not a success exit
