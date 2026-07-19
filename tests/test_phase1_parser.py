"""Phase 1 stubs — transformer, error mapping, chunked framing, validation.

Skipped by design in Step 0: these encode the *gates* for Steps 1 and 2, so the
roadmap is executable rather than prose.  Each test name is a requirement from
PROJECT_PLAN §5 Phase 1 / §6.1.
"""

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.phase1,
    pytest.mark.skip(reason="Phase 1 — Steps 1-2; see docs/ROADMAP.md"),
]


# --- M3: transformer ---------------------------------------------------------


def test_transformer_populates_every_dataclass_field() -> None:
    """Every grammar rule is covered; no field left at a default by accident."""


def test_transformer_assigns_block_occurrence_in_document_order() -> None:
    """multi_fail re-runs `mbist_march_c`; the two invocations must become
    BlockId(name, 1) and BlockId(name, 2), never one merged block."""


def test_src_line_round_trips_to_the_raw_log_line() -> None:
    """propagate_positions=True exists so a table row links back to the raw
    line — FA engineers always want it."""


# --- M4/M5: generator oracle and error mapping -------------------------------


def test_generated_failures_round_trip_exactly() -> None:
    """The property test: generator emits N seeded failures -> parse -> exactly
    N FailureEvents with matching vectors, pins and states."""


def test_syntax_error_becomes_positioned_parse_failed() -> None:
    """UnexpectedToken/UnexpectedCharacters -> ParseFailed carrying line,
    column and the offending source line — never a raised exception."""


@pytest.mark.perf
def test_parse_throughput_baseline_is_recorded() -> None:
    """1M-cycle corpus: record MB/s so later optimization has a baseline."""


# --- M6: raw-byte framing scanner and truncation salvage ---------------------


def test_frames_reassemble_byte_for_byte() -> None:
    """The lossless-framing property test: concatenating every emitted frame's
    raw bytes reproduces the input EXACTLY, original line endings included.
    This is what proves framing is total — every line in exactly one frame."""


def test_every_frame_parses_independently_with_its_fragment_rule() -> None:
    """Each frame is self-contained and maps 1:1 to a fragment start rule of
    the multi-start grammar."""


def test_crlf_golden_survives_the_chunked_path() -> None:
    """Frames are untouched byte slices; normalization exists only in the
    classification view."""


def test_comment_containing_end_cycle_does_not_split_a_frame() -> None:
    """Frame markers are recognized by LEADING TOKEN only, so comment or value
    text can never fake a boundary."""


def test_trivia_at_a_batch_boundary_attaches_to_the_preceding_frame() -> None:
    """Blank/comment lines immediately before and after a ~5k-cycle boundary:
    a fragment must start with its marker token, so leading trivia would lex to
    an orphan NEWLINE."""


def test_truncated_golden_salvages_exactly_three_cycles() -> None:
    """Every complete cycle before the break is recovered, block identity and
    the FAILSUMMARY cross-check are preserved, and the tail becomes a
    TestRun.warnings entry with its ABSOLUTE line number — delivered as a
    partial ParseComplete, never a crash."""


def test_frame_relative_error_positions_are_rebased_to_absolute() -> None:
    """Lark reports positions within the frame it was handed; all user-facing
    reporting stays absolute in the source file."""


# --- M7: two-tier semantic validation ----------------------------------------


@pytest.mark.parametrize(
    "rule",
    [
        "unsupported_major_version",
        "missing_required_metadata_key",
        "duplicate_metadata_key",
        "unparseable_timescale",
        "non_increasing_vector_within_block",
        "non_increasing_time_within_block",
    ],
)
def test_fatal_tier_yields_parse_failed(rule: str) -> None:
    """One malformed golden per rule, asserting tier, message and source line.

    Non-monotonic vectors/time can NEVER be a warning: the waveform bisects
    depend on sorted transitions.
    """


@pytest.mark.parametrize(
    "rule",
    [
        "duplicate_pindef_first_wins",
        "undeclared_pin_auto_declared_io",
        "duplicate_pin_event_in_cycle_first_wins",
        "reserved_word_as_identifier",
        "failsummary_count_mismatch",
    ],
)
def test_recoverable_tier_yields_warning_and_deterministic_rule(rule: str) -> None:
    """Warning text carries the source line, and the recovery rule is
    deterministic — not "whatever the dict happened to keep"."""


def test_model_invariants_are_not_delegated_to_the_validator() -> None:
    """WaveformSegment/WaveformSeries/TimingSet invariants live in the model as
    __post_init__ ValueErrors: `assert` vanishes under `python -O`, and
    builder-side checks do not guard alternate construction paths."""
