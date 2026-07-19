"""Composition root — ``python -m ate_fa_suite [file]``.

This module is the *only* place allowed to import across the §2.3 firewall in
both directions: composing the layers is precisely its job.  The firewall test
therefore checks the five layer packages and deliberately exempts this file.

Until Phase 3 lands, running the app prints a clear placeholder banner rather
than a traceback (Verification item 4) — and that same banner is what the
single-file zipapp prints from a venv with nothing pip-installed, which is the
Verification item 6 proof.
"""

from __future__ import annotations

import sys

BANNER = """\
ATE Test Log Visualizer & Diagnostics Suite v{version}

  GUI not yet implemented - see docs/ROADMAP.md Phase 3.

  Working today:
    - ADF-1 Lark grammar (multi-start, LALR/contextual)  ate_fa_suite/parsing/grammar/atelog.lark
    - Domain entities (frozen, slotted, tuple-backed)    ate_fa_suite/model/entities.py
    - Synthetic log generator                            python tools/gen_log.py --help
    - Release builder (wheelhouse + .pyz zipapp)         python tools/build_release.py

  Run the test suite with:  pytest
"""


def main(argv: list[str] | None = None) -> int:
    """Entry point.  Returns a process exit code; never raises for the
    not-yet-implemented GUI."""
    args = sys.argv[1:] if argv is None else argv

    from ate_fa_suite import __version__

    print(BANNER.format(version=__version__))

    if args:
        print(f"  Requested log: {args[0]}")
        print("  (loading is Phase 3 work; the parser lands in Phase 1.)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
