import io
import json
from datetime import datetime, timedelta, timezone

from pipeline import export_scene
from pipeline.storm_scenario import write_weather


def response():
    return io.BytesIO(json.dumps({"hourly": {
        "time": ["2026-10-22T21:00", "2026-10-22T22:00", "2026-10-22T23:00"],
        "cloud_cover": [100, 40, 80], "precipitation": [1.4, 0.2, 0.0],
    }}).encode())


def test_scenario_weather_keeps_actual_hour_and_interpolation(tmp_path, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: response())
    start = datetime(2026, 10, 22, 21, 30, tzinfo=timezone.utc)
    path = tmp_path / "weather.json"
    write_weather(path, 48.5, -10.75, start, start + timedelta(hours=1))
    rows = json.loads(path.read_text())["hourly"]
    assert rows == [
        {"t_s": 0, "cloud": 70, "precip": 0.2},
        {"t_s": 3600, "cloud": 60, "precip": 0.0},
    ]


def test_scene_weather_keeps_actual_hour_and_interpolation(tmp_path, monkeypatch):
    monkeypatch.setattr(export_scene, "urlopen", lambda *a, **k: response())
    path = tmp_path / "scene.json"
    path.write_text(json.dumps({
        "start": "2026-10-22T21:00:00Z", "start_lon_lat": [-10.75, 48.5],
        "fields": ["t_s"], "samples": [[0], [1800], [3600]],
    }))
    export_scene.add_weather(path)
    scene = json.loads(path.read_text())
    assert scene["samples"] == [[0, 100, 1.4], [1800, 70, 0.2], [3600, 40, 0.2]]
