"""Phase 4 stubs — the canvas introspection list from §5 Phase 4 Verification.

Skipped by design in Step 0.  Gate for Steps 5 and 6.  Every test here is a
canvas *introspection* test: render a known series, then assert item counts and
coordinates via ``canvas.find_withtag`` / ``canvas.coords`` — no screenshots, no
eyeballing.
"""

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.phase4,
    pytest.mark.skip(reason="Phase 4 — Steps 5-6; see docs/ROADMAP.md"),
]


# --- coverage and honesty ----------------------------------------------------


def test_flat_fully_retained_signal_yields_a_two_point_line() -> None:
    """create_line needs two points; the coverage endpoints supply them even
    when the pin never transitions in view."""


def test_two_retained_segments_yield_two_lines_with_hatch_between() -> None:
    """...and NO line item crossing the gap.  The renderer never extrapolates
    across its own storage optimization."""


def test_segment_ending_mid_screen_hatches_to_the_canvas_edge() -> None:
    """Hatch runs from the segment's COVERAGE edge, not its last transition."""


def test_empty_window_yields_hatch_only_and_zero_wave_items() -> None: ...


def test_segment_only_in_the_widened_margin_renders_nothing() -> None:
    """floor/ceil widen the integer query so edge data is never lost, but a
    segment present ONLY because of that widening must be skipped outright —
    not half-clamped into a reversed line or a backward-moving paint cursor."""


def test_all_display_coordinates_are_clamped_on_both_sides() -> None:
    """Segment endpoints AND transitions, both ends.  At high zoom a sub-unit
    overhang is thousands of pixels."""


# --- coalescing --------------------------------------------------------------


def test_busy_column_exits_at_the_final_transition_level() -> None:
    """The run following a busy column sits at the LAST state's y.  Dropping
    later transitions would leave the wave at a stale level until the next
    visible edge — wrong data, not just an aliasing artifact."""


def test_busy_column_emits_exactly_one_busy_marker() -> None: ...


# --- mismatch bands ----------------------------------------------------------


def test_mismatch_bands_come_from_failure_events_only() -> None:
    """Never from waveform XOR.  This inherits the §4 authority policy and stays
    valid where retention has gaps."""


def test_band_width_uses_strobe_window_then_cycle_period() -> None:
    """Both assembly-resolved; minimum MIN_BAND_PX.  No cycle data is needed at
    render time."""


def test_masked_compare_never_produces_a_band_in_either_mode() -> None: ...


def test_inferred_overlay_is_off_by_default() -> None:
    """With the overlay off, mismatch items appear ONLY at FailureEvent strobe
    times.  Held-state XOR between strobes is inference, not measurement."""


def test_inferred_overlay_is_confined_to_retained_segments() -> None: ...


def test_inferred_overlay_carries_its_legend_label() -> None: ...


# --- geometry, interaction, rendering conventions ----------------------------


def test_cursor_anchored_zoom_keeps_the_time_under_the_cursor_stationary() -> None:
    """Naive zoom about x=0 makes the failure fly off-screen."""


def test_x_state_renders_as_a_gray_hatched_block() -> None: ...


def test_z_state_renders_as_a_dashed_mid_level_line() -> None: ...


def test_strobe_ticks_use_assembly_resolved_instants() -> None:
    """The renderer evaluates no timing chain at all."""


def test_held_line_between_strobes_renders_dimmed() -> None:
    """So it reads as inference, not measurement."""


def test_legend_notes_the_nrz_idealization() -> None: ...


def test_driven_and_captured_series_are_visually_distinct() -> None:
    """On an IO lane both render together with the turnaround readable."""


def test_selection_sync_is_bidirectional() -> None: ...


def test_viewport_is_block_scoped() -> None:
    """Selecting a failure in another block SWAPS the series set rather than
    scrolling — cross-block time is not a single axis, because no such axis
    exists in the log."""


@pytest.mark.perf
def test_full_redraw_under_50ms_at_200_transitions_by_12_lanes() -> None: ...
