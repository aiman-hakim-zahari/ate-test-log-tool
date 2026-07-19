"""Parsing layer: raw bytes -> Lark -> ADF-1 dataclasses.

Imports ``lark``.  Must NEVER import ``tkinter`` (§2.3 import firewall).
"""

from __future__ import annotations
