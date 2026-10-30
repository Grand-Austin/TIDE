from copy import deepcopy
import json
import math
from types import SimpleNamespace

import pytest

from game.ship import ShipDamageModel


def sample(**values):
    defaults = dict(time_s=0.05, roll_deg=0.0, pitch_deg=0.0,
                    longitudinal_acceleration_mps2=0.0, lateral_acceleration_mps2=0.0,
                    vertical_acceleration_mps2=0.0, angular_acceleration_x_rad_s2=0.0,
                    angular_acceleration_y_rad_s2=0.0, angular_velocity_x_rad_s=0.0,
                    angular_velocity_y_rad_s=0.0, angular_velocity_z_rad_s=0.0,
                    heading_deg=0.0, wave_from_deg=0.0, hs_m=0.0, tp_s=11.5,
                    velocity_north_through_water_mps=0.0, velocity_east_through_water_mps=0.0)
    return SimpleNamespace(**(defaults | values))


@pytest.fixture
def model():
    vessel = SimpleNamespace(main={"g": 9.81, "Lpp": 175.0, "B": 25.4})
    return ShipDamageModel(vessel, seed=1)


@pytest.mark.parametrize("values,cause", [
    ({"roll_deg": 65.0}, "roll_capsize"),
    ({"roll_deg": -65.0}, "roll_capsize"),
    ({"pitch_deg": 35.0}, "extreme_pitch"),
    ({"pitch_deg": -35.0}, "extreme_pitch"),
    ({"vertical_acceleration_mps2": 3 * 9.81}, "extreme_acceleration"),
    ({"longitudinal_acceleration_mps2": -2 * 9.81}, "extreme_acceleration"),
    ({"lateral_acceleration_mps2": 2 * 9.81}, "extreme_acceleration"),
    ({"angular_acceleration_y_rad_s2": 0.4}, "extreme_acceleration"),
])
def test_single_extreme_response_is_terminal_and_irreversible(model, values, cause):
    state = model.initial_state()
    events = model.advance(state, sample(**values), 0.05)
    assert events[0]["kind"] == "ship_lost"
    assert state["lost"] and state["cause"] == cause
    before = deepcopy(state)
    assert model.advance(state, sample(), 1.0) == []
    assert state == before


def test_point_acceleration_matches_all_four_rigid_body_points(model):
    s = sample(vertical_acceleration_mps2=-1.2,
               angular_acceleration_x_rad_s2=-0.03, angular_acceleration_y_rad_s2=0.02,
               angular_velocity_x_rad_s=0.05, angular_velocity_y_rad_s=-0.04,
               angular_velocity_z_rad_s=0.03, pitch_deg=35)
    expected = max(abs(s.vertical_acceleration_mps2 +
                       (s.angular_velocity_x_rad_s*s.angular_velocity_z_rad_s - s.angular_acceleration_y_rad_s2)*x +
                       (s.angular_velocity_y_rad_s*s.angular_velocity_z_rad_s + s.angular_acceleration_x_rad_s2)*y)
                   for x in (-78.75, 78.75) for y in (-10.16, 10.16)) / 9.81
    event = model.advance(model.initial_state(), s, 0.05)[0]
    assert event["hull_vertical_g"] == pytest.approx(expected)


def test_repeated_frames_do_not_repeat_the_short_roll_lottery(model):
    state = model.initial_state()
    model.advance(state, sample(roll_deg=36), 0.05)
    threshold = state["roll_limit_deg"]
    for _ in range(100):
        model.advance(state, sample(roll_deg=36), 0.05)
    assert state["roll_excursions"] == 1
    assert state["roll_limit_deg"] == threshold
    restored = json.loads(json.dumps(state))
    for target in (state, restored):
        model.advance(target, sample(roll_deg=20), 0.05)
        model.advance(target, sample(roll_deg=-36), 0.05)
    assert state == restored
    assert state["roll_excursions"] == 2
    assert state["roll_limit_deg"] != threshold
    assert "roll_limit_deg" not in model.summary(state)


def test_sampled_short_roll_distribution_and_monotonicity(model):
    limits = []
    for seed in range(5000):
        model.seed = seed
        limits.append(model._roll_limit(1))
    observed = [sum(limit <= angle for limit in limits) / len(limits)
                for angle in (40, 45, 50, 55, 60, 65)]
    assert observed == sorted(observed)
    assert observed == pytest.approx([0.005, 0.05, 0.30, 0.65, 0.90, 1.0], abs=0.02)


@pytest.mark.parametrize("dt", [0.05, 0.025])
def test_sustained_rolling_survives_json_restore_and_stops_at_same_time(model, dt):
    state = model.initial_state()
    # A strong sampled excursion survives its initial peak; exposure still accumulates.
    state["roll_limit_deg"] = 64.0
    # 40-degree constant roll now exhausts the budget in 30 seconds.
    steps = round(30 / dt)
    for tick in range(1, steps + 1):
        if tick == steps // 2:
            state = json.loads(json.dumps(state))
        events = model.advance(state, sample(time_s=tick*dt, roll_deg=40), dt)
        assert bool(events) == (tick == steps)
    assert state["cause"] == "sustained_rolling"
    assert state["time_s"] == pytest.approx(30)


