"""Focused tests for the independent turning acceptance metrics and references."""

import math

import numpy as np
import pytest

from pipeline.validate_turning import (
    DEFAULT_MODEL,
    IMO_TURNING_LIMITS,
    REFERENCE_2021,
    REFERENCE_2025,
    analyze_track,
    circle_fit_diameter,
    compare_step_halving,
    course_projection,
    crossing_time,
    default_trial_specs,
    froude_length,
    froude_speed,
    froude_time,
    froude_yaw_rate,
    heading_rates_deg_s,
    integration_status,
    interpolate_at_time,
    rudder_response_lag,
    scaled_reference_2021,
    scaled_reference_2025,
    signed_peak,
    steady_window,
    symmetry_comparison,
    unwrap_turn_deg,
)


def test_unwrap_preserves_signed_turns_across_north():
    assert unwrap_turn_deg([350, 355, 0, 5], 350) == pytest.approx([0, 5, 10, 15])
    assert unwrap_turn_deg([10, 5, 0, 355], 10) == pytest.approx([0, -5, -10, -15])
    assert unwrap_turn_deg([0, 5, 10], 0) == pytest.approx([0, 5, 10])


def test_heading_rate_is_actual_unwrapped_derivative():
    rates = heading_rates_deg_s([0, 1, 2, 3], [350, 355, 0, 5])
    assert rates == pytest.approx([5, 5, 5, 5])
    rates = heading_rates_deg_s([0, 1, 2, 3], [10, 5, 0, 355])
    assert rates == pytest.approx([-5, -5, -5, -5])


def test_crossing_time_interpolates_and_never_extrapolates():
    times = [0, 1, 2, 3]
    turns = [0, 4, 12, 20]
    assert crossing_time(times, turns, 10) == pytest.approx(1 + (10 - 4) / (12 - 4))
    assert crossing_time(times, turns, 10.0) == pytest.approx(1.75)
    assert crossing_time(times, turns, 30) is None
    assert crossing_time(times, turns, -10) is None
    assert crossing_time(times, [-0.0, -4, -12, -20], -10) == pytest.approx(1.75)
    assert crossing_time(times, turns, 20) == pytest.approx(3.0)


def test_signed_peak_and_interpolation_keep_sign():
    assert signed_peak([0.1, -0.8, 0.7, -0.2]) == pytest.approx(-0.8)
    assert signed_peak([None, float("nan"), 0.3, -0.4]) == pytest.approx(-0.4)
    assert signed_peak([]) is None
    assert interpolate_at_time([0, 10, 20], [0.0, 10.0, 0.0], 5) == pytest.approx(5.0)
    assert interpolate_at_time([0, 10, 20], [0.0, 10.0, 0.0], 21) is None
    assert interpolate_at_time([0, 10, 20], [0.0, None, 0.0], 5) == pytest.approx(0.0)
    assert interpolate_at_time([0, 10], np.array([0.0, 10.0]), 5) == pytest.approx(5.0)


def test_steady_window_requires_a_real_180_degree_crossing():
    times = list(range(0, 401))
    turns = [min(180.0, time * 0.75) for time in times]
    window = steady_window(times, turns, window_s=120, settle_s=30)
    assert window is not None
    # 180 degrees is reached at 240 s; start = max(240 + 30, 400 - 120) = 280 s.
    assert window == pytest.approx((280.0, 400.0))
    assert steady_window(times, [0.0] * len(times)) is None
    short = steady_window([0, 100], [0, 50])
    assert short is None
    short_tail = steady_window(list(range(0, 301)),
                               [min(180.0, t * 0.75) for t in range(0, 301)])
    assert short_tail is None


def test_speed_loss_and_rudder_lag_helpers():
    times = [0, 1, 2, 3, 4, 5]
    actual = [0.0, 0.0, 3.5, 17.5, 31.5, 35.0]
    lag = rudder_response_lag(times, actual, [35.0] * len(times))
    assert lag["ordered_rudder_deg"] == pytest.approx(35.0)
    assert lag["first_motion_s"] == pytest.approx(2.0)
    assert lag["time_to_5pct_s"] == pytest.approx(2.0)
    assert lag["time_to_50pct_s"] == pytest.approx(3.0)
    assert lag["time_to_90pct_s"] == pytest.approx(4.0)
    start_zero = rudder_response_lag(times, actual, [0.0, 35.0, 35.0, 35.0, 35.0, 35.0])
    assert start_zero["ordered_rudder_deg"] == pytest.approx(35.0)
    assert start_zero["time_to_90pct_s"] == pytest.approx(4.0)
    assert rudder_response_lag(times, [None] * len(times), [35.0] * len(times)) is None


