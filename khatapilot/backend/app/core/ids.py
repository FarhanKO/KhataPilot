"""Identifiers. Random by default; switch to a counter for deterministic demo runs and tests."""

from __future__ import annotations

import itertools
import threading
import uuid

_lock = threading.Lock()
_counter: itertools.count[int] | None = None


def new_id(prefix: str) -> str:
    with _lock:
        if _counter is not None:
            return f"{prefix}_{next(_counter):06d}"
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def use_deterministic_ids(start: int = 1) -> None:
    global _counter
    with _lock:
        _counter = itertools.count(start)


def use_random_ids() -> None:
    global _counter
    with _lock:
        _counter = None
