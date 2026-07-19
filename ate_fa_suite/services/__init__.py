"""Background parsing and file services.

Must NEVER import ``tkinter`` (§2.3): the threading contract says only the main
thread touches Tk widgets, and the worker never calls Tk — not even
``root.after``.  Not importing it is how that is guaranteed rather than hoped.
"""

from __future__ import annotations
