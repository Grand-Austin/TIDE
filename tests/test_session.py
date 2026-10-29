from dataclasses import asdict
from types import SimpleNamespace
import json
import threading

import numpy as np
import pytest

from test_runtime import vessel, ConstantEnvironment, START
from game.session import Session
from game.simulation.runtime import Simulation


class PlainRules:
    budget_s = 100

    def evaluate(self, sample, consumed):
        return None, []

    def visible_reports(self, elapsed, lon, lat):
        return []


def make_session(vessel, path, restore=False):
    sim = Simulation(vessel, ConstantEnvironment())
    definition = {
        "seed": 1,
        "initial": {"heading_deg": 0, "speed_mps": 1},
        "mission": {"budget_s": 100},
        "area_bbox": [3, 4, 39, 41],
    }
    scenario = SimpleNamespace(
        definition=definition,
        initial_state=lambda s: s.initial_state(START, 3.5, 40, speed_mps=1),
    )
    return Session(scenario, sim, {}, path, restore=restore, rules=PlainRules())


def test_save_resume_is_exact_and_paused(vessel, tmp_path):
    a = make_session(vessel, tmp_path / "a")
    a.authorize_resume()
    a.advance_segment(0.35)
    b = make_session(vessel, tmp_path / "a", restore=True)
    assert b.pause_signal.is_set()
    b.authorize_resume()
    a.advance_segment(0.65)
    b.advance_segment(0.65)
    np.testing.assert_array_equal(a.state.x, b.state.x)
    np.testing.assert_array_equal(a.state.wave.phase, b.state.wave.phase)
    assert a.state.exposure == b.state.exposure
    assert a.cargo == b.cargo


def test_orders_pause_and_do_not_teleport(vessel, tmp_path):
    s = make_session(vessel, tmp_path)
    before = s.state.x.copy()
    s.set_order(90, 3)
    assert s.pause_signal.is_set()
    assert s.order.heading_deg == 90
    np.testing.assert_array_equal(s.state.x, before)
    s.advance_segment()
    np.testing.assert_array_equal(s.state.x, before)
    with pytest.raises(ValueError):
        s.set_order(float("nan"), 1)
    with pytest.raises(ValueError):
        s.set_order(0, -1)


def test_authority_receipts_and_hidden_data(vessel, tmp_path):
    s = make_session(vessel, tmp_path)
    with pytest.raises(ValueError):
        s.tool("advance", {}, "1", s.revision)
    with pytest.raises(ValueError):
        s.tool("advance", {"heading": 90}, "1", s.revision)
    s.receipts["old"] = {"paused": True, "revision": 0}
    assert s.tool("advance", {}, "old", 999) == s.receipts["old"]
    view = json.dumps(s.visible())
    for hidden in ("thresholds", "vulnerabil", "polygons", "wave_force"):
        assert hidden not in view
    s.authorize_resume()
    s.set_order(80, 2)
    with pytest.raises(ValueError):
        s.tool("advance", {}, "2", 0)


def test_operating_area_stop_rolls_back(vessel, tmp_path):
    s = make_session(vessel, tmp_path)
    s.sim.environment.bbox = [3, 4, 39, 40.00000001]
    s.authorize_resume()
    s.advance_segment(1)
    assert s.reason == "domain_boundary"
    assert s.pause_signal.is_set()
    assert s.state.tick == 0
    assert s.state.exposure.elapsed_s == 0


def test_time_rate_does_not_change_physical_consequences(vessel, tmp_path):
    a = make_session(vessel, tmp_path / "slow")
    b = make_session(vessel, tmp_path / "fast")
    a.rate = 1
    b.rate = 60
    for s in (a, b):
        s.set_order(20, 2)
        s.authorize_resume()
        s.advance_segment(1)
    np.testing.assert_array_equal(a.state.x, b.state.x)
    assert a.cargo == b.cargo


def test_grade_change_stops_on_the_first_accepted_step(vessel, tmp_path):
    s = make_session(vessel, tmp_path)
    s.cargo_model.config.update(threshold_g=0.0001, damage_time_s=0.0001)
    s.state.x[9] = 0.1
    s.authorize_resume()
    result = s.advance_segment(1)
    assert s.state.tick == 1
    assert result["paused"]
    assert result["stopping_reason"] == "cargo_grade_increased"
    lost = s.cargo["lost_units"]
    s.authorize_resume()
    s.advance_segment(0.1)
    assert s.cargo["lost_units"] == lost


def test_recovery_discards_uncommitted_tail_and_does_not_repeat_advance(
    vessel, tmp_path
):
    s = make_session(vessel, tmp_path)
    s.receipts["pending"] = {"pending": True, "revision": s.revision}
    s.save()
    s.record("uncommitted_tail", {"ignored": True})
    restored = make_session(vessel, tmp_path, restore=True)
    assert "uncommitted_tail" not in restored.log_path.read_text()
    assert restored.tool("advance", {}, "pending", restored.revision)["interrupted"]
    assert restored.state.tick == 0


def test_mismatched_snapshot_is_rejected(vessel, tmp_path):
    s = make_session(vessel, tmp_path)
    data = json.loads(s.snapshot_path.read_text())
    data["fingerprint"] = "wrong"
    s.snapshot_path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="does not match"):
        make_session(vessel, tmp_path, restore=True)


def test_terminal_event_is_not_duplicated_by_motion_stop(vessel, tmp_path):
    class EndRules(PlainRules):
        def evaluate(self, sample, consumed):
            return "timeout", [
                {
                    "time_s": sample.time_s,
                    "kind": "timeout",
                    "detail": "Budget exhausted",
                }
            ]

    s = make_session(vessel, tmp_path)
    s.rules = EndRules()
    s.authorize_resume()
    value = s.advance_segment(1)
    assert s.state.tick == 1 and s.outcome == "timeout"
    assert len([e for e in value["events"] if e["kind"] == "timeout"]) == 1
