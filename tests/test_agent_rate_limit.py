"""Offline acceptance checks for the global Agent model attempt budget."""

import json
import threading
from pathlib import Path

import httpx
import pytest

import agent.transport as transport_module
from agent.rate_limit import (
    DEFAULT_DB_PATH,
    LIMIT,
    WINDOW_S,
    RateLimiter,
    RateLimitError,
)
from agent.transport import ChatClient, DEFAULT_KEYS_PATH, ModelError, load_profile
from game.director import models as director_models


PROVIDER = {"base_url": "https://example.invalid/v1"}
PROFILE = {
    "provider": "nvidia_nim",
    "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
    "temperature": 0.0,
    "top_p": 1.0,
    "max_tokens": 64,
    "timeout_s": 5,
}
MESSAGE = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []
        self._lock = threading.Lock()

    def time(self):
        with self._lock:
            return self.now

    def sleep(self, seconds):
        assert seconds > 0
        with self._lock:
            self.sleeps.append(seconds)
            self.now += seconds


class RecordingLimiter(RateLimiter):
    def __init__(self, *args, times, **kwargs):
        self.times = times
        super().__init__(*args, **kwargs)

    def wait(self):
        super().wait()
        self.times.append(self._clock())


def key_file(tmp_path, *providers):
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({name: "unit-test-key" for name in providers}))
    return path


def limiter_for(tmp_path, clock, *, limit=LIMIT, window_s=WINDOW_S):
    return RateLimiter(
        tmp_path / "agent-rate-limit.sqlite",
        limit=limit,
        window_s=window_s,
        clock=clock.time,
        sleeper=clock.sleep,
    )


def client(tmp_path, profile=PROFILE, provider=PROVIDER, *, limiter, handler, sleep):
    return ChatClient(
        profile,
        provider,
        key_file(tmp_path, profile["provider"]),
        rate_limiter=limiter,
        http_transport=httpx.MockTransport(handler),
        sleeper=sleep,
    )


def assert_rolling_cap(times, limit, window):
    ordered = sorted(times)
    for start in ordered:
        count = sum(1 for value in ordered if start <= value < start + window)
        assert count <= limit


def test_fixed_policy_and_project_paths():
    root = Path(__file__).resolve().parents[1]
    assert LIMIT == 30
    assert WINDOW_S == 60.0
    assert DEFAULT_DB_PATH == root / "data" / "runs" / "agent-rate-limit.sqlite"
    assert DEFAULT_DB_PATH.is_absolute()
    assert DEFAULT_KEYS_PATH == Path("config/api_keys.local.json")


def test_thirty_first_attempt_waits_for_the_rolling_window(tmp_path):
    clock = FakeClock()
    limiter = limiter_for(tmp_path, clock)
    times = []
    for _ in range(30):
        limiter.wait()
        times.append(clock.time())

    assert times == [0.0] * 30
    limiter.wait()
    times.append(clock.time())

    assert times[-1] == pytest.approx(60.0)
    assert clock.sleeps and max(clock.sleeps) <= 0.5
    assert_rolling_cap(times, LIMIT, WINDOW_S)


def test_window_is_rolling_not_a_fixed_minute_bucket(tmp_path):
    clock = FakeClock()
    limiter = limiter_for(tmp_path, clock, limit=2)
    times = []

    limiter.wait()
    times.append(clock.time())
    clock.now = 59.0
    limiter.wait()
    times.append(clock.time())
    limiter.wait()
    times.append(clock.time())

    assert times == [0.0, pytest.approx(59.0), pytest.approx(60.0)]
    assert clock.now == pytest.approx(60.0)
    assert_rolling_cap(times, 2, WINDOW_S)


def test_retry_attempts_consume_the_shared_budget(tmp_path):
    clock = FakeClock()
    limiter = limiter_for(tmp_path, clock, limit=2)
    statuses = iter([503, 502, 200])
    attempts = []

    def handler(request):
        attempts.append(clock.time())
        return httpx.Response(next(statuses), json=MESSAGE)

    chat = client(
        tmp_path, limiter=limiter, handler=handler, sleep=clock.sleep
    )
    try:
        message = chat({"messages": []})
    finally:
        chat.close()

    assert message["content"] == "ok"
    assert len(attempts) == 3
    assert attempts[0] == pytest.approx(0.0)
    assert attempts[1] == pytest.approx(1.0)
    assert attempts[2] == pytest.approx(60.0)
    assert_rolling_cap(attempts, 2, WINDOW_S)


def test_three_failed_attempts_still_stop_with_a_sanitized_error(tmp_path):
    clock = FakeClock()
    limiter = limiter_for(tmp_path, clock)
    attempts = []

    def handler(_):
        attempts.append(clock.time())
        return httpx.Response(503)

    chat = client(
        tmp_path, limiter=limiter, handler=handler, sleep=clock.sleep
    )
    try:
        with pytest.raises(ModelError, match="temporarily unavailable"):
            chat({"messages": []})
    finally:
        chat.close()
    assert len(attempts) == 3


