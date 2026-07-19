"""Build the two fab-floor deployment artifacts (§1.2 differentiator 5).

Semiconductor lab PCs are offline and admin-locked.  Two artifacts cover that:

``dist/wheelhouse/``
    The app wheel plus every dependency wheel (just Lark).  Installed with
    ``pip install --no-index --find-links dist/wheelhouse ate-fa-suite`` — the
    ``--no-index`` is the whole point: anything missing from the wheelhouse
    fails **loudly** instead of being silently downloaded.

``dist/ate_fa_suite.pyz``
    A single-file zipapp bundling Lark, runnable on the machine's stock Python
    with no pip at all.  The true single-file deployment.

Both are exercised offline in CI on ``windows-latest``.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipapp
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST = REPO_ROOT / "dist"
WHEELHOUSE = DIST / "wheelhouse"
STAGE = DIST / "_pyz_stage"
PYZ = DIST / "ate_fa_suite.pyz"


def _run(args: Sequence[str]) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, check=True, cwd=REPO_ROOT)


def build_wheelhouse() -> Path:
    """``pip wheel .`` — resolves and builds the app wheel *and* Lark's."""
    if WHEELHOUSE.exists():
        shutil.rmtree(WHEELHOUSE)
    WHEELHOUSE.mkdir(parents=True)
    _run([sys.executable, "-m", "pip", "wheel", ".", "-w", str(WHEELHOUSE)])
    wheels = sorted(p.name for p in WHEELHOUSE.glob("*.whl"))
    print(f"wheelhouse: {len(wheels)} wheel(s): {', '.join(wheels)}")
    if not any(w.startswith("lark") for w in wheels):
        raise SystemExit(
            "wheelhouse is missing Lark - an offline install would fail"
        )
    return WHEELHOUSE


def build_zipapp() -> Path:
    """Stage app + deps into one directory, then zip it into a ``.pyz``.

    ``pip install --target`` is what pulls Lark *and* the ``atelog.lark``
    package data in; the grammar is reachable inside the zip because
    ``importlib.resources`` works over ``zipimport``, which is exactly why the
    parser never uses a ``__file__``-relative path (§3.2).
    """
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            ".",
            "--target",
            str(STAGE),
            "--no-compile",
        ]
    )

    # Trim installer bookkeeping - it is dead weight inside a zipapp.
    for junk in list(STAGE.glob("*.dist-info")) + list(STAGE.glob("bin")):
        shutil.rmtree(junk, ignore_errors=True)

    grammar = STAGE / "ate_fa_suite" / "parsing" / "grammar" / "atelog.lark"
    if not grammar.exists():
        raise SystemExit(
            "atelog.lark did not reach the zipapp stage - check the "
            "[tool.setuptools.package-data] declaration in pyproject.toml"
        )

    if PYZ.exists():
        PYZ.unlink()
    zipapp.create_archive(
        STAGE,
        target=PYZ,
        main="ate_fa_suite.__main__:main",
        compressed=True,
    )
    shutil.rmtree(STAGE, ignore_errors=True)
    print(f"zipapp: {PYZ} ({PYZ.stat().st_size / 1024:.0f} KiB)")
    return PYZ


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_release",
        description="Build the offline wheelhouse and the single-file zipapp.",
    )
    parser.add_argument("--wheelhouse-only", action="store_true")
    parser.add_argument("--zipapp-only", action="store_true")
    args = parser.parse_args(argv)

    DIST.mkdir(exist_ok=True)
    if not args.zipapp_only:
        build_wheelhouse()
    if not args.wheelhouse_only:
        build_zipapp()

    print("\nVerify offline, in a throwaway venv:")
    print(
        "  pip install --no-index --find-links dist/wheelhouse ate-fa-suite"
    )
    print("  python tools/smoke_offline.py")
    print("  python dist/ate_fa_suite.pyz")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
