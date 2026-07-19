"""Entity tests — these PASS in Step 0.

``model/entities.py`` is complete per §4, so its invariants are testable now.
The invariants are deliberately enforced in ``__post_init__`` raising
``ValueError`` rather than by ``assert`` (which vanishes under ``python -O``) or
by builder-side checks (which do not guard alternate construction paths) — so
these tests exercise them by direct construction.
"""

from __future__ import annotations

import pickle
from dataclasses import FrozenInstanceError

import pytest

from ate_fa_suite.model.entities import (
    BlockId,
    CompareEvent,
    FailCategory,
    LogHeader,
    LogicState,
    PinDef,
    PinDirection,
    PinTiming,
    TestRun,
    TimingSet,
    VectorLocation,
    WaveformSegment,
    WaveformSeries,
)

BLOCK = BlockId("mbist_march_c", 1)
LOW, HIGH = LogicState.LOW, LogicState.HIGH


def _header() -> LogHeader:
    return LogHeader("L", "1", "D", "T", "P", "2026-07-11T00:00:00", 1.0)


def _run(**kwargs: object) -> TestRun:
    base: dict[str, object] = dict(
        header=_header(),
        pins=(PinDef("DQ0", PinDirection.IO),),
        blocks=(),
        failures=(),
        driven_waves=(),
        expected_waves=(),
        captured_waves=(),
    )
    base.update(kwargs)
    return TestRun(**base)  # type: ignore[arg-type]


# --- frozen + slotted --------------------------------------------------------


def test_entities_are_frozen() -> None:
    block = BlockId("a", 1)
    with pytest.raises(FrozenInstanceError):
        block.name = "b"  # type: ignore[misc]


def test_entities_are_slotted() -> None:
    """`slots=True` is not cosmetic: it is what makes 10**6 cycles affordable."""
    assert not hasattr(BlockId("a", 1), "__dict__")
    assert not hasattr(_header(), "__dict__")


def test_messages_are_picklable() -> None:
    """Tuple-backed frozen dataclasses stay picklable, which is what makes the
    message schema reusable under a future multiprocessing worker."""
    run = _run()
    assert pickle.loads(pickle.dumps(run)) == run


# --- BlockId: identity of one INVOCATION ------------------------------------


def test_block_id_label_distinguishes_reruns() -> None:
    assert BlockId("mbist_march_c", 1).label() == "mbist_march_c"
    assert BlockId("mbist_march_c", 2).label() == "mbist_march_c#2"


def test_block_id_is_ordered() -> None:
    """`order=True` is what makes WaveKey tuples natively sortable/bisectable,
    which the sorted wave collections require."""
    assert BlockId("a", 1) < BlockId("a", 2) < BlockId("b", 1)
    keys = [(BlockId("b", 1), "DQ0"), (BlockId("a", 2), "CLK")]
    assert sorted(keys)[0][0] == BlockId("a", 2)


def test_vector_location_is_the_run_wide_address() -> None:
    """The same vector/time in two invocations are DIFFERENT addresses."""
    a = VectorLocation(BlockId("p", 1), 1200, 1_200_000)
    b = VectorLocation(BlockId("p", 2), 1200, 1_200_000)
    assert a != b
    assert len({a, b}) == 2


# --- WaveformSegment invariants ----------------------------------------------


def test_segment_accepts_a_single_retained_state() -> None:
    seg = WaveformSegment(0, 10, (0,), (LOW,))
    assert seg.state_at(7) is LOW


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        (dict(t_start=0, t_end=10, times=(), states=()), "empty"),
        (dict(t_start=0, t_end=10, times=(0, 5), states=(LOW,)), "not parallel"),
        (dict(t_start=5, t_end=0, times=(5,), states=(LOW,)), "start > end"),
        (dict(t_start=0, t_end=10, times=(3,), states=(LOW,)), "times[0] != t_start"),
        (dict(t_start=0, t_end=4, times=(0, 9), states=(LOW, HIGH)), "past t_end"),
        (
            dict(t_start=0, t_end=10, times=(0, 5, 5), states=(LOW, HIGH, LOW)),
            "not strictly ascending",
        ),
    ],
)
def test_segment_rejects_broken_invariants(
    kwargs: dict[str, object], reason: str
) -> None:
    with pytest.raises(ValueError):
        WaveformSegment(**kwargs)  # type: ignore[arg-type]


