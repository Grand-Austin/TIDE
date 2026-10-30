"""Named Agent profiles and the shared, rate-limited model HTTP transport.

All active TIDE Agent roles use this transport. It has no pi dependency.

Time and retry semantics
------------------------
- The client makes at most three HTTP attempts per call.
- Before every attempt, including each retry, the client reserves one slot from
  the shared ``agent.rate_limit`` limiter. The limiter enforces at most 30
  attempts in any rolling 60 seconds across the whole project.
- A network timeout or network error retries after 1 s and then 2 s.
- HTTP 429 and 5xx responses retry after the bounded ``Retry-After`` value, or
  after 1 s and then 2 s when that header is absent or invalid. A retry delay is
  clamped to 0-30 s. The shared limiter can then add a longer wait.
- Other HTTP errors and malformed responses fail immediately with a sanitized
  ``ModelError``. Credentials never enter an error message or a payload.
- If the shared limiter state is unavailable, the client fails closed:
  ``ModelError`` is raised and no HTTP request is made.

The default credential file is the ignored project file
``config/api_keys.local.json``. The default provider configuration comes from
``models.json``. Neither the profile nor any Agent argument can change the
global attempt cap or select a different limiter database.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

import httpx

from agent.rate_limit import RateLimiter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KEYS_PATH = Path("config/api_keys.local.json")


class ModelError(RuntimeError):
    pass


def load_profile(path, role="director"):
    config = json.loads(Path(path).read_text())
    if config.get("version") != 1:
        raise ValueError("Unsupported model configuration version")
    profile = dict(config["profiles"][config["roles"][role]])
    provider = config["providers"][profile["provider"]]
    if not provider["base_url"].startswith("https://"):
        raise ValueError("Provider URL must use HTTPS")
    if (
        not isinstance(profile["tool_round_limit"], int)
        or not 1 <= profile["tool_round_limit"] <= 10
    ):
        raise ValueError("Tool round limit must be between 1 and 10")
    if profile["timeout_s"] <= 0 or profile["max_tokens"] <= 0:
        raise ValueError("Model timeout and output budget must be positive")
    if profile["model"].startswith("REPLACE_"):
        raise ValueError("Select a verified model ID for this provider")
    return profile, provider


class ChatClient:
    """A direct OpenAI-compatible client with the shared project attempt budget.

    ``rate_limiter``, ``http_transport`` and ``sleeper`` are internal test seams.
    They are not read from a model profile, a tool, or the command line.
    """

    def __init__(
        self,
        profile,
        provider,
        keys_path=None,
        *,
        rate_limiter: RateLimiter | None = None,
        http_transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] | None = None,
    ):
        if keys_path is None:
            keys_path = DEFAULT_KEYS_PATH
        key = json.loads(Path(keys_path).read_text())[profile["provider"]]
        if not key or key.startswith("REPLACE_"):
            raise ValueError(
                "Fill the selected provider key in the local key configuration"
            )
        self.profile = profile
        self.rate_limiter = rate_limiter or RateLimiter()
        self._sleep = sleeper or time.sleep
        self.client = httpx.Client(
            base_url=provider["base_url"].rstrip("/") + "/",
            headers={"Authorization": "Bearer " + key},
            timeout=profile["timeout_s"],
            transport=http_transport,
        )
        self._closed = False

    def __call__(self, payload):
        for attempt in range(3):
            try:
                self.rate_limiter.wait()
            except Exception:
                raise ModelError("Model rate limit state is unavailable") from None
            try:
                response = self.client.post("chat/completions", json=payload)
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt == 2:
                    raise ModelError("Provider request timed out or failed") from None
                self._sleep(2**attempt)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 2:
                    raise ModelError(
                        f"Provider temporarily unavailable ({response.status_code})"
                    )
                try:
                    delay = float(response.headers.get("retry-after", 2**attempt))
                except ValueError:
                    delay = 2**attempt
                self._sleep(min(30, max(0, delay)))
                continue
            if response.is_error:
                raise ModelError(f"Provider rejected request ({response.status_code})")
            try:
                return response.json()["choices"][0]["message"]
            except (ValueError, KeyError, IndexError):
                raise ModelError("Malformed provider response") from None
        raise ModelError("Provider failed")

    def close(self):
        if not self._closed:
            self.client.close()
            self._closed = True

    def __enter__(self) -> "ChatClient":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


__all__ = [
    "ChatClient",
    "DEFAULT_KEYS_PATH",
    "ModelError",
    "PROJECT_ROOT",
    "load_profile",
]
