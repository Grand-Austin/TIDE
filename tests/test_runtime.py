from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from game.simulation.environment import EnvironmentError, SeaState
from game.simulation.runtime import Command, Settings, Simulation, _exceed_fraction

START = datetime(2026, 9, 9, 8, tzinfo=timezone.utc)


def test_exposure_counts_time_on_both_sides_of_zero():
    assert _exceed_fraction(-2, 2, 1) == pytest.approx(0.5)
    assert _exceed_fraction(0, 2, 1) == pytest.approx(0.5)
    assert _exceed_fraction(0, 0, 1) == 0
    assert _exceed_fraction(-2, -3, 1) == 1


def test_source_speed_table_does_not_limit_motion_or_commands(vessel):
    sim = Simulation(vessel, ConstantEnvironment())
    state = sim.initial_state(START, 3.5, 40, speed_mps=12)
    result = sim.advance(state, Command(0, 13), .2)
    assert result.stopping_reason == 'duration_reached'
    assert result.advanced_s == pytest.approx(.2)
    assert sim.observe(result.state).speed_through_water_mps > vessel.max_speed_mps
    assert np.all(np.isfinite(result.state.x))


class ConstantEnvironment:
    def __init__(self, sea=None, end=None):
        self.sea = sea or SeaState(0, 8, 0)
        self.end = end

    def sample(self, time, lon, lat):
        if self.end is not None and time > self.end:
            raise EnvironmentError("time_boundary", "End of test window")
        return self.sea


@pytest.fixture
def vessel():
    """Synthetic, deliberately simple coefficients; never a substitute S175."""
    mass = np.diag([1e6, 1e6, 1e6, 1e8, 1e8, 1e8])
    freqs = np.linspace(0.1, 4, 40)
    return SimpleNamespace(
        main=dict(g=9.81, m=1e6, k44=10, k55=10, k66=10, Lpp=50, B=10, T=3, rho=1025, C_B=0.6, name="synthetic"),
        MRB=mass, MA=mass * 0.1, M=mass * 1.1, Minv=np.linalg.inv(mass * 1.1),
        D=np.diag([1e4, 1e4, 1e4, 1e6, 1e6, 1e6]),
        G=np.diag([0, 0, 1e6, 1e8, 1e8, 0]), nonlinear_456=np.zeros(3),
        freqs=freqs, headings=np.arange(0, 2*np.pi, np.pi/6),
        force_rao=np.full((6, 40, 12), 1000+0j), max_speed_mps=10.0,
    )


def test_calm_equilibrium_and_input_immutability(vessel):
    sim = Simulation(vessel, ConstantEnvironment())
    state = sim.initial_state(START, 3.5, 40)
    before = deepcopy(state)
    result = sim.advance(state, Command(0, 0), 1)
    assert result.stopping_reason == "duration_reached"
    np.testing.assert_array_equal(result.state.x, np.zeros(12))
    np.testing.assert_array_equal(state.x, before.x)
    assert state.tick == 0 and state.exposure.elapsed_s == 0
    assert result.state.exposure.elapsed_s == 1
    assert result.trajectory[-1].course_over_ground_deg is None


def test_current_advects_without_double_counting(vessel):
    sim = Simulation(vessel, ConstantEnvironment(SeaState(0, 8, 0, 0.3, 0.4)), wind_parameters=None)
    state = sim.initial_state(START, 3.5, 40)
    result = sim.advance(state, Command(0, 0), 2)
    np.testing.assert_allclose(result.state.x[6:8], [0.8, 0.6], atol=1e-10)
    sample = result.trajectory[-1]
    assert sample.speed_through_water_mps == pytest.approx(0, abs=1e-9)
    assert sample.speed_over_ground_mps == pytest.approx(0.5)
    assert sample.velocity_north_through_water_mps == pytest.approx(0, abs=1e-9)
    assert sample.velocity_east_through_water_mps == pytest.approx(0, abs=1e-9)


