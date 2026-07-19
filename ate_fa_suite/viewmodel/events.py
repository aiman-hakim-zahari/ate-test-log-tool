"""Minimal observer / pub-sub (Phase 3 milestone 1).

Views subscribe and re-render; they never hold state or business logic.  This is
the whole of the "MVVM-lite" machinery — about thirty lines, deliberately not a
framework.
"""

from __future__ import annotations

from typing import Callable, Generic, TypeVar

T = TypeVar("T")

Listener = Callable[[T], None]


class Event(Generic[T]):
    """A typed signal with a payload.  Subscribe, unsubscribe, emit."""

    def __init__(self) -> None:
        self._listeners: list[Listener[T]] = []

    def subscribe(self, listener: Listener[T]) -> Callable[[], None]:
        """Register ``listener``; returns a callable that unsubscribes it."""
        raise NotImplementedError("Phase 3 M1 — see docs/ROADMAP.md")

    def unsubscribe(self, listener: Listener[T]) -> None:
        raise NotImplementedError("Phase 3 M1 — see docs/ROADMAP.md")

    def emit(self, payload: T) -> None:
        """Notify every listener, in subscription order."""
        raise NotImplementedError("Phase 3 M1 — see docs/ROADMAP.md")
