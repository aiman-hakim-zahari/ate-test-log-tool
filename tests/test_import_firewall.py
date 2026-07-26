"""AST checks that keep parser and UI dependencies in their own layers."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "ate_fa_suite"

#: package directory -> module prefixes it may never import.
FORBIDDEN: dict[str, tuple[str, ...]] = {
    "parsing": ("tkinter",),
    "model": ("tkinter",),
    "viewmodel": ("tkinter",),
    "services": ("tkinter",),
    "view": ("lark",),
}

#: Files exempt from the firewall, relative to the package root.
EXEMPT: frozenset[str] = frozenset({"__main__.py"})


def _module_files(package: str) -> list[Path]:
    directory = PACKAGE_ROOT / package
    return sorted(
        p
        for p in directory.rglob("*.py")
        if p.relative_to(PACKAGE_ROOT).as_posix() not in EXEMPT
    )


def _imported_roots(source: str) -> set[str]:
    """Every top-level module name imported anywhere in the file.

    Walks the whole tree, so imports nested inside functions, ``if`` blocks and
    ``try`` blocks are found too.
    """
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            # `from . import x` has module=None; relative imports never cross
            # the firewall, so only absolute ones matter here.
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_every_layer_package_is_covered() -> None:
    """If someone adds a layer package, the firewall must grow with it."""
    on_disk = {
        p.name
        for p in PACKAGE_ROOT.iterdir()
        if p.is_dir() and (p / "__init__.py").exists()
    }
    assert on_disk == set(FORBIDDEN), (
        f"layer packages {on_disk ^ set(FORBIDDEN)} are not in the firewall"
    )


def test_module_files_were_actually_found() -> None:
    """Guard against the firewall silently passing because it scanned nothing."""
    for package in FORBIDDEN:
        assert _module_files(package), f"no modules scanned under {package}/"


@pytest.mark.parametrize("package", sorted(FORBIDDEN))
def test_no_module_crosses_the_firewall(package: str) -> None:
    banned = FORBIDDEN[package]
    violations: list[str] = []
    for path in _module_files(package):
        roots = _imported_roots(path.read_text(encoding="utf-8"))
        for bad in banned:
            if bad in roots:
                rel = path.relative_to(PACKAGE_ROOT).as_posix()
                violations.append(f"{rel} imports {bad}")
    assert not violations, "import firewall breached: " + "; ".join(violations)


def test_ast_walk_detects_a_function_local_import() -> None:
    """Prove the detector is actually strong enough to be worth trusting —
    a sys.modules probe would never see this."""
    source = "def load():\n    import tkinter\n    return tkinter\n"
    assert "tkinter" in _imported_roots(source)


def test_ast_walk_detects_a_conditional_import() -> None:
    source = "import sys\nif sys.platform == 'win32':\n    from lark import Lark\n"
    assert "lark" in _imported_roots(source)


# --- supplementary smoke test (subprocess, so it stays meaningful) ----------


@pytest.mark.parametrize(
    ("package", "banned"),
    [
        ("ate_fa_suite.parsing.parser", "tkinter"),
        ("ate_fa_suite.model.entities", "tkinter"),
        ("ate_fa_suite.viewmodel.app_viewmodel", "tkinter"),
        ("ate_fa_suite.services.background", "tkinter"),
    ],
)
def test_importing_a_lower_layer_does_not_pull_in_tk(
    package: str, banned: str
) -> None:
    """Runs in a fresh interpreter: no other test can have imported Tk first."""
    code = (
        f"import importlib, sys; importlib.import_module({package!r}); "
        f"sys.exit(1 if {banned!r} in sys.modules else 0)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, (
        f"{package} pulled in {banned}\n{result.stdout}{result.stderr}"
    )
