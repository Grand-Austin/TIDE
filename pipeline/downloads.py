"""Download integrity and bounded retries for source collection."""

import hashlib
import json
from pathlib import Path
import time
import urllib.error


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, default=str, allow_nan=False) + "\n")
    temp.replace(path)


def retry(call):
    for attempt in range(3):
        try:
            return call()
        except Exception as error:
            code = getattr(
                error,
                "code",
                getattr(getattr(error, "response", None), "status_code", None),
            )
            transient = (
                code == 429
                or (isinstance(code, int) and 500 <= code <= 599)
                or isinstance(
                    error, (TimeoutError, ConnectionError, urllib.error.URLError)
                )
                or type(error).__name__
                in ("Timeout", "ConnectTimeout", "ReadTimeout", "ConnectionError")
            )
            if isinstance(error, urllib.error.HTTPError) and code not in (
                429,
                500,
                502,
                503,
                504,
            ):
                transient = False
            if not transient or attempt == 2:
                raise
            time.sleep(2**attempt)
