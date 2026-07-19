"""Verify that the zipapp includes Lark, the package, and its grammar."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


def check_ambient_has_no_lark() -> None:
    if importlib.util.find_spec("lark") is not None:
        raise SystemExit(
            "REFUSING to claim a zipapp proof: lark is importable from the "
            "ambient environment, so a bundled-Lark failure would be masked. "
            "Run this from a venv with nothing pip-installed."
        )
    print("  confirmed: ambient environment has no lark")


def check_banner(pyz: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(pyz)], capture_output=True, text=True
    )
    if result.returncode != 0 or "ate_fa_suite" not in result.stdout.lower():
        raise SystemExit(
            f"zipapp launch failed (exit {result.returncode})\n"
            f"{result.stdout}{result.stderr}"
        )
    print("  zipapp launched and printed its banner")


def check_grammar_from_archive(pyz: Path) -> None:
    sys.path.insert(0, str(pyz))
    from ate_fa_suite.parsing.parser import build_parser, load_grammar_text

    import ate_fa_suite

    origin = ate_fa_suite.__file__ or ""
    if pyz.name not in origin:
        raise SystemExit(
            f"ate_fa_suite was imported from {origin!r}, not from the zipapp"
        )
    print(f"  ate_fa_suite imported from inside {pyz.name}")

    text = load_grammar_text()
    if "cycle_batch" not in text:
        raise SystemExit("atelog.lark loaded from the archive but looks wrong")
    print(f"  atelog.lark served from the archive ({len(text)} chars)")

    build_parser().parse("END LOG\n", start="end_log")
    print("  bundled Lark parsed a fragment using the archived grammar")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: check_zipapp.py <path to ate_fa_suite.pyz>")
    pyz = Path(sys.argv[1]).resolve()
    if not pyz.is_file():
        raise SystemExit(f"no such zipapp: {pyz}")

    print(f"zipapp proof for {pyz} on {sys.version.split()[0]}")
    check_ambient_has_no_lark()
    check_banner(pyz)
    check_grammar_from_archive(pyz)
    print("OK - zipapp proof passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
