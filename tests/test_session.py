from dataclasses import asdict
from types import SimpleNamespace
import json
import math
import threading

import numpy as np
import pytest

from test_runtime import vessel, ConstantEnvironment, START
from game.cargo import CargoModel
from game.session import Session
from game.simulation.runtime import Simulation


class PlainRules:
    budget_s = 100

    def evaluate(self, sample, consumed):
        return None, []

    def visible_reports(self, elapsed, lon, lat):
        return []


def make_session(vessel, path, restore=False, cargo_model=None, cargo_config=None):
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
    return Session(
        scenario,
        sim,
        {"cargo": cargo_config} if cargo_config else {},
        path,
        restore=restore,
        rules=PlainRules(),
        cargo_model=cargo_model,
    )


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
    assert a.ship == b.ship


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
    before_ship = dict(s.ship)
    s.sim.environment.bbox = [3, 4, 39, 40.00000001]
    s.authorize_resume()
    s.advance_segment(1)
    assert s.reason == "domain_boundary"
    assert s.pause_signal.is_set()
    assert s.state.tick == 0
    assert s.state.exposure.elapsed_s == 0
    assert s.ship == before_ship
    assert not s.ship["lost"] and not s.cargo["lost_units"]


def test_ship_loss_overrides_escape_and_cargo_pause_and_cannot_resume(vessel, tmp_path):
    class EscapeRules(PlainRules):
        def evaluate(self, sample, consumed):
            return "escaped", [{"kind": "escaped", "time_s": sample.time_s}]

    s = make_session(vessel, tmp_path)
    s.rules = EscapeRules()
    s.state.x[9] = np.radians(70)
    s.authorize_resume()
    result = s.advance_segment(1)
    assert s.state.tick == 1
    assert result["outcome"] == result["stopping_reason"] == "ship_lost"
    assert result["paused"] and result["ship"]["lost"]
    assert result["cargo"]["lost_units"] == 800
    assert result["cargo"]["lost_teu"] == 1200
    assert result["cargo"]["remaining_units"] == 0
    assert result["cargo"]["grade"] == 5
    assert [event["kind"] for event in result["events"]].count("ship_lost") == 1
    assert not any(event["kind"] == "escaped" for event in result["events"])
    with pytest.raises(ValueError, match="ended"):
        s.authorize_resume()
    with pytest.raises(ValueError, match="ended"):
        s.set_order(0, 1)
    s.advance_segment(1)
    assert s.state.tick == 1
    restored = make_session(vessel, tmp_path, restore=True)
    assert restored.ship == s.ship and restored.cargo == s.cargo
    assert restored.outcome == "ship_lost"
    assert restored.tool("finish_mission", {}, "finish", restored.revision)["outcome"] == "ship_lost"


def test_ship_exposure_and_active_roll_draw_survive_session_restore(vessel, tmp_path):
    s = make_session(vessel, tmp_path)
    s.ship_model.advance(s.ship, SimpleNamespace(**(s.last_sample | {"roll_deg": 36})), 0.05)
    s.ship["structural_dose"] = 0.25
    s.ship["following_dose"] = 0.4
    s.save()
    restored = make_session(vessel, tmp_path, restore=True)
    assert restored.ship == s.ship
    for session in (s, restored):
        session.authorize_resume()
        session.advance_segment(0.5)
    assert restored.ship == s.ship
    assert restored.cargo == s.cargo


def test_snapshot_without_ship_algorithm_is_rejected(vessel, tmp_path):
    s = make_session(vessel, tmp_path)
    saved = json.loads(s.snapshot_path.read_text())
    del saved["ship"]
    saved["fingerprint"] = "before-ship-damage"
    s.snapshot_path.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="does not match"):
        make_session(vessel, tmp_path, restore=True)


def test_numerical_failure_is_not_a_shipwreck(vessel, tmp_path, monkeypatch):
    s = make_session(vessel, tmp_path)
    before_ship, before_cargo = dict(s.ship), json.loads(json.dumps(s.cargo))
    monkeypatch.setattr("game.simulation.runtime.derivative", lambda *args: np.full(12, np.nan))
    s.authorize_resume()
    result = s.advance_segment(1)
    assert result["stopping_reason"] == "numerical_failure"
    assert result["outcome"] is None
    assert s.state.tick == 0 and s.ship == before_ship and s.cargo == before_cargo


def test_following_sea_loss_settles_all_cargo_on_the_first_accepted_step(vessel, tmp_path):
    from game.simulation.environment import SeaState
    from game.simulation.runtime import Command

    s = make_session(vessel, tmp_path)
    s.sim.environment.source.sea = SeaState(8.2, 8, 0)
    s.state.x[0] = 30
    s.state.x[11] = math.pi
    s.order = Command(180, 1)
    s.ship["following_dose"] = 1 - 1e-6
    s.authorize_resume()
    result = s.advance_segment(1)
    assert s.state.tick == 1
    assert result["outcome"] == "ship_lost"
    assert result["ship"]["cause"] == "following_sea_instability"
    assert result["cargo"]["lost_units"] == 800
    assert result["paused"]


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
    config = {"median_g": 0.0001, "sigma": 0.0, "exposure_s": 0.05, "strength": 1.0}
    s = make_session(vessel, tmp_path, cargo_config=config)
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


def test_save_resume_during_an_unfinished_overload_preserves_the_counter(vessel, tmp_path):
    config = {"median_g": 0.0001, "sigma": 0.0, "exposure_s": 0.1, "strength": 1.0}
    s = make_session(vessel, tmp_path, cargo_config=config)
    s.state.x[9] = 0.1
    s.authorize_resume()
    s.advance_segment(0.05)

    assert s.state.tick == 1
    assert s.cargo["failed_count"] == 0
    assert s.cargo["overload_started_steps"]
    s.save()

    restored = make_session(vessel, tmp_path, restore=True, cargo_config=config)
    assert restored.cargo == s.cargo
    restored.authorize_resume()
    result = restored.advance_segment(0.05)
    assert result["stopping_reason"] == "cargo_grade_increased"
    assert restored.cargo["failed_count"] == 64
    s.advance_segment(0.05)
    assert restored.cargo == s.cargo


def test_old_algorithm_snapshot_is_rejected_by_the_fingerprint(vessel, tmp_path):
    from archive.game.cargo import CargoModel as LegacyCargoModel

    make_session(vessel, tmp_path, cargo_model=LegacyCargoModel(seed=1))
    with pytest.raises(ValueError, match="does not match"):
        make_session(vessel, tmp_path, restore=True)


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
