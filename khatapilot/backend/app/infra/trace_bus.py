"""In-process publish/subscribe for decision-trace events; the interface layer streams it as SSE."""

from __future__ import annotations

import itertools
import queue
import threading
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


class TraceBus:
    def __init__(self, history: int = 300) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue] = []
        self._recent: deque[dict[str, Any]] = deque(maxlen=history)
        self._ids = itertools.count(1)

    def publish(self, event: dict[str, Any]) -> None:
        """Every event gets a sequence id, so a client that replays history and then listens can drop repeats."""
        with self._lock:
            event = {"trace_id": next(self._ids), **event}
            self._recent.append(event)
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(event)
            except queue.Full:  # a slow browser tab misses events; it can reload the history
                pass

    @contextmanager
    def subscribe(self, maxsize: int = 1000) -> Iterator[queue.Queue]:
        subscriber: queue.Queue = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._subscribers.append(subscriber)
        try:
            yield subscriber
        finally:
            with self._lock:
                self._subscribers.remove(subscriber)

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._recent)[-limit:]

    def clear(self) -> None:
        """Forget the history (the demo shop was reloaded) and tell open panels to start afresh."""
        with self._lock:
            self._recent.clear()
        self.publish({"type": "reset"})
