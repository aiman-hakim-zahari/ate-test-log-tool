"""Fail-signature clustering and shared filter predicates."""

from __future__ import annotations

import re
from typing import Callable, Final

from ate_fa_suite.model.entities import (
    FailureEvent,
    SignatureCluster,
    TestRun,
)

# Number of vectors in one signature bucket.
SIGNATURE_BUCKET: Final = 100

# Matches indexed pin names such as DQ3 and DQ[3].
BUS_MEMBER_RE: Final = re.compile(r"^(?P<base>[A-Za-z_]\w*?)\[?(?P<index>\d+)\]?$")

# The table and signature panel use the same predicate type.
FailurePredicate = Callable[[FailureEvent], bool]


def collapse_pin(pin: str) -> str:
    """``"DQ3"`` -> ``"DQ[*]"``; a non-bus pin returns unchanged."""
    raise NotImplementedError("Phase 2 M2 — see docs/ROADMAP.md")


def cluster(run: TestRun) -> tuple[SignatureCluster, ...]:
    """Cluster every failure and rank the clusters by descending share."""
    raise NotImplementedError("Phase 2 M2 — see docs/ROADMAP.md")


def summarize(cluster_: SignatureCluster) -> str:
    """Write one report-ready sentence for a cluster."""
    raise NotImplementedError("Phase 2 M5 — see docs/ROADMAP.md")


def export_csv(run: TestRun, failures: tuple[FailureEvent, ...]) -> str:
    """Failures as CSV (Phase 2 milestone 5)."""
    raise NotImplementedError("Phase 2 M5 — see docs/ROADMAP.md")


def export_fa_summary(run: TestRun) -> str:
    """Create a plain-text failure-analysis summary."""
    raise NotImplementedError("Phase 2 M5 — see docs/ROADMAP.md")
