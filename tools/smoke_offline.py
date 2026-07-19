"""Smoke-test the installed wheel without using the source checkout."""

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
    """Fail if Python imported the source checkout instead of the artifact."""
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

    # Check the fragment rules used by indexed parsing too.
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
