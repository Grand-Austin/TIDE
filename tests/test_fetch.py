import json
from pathlib import Path
import numpy as np
import pytest
import xarray as xr
import copernicusmarine

from pipeline import fetch, downloads


def sample(path):
    xr.Dataset(
        {"VHM0": (("time", "latitude", "longitude"), np.ones((2, 2, 2)))},
        coords={
            "time": np.array(
                ["2026-09-09", "2026-09-09T01:00"], dtype="datetime64[ns]"
            ),
            "latitude": [39.0, 41.0],
            "longitude": [3.0, 4.0],
        },
    ).to_netcdf(path)


def request():
    return {
        "dataset_id": "test",
        "dataset_version": "v1",
        "variables": ["VHM0"],
        "start_datetime": "2026-09-09T00:00:00",
        "end_datetime": "2026-09-09T01:00:00",
    }


def test_cache_verification_and_corruption(tmp_path, monkeypatch):
    calls = []

    def subset(**kw):
        calls.append(kw)
        sample(Path(kw["output_directory"]) / kw["output_filename"])

    monkeypatch.setattr(copernicusmarine, "subset", subset)
    path = fetch.collect(tmp_path, "waves.nc", request())
    assert fetch.collect(tmp_path, "waves.nc", request()) == path and len(calls) == 1
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        fetch.collect(tmp_path, "waves.nc", request())


def test_invalid_download_is_not_published(tmp_path, monkeypatch):
    def subset(**kw):
        sample(Path(kw["output_directory"]) / kw["output_filename"])

    monkeypatch.setattr(copernicusmarine, "subset", subset)
    req = request()
    req["end_datetime"] = "2026-09-10T00:00:00"
    with pytest.raises(ValueError, match="coverage"):
        fetch.collect(tmp_path, "waves.nc", req)
    assert not (tmp_path / "waves.nc").exists()
    assert (tmp_path / "waves.nc.failure.json").exists()


def test_window_padding_temperature_days_and_partial_failure(tmp_path, monkeypatch):
    spec = {
        "start": "2026-09-09T15:15:00Z",
        "end": "2026-09-09T18:15:00Z",
        "bbox": [3.5, 3.9, 39.7, 40.1],
        "sources": {
            k: {"dataset_id": k, "dataset_version": "v", "variables": ["x"]}
            for k in ("waves", "temperature")
        },
    }
    file = tmp_path / "request.json"
    file.write_text(json.dumps(spec))
    calls = []

    def collect(out, name, req):
        calls.append(req)
        if name == "waves.nc":
            raise TimeoutError()
        path = out / name
        path.with_suffix(".json").write_text("{}")
        return path

    monkeypatch.setattr(fetch, "collect", collect)
    with pytest.raises(RuntimeError, match="waves"):
        fetch.fetch_window(file, tmp_path)
    assert calls[0]["start_datetime"] == "2026-09-09T15:00:00"
    assert calls[0]["end_datetime"] == "2026-09-09T19:00:00"
    assert calls[0]["minimum_longitude"] < 3.5
    assert calls[1]["start_datetime"] == "2026-09-09T00:00:00"
    assert (
        json.loads((tmp_path / "manifest.json").read_text())["waves"]["status"]
        == "failed"
    )
    assert len(fetch.DAY_DATASETS) == 3


def test_retry_is_bounded(monkeypatch):
    calls = []
    monkeypatch.setattr(downloads.time, "sleep", lambda _: None)

    def fail():
        calls.append(1)
        raise TimeoutError()

    with pytest.raises(TimeoutError):
        downloads.retry(fail)
    assert len(calls) == 3


def test_request_version_changes_do_not_overwrite(tmp_path, monkeypatch):
    def subset(**kw):
        sample(Path(kw["output_directory"]) / kw["output_filename"])

    monkeypatch.setattr(copernicusmarine, "subset", subset)
    path = fetch.collect(tmp_path, "waves.nc", request())
    original = path.read_bytes()
    req = request()
    req["dataset_version"] = "v2"
    with pytest.raises(ValueError):
        fetch.collect(tmp_path, "waves.nc", req)
    assert path.read_bytes() == original
