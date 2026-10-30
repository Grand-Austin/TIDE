"""Project-wide limiter for outgoing Agent model HTTP attempts.

Policy
------
- Every outgoing model HTTP attempt consumes one slot. Retries consume
  additional slots.
- At most ``LIMIT`` attempts may start in any rolling ``WINDOW_S`` seconds.
- The budget is shared by every Agent role, profile, provider, client, thread
  and process in this TIDE project. The shared store is the fixed project file
  ``data/runs/agent-rate-limit.sqlite``.
- A caller reserves a slot immediately before it posts the HTTP request. No
  SQLite transaction or lock is held across a sleep or a network request.
- When the rolling window is full, the caller waits in short bounded chunks and
  then rechecks the shared store. This avoids a burst at a fixed-minute
  boundary and keeps shutdown responsive.
- Attempt timestamps use wall-clock seconds. After a reboot, old rows simply
  remain part of the rolling window until they expire.
- If the shared state cannot be read or written, ``wait`` raises
  ``RateLimitError``. The transport must then fail closed without an HTTP
  request.

The database stores only attempt timestamps. It stores no credentials, key
hashes, provider names, prompts, responses or role data.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Callable

LIMIT = 30
WINDOW_S = 60.0
MAX_SLEEP_S = 0.5

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "runs" / "agent-rate-limit.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL
)
"""


class RateLimitError(RuntimeError):
    """The shared limiter state is unavailable, so no request may be sent."""


class RateLimiter:
    """A transaction-safe, process-shared rolling-window attempt limiter.

    ``limit``, ``window_s``, ``clock`` and ``sleeper`` are internal test seams.
    Production callers use the module defaults. Model profiles, tool arguments
    and command-line options cannot change them.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        limit: int = LIMIT,
        window_s: float = WINDOW_S,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
        max_sleep_s: float = MAX_SLEEP_S,
    ):
        if not 1 <= int(limit) <= LIMIT:
            raise ValueError(f"Attempt limit must be between 1 and {LIMIT}")
        if float(window_s) <= 0:
            raise ValueError("Attempt window must be positive")
        if float(max_sleep_s) <= 0:
            raise ValueError("Sleep interval must be positive")
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        self.limit = int(limit)
        self.window_s = float(window_s)
        self.max_sleep_s = float(max_sleep_s)
        self._clock = clock or time.time
        self._sleeper = sleeper or time.sleep

    def wait(self) -> None:
        """Reserve one attempt slot, waiting until the rolling cap permits it.

        The reservation and all reads and writes are one short SQLite
        transaction. The wait itself holds no database lock.
        """
        while True:
            try:
                delay = self._reserve()
            except (sqlite3.Error, OSError):
                raise RateLimitError("Shared model rate limit state is unavailable") from None
            if delay <= 0:
                return
            self._sleeper(min(delay, self.max_sleep_s))

    # An explicit alias for callers that describe the operation as acquisition.
    acquire = wait

    def _reserve(self) -> float:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            str(self.db_path), timeout=5.0, isolation_level=None
        )
        try:
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("BEGIN IMMEDIATE")
            try:
                now = float(self._clock())
                connection.execute(_SCHEMA)
                cutoff = now - self.window_s
                connection.execute("DELETE FROM attempts WHERE at <= ?", (cutoff,))
                count, oldest = connection.execute(
                    "SELECT COUNT(*), MIN(at) FROM attempts"
                ).fetchone()
                if count < self.limit:
                    connection.execute(
                        "INSERT INTO attempts (at) VALUES (?)", (now,)
                    )
                    connection.execute("COMMIT")
                    return 0.0
                connection.execute("COMMIT")
                return max(0.0, float(oldest) + self.window_s - now)
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        finally:
            connection.close()


__all__ = [
    "DEFAULT_DB_PATH",
    "LIMIT",
    "MAX_SLEEP_S",
    "PROJECT_ROOT",
    "RateLimitError",
    "RateLimiter",
    "WINDOW_S",
]