def test_course_projection_uses_initial_heading_axes():
    advance, transfer = course_projection([0, 100, 100], [0, 0, 20], 90)
    assert advance == pytest.approx([0, 100, 100])
    assert transfer == pytest.approx([0, 0, -20])
    advance, transfer = course_projection([0, 20], [0, 100], 0)
    assert advance == pytest.approx([0, 100])
    assert transfer == pytest.approx([0, 20])


def test_circle_fit_recovers_a_known_steady_turn_diameter():
    angles = np.linspace(0, 2 * math.pi, 200, endpoint=False)
    east = 10 + 50 * np.cos(angles)
    north = -5 + 50 * np.sin(angles)
    fit = circle_fit_diameter(east, north)
    assert fit["diameter_m"] == pytest.approx(100.0, abs=1e-9)
    assert fit["residual_rms_m"] == pytest.approx(0.0, abs=1e-9)
    assert circle_fit_diameter([0, 1], [0, 1]) is None


def test_analyze_track_recovers_an_analytic_steady_circle():
    speed = 6.214
    rate_deg_s = 0.75
    rate = math.radians(rate_deg_s)
    radius = speed / rate
    times = np.arange(0.0, 481.0, 1.0)
    headings = (rate_deg_s * times) % 360.0
    psi = np.radians(headings)
    east = radius * (1 - np.cos(psi))
    north = radius * np.sin(psi)
    analysis = analyze_track(
        times=times, east_m=east, north_m=north, headings_deg=headings,
        speed_mps=np.full_like(times, speed),
        roll_deg=np.full_like(times, 2.0),
        actual_rudder_deg=np.full_like(times, 35.0),
        ordered_rudder_deg=np.full_like(times, 35.0),
        initial_heading_deg=0.0,
    )
    assert analysis["time_to_10_deg_s"] == pytest.approx(10 / rate_deg_s)
    assert analysis["time_to_30_deg_s"] == pytest.approx(30 / rate_deg_s)
    assert analysis["time_to_90_deg_s"] == pytest.approx(90 / rate_deg_s)
    assert analysis["time_to_180_deg_s"] == pytest.approx(180 / rate_deg_s)
    assert analysis["peak_heading_rate_deg_s"] == pytest.approx(rate_deg_s)
    assert analysis["steady_heading_rate_deg_s"] == pytest.approx(rate_deg_s)
    assert analysis["advance_at_90_m"] == pytest.approx(radius, rel=1e-9)
    assert analysis["tactical_diameter_at_180_m"] == pytest.approx(2 * radius, rel=1e-9)
    assert analysis["steady_diameter"]["diameter_m"] == pytest.approx(2 * radius, rel=1e-9)
    assert analysis["speed_loss_final_mps"] == pytest.approx(0.0)
    assert analysis["speed_at_90_deg_mps"] == pytest.approx(speed)
    assert analysis["max_heel_deg"] == pytest.approx(2.0)
    assert [item["time_s"] for item in analysis["early_response"]] == [30.0, 60.0, 120.0]
    assert analysis["early_response"][2]["turn_deg"] == pytest.approx(90.0)


def test_analyze_track_reports_null_without_a_crossing():
    times = list(range(0, 101))
    analysis = analyze_track(
        times=times, east_m=[0.0] * len(times), north_m=[0.0] * len(times),
        headings_deg=[0.0] * len(times), speed_mps=[5.0] * len(times),
        initial_heading_deg=0.0,
    )
    assert analysis["time_to_10_deg_s"] is None
    assert analysis["time_to_90_deg_s"] is None
    assert analysis["advance_at_90_m"] is None
    assert analysis["tactical_diameter_at_180_m"] is None
    assert analysis["steady_diameter"] is None
    assert analysis["steady_heading_rate_deg_s"] is None


