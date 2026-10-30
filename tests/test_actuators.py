import math

import numpy as np
import pytest

from game.simulation import actuators
from game.simulation.container import actuator_forces
from game.simulation.runtime import Command, Settings, Simulation
from game.simulation.vessel import load_s175
from game.session import motion_dict, motion_state
from test_runtime import ConstantEnvironment, START


@pytest.mark.parametrize("u,v,r,delta,rpm", [
    (5, 0, 0, 0.1, 70), (7, .25, .002, -.1, 85),
    (6.8, -.3, -.015, .06, 75), (6.2, .5, .045, .03, 90),
])
def test_si_actuator_terms_match_the_octave_verified_full_source(u, v, r, delta, rpm):
    source = actuator_forces([u, v, r, 0, 0, 0, .01, .03, delta, rpm], [delta, rpm])
    expected = [
        .825*source["propeller_thrust_n"] + source["rudder_surge_force_n"],
        source["rudder_sway_force_n"], 0,
        source["rudder_roll_moment_nm"], 0, source["rudder_yaw_moment_nm"],
    ]
    np.testing.assert_allclose(actuators.loads([u, v, 0, .01, 0, r], delta, rpm), expected,
                               rtol=1e-12, atol=1e-7)


def test_zero_limits_are_continuous_and_source_servo_limits_are_retained():
    np.testing.assert_array_equal(actuators.loads(np.zeros(6), 0, 0), np.zeros(6))
    small = actuators.loads([1e-8, 0, 0, 0, 0, 0], .1, 70)
    np.testing.assert_allclose(actuators.loads(np.zeros(6), .1, 70), small, rtol=1e-8)
    assert actuators.rates(0, 70, math.pi, 1000)[0] == pytest.approx(math.radians(5))
    assert actuators.rates(0, 70, 0, 1000)[1] == pytest.approx((160-70)/(5.65/(70/60)))
    with pytest.raises(ValueError, match="astern"):
        actuators.loads([-1, 0, 0, 0, 0, 0], 0, 70)
    with pytest.raises(ValueError, match="reverse"):
        actuators.loads([5, 0, 0, 0, 0, 0], 0, -1)
    with pytest.raises(ValueError, match="envelope"):
        actuators.loads([.2, 0, 0, 0, 0, .01], math.radians(10), 20)


def test_rpm_inversion_has_physical_saturation_and_coasting():
    velocity = [5, 0, 0, 0, 0, 0]
    for thrust in [1e3, 1e5, 1e6]:
        rpm = actuators.rpm_for_thrust(thrust, velocity)
        assert actuators.loads(velocity, 0, rpm)[0] == pytest.approx(thrust)
    assert actuators.rpm_for_thrust(-1e5, velocity) == 0


@pytest.mark.parametrize("call", [
    lambda: actuators.rpm_for_thrust(1e5, [float("nan"),0,0,0,0,0]),
    lambda: actuators.rpm_for_thrust(1e5, [5,0,0,0,0,0], rho=0),
    lambda: actuators.rates(float("nan"), 70, 0, 70),
    lambda: actuators.rates(0, float("inf"), 0, 70),
])
def test_actuator_inverse_and_servos_reject_nonfinite_inputs(call):
    with pytest.raises(ValueError):
        call()


def physical_sim(**settings):
    return Simulation(load_s175(), ConstantEnvironment(), wind_parameters=None,
                      settings=Settings(actuator_model="son_nomoto", **settings))


def test_runtime_uses_s175_matrices_and_applies_all_actuator_components():
    sim = physical_sim()
    original = load_s175()
    for name in ("M", "MA", "D", "G"):
        np.testing.assert_array_equal(getattr(sim.vessel, name), getattr(original, name))
    state = sim.initial_state(START, 3.5, 40, speed_mps=5)
    state.control.rudder_rad = math.radians(5)
    force = sim._control(state, Command(90, 5), sim.environment.sea)
    assert force[1] < 0 and force[3] > 0 and force[5] > 0
    assert force[2] == force[4] == 0
    assert sim.algorithm_id != Simulation.algorithm_id


