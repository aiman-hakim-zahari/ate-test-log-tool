"""Offline-install proof (PROJECT_PLAN Verification item 5).

Run this from a throwaway venv that was populated with

    pip install --no-index --find-links dist/wheelhouse ate-fa-suite

It asserts the two things an editable install can never prove:

(a) ``atelog.lark`` really ships as **package data** and is reachable through
    ``importlib.resources`` — not via a ``__file__`` path that happens to work
    only in a source tree;
(b) ``tkinter`` genuinely works on the target Windows install — a Tk root can be
    created and destroyed.

The sample log is embedded here on purpose: ``sample_logs/`` is not installed
into the wheel, so an external file would test the checkout rather than the
installed artifact.
"""

from __future__ import annotations

import sys

SAMPLE = """#ATELOG v1.0
LOT: SMOKE-001
WAFER: 1
DEVICE: SMOKE_DUT
TESTER: SMOKE
PROGRAM: smoke
DATE: 2026-07-11T00:00:00
TIMESCALE: 1ns

PINDEF CLK IN
PINDEF DQ0 IO

// a standalone comment, to prove the NEWLINE terminal shipped intact
TESTBLOCK smoke_block
CYCLE 1 T=0
PIN CLK DRV 1
PIN DQ0 EXP 1 GOT 0 FAIL    // trailing comment
END CYCLE
FAILSUMMARY 1 VECTORS 1
END TESTBLOCK
END LOG
"""


def check_is_the_installed_copy() -> None:
    """Prove we are testing the ARTIFACT, not the checkout.

    Without this the whole proof is self-deception: run from a repo root with
    the source tree on ``sys.path`` and every assertion below would pass while
    exercising the working copy.
    """
    import ate_fa_suite

    origin = ate_fa_suite.__file__ or "<namespace package>"
    print(f"  ate_fa_suite imported from {origin}")
    if "site-packages" not in origin.replace("\\", "/") and ".pyz" not in origin:
        raise SystemExit(
            "REFUSING to claim an offline proof: ate_fa_suite was imported from "
            "a source tree, not from the installed wheel or the zipapp."
        )


def check_grammar_ships() -> None:
    from ate_fa_suite.parsing.parser import build_parser, load_grammar_text

    text = load_grammar_text()
    if "NEWLINE" not in text or "cycle_batch" not in text:
        raise SystemExit("atelog.lark loaded but looks wrong")
    print(f"  grammar loaded via importlib.resources ({len(text)} chars)")

    parser = build_parser()
    parser.parse(SAMPLE, start="document")
    print("  golden log parsed with start='document'")

    # The fragment start rules must ship too - they are what the chunked path
    # runs on, and a packaging mistake that dropped them would only show up in
    # production on a large file.
    parser.parse("END LOG\n", start="end_log")
    print("  fragment start rule 'end_log' available")


def check_tk_works() -> None:
    import tkinter

    root = tkinter.Tk()
    root.withdraw()
    version = root.tk.call("info", "patchlevel")
    root.destroy()
    print(f"  tkinter Tk root created and destroyed (Tcl/Tk {version})")


def main() -> int:
    print(f"offline smoke test on {sys.version.split()[0]} @ {sys.executable}")
    check_is_the_installed_copy()
    check_grammar_ships()
    check_tk_works()
    print("OK - offline install proof passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