def test_clients_threads_and_profiles_share_one_atomic_database(tmp_path):
    times = []
    barrier = threading.Barrier(2)
    errors = []
    calls = []

    def handler(request):
        calls.append(request.headers["authorization"])
        return httpx.Response(200, json=MESSAGE)

    first_limiter = RecordingLimiter(
        tmp_path / "shared.sqlite", limit=3, window_s=0.2, times=times
    )
    second_limiter = RecordingLimiter(
        tmp_path / "shared.sqlite", limit=3, window_s=0.2, times=times
    )
    first_profile = dict(PROFILE, model="first-model", quota_db="/tmp/ignored")
    second_profile = dict(PROFILE, model="second-model", rate_limit=999)
    clients = [
        client(
            tmp_path,
            first_profile,
            {"base_url": "https://first.invalid/v1"},
            limiter=first_limiter,
            handler=handler,
            sleep=lambda _: None,
        ),
        client(
            tmp_path,
            second_profile,
            {"base_url": "https://second.invalid/v1"},
            limiter=second_limiter,
            handler=handler,
            sleep=lambda _: None,
        ),
    ]
    clients[0].client.headers["Authorization"] = "Bearer first-key"
    clients[1].client.headers["Authorization"] = "Bearer second-key"

    def worker(chat):
        try:
            barrier.wait(timeout=5)
            for _ in range(3):
                chat({"messages": []})
        except Exception as error:  # noqa: BLE001 - the assertion reports it.
            errors.append(error)

    threads = [threading.Thread(target=worker, args=(chat,)) for chat in clients]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    for chat in clients:
        chat.close()

    assert not errors
    assert len(times) == 6
    assert len(calls) == 6
    assert "Bearer first-key" in calls and "Bearer second-key" in calls
    assert_rolling_cap(times, 3, 0.2)


def test_profile_fields_cannot_raise_the_cap_or_select_another_database(tmp_path):
    clock = FakeClock()
    first_db = tmp_path / "shared.sqlite"
    ignored_db = tmp_path / "chosen-by-profile.sqlite"
    times = []

    def handler(_):
        times.append(clock.time())
        return httpx.Response(200, json=MESSAGE)

    prov_a = {"base_url": "https://a.invalid/v1"}
    prov_b = {"base_url": "https://b.invalid/v1"}
    profile_a = dict(
        PROFILE,
        provider="provider_a",
        rate_limit=999,
        rate_limit_db=str(ignored_db),
    )
    profile_b = dict(
        PROFILE,
        provider="provider_b",
        quota_db=str(ignored_db),
        attempts_per_minute=1000,
    )

    def build(profile, provider):
        return ChatClient(
            profile,
            provider,
            key_file(tmp_path, "provider_a", "provider_b"),
            rate_limiter=RateLimiter(
                first_db,
                limit=1,
                clock=clock.time,
                sleeper=clock.sleep,
            ),
            http_transport=httpx.MockTransport(handler),
            sleeper=clock.sleep,
        )

    first = build(profile_a, prov_a)
    second = build(profile_b, prov_b)
    try:
        first({"messages": []})
        second({"messages": []})
    finally:
        first.close()
        second.close()

    assert times == [pytest.approx(0.0), pytest.approx(60.0)]
    assert not ignored_db.exists()


def test_no_reservation_until_an_http_attempt_is_made(tmp_path):
    clock = FakeClock()
    db = tmp_path / "unused.sqlite"
    limiter = RateLimiter(db, clock=clock.time, sleeper=clock.sleep)
    chat = client(
        tmp_path,
        limiter=limiter,
        handler=lambda _: httpx.Response(200, json=MESSAGE),
        sleep=clock.sleep,
    )

    chat.close()
    chat.close()
    assert not db.exists()

    chat = client(
        tmp_path,
        limiter=limiter,
        handler=lambda _: httpx.Response(200, json=MESSAGE),
        sleep=clock.sleep,
    )
    with chat:
        chat({"messages": []})
    assert db.exists()


def test_limiter_failure_fails_closed_before_http(tmp_path):
    clock = FakeClock()
    bad_database = tmp_path / "not-a-database"
    bad_database.mkdir()
    limiter = RateLimiter(
        bad_database, clock=clock.time, sleeper=clock.sleep
    )
    attempts = []

    def handler(_):
        attempts.append(True)
        return httpx.Response(200, json=MESSAGE)

    chat = client(
        tmp_path, limiter=limiter, handler=handler, sleep=clock.sleep
    )
    try:
        with pytest.raises(ModelError, match="rate limit"):
            chat({"messages": []})
    finally:
        chat.close()

    assert attempts == []
    with pytest.raises(RateLimitError):
        limiter.wait()


def test_default_key_path_and_shared_nim_builder_profile(tmp_path, monkeypatch):
    profile, provider = load_profile("models.json", "builder")
    roles = json.loads(Path("models.json").read_text())["roles"]

    assert roles["builder"] == "builder_nim"
    assert profile["provider"] == "nvidia_nim"
    assert profile["model"] == "nvidia/nemotron-3.5-lightning-30b-a3b"
    assert provider["base_url"] == "https://integrate.api.nvidia.com/v1"

    keys = tmp_path / "default-keys.json"
    keys.write_text(json.dumps({"nvidia_nim": "unit-test-default-key"}))
    monkeypatch.setattr(transport_module, "DEFAULT_KEYS_PATH", keys)
    clock = FakeClock()
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=MESSAGE)

    chat = ChatClient(
        profile,
        provider,
        rate_limiter=limiter_for(tmp_path, clock),
        http_transport=httpx.MockTransport(handler),
        sleeper=clock.sleep,
    )
    try:
        chat({"messages": []})
    finally:
        chat.close()

    assert seen[0].headers["authorization"] == "Bearer unit-test-default-key"
    assert str(seen[0].url) == "https://integrate.api.nvidia.com/v1/chat/completions"


def test_compatibility_module_reexports_the_same_transport_symbols():
    assert director_models.ModelError is ModelError
    assert director_models.ChatClient is ChatClient
    assert director_models.load_profile is load_profile


def test_internal_limiter_cannot_be_configured_above_the_global_cap(tmp_path):
    with pytest.raises(ValueError, match="between 1 and 30"):
        RateLimiter(tmp_path / "rate.sqlite", limit=31)