def test_repeated_severe_motion_accumulates_but_ordinary_motion_does_not(model):
    state = model.initial_state()
    for tick in range(1, 1201):
        angle = 20 * math.sin(tick * 0.05)
        model.advance(state, sample(roll_deg=angle, pitch_deg=8,
                                    vertical_acceleration_mps2=0.7*9.81), 0.05)
    assert state["roll_dose"] == state["structural_dose"] == 0
    severe = sample(pitch_deg=20)
    for _ in range(600):
        model.advance(state, severe, 0.05)
    dose = state["structural_dose"]
    model.advance(state, sample(), 10)
    assert state["structural_dose"] == dose
    for _ in range(600):
        model.advance(state, severe, 0.05)
    assert state["cause"] == "structural_overload"


@pytest.mark.parametrize("values", [
    {"vertical_acceleration_mps2": 1.5*9.81},
    {"longitudinal_acceleration_mps2": 9.81},
    {"lateral_acceleration_mps2": -9.81},
])
def test_repeated_acceleration_can_fail_without_large_angles(model, values):
    state = model.initial_state()
    for _ in range(1200):
        model.advance(state, sample(**values), 0.05)
    assert state["cause"] == "structural_overload"


def following_sample(model, **values):
    critical = max(model.following_speed, 0.75 * model.g * 11.5 / (2 * math.pi))
    return sample(**(dict(heading_deg=180.0, hs_m=7.0,
                          velocity_north_through_water_mps=-2*critical) | values))


def test_thirty_degree_heel_accumulates_significant_risk_within_thirty_seconds(model):
    state = model.initial_state()
    assert model.advance(state, sample(roll_deg=30), 30) == []
    assert state["roll_dose"] == pytest.approx(0.25)


@pytest.mark.parametrize("dt", [0.05, 0.025])
def test_sustained_fast_following_seas_lose_ship_without_large_motion(model, dt):
    state = model.initial_state()
    s = following_sample(model)
    for tick in range(1, round(30 / dt) + 1):
        s.time_s = tick * dt
        events = model.advance(state, s, dt)
        assert bool(events) == (tick == round(30 / dt))
    assert state["cause"] == "following_sea_instability"
    assert state["time_s"] == pytest.approx(30)
    assert state["roll_dose"] == state["structural_dose"] == 0


@pytest.mark.parametrize("changes", [
    {"hs_m": 0.0}, {"heading_deg": 0.0}, {"heading_deg": 90.0},
    {"heading_deg": 134.9}, {"heading_deg": 225.1},
    {"tp_s": 5.0}, {"tp_s": 30.0},
    {"velocity_north_through_water_mps": 30.0},
    {"velocity_north_through_water_mps": 0.0, "velocity_east_through_water_mps": 30.0},
    {"velocity_north_through_water_mps": -5.0},
])
def test_high_speed_alone_or_unmatched_wave_geometry_does_not_create_following_risk(model, changes):
    state = model.initial_state()
    assert model.advance(state, following_sample(model, **changes), 1200) == []
    assert state["following_dose"] == 0


@pytest.mark.parametrize("heading", [135.0, 180.0, 225.0])
def test_following_and_quartering_directions_are_included(model, heading):
    s = following_sample(model, heading_deg=heading)
    assert model._following_load(s) == pytest.approx(1)


def test_risk_uses_actual_water_velocity_and_is_invariant_to_compass_rotation(model):
    a = following_sample(model)
    b = following_sample(model, heading_deg=270, wave_from_deg=90,
                         velocity_north_through_water_mps=0,
                         velocity_east_through_water_mps=a.velocity_north_through_water_mps)
    assert model._following_load(b) == pytest.approx(model._following_load(a))
    b.heading_deg = 630
    assert model._following_load(b) == pytest.approx(model._following_load(a))


def test_roughness_and_loss_of_directional_control_increase_following_risk(model):
    base = following_sample(model)
    mild = following_sample(model, hs_m=3.5)
    yawing = following_sample(model, angular_velocity_z_rad_s=math.radians(2))
    assert model._following_load(mild) == pytest.approx(0.5 * model._following_load(base))
    assert model._following_load(yawing) == pytest.approx(2 * model._following_load(base))


def test_following_dose_persists_through_quiet_water_and_json_restore(model):
    state = model.initial_state()
    model.advance(state, following_sample(model), 15)
    assert state["following_dose"] == pytest.approx(0.5)
    model.advance(state, sample(), 120)
    restored = json.loads(json.dumps(state))
    for target in (state, restored):
        events = model.advance(target, following_sample(model), 15)
        assert events[0]["detail"] == "following_sea_instability"
    assert restored == state


@pytest.mark.parametrize("values,dt", [({"roll_deg": float("nan")}, 0.05),
                                      ({"angular_acceleration_y_rad_s2": float("inf")}, 0.05),
                                      ({"velocity_north_through_water_mps": float("nan")}, 0.05),
                                      ({"hs_m": -1}, 0.05), ({"tp_s": 0}, 0.05),
                                      ({}, 0), ({}, float("nan"))])
def test_invalid_response_does_not_create_a_casualty_or_mutate_state(model, values, dt):
    state = model.initial_state()
    before = deepcopy(state)
    with pytest.raises(ValueError):
        model.advance(state, sample(**values), dt)
    assert state == before
