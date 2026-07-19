"""Tkinter views: layout and rendering only, no state and no business logic.

Imports ``tkinter``.  Must NEVER import ``lark`` (§2.3 import firewall) — views
consume the immutable ``TestRun`` the ViewModel hands them and know nothing
about how it was parsed.
"""

from __future__ import annotations
