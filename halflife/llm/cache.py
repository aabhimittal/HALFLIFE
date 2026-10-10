"""Response cache for live runs.

The cache exists so a crashed or interrupted live run can be resumed without
paying twice. It must not change the experiment, so keys are scoped: each
trial runs inside ``cache_scope("<seed>:<trial>")``, and a key is
(scope, request, how many times this exact request has occurred in this
scope). A resumed trial replays exactly; two different trials that happen to
send identical prompts still get independent model calls.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_scope: contextvars.ContextVar[tuple[str, dict] | None] = contextvars.ContextVar("halflife_cache_scope", default=None)


@contextmanager
def cache_scope(name: str) -> Iterator[None]:
    token = _scope.set((name, {}))
    try:
        yield
    finally:
        _scope.reset(token)


def request_key(request: dict) -> str:
    """Stable key for one request in the current scope (advances the occurrence counter)."""
    body = json.dumps(request, sort_keys=True, separators=(",", ":"))
    scope = _scope.get()
    name, counts = scope if scope else ("global", None)
    digest = hashlib.sha256(body.encode()).hexdigest()
    n = 0
    if counts is not None:
        n = counts.get(digest, 0)
        counts[digest] = n + 1
    return hashlib.sha256(f"{name}|{digest}|{n}".encode()).hexdigest()


class ResponseCache:
    """SQLite-backed, thread-safe store of response texts."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, text TEXT NOT NULL)")
        self._db.commit()
        self.hits = self.misses = 0

    def get(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT text FROM responses WHERE key = ?", (key,)).fetchone()
            if row is None:
                self.misses += 1
                return None
            self.hits += 1
            return row[0]

    def put(self, key: str, text: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO responses (key, text) VALUES (?, ?)", (key, text))
            self._db.commit()

    def __len__(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM responses").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._db.close()
