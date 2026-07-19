"""Grammar package — holds ``atelog.lark`` as *package data*, not code.

``atelog.lark`` is declared in ``pyproject.toml`` under
``[tool.setuptools.package-data]`` and is read through
``importlib.resources.files("ate_fa_suite.parsing.grammar")`` — never a
``__file__``-relative path.  That is precisely what keeps it working from an
installed wheel or a zipapp; an editable install reads the source tree and
therefore proves nothing about packaging (§3.2, Verification item 5).
"""

from __future__ import annotations