def test_segment_state_at_holds_between_transitions() -> None:
    seg = WaveformSegment(0, 20, (0, 5, 10), (LOW, HIGH, LOW))
    assert seg.state_at(0) is LOW
    assert seg.state_at(4) is LOW
    assert seg.state_at(5) is HIGH
    assert seg.state_at(9) is HIGH
    assert seg.state_at(15) is LOW


def test_clipped_reanchors_the_carry_in_state() -> None:
    """The `times[0] == t_start` invariant must survive clipping — this is what
    fixes the flat-signal case and avoids enormous negative Tk coordinates for
    a long-quiet pin."""
    seg = WaveformSegment(0, 20, (0, 5, 10), (LOW, HIGH, LOW))
    clip = seg.clipped(7, 15)
    assert clip is not None
    assert clip.t_start == 7 and clip.t_end == 15
    assert clip.times[0] == 7
    assert clip.states[0] is HIGH  # carried in, not dropped
    assert clip.times == (7, 10)


def test_clipped_returns_none_when_disjoint() -> None:
    seg = WaveformSegment(10, 20, (10,), (LOW,))
    assert seg.clipped(0, 5) is None
    assert seg.clipped(30, 40) is None


def test_clipped_of_a_flat_signal_still_yields_a_segment() -> None:
    seg = WaveformSegment(0, 100, (0,), (HIGH,))
    clip = seg.clipped(40, 60)
    assert clip is not None
    assert clip.times == (40,) and clip.states == (HIGH,)


# --- WaveformSeries: gaps are STRUCTURAL -------------------------------------


def _two_segment_series() -> WaveformSeries:
    return WaveformSeries(
        BLOCK,
        "DQ0",
        (
            WaveformSegment(0, 10, (0, 5), (LOW, HIGH)),
            WaveformSegment(100, 110, (100, 105), (HIGH, LOW)),
        ),
    )


def test_series_rejects_overlapping_or_unsorted_segments() -> None:
    with pytest.raises(ValueError):
        WaveformSeries(
            BLOCK,
            "DQ0",
            (
                WaveformSegment(0, 50, (0,), (LOW,)),
                WaveformSegment(20, 60, (20,), (HIGH,)),
            ),
        )


def test_state_at_returns_none_inside_a_retention_gap() -> None:
    """A gap and a held state are DIFFERENT FACTS. `None` means the tool
    discarded the data; the renderer must hatch it, never extrapolate."""
    series = _two_segment_series()
    assert series.state_at(5) is HIGH
    assert series.state_at(50) is None  # the gap
    assert series.state_at(105) is LOW


def test_state_at_returns_none_outside_all_segments() -> None:
    series = _two_segment_series()
    assert series.state_at(-1) is None
    assert series.state_at(10_000) is None


def test_window_clips_to_the_overlapping_segments_only() -> None:
    series = _two_segment_series()
    assert len(series.window(0, 200)) == 2
    assert len(series.window(0, 50)) == 1
    assert series.window(20, 90) == ()  # entirely inside the gap


def test_window_preserves_the_segment_invariant() -> None:
    for seg in _two_segment_series().window(3, 108):
        assert seg.times[0] == seg.t_start


# --- TimingSet / TestRun structural invariants -------------------------------


def test_timing_set_requires_sorted_unique_pins() -> None:
    ok = TimingSet("ts1", (("CLK", PinTiming()), ("DQ0", PinTiming())))
    assert ok.name == "ts1"
    with pytest.raises(ValueError):
        TimingSet("ts2", (("DQ0", PinTiming()), ("CLK", PinTiming())))
    with pytest.raises(ValueError):
        TimingSet("ts3", (("CLK", PinTiming()), ("CLK", PinTiming())))