def test_endpoint_sample_reports_body_frame_angular_rates(vessel, monkeypatch):
    import game.simulation.runtime as runtime
    original_derivative = runtime.derivative
    derivatives = []

    def recording_derivative(*args):
        value = original_derivative(*args)
        derivatives.append(value)
        return value

    monkeypatch.setattr(runtime, "derivative", recording_derivative)
    sim = Simulation(vessel, ConstantEnvironment())
    state = sim.initial_state(START, 3.5, 40, speed_mps=1)
    state.x[3:6] = [0.03, 0.04, -0.02]
    state.x[9:11] = [0.1, -0.05]
    result = sim.advance(state, Command(0, 1), 0.2)
    sample = result.trajectory[-1]
    endpoint_derivative = derivatives[-1]

    assert sample.angular_acceleration_x_rad_s2 == pytest.approx(float(endpoint_derivative[3]))
    assert sample.angular_velocity_y_rad_s == pytest.approx(float(result.state.x[4]))
    assert sample.angular_velocity_z_rad_s == pytest.approx(float(result.state.x[5]))
    assert sample.angular_velocity_x_rad_s == pytest.approx(float(result.state.x[3]))
    assert sample.angular_acceleration_y_rad_s2 == pytest.approx(float(endpoint_derivative[4]))
    acceleration = endpoint_derivative[:3] + np.cross(result.state.x[3:6], result.state.x[:3])
    assert sample.longitudinal_acceleration_mps2 == pytest.approx(float(acceleration[0]))
    assert sample.vertical_acceleration_mps2 == pytest.approx(float(acceleration[2]))
    water = sim._water_velocity(result.state.x, sim.environment.sea)
    assert sample.velocity_north_through_water_mps == pytest.approx(float(water[0]))
    assert sample.velocity_east_through_water_mps == pytest.approx(float(water[1]))


def test_split_advancement_preserves_endpoint_angular_fields(vessel):
    sim = Simulation(vessel, ConstantEnvironment(SeaState(1.0, 8, 40)), seed=23)
    state = sim.initial_state(START, 3.5, 40, heading_deg=359, speed_mps=1)
    command = Command(15, 2)
    full = sim.advance(state, command, 2)
    first = sim.advance(state, command, 1)
    split = sim.advance(first.state, command, 1)

    full_by_time = {sample.time_s: sample for sample in full.trajectory}
    split_by_time = {sample.time_s: sample for sample in first.trajectory + split.trajectory}
    assert set(split_by_time) == set(full_by_time)
    for time_s in full_by_time:
        for field_name in ("angular_acceleration_x_rad_s2", "angular_velocity_y_rad_s", "angular_velocity_z_rad_s",
                           "angular_velocity_x_rad_s", "angular_acceleration_y_rad_s2",
                           "longitudinal_acceleration_mps2", "vertical_acceleration_mps2",
                           "velocity_north_through_water_mps", "velocity_east_through_water_mps"):
            assert split_by_time[time_s].__getattribute__(field_name) == pytest.approx(
                full_by_time[time_s].__getattribute__(field_name)
            )


def test_split_advancement_preserves_wave_control_and_exposure(vessel):
    sim = Simulation(vessel, ConstantEnvironment(SeaState(1.0, 8, 40)), seed=23)
    state = sim.initial_state(START, 3.5, 40, heading_deg=359, speed_mps=1)
    command = Command(15, 2)
    full = sim.advance(state, command, 3)
    first = sim.advance(state, command, 1.35)
    split = sim.advance(first.state, command, 1.65)
    assert full.stopping_reason == split.stopping_reason == "duration_reached"
    np.testing.assert_array_equal(full.state.x, split.state.x)
    np.testing.assert_array_equal(full.state.wave.phase, split.state.wave.phase)
    assert full.state.control == split.state.control
    assert full.state.exposure == split.state.exposure
    assert full.state.exposure.roll_squared_rad2_s > 0


def test_environment_stop_returns_last_valid_state(vessel):
    env = ConstantEnvironment(end=START+timedelta(seconds=0.3))
    sim = Simulation(vessel, env)
    result = sim.advance(sim.initial_state(START, 3.5, 40), Command(0, 1), 1)
    assert result.stopping_reason == "time_boundary"
    assert result.advanced_s == pytest.approx(0.3)
    assert result.state.tick == 6
    assert result.trajectory[-1].time_s == pytest.approx(0.3)
    assert result.state.exposure.elapsed_s == pytest.approx(0.3)


def test_pause_is_immediate_and_invalid_commands_do_not_mutate(vessel):
    sim = Simulation(vessel, ConstantEnvironment())
    state = sim.initial_state(START, 3.5, 40)
    assert sim.advance(state, Command(0, 0), 1, should_pause=lambda: True).advanced_s == 0
    for command in (Command(0, -1), Command(float("nan"), 1), Command(0, float("inf"))):
        with pytest.raises(ValueError):
            sim.advance(state, command, 1)
    with pytest.raises(ValueError, match="multiple"):
        sim.advance(state, Command(0, 1), 0.075)
    assert state.tick == 0