def test_froude_scaling_uses_published_relations():
    scale = REFERENCE_2021["scale"]
    assert froude_length(1, scale) == pytest.approx(scale)
    assert froude_time(1, scale) == pytest.approx(math.sqrt(scale))
    assert froude_speed(1, scale) == pytest.approx(math.sqrt(scale))
    assert froude_yaw_rate(1, scale) == pytest.approx(1 / math.sqrt(scale))

    scaled = scaled_reference_2021()
    right = scaled["right"]["full_scale"]
    left = scaled["left"]["full_scale"]
    assert right["time_to_90_s"] == pytest.approx(23.06 * math.sqrt(scale))
    assert left["time_to_90_s"] == pytest.approx(24.56 * math.sqrt(scale))
    assert 145.0 < right["time_to_90_s"] < 165.0
    assert 145.0 < left["time_to_90_s"] < 165.0
    assert right["time_to_180_s"] == pytest.approx(44.6 * math.sqrt(scale))
    assert right["advance_m"] == pytest.approx(14.3201 * scale)
    assert right["tactical_diameter_m"] == pytest.approx(17.5743 * scale)
    assert right["steady_diameter_m"] == pytest.approx(16.1718 * scale)


def test_2025_reference_stays_separate_and_scales_independently():
    scaled = scaled_reference_2025()
    assert scaled["scale"] == pytest.approx(57.689)
    assert scaled["tactical_diameter_m_full_scale"] == pytest.approx(4.51 * 175.0)
    assert scaled["steady_diameter_m_full_scale"] == pytest.approx(4.11 * 175.0)
    assert scaled["time_to_180_s_full_scale"] == pytest.approx(
        35.27 * math.sqrt(57.689))
    assert REFERENCE_2021["scale"] != REFERENCE_2025["scale"]
    assert REFERENCE_2021["name"] != REFERENCE_2025["name"]


def test_imo_values_are_comparison_limits_not_a_certification():
    assert IMO_TURNING_LIMITS["advance_limit_m"] == pytest.approx(787.5)
    assert IMO_TURNING_LIMITS["tactical_diameter_limit_m"] == pytest.approx(875.0)
    assert "not" in IMO_TURNING_LIMITS["note"]


def test_step_halving_checks_convergence_without_exact_equality():
    fine = {
        "time_to_90_deg_s": 120.0,
        "steady_diameter_m": 1000.0,
        "speed_loss_final_mps": 1.0,
    }
    close = {
        "time_to_90_deg_s": 121.0,
        "steady_diameter_m": 1010.0,
        "speed_loss_final_mps": 1.02,
    }
    divergent = {
        "time_to_90_deg_s": 200.0,
        "steady_diameter_m": 1010.0,
        "speed_loss_final_mps": 1.02,
    }
    assert compare_step_halving(fine, close)["passed"]
    result = compare_step_halving(fine, divergent)
    assert not result["passed"]
    assert not result["rows"]["time_to_90_deg_s"]["passed"]


def test_symmetry_comparison_uses_absolute_signed_rates():
    starboard = {
        "time_to_90_deg_s": 120.0,
        "peak_heading_rate_deg_s": 0.80,
        "steady_heading_rate_deg_s": 0.75,
        "steady_diameter": {"diameter_m": 1000.0},
    }
    port = {
        "time_to_90_deg_s": 121.0,
        "peak_heading_rate_deg_s": -0.79,
        "steady_heading_rate_deg_s": -0.74,
        "steady_diameter": {"diameter_m": 1010.0},
    }
    rows = {row["metric"]: row for row in symmetry_comparison(starboard, port)}
    assert rows["peak_heading_rate_deg_s"]["starboard"] == pytest.approx(0.80)
    assert rows["peak_heading_rate_deg_s"]["port"] == pytest.approx(0.79)
    assert rows["peak_heading_rate_deg_s"]["absolute_difference"] == pytest.approx(0.01)
    assert rows["time_to_90_deg_s"]["absolute_difference"] == pytest.approx(1.0)


def test_existing_mode_names_remain_accepted_and_default_specs_are_bounded():
    assert integration_status("son_nomoto")["status"] == "available"
    assert integration_status("teaching")["status"] == "available"
    son = default_trial_specs("son_nomoto")
    assert any(spec["rudder_deg"] == pytest.approx(10.0) for spec in son)
    assert not any(abs(spec.get("rudder_deg") or 0) > 10.0 for spec in son)
    assert any(spec["kind"] == "speed_pi_rudder" for spec in son)
    teaching = default_trial_specs("teaching")
    assert all(spec["kind"] == "heading" for spec in teaching)
    physical = default_trial_specs(DEFAULT_MODEL)
    assert {abs(spec["rudder_deg"]) for spec in physical
            if spec["kind"] != "heading"} == {35.0}
    assert {spec["speed_command_kn"] for spec in physical
            if spec["kind"] == "speed_pi_rudder"} == {10.0, 12.0}
    assert DEFAULT_MODEL == "s175_maneuvering"
