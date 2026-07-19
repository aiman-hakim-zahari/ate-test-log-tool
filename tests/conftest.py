"""Shared fixtures and path constants."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_LOGS = REPO_ROOT / "sample_logs"
PACKAGE_ROOT = REPO_ROOT / "ate_fa_suite"

# Allow `import tools.gen_log` (the Phase 1 property-test oracle) when running
# from a checkout without an editable install.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope="session")
def sample_logs() -> Path:
    return SAMPLE_LOGS


@pytest.fixture(scope="session")
def clean_pass_text() -> str:
    return (SAMPLE_LOGS / "clean_pass.atelog").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def multi_fail_text() -> str:
    return (SAMPLE_LOGS / "multi_fail.atelog").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def truncated_text() -> str:
    return (SAMPLE_LOGS / "truncated.atelog").read_text(encoding="utf-8")