def test_test_run_requires_sorted_unique_wave_keys() -> None:
    a = WaveformSeries(BlockId("a", 1), "DQ0", (WaveformSegment(0, 1, (0,), (LOW,)),))
    b = WaveformSeries(BlockId("b", 1), "DQ0", (WaveformSegment(0, 1, (0,), (LOW,)),))
    _run(driven_waves=(a, b))  # sorted: fine
    with pytest.raises(ValueError):
        _run(driven_waves=(b, a))
    with pytest.raises(ValueError):
        _run(captured_waves=(a, a))


def test_test_run_requires_unique_timing_set_names() -> None:
    with pytest.raises(ValueError):
        _run(timing_sets=(TimingSet("ts", ()), TimingSet("ts", ())))


def test_wave_collections_stay_provenance_separated() -> None:
    """Three collections, never merged: DRV is programmed stimulus, GOT is a
    comparator observation.  The same WaveKey may legitimately appear in all
    three — which is only possible because they are separate tuples."""
    series = WaveformSeries(BLOCK, "DQ0", (WaveformSegment(0, 1, (0,), (LOW,)),))
    run = _run(
        driven_waves=(series,),
        expected_waves=(series,),
        captured_waves=(series,),
    )
    assert run.driven_waves is not run.captured_waves
    assert len(run.expected_waves) == 1


def test_wave_collections_are_tuples_not_mappings() -> None:
    """Tuple-backed on purpose: a frozen dataclass holding a dict is only
    SHALLOWLY immutable, and MappingProxyType is unpicklable."""
    run = _run()
    for waves in (run.driven_waves, run.expected_waves, run.captured_waves):
        assert isinstance(waves, tuple)


# --- classify: authority policy (the exhaustive table is Phase 2 M1) ---------


def test_classify_returns_none_for_non_failing_compares() -> None:
    assert FailCategory.classify(HIGH, LOW, failed=False) is None


def test_classify_flags_masked_compare_as_inconsistent() -> None:
    """An expected X is a mask — a masked compare cannot fail, so a FAIL flag
    on one is a contradiction, surfaced rather than reinterpreted."""
    assert (
        FailCategory.classify(LogicState.UNKNOWN, LOW, failed=True)
        is FailCategory.INCONSISTENT
    )


def test_classify_flags_agreeing_states_as_inconsistent() -> None:
    assert (
        FailCategory.classify(HIGH, HIGH, failed=True)
        is FailCategory.INCONSISTENT
    )


@pytest.mark.parametrize(
    ("expected", "actual", "category"),
    [
        (HIGH, LOW, FailCategory.SA0_CANDIDATE),
        (LOW, HIGH, FailCategory.SA1_CANDIDATE),
        (HIGH, LogicState.UNKNOWN, FailCategory.FLOATING),
        (HIGH, LogicState.HIGH_Z, FailCategory.OPEN_TRISTATE),
        (HIGH, LogicState.WEAK_LOW, FailCategory.WEAK_DRIVE),
        (LOW, LogicState.WEAK_HIGH, FailCategory.WEAK_DRIVE),
        (LogicState.HIGH_Z, HIGH, FailCategory.OTHER),
        (LogicState.WEAK_LOW, HIGH, FailCategory.OTHER),
    ],
)
def test_classify_kinds(
    expected: LogicState, actual: LogicState, category: FailCategory
) -> None:
    assert FailCategory.classify(expected, actual, failed=True) is category


def test_compare_event_records_the_testers_flag() -> None:
    """The PASS/FAIL flag decides failure MEMBERSHIP; states are evidence only
    for the KIND.  `passed` is therefore stored verbatim, never derived."""
    event = CompareEvent("DQ0", HIGH, HIGH, passed=False)
    assert event.passed is False
    assert (
        FailCategory.classify(event.expected, event.actual, not event.passed)
        is FailCategory.INCONSISTENT
    )
