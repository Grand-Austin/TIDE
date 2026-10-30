import json
import math
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
import zlib

import numpy as np
import pytest

from game.live import FIELDS, Game
from game.simulation.turn_rate import (
    heading_turn_rates_deg_s,
    sample_turn_rate_deg_s,
    turn_rate_deg_s,
    turn_rate_series,
    unwrap_headings_deg,
)
from pipeline.export_replay import export


def replay_sample(t, heading, *, roll=0.0, pitch=0.0, q=None, r=None):
    sample = {
        "time_s": t, "lon": 3.65, "lat": 40.05, "heading_deg": heading,
        "speed_over_ground_mps": 3.0, "speed_through_water_mps": 3.0,
        "roll_deg": roll, "pitch_deg": pitch, "heave_m": 0.0,
        "hs_m": 1.0, "tp_s": 8.0, "wave_from_deg": 0.0,
        "wind_east_mps": 0.0, "wind_north_mps": 0.0,
    }
    if q is not None:
        sample["angular_velocity_y_rad_s"] = q
    if r is not None:
        sample["angular_velocity_z_rad_s"] = r
    return sample


def write_session(path, samples, orders=()):
    rows = [{"time_s": 0, "kind": "session_started", "data": {"seed": 3}}]
    rows += [{"time_s": t, "kind": "player_order", "data": {"heading_deg": h, "speed_mps": 3.0}}
             for t, h in orders]
    cargo = {"lost_units": [], "lost_teu": 0, "grade": 0, "total_teu": 1200}
    rows += [{"time_s": sample["time_s"], "kind": "advance",
              "data": {"vessel": sample, "cargo": cargo, "events": []}}
             for sample in samples]
    (path / "events.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    (path / "result.json").write_text(json.dumps({
        "utc": "2026-09-09T15:00:00+00:00", "elapsed_s": 0.0, "outcome": None,
    }))
    return path


def test_turn_rate_uses_roll_pitch_and_actual_body_rates():
    roll, pitch = 25.0, -10.0
    q, r = math.radians(-2.0), math.radians(4.0)
    expected = math.degrees(
        (math.sin(math.radians(roll)) * q + math.cos(math.radians(roll)) * r)
        / math.cos(math.radians(pitch))
    )
    assert turn_rate_deg_s(roll, pitch, q, r) == pytest.approx(expected)
    assert turn_rate_deg_s(roll, pitch, q, r) != pytest.approx(math.degrees(r))
    assert turn_rate_deg_s(0.0, 0.0, 0.0, r) == pytest.approx(math.degrees(r))


def avoid_live_run_collision(monkeypatch, tmp_path):
    """Give each Game a distinct far-future run directory (live names use whole seconds)."""
    value = zlib.crc32(str(tmp_path).encode())
    stamp = datetime(3000 + value % 1000, 1 + (value >> 10) % 12, 1 + (value >> 14) % 28,
                     (value >> 18) % 24, (value >> 22) % 60, (value >> 26) % 60)
    monkeypatch.setattr("game.live.datetime", SimpleNamespace(now=lambda: stamp))


def test_historical_heading_fallback_unwraps_north_and_ignores_orders(tmp_path):
    samples = [replay_sample(1, 350), replay_sample(2, 355), replay_sample(3, 0)]
    replay = export(write_session(tmp_path, samples, orders=[(1, 90.0)]))

    assert replay["turn_rate"] == pytest.approx([5.0, 5.0, 5.0], abs=1e-3)
    assert len(replay["turn_rate"]) == len(replay["t"]) == len(replay["heading"])


def test_replay_prefers_sample_body_rates(tmp_path):
    roll, pitch = 20.0, 5.0
    q1, r1 = math.radians(1.0), math.radians(2.0)
    q2, r2 = math.radians(-1.0), math.radians(-3.0)
    samples = [
        replay_sample(1, 0.0, roll=roll, pitch=pitch, q=q1, r=r1),
        replay_sample(2, 1.0, roll=roll, pitch=pitch, q=q2, r=r2),
    ]
    replay = export(write_session(tmp_path, samples))

    expected = [turn_rate_deg_s(roll, pitch, q1, r1), turn_rate_deg_s(roll, pitch, q2, r2)]
    assert replay["turn_rate"] == pytest.approx(expected, abs=1e-3)
    assert replay["turn_rate"] != pytest.approx([
        turn_rate_deg_s(roll, pitch, 0.0, r1),
        turn_rate_deg_s(roll, pitch, 0.0, r2),
    ])


def test_helper_mixes_body_rates_with_heading_fallback():
    samples = [
        replay_sample(1, 350, q=0.0, r=math.radians(6.0)),
        replay_sample(2, 355),
        replay_sample(3, 0),
    ]
    assert unwrap_headings_deg([350, 355, 0]) == [350, 355, 360]
    assert heading_turn_rates_deg_s(samples) == pytest.approx([5.0, 5.0, 5.0])
    assert turn_rate_series(samples)[0] == pytest.approx(6.0)
    assert turn_rate_series(samples)[1:] == pytest.approx([5.0, 5.0])
    assert sample_turn_rate_deg_s(samples[1]) is None


def test_live_samples_append_turn_rate_aligned_with_fields(monkeypatch, tmp_path):
    avoid_live_run_collision(monkeypatch, tmp_path)
    game = Game("biscay-20251218-03")
    try:
        with game.lock:
            sample = dict(game.session.last_sample)
        row = game.samples[-1]
        index = FIELDS.index("turn_rate")
        assert index == len(FIELDS) - 1
        assert len(row) == len(FIELDS)
        assert row[index] == pytest.approx(round(sample_turn_rate_deg_s(sample), 6))
    finally:
        game.stopped = True


def test_preview_uses_session_clock_and_does_not_mutate_the_state(monkeypatch, tmp_path):
    avoid_live_run_collision(monkeypatch, tmp_path)
    game = Game("biscay-20251218-03")
    try:
        with game.lock:
            session = game.session
            session.pause(manual=True)
            session.authorize_resume()
            session.advance_segment(2.0)
            session.pause(manual=True)
            start = session.last_sample["time_s"]
            heading = session.last_sample["heading_deg"]
            speed_knots = session.last_sample["speed_through_water_mps"] * 3600 / 1852
            before_state = deepcopy(session.state)
            before_sample = deepcopy(session.last_sample)

        assert start > 0
        preview = game.preview(heading, speed_knots, horizon_s=20.0)

        assert preview["from_s"] == pytest.approx(start)
        assert preview["track"][-1][2] == pytest.approx(start + 20.0)
        assert preview["track"][0][2] > start  # absolute session time, not tick * a coarser dt

        with game.lock:
            assert session.last_sample == before_sample
            np.testing.assert_array_equal(session.state.x, before_state.x)
            assert session.state.tick == before_state.tick
            assert session.state.control == before_state.control
            assert session.state.exposure == before_state.exposure
            assert session.state.wave.time_s == before_state.wave.time_s
            np.testing.assert_array_equal(session.state.wave.phase, before_state.wave.phase)
    finally:
        game.stopped = True
