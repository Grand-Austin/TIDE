"""Named Agent profiles and direct compatible HTTP transport. No pi dependency."""

import json
import time
from pathlib import Path

import httpx


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
    def __init__(self, profile, provider, keys_path):
        key = json.loads(Path(keys_path).read_text())[profile["provider"]]
        if not key or key.startswith("REPLACE_"):
            raise ValueError(
                "Fill the selected provider key in the local key configuration"
            )
        self.profile = profile
        self.client = httpx.Client(
            base_url=provider["base_url"].rstrip("/") + "/",
            headers={"Authorization": "Bearer " + key},
            timeout=profile["timeout_s"],
        )

    def __call__(self, payload):
        for attempt in range(3):
            try:
                response = self.client.post("chat/completions", json=payload)
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt == 2:
                    raise ModelError("Provider request timed out or failed") from None
                time.sleep(2**attempt)
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
                time.sleep(min(30, max(0, delay)))
                continue
            if response.is_error:
                raise ModelError(f"Provider rejected request ({response.status_code})")
            try:
                return response.json()["choices"][0]["message"]
            except (ValueError, KeyError, IndexError):
                raise ModelError("Malformed provider response") from None
        raise ModelError("Provider failed")

    def close(self):
        self.client.close()