def test_physical_state_survives_split_advancement_and_snapshot():
    sim = physical_sim()
    state = sim.initial_state(START, 3.5, 40, heading_deg=359, speed_mps=5)
    command = Command(45, 6)
    full = sim.advance(state, command, 2)
    first = sim.advance(state, command, .65)
    restored = motion_state(motion_dict(first.state))
    split = sim.advance(restored, command, 1.35)
    assert full.stopping_reason == split.stopping_reason == "duration_reached"
    np.testing.assert_array_equal(full.state.x, split.state.x)
    assert full.state.control == split.state.control
    assert 0 < full.trajectory[-1].rudder_angle_deg < 10
    assert 0 < full.trajectory[-1].ordered_rudder_deg <= 10
    assert 0 < full.trajectory[-1].shaft_rpm <= 160
    assert state.control.rudder_rad == 0


def test_servo_step_halving_converges_and_pause_rolls_back():
    results = []
    for dt in [.1, .05, .025]:
        sim = physical_sim(dt_s=dt, wave_period_s=.1)
        state = sim.initial_state(START, 3.5, 40, speed_mps=5)
        before = motion_dict(state)
        paused = sim.advance(state, Command(90, 6), 1, should_pause=lambda: True)
        assert motion_dict(paused.state)["control"] == before["control"]
        assert motion_dict(paused.state)["x"] == before["x"]
        assert paused.advanced_s == 0
        results.append(sim.advance(state, Command(90, 6), 2).state)
    assert abs(results[1].x[11]-results[2].x[11]) < abs(results[0].x[11]-results[2].x[11])


def test_new_actuator_ignores_teaching_force_caps_and_prevents_windup():
    sim = physical_sim(max_yaw_moment_nm=1, max_thrust_n=1, yaw_natural_frequency=.08)
    state = sim.initial_state(START, 3.5, 40, speed_mps=5)
    state.control.heading_ref = math.radians(5)
    state.control.speed_ref = 100
    sim._control(state, Command(5, 100), sim.environment.sea)
    assert state.control.rudder_order_rad == pytest.approx(actuators.RUDDER_LIMIT_RAD)
    assert state.control.shaft_order_rpm == 160
    assert state.control.heading_integral == state.control.speed_integral == 0
    state.control.rudder_rad = actuators.RUDDER_LIMIT_RAD
    assert sim._control(state, Command(5, 100), sim.environment.sea)[5] > 1


def test_physical_zero_start_and_invalid_configuration():
    sim = physical_sim()
    state = sim.initial_state(START, 3.5, 40)
    result = sim.advance(state, Command(0, 1), .5)
    assert result.stopping_reason == "duration_reached"
    assert result.state.x[0] > 0 and result.state.control.shaft_rpm > 0
    assert result.state.control.rudder_rad == 0
    assert result.state.x[11] == pytest.approx(0)
    with pytest.raises(ValueError, match="actuator_model"):
        Settings(actuator_model="unknown")


def test_maneuvering_diagnostic_runs_the_coupled_state():
    from pipeline.validate_maneuvering import trial

    row, samples = trial("rudder_left", seconds=1)
    assert row["stop"] == "duration_reached"
    assert row["advanced_s"] == 1
    assert row["net_heading_change_deg"] < 0
    assert samples[-1]["rudder_angle_deg"] < 0
    assert row["matrix_sha256"]["D"]


def test_low_speed_yaw_outside_inflow_envelope_stops_without_committing_state():
    sim = physical_sim()
    state = sim.initial_state(START, 3.5, 40, speed_mps=.2)
    state.x[5] = .01
    before = motion_dict(state)
    result = sim.advance(state, Command(0, 1), 1)
    assert result.stopping_reason == "model_limit"
    assert result.advanced_s == 0
    assert motion_dict(result.state)["x"] == before["x"]
    assert motion_dict(result.state)["control"] == before["control"]
    assert "envelope" in result.events[0].detail
