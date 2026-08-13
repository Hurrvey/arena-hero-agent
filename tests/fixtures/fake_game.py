from __future__ import annotations

from collections.abc import Iterable


class FakeGameClient:
    def __init__(self, events: Iterable[object]) -> None:
        self._events = tuple(events)
        self.closed = False

    def events(self):
        yield from self._events

    def close(self) -> None:
        self.closed = True
