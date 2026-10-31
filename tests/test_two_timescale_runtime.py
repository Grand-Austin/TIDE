"""Focused runtime integration tests for the S175 two-time-scale mode."""

import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from game.cargo import CargoModel
from game.session import Session
from game.simulation import two_timescale
from game.simulation.environment import EnvironmentError, SeaState
from game.simulation.runtime import (
    MANEUVERING_ALGORITHM,
    MANEUVERING_MODEL,
    Command,
    Settings,
    Simulation,
)
from game.simulation.vessel import load_s175
from game.simulation.wind import WindParameters

START = datetime(2026, 1, 23, tzinfo=timezone.utc)
LON, LAT = -8.0, 47.0
KNOT = 1852.0 / 3600.0
VESSEL = load_s175()


class CalmWater:
    def __init__(self, sea=None, end=None):
        self.sea = sea or SeaState(0.0, 1.0, 0.0)
        self.end = end

    def sample(self, time, lon, lat):
        if self.end is not None and time > self.end:
            raise EnvironmentError("time_boundary", "End of test window")
        return self.sea


def new_sim(dt_s=0.1, *, environment=None, wind_parameters=None):
    return Simulation(
        VESSEL, environment or CalmWater(), seed=23,
        settings=Settings(actuator_model=MANEUVERING_MODEL, dt_s=dt_s,
                          wave_period_s=max(0.1, dt_s), record_period_s=1.0),
        wind_parameters=wind_parameters,
    )


def initial(sim, speed_mps=5.14, heading_deg=0.0):
    return sim.initial_state(START, LON, LAT, heading_deg=heading_deg, speed_mps=speed_mps)


def test_calm_straight_equilibrium_keeps_lf_straight_and_wf_zero():
    sim = new_sim()
    state = initial(sim)
    assert sim.algorithm_id == MANEUVERING_ALGORITHM
    assert state.lf is not None and state.wf is not None
    assert state.control.shaft_rpm > 0

    result = sim.advance(state, Command(0.0, 5.14), 30.0)

    assert result.stopping_reason == "duration_reached"
    assert result.state.lf[0] == pytest.approx(5.14, abs=0.03)
    assert abs(result.state.lf[1]) < 1e-5
    assert abs(result.state.lf[7]) < 1e-5
    np.testing.assert_array_equal(result.state.wf, np.zeros(12))
    assert result.state.control.shaft_rpm <= 160.0
    sample = result.trajectory[-1]
    assert sample.speed_through_water_mps == pytest.approx(5.14, abs=0.03)
    assert math.isfinite(sample.lateral_acceleration_mps2)


def test_normal_35_degree_turn_uses_actual_actuators_and_loses_speed(monkeypatch):
    sim = new_sim(dt_s=0.05)
    settle = sim.advance(initial(sim, 6.214), Command(0.0, 6.214), 60.0)
    rpm = settle.state.control.shaft_rpm
    assert 0.0 < rpm < 160.0

    state = initial(sim, 6.214)
    state.control.shaft_rpm = state.control.shaft_order_rpm = rpm
    rudder_order = math.radians(35.0)
    original = sim._control

    def constant_shaft_control(*args, **kwargs):
        candidate = args[0]
        force = original(*args, **kwargs)
        candidate.control.rudder_order_rad = rudder_order
        candidate.control.shaft_order_rpm = rpm
        return force

    monkeypatch.setattr(sim, "_control", constant_shaft_control)
    result = sim.advance(state, Command(0.0, 6.214), 120.0)

    assert result.stopping_reason == "duration_reached"
    final = result.trajectory[-1]
    assert final.heading_deg > 20.0  # the rudder actually turns the LF mean ship
    assert result.state.lf[0] < 6.0  # no commanded-speed pinning in this trial
    assert abs(math.degrees(result.state.lf[6])) > 0.05  # real heel from the turn
    np.testing.assert_array_equal(result.state.wf, np.zeros(12))
    assert max(abs(s.rudder_angle_deg) for s in result.trajectory) <= 35.0 + 1e-9
    assert max(s.shaft_rpm for s in result.trajectory) <= 160.0 + 1e-9
    changes = [abs(b.rudder_angle_deg - a.rudder_angle_deg)
               for a, b in zip(result.trajectory, result.trajectory[1:])]
    assert max(changes) <= math.degrees(math.radians(5.0)) + 1e-6
    assert abs(math.degrees(final.angular_velocity_z_rad_s)) > 0.05


def test_actual_gyro_and_cargo_loads_are_composed_and_finite():
    sim = new_sim(environment=CalmWater(SeaState(2.0, 8.0, 40.0, 0.2, 0.1, 4.0, 2.0)))
    result = sim.advance(initial(sim), Command(20.0, 5.0), 20.0)
    assert result.stopping_reason == "duration_reached"
    cargo_model = CargoModel(seed=1)
    cargo = cargo_model.initial_state()
    for sample in result.trajectory:
        values = (sample.lateral_acceleration_mps2, sample.angular_acceleration_x_rad_s2,
                  sample.angular_velocity_x_rad_s, sample.angular_velocity_y_rad_s,
                  sample.angular_velocity_z_rad_s)
        assert all(math.isfinite(value) for value in values)
        cargo_model.advance(cargo, sample, sim.settings.dt_s)
    final = result.trajectory[-1]
    assert final.angular_velocity_z_rad_s == pytest.approx(result.state.x[5])
    assert math.isfinite(final.angular_acceleration_y_rad_s2)
    assert math.isfinite(final.vertical_acceleration_mps2)


