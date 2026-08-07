"""Tests for the log generator and release builder."""

from __future__ import annotations

import hashlib
import io
import inspect
from dataclasses import FrozenInstanceError

import pytest

from ate_fa_suite.model.entities import ParseComplete
from ate_fa_suite.parsing.parser import LogParser
from tools import build_release, gen_log


# --- gen_log: the oracle must only ever emit well-formed ADF-1 --------------


def test_generator_public_signatures_are_explicit_and_stable() -> None:
    """M7 adds a planning layer without changing either legacy entry point."""
    assert str(inspect.signature(gen_log.generate)) == (
        "(out: 'TextIO', *, cycles: 'int', pins: 'int', "
        "fail_rate: 'float', seed: 'int', blocks: 'int' = 1, "
        "period: 'int' = 1000, start_vector: 'int' = 1) -> "
        "'tuple[InjectedFailure, ...]'"
    )
    assert str(inspect.signature(gen_log.write_log)) == (
        "(path: 'Path', *, cycles: 'int', pins: 'int', "
        "fail_rate: 'float', seed: 'int', blocks: 'int' = 1, "
        "period: 'int' = 1000, start_vector: 'int' = 1) -> "
        "'tuple[InjectedFailure, ...]'"
    )
    assert str(inspect.signature(gen_log.plan_run)) == (
        "(*, cycles: 'int', pins: 'int', fail_rate: 'float', "
        "seed: 'int', blocks: 'int' = 1, period: 'int' = 1000, "
        "start_vector: 'int' = 1) -> 'RunPlan'"
    )
    assert str(inspect.signature(gen_log.write_adf1)) == (
        "(out: 'TextIO', plan: 'RunPlan') -> 'None'"
    )


@pytest.mark.parametrize(
    ("kwargs", "sha256"),
    [
        (
            dict(cycles=4, pins=4, fail_rate=0.5, seed=7),
            "f8072d7ed7e6ef3853612e68c83961020a1137518b59f6973d05462ec174efd0",
        ),
        (
            dict(
                cycles=7,
                pins=5,
                fail_rate=0.3,
                seed=9,
                blocks=3,
                period=7,
                start_vector=5,
            ),
            "700553864e8d6ff564c034879b52348652aec692052ebfb28223e1bdeb000dd0",
        ),
    ],
)
def test_plan_refactor_preserves_legacy_bytes_and_oracle(
    kwargs: dict[str, int | float], sha256: str
) -> None:
    """The planning split must not perturb even one seeded RNG decision."""
    generated = io.StringIO()
    injected = gen_log.generate(generated, **kwargs)  # type: ignore[arg-type]

    plan = gen_log.plan_run(**kwargs)  # type: ignore[arg-type]
    planned = io.StringIO()
    assert gen_log.write_adf1(planned, plan) is None

    raw = generated.getvalue().encode("utf-8")
    assert hashlib.sha256(raw).hexdigest() == sha256
    assert planned.getvalue() == generated.getvalue()
    assert plan.failures == injected


def test_run_plan_is_deeply_immutable_and_compact() -> None:
    plan = gen_log.plan_run(
        cycles=100_000,
        pins=3,
        fail_rate=0.0,
        seed=11,
        blocks=3,
        period=7,
        start_vector=5,
    )

    with pytest.raises(FrozenInstanceError):
        setattr(plan, "seed", 12)
    with pytest.raises(FrozenInstanceError):
        setattr(plan.header, "lot", "changed")
    with pytest.raises(FrozenInstanceError):
        setattr(plan.blocks[0], "cycle_count", 1)

    assert plan.pin_defs == (("CLK", "IN"), ("RST_N", "IN"), ("DQ0", "IO"))
    assert tuple(block.cycle_count for block in plan.blocks) == (
        33_333,
        33_333,
        33_334,
    )
    assert tuple(block.occurrence for block in plan.blocks) == (1, 2, 3)
    assert plan.failures == ()
    assert all(block.failures == () for block in plan.blocks)
    assert not hasattr(plan, "__dict__")
    assert not hasattr(plan.blocks[0], "cycles")


def test_write_adf1_is_deterministic_and_draws_no_randomness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = gen_log.plan_run(
        cycles=40, pins=6, fail_rate=0.2, seed=42, blocks=2
    )

    def forbidden_random(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"writer drew randomness: {args!r}, {kwargs!r}")

    monkeypatch.setattr(gen_log.random, "Random", forbidden_random)
    first, second = io.StringIO(), io.StringIO()
    gen_log.write_adf1(first, plan)
    gen_log.write_adf1(second, plan)
    assert first.getvalue() == second.getvalue()


def test_plan_keeps_restarted_block_vectors_distinct() -> None:
    plan = gen_log.plan_run(
        cycles=7,
        pins=4,
        fail_rate=1.0,
        seed=3,
        blocks=3,
        start_vector=5,
    )

    by_occurrence = {
        block.occurrence: {failure.vector for failure in block.failures}
        for block in plan.blocks
    }
    assert by_occurrence == {1: {5, 6}, 2: {5, 6}, 3: {5, 6, 7}}
    assert all(
        failure.occurrence == block.occurrence
        for block in plan.blocks
        for failure in block.failures
    )


@pytest.mark.parametrize("period", [0, -1, -1000])
def test_generate_rejects_non_positive_period(period: int) -> None:
    """Generated cycle times must increase."""
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
    """Conflicting single-artifact flags must fail."""
    with pytest.raises(SystemExit) as exc:
        build_release.main(["--wheelhouse-only", "--zipapp-only"])
    assert exc.value.code == 2  # argparse usage error, not a success exit
