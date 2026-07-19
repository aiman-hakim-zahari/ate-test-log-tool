"""Phase 2 stubs — classifier, clustering, waveform builder, filters, exports.

Skipped by design in Step 0.  Gate for Step 3: pure unit tests, no display.
"""

from __future__ import annotations

import itertools

import pytest

from ate_fa_suite.model.entities import LogicState

pytestmark = [
    pytest.mark.phase2,
    pytest.mark.skip(reason="Phase 2 — Step 3; see docs/ROADMAP.md"),
]

#: 6 expected x 6 actual x 2 flags = 72 cases, exhaustively.
TRUTH_TABLE = list(
    itertools.product(LogicState, LogicState, (True, False))
)


# --- M1: classifier authority policy -----------------------------------------


@pytest.mark.parametrize(("expected", "actual", "failed"), TRUTH_TABLE)
def test_classify_truth_table_is_exhaustive(
    expected: LogicState, actual: LogicState, failed: bool
) -> None:
    """All 72 combinations pinned down.

    The authority policy: the tester's PASS/FAIL flag decides failure
    MEMBERSHIP, the states decide only the KIND, and contradictions surface as
    INCONSISTENT rather than being silently reinterpreted.
    """


def test_non_masked_pass_with_disagreeing_states_is_a_warning() -> None:
    """Symmetric to INCONSISTENT: a PASS line whose states disagree is a log
    inconsistency, not a failure."""


def test_inconsistent_records_are_never_folded_into_stuck_at_counts() -> None:
    """They stay visible as evidence — shown in the table, clustered under
    their own signature, surfaced in the status bar."""


# --- M2: signature clustering ------------------------------------------------


@pytest.mark.parametrize(
    ("pin", "group"),
    [("DQ3", "DQ[*]"), ("DQ[7]", "DQ[*]"), ("CLK", "CLK"), ("RST_N", "RST_N")],
)
def test_bus_collapsing(pin: str, group: str) -> None:
    """DQ3 -> DQ[*]; a non-bus pin is unchanged."""


def test_signatures_never_merge_across_block_invocations() -> None:
    """Vector numbers restart per invocation, so FailSignature carries its
    BlockId — clustering by bare vector would merge unrelated failures."""


def test_single_failure_log_reports_hundred_percent_share() -> None:
    """The share arithmetic must not divide by zero or produce 0.999...."""


def test_zero_failure_log_produces_an_empty_ranking() -> None:
    """clean_pass.atelog: no clusters, no crash."""


def test_cluster_ranking_snapshot_with_a_fixed_seed() -> None:
    """Deterministic ordering, so the FA summary is reproducible."""


# --- M3: waveform builder ----------------------------------------------------


def test_two_distant_failures_yield_two_disjoint_segments() -> None:
    """...and state_at returns None in the gap: the tool discarded that data,
    which is a different fact from 'held'."""


def test_overlapping_retention_windows_merge_into_one_segment() -> None:
    """Adjacent windows merge too, and the times[0] == t_start invariant holds
    on the merged result."""


def test_failure_at_first_and_last_vector_clamps_its_window() -> None:
    """Window clamping at the block edges."""


def test_drive_only_pin_gets_a_driven_series_only() -> None:
    """CLK: nothing is ever synthesized into expected or captured for it —
    stimulus is not observation."""


def test_io_pin_yields_disjoint_driven_and_captured_series() -> None:
    """An IO pin alternating drive and compare must show no cross-contamination
    between the two collections."""


def test_timing_chain_is_resolved_at_assembly_not_at_render() -> None:
    """Cycle.timeset -> TimingSet entry -> PinDef.timing -> NRZ idealization,
    evaluated ONCE while Cycle objects still exist.  Drive edges and strobe
    placement are baked into transition times, FailureEvent.strobe_time is
    stamped, and the cycles are then discarded."""


def test_strobe_time_defaults_to_cycle_time_when_timing_is_unknown() -> None:
    """The NRZ idealization: no PinTiming anywhere in the chain."""


def test_cycle_period_is_resolved_from_the_neighbouring_cycle_delta() -> None:
    """It drives mismatch-band width, so the renderer needs no cycle data."""


def test_builder_output_satisfies_test_run_post_init() -> None:
    """Each wave tuple sorted and unique by WaveKey."""


# --- M4/M5: filters and exports ----------------------------------------------


def test_filter_predicates_compose() -> None:
    """Pin substring, vector range, block, category — shared by the table
    search box and signature-panel click-to-filter."""


def test_csv_export_round_trips_every_failure_row() -> None: ...


def test_fa_summary_reads_as_8d_language() -> None:
    """'83% of failures: SA0-candidate on DQ[7:0], vectors 1200-1299.'"""
