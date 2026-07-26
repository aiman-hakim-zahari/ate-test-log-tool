"""STDF V4-2007 reader: a real, standardized production format.

Built on stdlib ``struct`` alone, so ``lark`` stays the project's only runtime
dependency and the wheelhouse stays at two wheels.  This lives as a subpackage
of ``parsing`` on purpose: the import firewall's ``_module_files()`` walks with
``rglob``, so this code is covered by the ``parsing`` rule against ``tkinter``
without needing a ``FORBIDDEN`` entry of its own.
"""

from __future__ import annotations

from ate_fa_suite.parsing.stdf.parser import StdfParser

__all__ = ["StdfParser"]
