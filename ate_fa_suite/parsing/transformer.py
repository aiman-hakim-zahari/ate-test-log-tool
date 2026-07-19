"""``lark.Transformer`` -> ADF-1 dataclasses (Phase 1 milestone 3).

Typed stub — implementing this is the first slice of the portfolio work.
Every grammar rule in ``atelog.lark`` gets a method here; the transformer is
the *only* place Lark ``Tree``/``Token`` objects are allowed to escape into.
"""

from __future__ import annotations

from lark import Token, Transformer, Tree

from ate_fa_suite.model.entities import (
    Cycle,
    LogHeader,
    LogicState,
    PinDef,
)


class AteLogTransformer(Transformer[Token, Tree[Token]]):
    """Bottom-up transformation of a parse tree into frozen dataclasses.

    Note the deliberate asymmetry with the grammar: the transformer produces
    ``Cycle`` objects, but they are *transient*.  The assembler consumes them to
    resolve the §6.3 timing chain (baking drive edges and strobe placement into
    waveform transition times, stamping ``FailureEvent.strobe_time``) and then
    discards them — nothing downstream of assembly ever sees a ``Cycle``.
    """

    def header(self, children: list[Token]) -> LogHeader:
        raise NotImplementedError("Phase 1 M3 — see docs/ROADMAP.md")

    def pindef(self, children: list[Token]) -> PinDef:
        raise NotImplementedError("Phase 1 M3 — see docs/ROADMAP.md")

    def cycle(self, children: list[Token]) -> Cycle:
        raise NotImplementedError("Phase 1 M3 — see docs/ROADMAP.md")


def state_of(token: str) -> LogicState:
    """Map a raw ``STATE`` terminal to its ``LogicState`` member."""
    return LogicState(token)
