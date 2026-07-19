"""ATE Test Log Visualizer & Diagnostics Suite.

Datalog -> per-pin waveform reconstruction around the failing vector ->
automated fail-signature clustering, for semiconductor failure analysis.

Layering (enforced by the §2.3 import firewall, `tests/test_import_firewall.py`):

    view/                     imports tkinter, NEVER lark
    viewmodel/ services/      pure Python
    model/ parsing/           imports lark, NEVER tkinter
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
