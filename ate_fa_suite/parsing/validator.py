"""Post-assembly semantic validation (Phase 1 milestone 7).

The grammar accepts structurally valid but semantically wrong logs, so this
pass enforces the rest of the spec under an explicit **two-tier policy**.

Fatal -> ``ParseFailed``
    * unsupported ``#ATELOG`` major version;
    * required metadata violated — each of the seven keys exactly once,
      ``TIMESCALE`` parseable as a valid unit;
    * non-strictly-increasing vector or time within a block invocation.  The
      waveform bisects depend on sorted transitions, so this can never be
      downgraded to a warning.

Recoverable -> ``TestRun.warnings`` + a deterministic rule
    * duplicate ``PINDEF``                        -> first wins;
    * pin event on an undeclared pin              -> auto-declare ``IO``, warn;
    * duplicate pin event for a pin in one cycle  -> first wins;
    * reserved word used as an identifier where the contextual lexer happened
      to accept it;
    * ``FAILSUMMARY`` mismatches — **two separate checks**, because the record
      makes two independently checkable claims (§3.1):

      - declared *count* vs observed failing **compare lines** (pin-granular:
        one vector with three failing pins contributes three);
      - declared ``VECTORS`` vs the observed **set of vectors** with >= 1
        ``FAIL``.

      They are reported as distinct warnings rather than one combined check: a
      log can get the count right and the vector list wrong, or vice versa, and
      collapsing them would hide which witness disagreed.

``WaveformSegment`` / ``WaveformSeries`` / ``TimingSet`` structural invariants
are deliberately *not* validated here: they live in the model itself as
``__post_init__`` checks raising ``ValueError`` (§4), because ``assert``
statements vanish under ``python -O`` and builder-side checks do not guard
alternate construction paths.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:  # import cycle: transformer imports this module at runtime
    from ate_fa_suite.parsing.transformer import ParsedDocument

#: The seven metadata keys ADF-1 requires, each exactly once.
REQUIRED_META_KEYS: Final[tuple[str, ...]] = (
    "LOT",
    "WAFER",
    "DEVICE",
    "TESTER",
    "PROGRAM",
    "DATE",
    "TIMESCALE",
)

#: ``TIMESCALE`` units accepted by ADF-1 v1, in nanoseconds.
TIMESCALE_UNITS_NS: Final[dict[str, float]] = {
    "ps": 0.001,
    "ns": 1.0,
    "us": 1000.0,
    "ms": 1_000_000.0,
}


class ValidationError(Exception):
    """A *fatal*-tier violation; the caller maps this to ``ParseFailed``."""

    def __init__(self, message: str, line: int, column: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Result of the recoverable tier: warnings, each carrying a source line."""

    warnings: tuple[str, ...] = ()


def parse_timescale(value: str, src_line: int = 0) -> float:
    """``"1ns"`` -> ``1.0`` (nanoseconds).  Fatal-tier on an unknown unit.

    Implemented ahead of the rest of M7 because ``LogHeader.timescale_ns`` is a
    ``float`` and cannot be populated without it; the surrounding two-tier pass
    is still Step 2 work.
    """
    text = value.strip()
    for suffix, factor in TIMESCALE_UNITS_NS.items():
        if text.endswith(suffix):
            magnitude = text[: -len(suffix)].strip()
            try:
                scale = float(magnitude)
            except ValueError:
                raise ValidationError(
                    f"TIMESCALE magnitude {magnitude!r} is not a number", src_line
                ) from None
            # isfinite BEFORE the positivity test, and not folded into it:
            # `float()` happily accepts "NaN" and overflows "1e309" to inf, and
            # `nan <= 0` is False — so a positivity check alone lets both
            # through and every downstream time computation silently becomes
            # nan/inf.
            if not math.isfinite(scale):
                raise ValidationError(
                    f"TIMESCALE must be a finite number, got {text!r}", src_line
                )
            if scale <= 0:
                raise ValidationError(
                    f"TIMESCALE must be positive, got {text!r}", src_line
                )
            return scale * factor
    raise ValidationError(
        f"TIMESCALE {text!r} has no recognized unit "
        f"({', '.join(TIMESCALE_UNITS_NS)})",
        src_line,
    )


def validate(document: ParsedDocument) -> ValidationReport:
    """Run both tiers over a ``ParsedDocument``, **before** assembly.

    Takes a ``ParsedDocument`` rather than the assembled ``TestRun`` the plan's
    §5 M7 wording implies ("a post-assembly pass").  That wording cannot be
    satisfied: assembly deliberately discards the cycles, passing compares,
    duplicate ``PINDEF``s, magic-line version and declared ``FAILSUMMARY``
    count that these very rules test, and §6.3 requires ``Cycle`` objects be
    discarded.  Since the evidence cannot move later without breaking a
    load-bearing invariant, the pass moves earlier.

    Raises ``ValidationError`` for fatal-tier violations; returns recoverable
    findings as warnings for ``TestRun.warnings``.
    """
    raise NotImplementedError("Phase 1 M7 — see docs/ROADMAP.md")
