"""Bounded post-submit queue that preserves critical records under pressure."""

from __future__ import annotations

from collections import deque
from threading import Condition
from time import monotonic


class RuntimeEventQueue:
    def __init__(self, maxsize: int = 256) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be positive")
        self.maxsize = maxsize
        self._items: deque[tuple[bool, object]] = deque()
        self._condition = Condition()

    def put_low(self, item: object) -> bool:
        with self._condition:
            if len(self._items) >= self.maxsize:
                return False
            self._items.append((False, item))
            self._condition.notify()
            return True

    def put_critical(self, item: object) -> bool:
        with self._condition:
            if len(self._items) >= self.maxsize:
                for index, (critical, _) in enumerate(self._items):
                    if not critical:
                        del self._items[index]
                        break
                else:
                    return False
            self._items.append((True, item))
            self._condition.notify()
            return True

    def get(self, timeout: float | None = None) -> object:
        deadline = None if timeout is None else monotonic() + timeout
        with self._condition:
            while not self._items:
                remaining = None if deadline is None else deadline - monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError("runtime queue is empty")
                self._condition.wait(remaining)
            return self._items.popleft()[1]

    def qsize(self) -> int:
        with self._condition:
            return len(self._items)
