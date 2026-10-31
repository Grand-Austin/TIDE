"""Quota timestamps must describe admission, including time spent on the lock."""

import sqlite3
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor


def test_attempt_time_is_taken_after_the_database_lock(tmp_path, monkeypatch):
    from agent.rate_limit import RateLimiter

    now = [100.0]
    connect = sqlite3.connect

    class Connection:
        def __init__(self, *args, **kwargs):
            self.connection = connect(*args, **kwargs)

        def execute(self, sql, *args):
            result = self.connection.execute(sql, *args)
            if sql == "BEGIN IMMEDIATE":
                now[0] = 105.0
            return result

        def close(self):
            self.connection.close()

    monkeypatch.setattr(sqlite3, "connect", Connection)
    path = tmp_path / "quota.sqlite"
    limiter = RateLimiter(path, clock=lambda: now[0])
    limiter.wait()
    connection = connect(path)
    try:
        assert connection.execute("SELECT at FROM attempts").fetchone()[0] == 105.0
    finally:
        connection.close()


def test_separate_processes_share_thirty_admissions(tmp_path):
    path = tmp_path / "shared.sqlite"
    program = (
        "import json,sys; from agent.rate_limit import RateLimiter; "
        "limiter=RateLimiter(sys.argv[1],clock=lambda:1000.0); "
        "print(json.dumps([limiter._reserve() for _ in range(10)]))"
    )

    def run(_):
        result = subprocess.run(
            [sys.executable, "-c", program, str(path)],
            capture_output=True, text=True, check=True, timeout=20,
        )
        return json.loads(result.stdout)

    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(run, range(4)))
    assert sum(delay == 0 for batch in results for delay in batch) == 30