def test_numerical_failure_is_distinct_and_rolls_back(vessel, monkeypatch):
    import game.simulation.runtime as runtime
    sim = Simulation(vessel, ConstantEnvironment())
    state = sim.initial_state(START, 3.5, 40)

    def failing_derivative(*args):
        raise np.linalg.LinAlgError("Numerical solve failed")

    monkeypatch.setattr(runtime, "derivative", failing_derivative)
    result = sim.advance(state, Command(0, 1), 1)
    assert result.stopping_reason == "numerical_failure"
    assert result.advanced_s == 0
    np.testing.assert_array_equal(result.state.x, state.x)
    assert result.state.control == state.control


def test_turn_uses_short_angle_and_controls_are_bounded(vessel):
    sim = Simulation(vessel, ConstantEnvironment())
    state = sim.initial_state(START, 3.5, 40, heading_deg=359)
    original = state.control.heading_ref
    force = sim._control(state, Command(1, 10), SeaState(0, 8, 0))
    assert 0 < state.control.heading_ref - original < np.deg2rad(1)
    assert abs(force[0]) <= sim.settings.max_thrust_n
    assert abs(force[5]) <= sim.settings.max_yaw_moment_nm
    np.testing.assert_array_equal(force[1:5], 0)


@pytest.mark.parametrize("speed_mps", [1, 5, 10])
@pytest.mark.parametrize("heading_deg", [-90, 90])
def test_yaw_moment_limit_is_fixed_at_every_speed(vessel, speed_mps, heading_deg):
    settings = Settings(max_yaw_moment_nm=10_000)
    sim = Simulation(vessel, ConstantEnvironment(), settings=settings)
    state = sim.initial_state(START, 3.5, 40, speed_mps=speed_mps)
    state.control.heading_ref = np.deg2rad(heading_deg)

    force = sim._control(state, Command(heading_deg, speed_mps), SeaState(0, 8, 0))

    assert force[5] == pytest.approx(np.copysign(settings.max_yaw_moment_nm, heading_deg))


def test_heading_integral_accumulates_only_near_the_order(vessel):
    sim = Simulation(vessel, ConstantEnvironment())
    state = sim.initial_state(START, 3.5, 40)
    state.control.heading_ref = np.deg2rad(5)
    sim._control(state, Command(5, 0), SeaState(0, 8, 0))
    integral = state.control.heading_integral
    assert integral > 0

    state.control.heading_ref = np.deg2rad(30)
    sim._control(state, Command(30, 0), SeaState(0, 8, 0))
    assert state.control.heading_integral == integral


def test_storm_settings_preserve_s175_damping_and_dissipate_energy():
    from game.simulation.dynamics import derivative
    from game.simulation.vessel import load_s175

    vessel = load_s175()
    state = np.zeros(12)
    # This coupled roll/yaw motion gained energy under the former D66 override.
    state[3], state[5] = 0.001, 0.0004347365205377971
    paths = sorted(Path("scenarios").glob("biscay-*/scenario.json"))
    assert paths
    for path in paths:
        settings = Settings(**json.loads(path.read_text())["settings"])
        sim = Simulation(vessel, ConstantEnvironment(), settings=settings, wind_parameters=None)
        np.testing.assert_array_equal(sim.vessel.D, vessel.D)
        assert np.linalg.eigvalsh(sim.vessel.D).min() >= 0
        acceleration = derivative(state, np.zeros(6), sim.vessel, (0, 0))[:6]
        assert state[:6] @ sim.vessel.M @ acceleration < 0


def test_step_halving_converges(vessel):
    env = ConstantEnvironment()
    results = []
    for dt in (0.05, 0.025, 0.0125):
        sim = Simulation(vessel, env, settings=Settings(dt_s=dt))
        state = sim.initial_state(START, 3.5, 40, speed_mps=1)
        state.x[9] = 0.01
        results.append(sim.advance(state, Command(0, 1), 2).state.x)
    assert np.linalg.norm(results[1] - results[2]) < np.linalg.norm(results[0] - results[2])


