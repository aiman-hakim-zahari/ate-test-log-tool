"""Presentation state and commands — pure Python, unit-testable without a display.

Must NEVER import ``tkinter`` (§2.3 import firewall): that is exactly what makes
the ViewModel testable headless in CI.
"""

from __future__ import annotations