def test_wind_and_current_use_lf_ground_advection_once():
    current = SeaState(0.0, 1.0, 0.0, 0.4, 0.2, 0.0, 0.0)
    wind = SeaState(0.0, 1.0, 0.0, 0.4, 0.2, 8.0, 0.0)
    no_wind = new_sim(environment=CalmWater(current))
    windy = new_sim(environment=CalmWater(wind), wind_parameters=WindParameters())
    a = no_wind.advance(initial(no_wind, 5.0), Command(0.0, 5.0), 20.0)
    b = windy.advance(initial(windy, 5.0), Command(0.0, 5.0), 20.0)

    assert a.state.lf[4] == pytest.approx(5.0*20.0 + 0.2*20.0, abs=0.2)
    assert a.state.lf[5] == pytest.approx(0.4*20.0, abs=0.2)
    assert a.trajectory[-1].speed_through_water_mps == pytest.approx(5.0, abs=0.05)
    assert np.linalg.norm(b.state.wind_force[:2]) > 0.0
    assert b.state.lf[5] != pytest.approx(a.state.lf[5])


def test_split_run_is_exact_and_does_not_mutate_the_input_state():
    sim = new_sim()
    state = initial(sim)
    before_lf, before_wf = state.lf.copy(), state.wf.copy()
    command = Command(35.0, 5.0)
    full = sim.advance(state, command, 3.0)
    first = sim.advance(state, command, 1.0)
    second = sim.advance(first.state, command, 2.0)

    np.testing.assert_array_equal(state.lf, before_lf)
    np.testing.assert_array_equal(state.wf, before_wf)
    np.testing.assert_array_equal(second.state.lf, full.state.lf)
    np.testing.assert_array_equal(second.state.wf, full.state.wf)
    np.testing.assert_array_equal(second.state.x, full.state.x)
    np.testing.assert_array_equal(second.state.wave.phase, full.state.wave.phase)
    assert second.state.control == full.state.control
    assert second.state.exposure == full.state.exposure


class PlainRules:
    budget_s = 100

    def evaluate(self, sample, consumed):
        return None, []

    def visible_reports(self, elapsed, lon, lat):
        return []


def test_session_snapshot_round_trip_preserves_lf_wf_arrays(tmp_path):
    definition = {
        "seed": 1,
        "initial": {"heading_deg": 0, "speed_mps": 5.0},
        "mission": {"budget_s": 100},
        "area_bbox": [-9.0, -7.0, 46.0, 48.0],
    }
    sim = new_sim()

    def make(restore=False):
        scenario = SimpleNamespace(
            definition=definition,
            initial_state=lambda s: s.initial_state(START, LON, LAT, speed_mps=5.0),
        )
        return Session(scenario, sim, {}, tmp_path, restore=restore, rules=PlainRules())

    a = make()
    a.authorize_resume()
    a.set_order(30.0, 10.0)
    a.authorize_resume()
    a.advance_segment(2.0)
    b = make(restore=True)
    assert b.state.lf is not None and b.state.wf is not None

    for session in (a, b):
        session.authorize_resume()
        session.advance_segment(1.0)
    np.testing.assert_array_equal(b.state.lf, a.state.lf)
    np.testing.assert_array_equal(b.state.wf, a.state.wf)
    np.testing.assert_array_equal(b.state.x, a.state.x)
    assert b.state.control == a.state.control


def test_numerical_failure_rolls_back_lf_wf_and_exposure(monkeypatch):
    sim = new_sim()
    state = initial(sim)
    before = (state.lf.copy(), state.wf.copy(), state.tick, state.exposure.elapsed_s)
    calls = 0

    def failing_derivative(*args, **kwargs):
        nonlocal calls
        calls += 1
        return np.full(8, np.nan) if calls == 4 else np.zeros(8)

    monkeypatch.setattr(two_timescale, "lf_derivative", failing_derivative)
    result = sim.advance(state, Command(0.0, 5.0), 1.0)

    assert result.stopping_reason == "numerical_failure"
    assert result.advanced_s == 0.0
    np.testing.assert_array_equal(result.state.lf, before[0])
    np.testing.assert_array_equal(result.state.wf, before[1])
    assert result.state.tick == before[2]
    assert result.state.exposure.elapsed_s == before[3]


def test_environment_failure_rolls_back_the_last_step():
    sim = new_sim(environment=CalmWater(end=START + timedelta(seconds=0.2)))
    state = initial(sim)
    result = sim.advance(state, Command(0.0, 5.0), 1.0)

    assert result.stopping_reason == "time_boundary"
    assert result.state.tick == 2
    assert result.advanced_s == pytest.approx(0.2)
    assert result.trajectory[-1].time_s == pytest.approx(0.2)


def test_legacy_modes_and_algorithm_ids_remain_available():
    assert Settings(actuator_model="teaching").actuator_model == "teaching"
    assert Settings(actuator_model="son_nomoto").actuator_model == "son_nomoto"
    teaching = Simulation(VESSEL, CalmWater(), wind_parameters=None)
    son = Simulation(VESSEL, CalmWater(), wind_parameters=None,
                     settings=Settings(actuator_model="son_nomoto", dt_s=0.1,
                                       wave_period_s=0.2, record_period_s=1.0))
    assert teaching.algorithm_id == "s175_fixed_clock_v1"
    assert son.algorithm_id == "s175_son_nomoto_actuator_v1"
    result = teaching.advance(teaching.initial_state(START, LON, LAT, speed_mps=2.0),
                              Command(0.0, 2.0), 1.0)
    assert result.stopping_reason == "duration_reached"