def test_real_s175_in_committed_environment():
    from game.simulation.environment import CopernicusEnvironment
    from game.simulation.vessel import load_s175
    if not Path("data/processed/s175").exists():
        pytest.skip("Run python -m pipeline.prepare_vessel for the S175 integration test")
    with CopernicusEnvironment.from_directory("data/copernicus") as env:
        sim = Simulation(load_s175(), env)
        state = sim.initial_state(START, 3.5, 40, heading_deg=90, speed_mps=2)
        result = sim.advance(state, Command(100, 3), 10)
        assert result.stopping_reason == "duration_reached"
        assert np.all(np.isfinite(result.state.x))
        assert result.state.x[7] > 0
        assert result.state.exposure.roll_squared_rad2_s > 0
        assert np.linalg.norm(result.state.wind_force) > 0
        assert result.trajectory[-1].sst_c is not None


def test_wind_changes_motion_and_split_calls_preserve_forces(vessel):
    wind = ConstantEnvironment(SeaState(0, 8, 0, wind_east_mps=12))
    sim = Simulation(vessel, wind)
    state = sim.initial_state(START, 3.5, 40)
    command = Command(0, 0)
    full = sim.advance(state, command, 2)
    split = sim.advance(sim.advance(state, command, 0.75).state, command, 1.25)
    disabled = Simulation(vessel, wind, wind_parameters=None).advance(state, command, 2)
    assert full.stopping_reason == split.stopping_reason == "duration_reached"
    assert full.state.x[7] > disabled.state.x[7]
    assert full.state.wind_force[1] > 0
    np.testing.assert_array_equal(full.state.x, split.state.x)
    np.testing.assert_array_equal(full.state.wind_force, split.state.wind_force)


def test_sst_does_not_add_spurious_forces(vessel):
    cold = Simulation(vessel, ConstantEnvironment(SeaState(1, 8, 0, sst_c=10)))
    warm = Simulation(vessel, ConstantEnvironment(SeaState(1, 8, 0, sst_c=30)))
    state = cold.initial_state(START, 3.5, 40, speed_mps=1)
    a = cold.advance(state, Command(0, 1), 1)
    b = warm.advance(state, Command(0, 1), 1)
    np.testing.assert_array_equal(a.state.x, b.state.x)
    assert a.trajectory[-1].sst_c == 10
    assert b.trajectory[-1].sst_c == 30


def test_accepted_step_callback_stops_without_changing_numerics(vessel):
    sim = Simulation(vessel, ConstantEnvironment())
    state = sim.initial_state(START, 3.5, 40, speed_mps=1)
    samples = []
    def observe(sample, dt):
        samples.append((sample.time_s, dt))
        return 'checkpoint' if sample.time_s >= 0.2 else None
    result = sim.advance(state, Command(0, 1), 1, on_step=observe)
    reference = sim.advance(state, Command(0, 1), 0.2)
    assert result.stopping_reason == 'checkpoint'
    assert len(samples) == 4
    np.testing.assert_array_equal(result.state.x, reference.state.x)
    assert result.state.exposure == reference.state.exposure
    samples.clear()
    sim.advance(state, Command(0, 1), 1, on_step=observe, should_pause=lambda: True)
    assert samples == []


@pytest.mark.parametrize("state_index", [9, 10])
@pytest.mark.parametrize("angle_deg", [35, 45])
def test_roll_and_pitch_above_30_degrees_are_not_rejected_or_clamped(vessel, state_index, angle_deg):
    sim = Simulation(vessel, ConstantEnvironment())
    state = sim.initial_state(START, 3.5, 40, speed_mps=0)
    state.x[state_index] = np.deg2rad(angle_deg)

    result = sim.advance(state, Command(0, 0), 0.2)

    assert result.stopping_reason == "duration_reached"
    assert result.advanced_s == pytest.approx(0.2)
    assert np.all(np.isfinite(result.state.x))
    assert abs(result.state.x[state_index]) > np.deg2rad(30)


def test_pitch_at_euler_singularity_stops_without_committing_a_step(vessel):
    sim = Simulation(vessel, ConstantEnvironment())
    state = sim.initial_state(START, 3.5, 40, speed_mps=0)
    state.x[10] = np.pi / 2
    original = deepcopy(state)

    result = sim.advance(state, Command(0, 0), 0.2)

    assert result.stopping_reason == "model_limit"
    assert result.advanced_s == 0
    assert result.state.tick == original.tick
    np.testing.assert_array_equal(result.state.x, original.x)
    assert any("singular" in event.detail.lower() for event in result.events)
