"""Independent S175 turning acceptance diagnostic.

This module measures actual calm-water turning from the common Simulation,
Settings, Command and Sample interfaces. It does not tune forces, clamp yaw
rate or command headings directly. The primary physical trial holds an
equilibrium shaft RPM constant while the rudder order is +/-35 degrees.

Scope limits:
- The 2021 self-propulsion reference is a 1:42.8 model result. Froude scaling
  is length * scale, time * sqrt(scale), speed * sqrt(scale), and yaw rate /
  sqrt(scale). Its full-scale 90-degree times are about 151-161 s.
- The 2025 MMG reference uses a different scale, loading and procedure. It is
  stored separately and is not merged with the 2021 values.
- The IMO advance and tactical-diameter values are comparison limits only.
  This diagnostic does not claim an IMO certification.
- A steady yaw-rate average is reported only after the trial has passed 180
  degrees. A missing crossing is reported as null, never extrapolated.

The default model name is ``s175_maneuvering``. Until the coordinated runtime
mode exists, the command reports ``pending_integration`` instead of inventing
motion results. Existing mode names remain selectable for baseline checks.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import importlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from game.simulation.environment import SeaState
from game.simulation.runtime import Command, Settings, Simulation
from game.simulation.vessel import load_s175

KNOT_MPS = 1852.0 / 3600.0
EARTH_RADIUS_M = 6_371_000.0
S175_LPP_M = 175.0
DEFAULT_MODEL = "s175_maneuvering"
EXISTING_MODELS = ("teaching", "son_nomoto")
CALM_START = datetime(2026, 1, 23, tzinfo=timezone.utc)
CALM_LON, CALM_LAT = -8.0, 47.0

# Froude scaling from the 1:42.8 model in Shang, Zhan & Liu (2021), Table 6.
REFERENCE_2021 = {
    "name": "2021 S175 self-propulsion, 1:42.8, model scale",
    "source": "Shang, Zhan & Liu, J. Mar. Sci. Eng. 2021, 9(9), 1017, Table 6",
    "doi": "10.3390/jmse9091017",
    "url": "https://www.mdpi.com/2077-1312/9/9/1017",
    "scale": 42.8,
    "initial_speed_kn_full_scale": 12.08,
    "rudder_deg": 35.0,
    "procedure": "Self-propulsion equilibrium; rudder to +/-35 deg; propeller speed held constant",
    "right_model": {
        "advance_m": 14.3201,
        "tactical_diameter_m": 17.5743,
        "steady_diameter_m": 16.1718,
        "positive_constant_distance_m": 7.5352,
        "time_to_90_s": 23.06,
        "time_to_180_s": 44.6,
    },
    "left_model": {
        "advance_m": 15.9413,
        "tactical_diameter_m": 17.9131,
        "steady_diameter_m": 17.0507,
        "positive_constant_distance_m": 7.5233,
        "time_to_90_s": 24.56,
        "time_to_180_s": 46.56,
    },
    "note": "Right is starboard (+35 deg); left is port (-35 deg). Values are model-scale.",
}

# Kept separate: different scale, loading condition and procedure.
REFERENCE_2025 = {
    "name": "2025 S175 3-DOF MMG open-water reference, 1:57.689",
    "source": "J. Mar. Sci. Eng. 2025, 13(8), 1578",
    "doi": "10.3390/jmse13081578",
    "scale": 57.689,
    "length_m_full_scale": S175_LPP_M,
    "model_time_to_180_s": 35.27,
    "tactical_diameter_L": 4.51,
    "steady_diameter_L": 4.11,
    "procedure": "Open-water MMG turning circle; different loading and procedure from the 2021 test",
    "note": "Diameter values are in ship lengths. Do not merge these values with the 2021 fixture.",
}

IMO_TURNING_LIMITS = {
    "length_m": S175_LPP_M,
    "advance_limit_m": 4.5 * S175_LPP_M,
    "tactical_diameter_limit_m": 5.0 * S175_LPP_M,
    "note": "Comparison limits only; this diagnostic is not an IMO certification.",
}

STEP_HALVING_TOLERANCES = {
    "time_to_10_deg_s": (1.0, 0.02),
    "time_to_30_deg_s": (1.0, 0.02),
    "time_to_90_deg_s": (2.0, 0.02),
    "time_to_180_deg_s": (3.0, 0.02),
    "peak_heading_rate_deg_s": (0.02, 0.03),
    "steady_heading_rate_deg_s": (0.015, 0.03),
    "advance_at_90_m": (10.0, 0.03),
    "tactical_diameter_at_180_m": (10.0, 0.03),
    "steady_diameter_m": (20.0, 0.03),
    "max_heel_deg": (0.5, 0.05),
    "speed_loss_final_mps": (0.05, 0.03),
}


class CalmWater:
    """Zero wave, wind and current environment for the acceptance trials."""

    def sample(self, time, lon, lat):
        return SeaState(0.0, 1.0, 0.0)


def froude_length(value_model: float, scale: float) -> float:
    return float(value_model) * float(scale)


def froude_time(value_model: float, scale: float) -> float:
    return float(value_model) * math.sqrt(float(scale))


def froude_speed(value_model: float, scale: float) -> float:
    return float(value_model) * math.sqrt(float(scale))


def froude_yaw_rate(value_model: float, scale: float) -> float:
    return float(value_model) / math.sqrt(float(scale))


def scaled_reference_2021() -> dict[str, Any]:
    """Return the 2021 model values and their Froude-scaled full-scale values."""
    scale = REFERENCE_2021["scale"]
    result = {key: value for key, value in REFERENCE_2021.items()
              if key not in ("right_model", "left_model")}
    for side in ("right_model", "left_model"):
        model = REFERENCE_2021[side]
        result[side.replace("_model", "")] = {
            "model": dict(model),
            "full_scale": {
                "advance_m": froude_length(model["advance_m"], scale),
                "tactical_diameter_m": froude_length(model["tactical_diameter_m"], scale),
                "steady_diameter_m": froude_length(model["steady_diameter_m"], scale),
                "positive_constant_distance_m": froude_length(
                    model["positive_constant_distance_m"], scale),
                "time_to_90_s": froude_time(model["time_to_90_s"], scale),
                "time_to_180_s": froude_time(model["time_to_180_s"], scale),
            },
        }
    return result


def scaled_reference_2025() -> dict[str, Any]:
    """Return the separate 2025 reference and its full-scale conversions."""
    scale = REFERENCE_2025["scale"]
    length = REFERENCE_2025["length_m_full_scale"]
    return {
        **REFERENCE_2025,
        "tactical_diameter_m_full_scale": REFERENCE_2025["tactical_diameter_L"] * length,
        "steady_diameter_m_full_scale": REFERENCE_2025["steady_diameter_L"] * length,
        "time_to_180_s_full_scale": froude_time(REFERENCE_2025["model_time_to_180_s"], scale),
    }


def reference_payload() -> dict[str, Any]:
    return {
        "reference_2021": scaled_reference_2021(),
        "reference_2025": scaled_reference_2025(),
        "imo_turning_limits": dict(IMO_TURNING_LIMITS),
    }


def _field(sample, name: str, default=None):
    if isinstance(sample, Mapping):
        return sample.get(name, default)
    return getattr(sample, name, default)


def _wrap_deg(value: float) -> float:
    return (float(value) + 180.0) % 360.0 - 180.0


def unwrap_turn_deg(headings: Sequence[float], initial_heading_deg: float | None = None) -> list[float]:
    """Return signed heading change from an initial heading, crossing north cleanly."""
    headings = [float(value) for value in headings]
    if not headings:
        return []
    initial = headings[0] if initial_heading_deg is None else float(initial_heading_deg)
    unwrapped = [initial + _wrap_deg(headings[0] - initial)]
    for heading in headings[1:]:
        unwrapped.append(unwrapped[-1] + _wrap_deg(heading - unwrapped[-1]))
    return [value - initial for value in unwrapped]


def heading_rates_deg_s(times: Sequence[float], headings: Sequence[float]) -> list[float | None]:
    """Signed actual heading derivative from unwrapped headings over timestamps."""
    times = [float(value) for value in times]
    headings = [float(value) for value in headings]
    if len(times) != len(headings):
        raise ValueError("times and headings must have equal length")
    if not times:
        return []
    turns = unwrap_turn_deg(headings)
    if len(times) == 1:
        return [None]
    rates: list[float | None] = []
    for index in range(len(times)):
        if index == 0:
            left, right = 0, 1
        elif index == len(times) - 1:
            left, right = index - 1, index
        else:
            left, right = index - 1, index + 1
        dt = times[right] - times[left]
        if dt <= 0 or not all(map(math.isfinite, (dt, turns[left], turns[right]))):
            rates.append(None)
        else:
            rates.append((turns[right] - turns[left]) / dt)
    return rates


def crossing_time(times: Sequence[float], turn_deg: Sequence[float], target_deg: float) -> float | None:
    """First interpolated time when the signed turn reaches the target, or None.

    A positive target requires a starboard crossing. A negative target requires
    a port crossing. The function never extrapolates beyond the samples.
    """
    times = [float(value) for value in times]
    turn_deg = [float(value) for value in turn_deg]
    if len(times) != len(turn_deg):
        raise ValueError("times and turn_deg must have equal length")
    if not times:
        return None
    target = float(target_deg)
    if target > 0 and turn_deg[0] >= target:
        return times[0]
    if target < 0 and turn_deg[0] <= target:
        return times[0]
    if target == 0:
        return times[0]
    for index in range(1, len(times)):
        before, after = turn_deg[index - 1], turn_deg[index]
        if target > 0 and before < target <= after:
            if after == before:
                return times[index]
            fraction = (target - before) / (after - before)
            return times[index - 1] + fraction * (times[index] - times[index - 1])
        if target < 0 and before > target >= after:
            if after == before:
                return times[index]
            fraction = (target - before) / (after - before)
            return times[index - 1] + fraction * (times[index] - times[index - 1])
    return None


def interpolate_at_time(times: Sequence[float], values: Sequence[float | None],
                        at_s: float | None) -> float | None:
    """Linear interpolation inside the sample interval; no extrapolation."""
    if at_s is None or len(values) == 0:
        return None
    times = [float(value) for value in times]
    if len(times) != len(values):
        raise ValueError("times and values must have equal length")
    if not math.isfinite(float(at_s)) or at_s < times[0] or at_s > times[-1]:
        return None
    pairs = [(time, float(value)) for time, value in zip(times, values)
             if value is not None and math.isfinite(float(value))]
    if not pairs:
        return None
    pair_times = [pair[0] for pair in pairs]
    pair_values = [pair[1] for pair in pairs]
    return float(np.interp(float(at_s), pair_times, pair_values))


def signed_peak(values: Sequence[float | None]) -> float | None:
    """Value with the largest magnitude, keeping its sign."""
    finite = [float(value) for value in values
              if value is not None and math.isfinite(float(value))]
    if not finite:
        return None
    return max(finite, key=abs)


def window_mean(times: Sequence[float], values: Sequence[float | None],
                window: tuple[float, float] | None) -> float | None:
    if window is None:
        return None
    selected = [float(value) for time, value in zip(times, values)
                if window[0] <= float(time) <= window[1]
                and value is not None and math.isfinite(float(value))]
    return float(np.mean(selected)) if selected else None


def steady_window(times: Sequence[float], turn_deg: Sequence[float], *,
                  min_turn_deg: float = 180.0, window_s: float = 120.0,
                  settle_s: float = 30.0,
                  min_window_s: float = 60.0) -> tuple[float, float] | None:
    """Last window after the 180-degree crossing plus a settling allowance.

    The window is ``[max(t_180 + settle_s, t_end - window_s), t_end]``. It is
    None when the trial never reaches +/-180 degrees or has no sustained time
    left to call the turn steady.
    """
    times = [float(value) for value in times]
    turn_deg = [float(value) for value in turn_deg]
    if not times or len(times) != len(turn_deg):
        return None
    if abs(turn_deg[-1]) < min_turn_deg:
        return None
    target = math.copysign(min_turn_deg, turn_deg[-1])
    reached = crossing_time(times, turn_deg, target)
    if reached is None:
        return None
    start = max(reached + settle_s, times[-1] - window_s)
    if times[-1] - start < min_window_s:
        return None
    return (start, times[-1])


def rudder_response_lag(times: Sequence[float], actual_deg: Sequence[float | None],
                        ordered_deg: Sequence[float | None], *,
                        order_time_s: float = 0.0,
                        thresholds: Sequence[float] = (0.05, 0.5, 0.9)) -> dict[str, Any] | None:
    """Time from the rudder order to actual-rudder movement thresholds."""
    if not any(value is not None for value in actual_deg):
        return None
    ordered = [(float(time), float(value)) for time, value in zip(times, ordered_deg)
               if value is not None and time >= order_time_s
               and math.isfinite(float(value)) and abs(float(value)) > 1e-9]
    if not ordered:
        return None
    command = max(ordered, key=lambda pair: abs(pair[1]))[1]
    if not math.isfinite(command) or abs(command) < 1e-9:
        return None
    sign = 1.0 if command > 0 else -1.0
    magnitude = abs(command)
    result: dict[str, Any] = {"ordered_rudder_deg": float(command)}

    def first_at(threshold: float) -> float | None:
        for time, actual in zip(times, actual_deg):
            if time < order_time_s or actual is None:
                continue
            if sign * float(actual) >= threshold:
                return float(time)
        return None

    result["first_motion_s"] = first_at(max(0.5, 0.02 * magnitude))
    for threshold in thresholds:
        name = f"time_to_{int(round(threshold * 100))}pct_s"
        result[name] = first_at(threshold * magnitude)
    return result


def course_projection(east_m: Sequence[float], north_m: Sequence[float],
                      initial_heading_deg: float) -> tuple[list[float], list[float]]:
    """Project local track points onto initial heading and starboard axes."""
    psi = math.radians(float(initial_heading_deg))
    forward_east, forward_north = math.sin(psi), math.cos(psi)
    right_east, right_north = math.cos(psi), -math.sin(psi)
    advance = [float(east) * forward_east + float(north) * forward_north
               for east, north in zip(east_m, north_m)]
    transfer = [float(east) * right_east + float(north) * right_north
                for east, north in zip(east_m, north_m)]
    return advance, transfer


def circle_fit_diameter(east_m: Sequence[float], north_m: Sequence[float]) -> dict[str, float] | None:
    """Algebraic circle fit of measured ground-track points."""
    x = np.asarray(east_m, dtype=float)
    y = np.asarray(north_m, dtype=float)
    if x.shape != y.shape or x.size < 3:
        return None
    finite = np.isfinite(x) & np.isfinite(y)
    x, y = x[finite], y[finite]
    if x.size < 3:
        return None
    matrix = np.column_stack([x, y, np.ones_like(x)])
    rhs = -(x * x + y * y)
    solution, _, rank, _ = np.linalg.lstsq(matrix, rhs, rcond=None)
    if rank < 3:
        return None
    d, e, f = (float(value) for value in solution)
    center_east, center_north = -d / 2, -e / 2
    radius_squared = center_east * center_east + center_north * center_north - f
    if not math.isfinite(radius_squared) or radius_squared <= 0:
        return None
    radius = math.sqrt(radius_squared)
    residual = float(np.sqrt(np.mean((np.hypot(x - center_east, y - center_north) - radius) ** 2)))
    return {
        "diameter_m": 2.0 * radius,
        "radius_m": radius,
        "residual_rms_m": residual,
        "point_count": int(x.size),
    }


def compare_step_halving(fine: Mapping[str, Any], coarse: Mapping[str, Any],
                         tolerances: Mapping[str, tuple[float, float]] | None = None) -> dict[str, Any]:
    """Compare coarse and fine time-step metrics without requiring exact equality."""
    tolerances = tolerances or STEP_HALVING_TOLERANCES
    rows: dict[str, Any] = {}
    all_passed = True
    for key, (abs_tolerance, rel_tolerance) in tolerances.items():
        fine_value = fine.get(key)
        coarse_value = coarse.get(key)
        if fine_value is None and coarse_value is None:
            rows[key] = {"fine": None, "coarse": None, "passed": True, "note": "both null"}
            continue
        if fine_value is None or coarse_value is None:
            rows[key] = {"fine": fine_value, "coarse": coarse_value, "passed": False,
                         "note": "one value is null"}
            all_passed = False
            continue
        fine_value, coarse_value = float(fine_value), float(coarse_value)
        difference = abs(fine_value - coarse_value)
        scale = max(abs(fine_value), abs(coarse_value))
        relative = difference / scale if scale > 0 else None
        allowed = abs_tolerance + rel_tolerance * scale
        passed = difference <= allowed
        rows[key] = {
            "fine": fine_value, "coarse": coarse_value,
            "absolute_difference": difference, "relative_difference": relative,
            "allowed_difference": allowed, "passed": passed,
        }
        all_passed = all_passed and passed
    return {"passed": all_passed, "rows": rows}


def flat_metrics(analysis: Mapping[str, Any]) -> dict[str, float | None]:
    """Flatten the main analysis values used for symmetry and step halving."""
    steady_fit = analysis.get("steady_diameter") or {}
    lag = analysis.get("rudder_response_lag") or {}
    return {
        "time_to_10_deg_s": analysis.get("time_to_10_deg_s"),
        "time_to_30_deg_s": analysis.get("time_to_30_deg_s"),
        "time_to_90_deg_s": analysis.get("time_to_90_deg_s"),
        "time_to_180_deg_s": analysis.get("time_to_180_deg_s"),
        "peak_heading_rate_deg_s": analysis.get("peak_heading_rate_deg_s"),
        "steady_heading_rate_deg_s": analysis.get("steady_heading_rate_deg_s"),
        "peak_body_yaw_rate_deg_s": analysis.get("peak_body_yaw_rate_deg_s"),
        "advance_at_90_m": analysis.get("advance_at_90_m"),
        "tactical_diameter_at_180_m": analysis.get("tactical_diameter_at_180_m"),
        "steady_diameter_m": steady_fit.get("diameter_m"),
        "max_heel_deg": analysis.get("max_heel_deg"),
        "speed_loss_final_mps": analysis.get("speed_loss_final_mps"),
        "rudder_time_to_5pct_s": lag.get("time_to_5pct_s"),
        "rudder_time_to_90pct_s": lag.get("time_to_90pct_s"),
    }


def symmetry_comparison(starboard: Mapping[str, Any], port: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Compare starboard and port trials using absolute signed quantities."""
    absolute_keys = {"peak_heading_rate_deg_s", "steady_heading_rate_deg_s",
                     "peak_body_yaw_rate_deg_s"}
    rows = []
    for key in sorted(set(flat_metrics(starboard)) | set(flat_metrics(port))):
        a = flat_metrics(starboard).get(key)
        b = flat_metrics(port).get(key)
        if a is None or b is None:
            rows.append({"metric": key, "starboard": a, "port": b,
                         "absolute_difference": None, "relative_difference": None})
            continue
        if key in absolute_keys:
            a, b = abs(float(a)), abs(float(b))
        else:
            a, b = float(a), float(b)
        difference = abs(a - b)
        scale = max(abs(a), abs(b))
        rows.append({
            "metric": key, "starboard": a, "port": b,
            "absolute_difference": difference,
            "relative_difference": difference / scale if scale > 0 else None,
        })
    return rows


def analyze_track(*, times: Sequence[float], east_m: Sequence[float],
                  north_m: Sequence[float], headings_deg: Sequence[float],
                  speed_mps: Sequence[float],
                  roll_deg: Sequence[float | None] | None = None,
                  actual_rudder_deg: Sequence[float | None] | None = None,
                  ordered_rudder_deg: Sequence[float | None] | None = None,
                  body_yaw_rate_deg_s: Sequence[float | None] | None = None,
                  initial_heading_deg: float | None = None,
                  order_time_s: float | None = None,
                  steady_window_s: float = 120.0) -> dict[str, Any]:
    """Compute turning metrics from an actual track and heading history."""
    times = [float(value) for value in times]
    east_m = [float(value) for value in east_m]
    north_m = [float(value) for value in north_m]
    headings_deg = [float(value) for value in headings_deg]
    speed_mps = [float(value) for value in speed_mps]
    if not times:
        raise ValueError("At least one sample is required")
    if not (len(times) == len(east_m) == len(north_m) == len(headings_deg) == len(speed_mps)):
        raise ValueError("Track arrays must have equal length")
    if any(before >= after for before, after in zip(times, times[1:])):
        raise ValueError("Track times must increase")
    order_time = times[0] if order_time_s is None else float(order_time_s)
    initial_heading = headings_deg[0] if initial_heading_deg is None else float(initial_heading_deg)

    turns = unwrap_turn_deg(headings_deg, initial_heading)
    rate_values = heading_rates_deg_s(times, headings_deg)
    rates = [np.nan if value is None else value for value in rate_values]
    direction = 1.0 if turns[-1] >= 0 else -1.0
    crossings = {target: crossing_time(times, turns, direction * target)
                 for target in (10, 30, 90, 180)}
    steady = steady_window(times, turns, window_s=steady_window_s)

    advance, transfer = course_projection(east_m, north_m, initial_heading)
    initial_speed = interpolate_at_time(times, speed_mps, order_time)
    final_speed = speed_mps[-1]
    steady_speed = window_mean(times, speed_mps, steady)
    speed_at = {target: interpolate_at_time(times, speed_mps, crossing)
                for target, crossing in crossings.items()}

    roll_values = list(roll_deg) if roll_deg is not None else None
    if roll_values is not None and len(roll_values) != len(times):
        raise ValueError("roll_deg must match the track length")
    if roll_values and any(value is not None for value in roll_values):
        finite_roll = [(time, float(value)) for time, value in zip(times, roll_values)
                       if value is not None and math.isfinite(float(value))]
        max_heel_time, max_heel_signed = max(finite_roll, key=lambda pair: abs(pair[1]))
        max_heel = abs(max_heel_signed)
    else:
        max_heel_time, max_heel_signed, max_heel = None, None, None

    steady_fit = None
    if steady is not None:
        selected = [index for index, time in enumerate(times)
                    if steady[0] <= time <= steady[1]]
        if len(selected) >= 3:
            steady_fit = circle_fit_diameter([east_m[index] for index in selected],
                                             [north_m[index] for index in selected])

    lag = None
    if actual_rudder_deg is not None and ordered_rudder_deg is not None:
        if len(actual_rudder_deg) != len(times) or len(ordered_rudder_deg) != len(times):
            raise ValueError("rudder arrays must match the track length")
        lag = rudder_response_lag(times, actual_rudder_deg, ordered_rudder_deg,
                                  order_time_s=order_time)

    body_peak = signed_peak(body_yaw_rate_deg_s) if body_yaw_rate_deg_s is not None else None
    body_steady = window_mean(times, body_yaw_rate_deg_s, steady) if body_yaw_rate_deg_s is not None else None

    early = []
    for target_time in (30.0, 60.0, 120.0):
        if target_time < times[0] or target_time > times[-1]:
            continue
        early.append({
            "time_s": target_time,
            "heading_deg": interpolate_at_time(times, headings_deg, target_time),
            "turn_deg": interpolate_at_time(times, turns, target_time),
            "heading_rate_deg_s": interpolate_at_time(times, rates, target_time),
            "speed_mps": interpolate_at_time(times, speed_mps, target_time),
            "east_m": interpolate_at_time(times, east_m, target_time),
            "north_m": interpolate_at_time(times, north_m, target_time),
            "advance_m": interpolate_at_time(times, advance, target_time),
            "transfer_m": interpolate_at_time(times, transfer, target_time),
            "roll_deg": interpolate_at_time(times, roll_values, target_time)
            if roll_values is not None else None,
            "rudder_deg": interpolate_at_time(times, list(actual_rudder_deg), target_time)
            if actual_rudder_deg is not None else None,
        })

    return {
        "sample_count": len(times),
        "duration_s": times[-1] - times[0],
        "order_time_s": order_time,
        "initial_heading_deg": initial_heading,
        "final_turn_deg": turns[-1],
        "direction": "starboard" if direction > 0 else "port",
        "time_to_10_deg_s": crossings[10],
        "time_to_30_deg_s": crossings[30],
        "time_to_90_deg_s": crossings[90],
        "time_to_180_deg_s": crossings[180],
        "peak_heading_rate_deg_s": signed_peak(rates),
        "steady_heading_rate_deg_s": window_mean(times, rates, steady),
        "steady_window_s": list(steady) if steady is not None else None,
        "steady_window_definition": "last window after 180 deg crossing plus 30 s settle, capped at 120 s with a 60 s minimum",
        "peak_body_yaw_rate_deg_s": body_peak,
        "steady_body_yaw_rate_deg_s": body_steady,
        "body_yaw_note": "body yaw is r from the six-DOF state; heading rate is the Euler derivative",
        "initial_speed_mps": initial_speed,
        "speed_at_10_deg_mps": speed_at[10],
        "speed_at_30_deg_mps": speed_at[30],
        "speed_at_90_deg_mps": speed_at[90],
        "speed_at_180_deg_mps": speed_at[180],
        "final_speed_mps": final_speed,
        "steady_speed_mps": steady_speed,
        "speed_loss_final_mps": (initial_speed - final_speed)
        if initial_speed is not None else None,
        "speed_loss_final_fraction": (initial_speed - final_speed) / initial_speed
        if initial_speed not in (None, 0) else None,
        "speed_loss_steady_mps": (initial_speed - steady_speed)
        if initial_speed is not None and steady_speed is not None else None,
        "advance_at_90_m": interpolate_at_time(times, advance, crossings[90]),
        "advance_at_180_m": interpolate_at_time(times, advance, crossings[180]),
        "transfer_at_180_m": interpolate_at_time(times, transfer, crossings[180]),
        "tactical_diameter_at_180_m": abs(interpolate_at_time(times, transfer, crossings[180]))
        if interpolate_at_time(times, transfer, crossings[180]) is not None else None,
        "steady_diameter": steady_fit,
        "max_heel_deg": max_heel,
        "max_heel_signed_deg": max_heel_signed,
        "max_heel_time_s": max_heel_time,
        "rudder_response_lag": lag,
        "observed_max_rudder_deg": max((abs(float(value)) for value in actual_rudder_deg
                                        if value is not None), default=None)
        if actual_rudder_deg is not None else None,
        "early_response": early,
    }


def analyze_samples(samples: Sequence, *, initial_heading_deg: float | None = None,
                    order_time_s: float | None = None,
                    steady_window_s: float = 120.0) -> dict[str, Any]:
    """Analyze common runtime Sample objects or mappings."""
    samples = list(samples)
    if not samples:
        raise ValueError("At least one sample is required")
    lon0 = float(_field(samples[0], "lon"))
    lat0 = float(_field(samples[0], "lat"))
    east = [(float(_field(sample, "lon")) - lon0) * math.radians(1.0)
            * EARTH_RADIUS_M * math.cos(math.radians(lat0)) for sample in samples]
    north = [(float(_field(sample, "lat")) - lat0) * math.radians(1.0)
             * EARTH_RADIUS_M for sample in samples]
    body = [_field(sample, "angular_velocity_z_rad_s") for sample in samples]
    body_deg = [math.degrees(float(value)) if value is not None else None for value in body]
    return analyze_track(
        times=[float(_field(sample, "time_s")) for sample in samples],
        east_m=east,
        north_m=north,
        headings_deg=[float(_field(sample, "heading_deg")) for sample in samples],
        speed_mps=[float(_field(sample, "speed_through_water_mps")) for sample in samples],
        roll_deg=[_field(sample, "roll_deg") for sample in samples],
        actual_rudder_deg=[_field(sample, "rudder_angle_deg") for sample in samples],
        ordered_rudder_deg=[_field(sample, "ordered_rudder_deg") for sample in samples],
        body_yaw_rate_deg_s=body_deg,
        initial_heading_deg=initial_heading_deg,
        order_time_s=order_time_s,
        steady_window_s=steady_window_s,
    )


def available_models() -> list[str]:
    result = []
    for model in (*EXISTING_MODELS, DEFAULT_MODEL):
        try:
            Settings(actuator_model=model)
        except (TypeError, ValueError):
            continue
        result.append(model)
    return result


def integration_status(model: str) -> dict[str, Any]:
    try:
        Settings(actuator_model=model)
    except (TypeError, ValueError) as error:
        return {
            "status": "pending_integration",
            "requested_model": model,
            "reason": str(error),
            "available_models": available_models(),
            "message": "The coordinated physical mode is not available yet; no turning outputs were fabricated.",
        }
    return {"status": "available", "requested_model": model}


def _model_rudder_limit_deg(model: str) -> float | None:
    if model == "son_nomoto":
        from game.simulation import actuators
        return math.degrees(actuators.RUDDER_LIMIT_RAD)
    if model == DEFAULT_MODEL:
        # Prefer a source constant exposed by the coordinated mode. The generic
        # original actuator limit stays untouched; the 35-degree contract is the
        # fallback and the observed rudder angle is also checked.
        candidates = (
            ("game.simulation.maneuvering", ("RUDDER_LIMIT_RAD", "RUDDER_LIMIT_DEG")),
            ("game.simulation.actuators", ("MANEUVERING_RUDDER_LIMIT_RAD",
                                            "MANEUVERING_RUDDER_LIMIT_DEG")),
        )
        for module_name, names in candidates:
            try:
                module = importlib.import_module(module_name)
            except ImportError:
                continue
            for name in names:
                value = getattr(module, name, None)
                if value is None or not math.isfinite(float(value)):
                    continue
                return math.degrees(abs(float(value))) if name.endswith("_RAD") else abs(float(value))
        return 35.0
    return None


def _slug(value: float) -> str:
    return f"{value:g}".replace(".", "p")


def default_trial_specs(model: str) -> list[dict[str, Any]]:
    limit = _model_rudder_limit_deg(model)
    specs: list[dict[str, Any]] = []
    if limit is not None and model != "teaching":
        for speed_kn in (12.08, 10.0):
            for sign, label in ((1, "starboard"), (-1, "port")):
                specs.append({
                    "name": f"full_rudder_{label}_{_slug(speed_kn)}kn_constant_shaft",
                    "kind": "constant_shaft",
                    "initial_speed_kn": speed_kn,
                    "speed_command_kn": speed_kn,
                    "heading_deg": 0.0,
                    "rudder_deg": sign * limit,
                })
        for speed_kn in (12.0, 10.0):
            for sign, label in ((1, "starboard"), (-1, "port")):
                specs.append({
                    "name": f"full_rudder_{label}_{_slug(speed_kn)}kn_speed_pi",
                    "kind": "speed_pi_rudder",
                    "initial_speed_kn": speed_kn,
                    "speed_command_kn": speed_kn,
                    "heading_deg": 0.0,
                    "rudder_deg": sign * limit,
                })
    for heading in (30.0, 90.0):
        specs.append({
            "name": f"heading_{_slug(heading)}_pid_10kn",
            "kind": "heading",
            "initial_speed_kn": 10.0,
            "speed_command_kn": 10.0,
            "heading_deg": heading,
            "rudder_deg": None,
        })
    return specs


def _settings(model: str, dt_s: float) -> Settings:
    return Settings(actuator_model=model, dt_s=dt_s,
                    wave_period_s=max(0.1, dt_s), record_period_s=1.0)


def _actual_shaft_rpm(state) -> float | None:
    for name in ("shaft_rpm", "shaft_order_rpm"):
        value = getattr(state.control, name, None)
        if value is not None and math.isfinite(float(value)) and float(value) > 0:
            return float(value)
    return None


def _run_trial(spec: Mapping[str, Any], *, model: str, dt_s: float, duration_s: float,
               settle_s: float, steady_window_s: float) -> dict[str, Any]:
    sim = Simulation(load_s175(), CalmWater(), wind_parameters=None,
                     settings=_settings(model, dt_s))
    initial_mps = float(spec["initial_speed_kn"]) * KNOT_MPS
    state = sim.initial_state(CALM_START, CALM_LON, CALM_LAT,
                              heading_deg=0.0, speed_mps=initial_mps)
    order_time = 0.0
    equilibrium_rpm = None
    settle_result = None
    if spec["kind"] == "constant_shaft":
        settle_result = sim.advance(state, Command(0.0, initial_mps), settle_s)
        if settle_result.stopping_reason != "duration_reached":
            raise RuntimeError(f"Equilibrium settle stopped: {settle_result.stopping_reason}")
        equilibrium_rpm = _actual_shaft_rpm(settle_result.state)
        if equilibrium_rpm is None:
            raise RuntimeError("The mode exposes no positive shaft RPM for a constant-shaft trial")
        # Start the measured turn at the requested initial speed with the
        # measured equilibrium shaft RPM already on the shaft.
        state = sim.initial_state(CALM_START, CALM_LON, CALM_LAT,
                                  heading_deg=0.0, speed_mps=initial_mps)
        state.control.shaft_rpm = equilibrium_rpm
        state.control.shaft_order_rpm = equilibrium_rpm
        rudder_order = math.radians(float(spec["rudder_deg"]))
        original_control = sim._control

        def forced_control(*args, **kwargs):
            candidate = args[0]
            force = original_control(*args, **kwargs)
            candidate.control.rudder_order_rad = rudder_order
            candidate.control.shaft_order_rpm = equilibrium_rpm
            return force

        sim._control = forced_control  # Diagnostic-only order override.
        command = Command(0.0, initial_mps)
    elif spec["kind"] == "speed_pi_rudder":
        rudder_order = math.radians(float(spec["rudder_deg"]))
        original_control = sim._control

        def forced_rudder(*args, **kwargs):
            candidate = args[0]
            force = original_control(*args, **kwargs)
            # The genuine speed PI keeps writing shaft_order_rpm.
            candidate.control.rudder_order_rad = rudder_order
            return force

        sim._control = forced_rudder  # Diagnostic-only rudder-order override.
        command = Command(0.0, float(spec["speed_command_kn"]) * KNOT_MPS)
    else:
        command = Command(float(spec["heading_deg"]),
                          float(spec["speed_command_kn"]) * KNOT_MPS)

    initial_sample = sim.observe(state)
    result = sim.advance(state, command, duration_s)
    samples = [initial_sample, *result.trajectory]
    if len(samples) > 1 and samples[0].time_s == samples[1].time_s:
        samples = samples[1:]
    analysis = analyze_samples(samples, initial_heading_deg=0.0, order_time_s=order_time,
                               steady_window_s=steady_window_s)
    warnings = []
    if model == DEFAULT_MODEL and spec["kind"] in ("constant_shaft", "speed_pi_rudder"):
        observed = analysis.get("observed_max_rudder_deg")
        if observed is None or observed < 0.98 * abs(float(spec["rudder_deg"])):
            warnings.append(
                f"Observed rudder {observed!r} deg does not reach the 35-degree source contract")
    payload = {
        "name": spec["name"],
        "model": model,
        "algorithm": sim.algorithm_id,
        "settings": asdict(sim.settings),
        "spec": dict(spec),
        "command": asdict(command),
        "initial_speed_mps": initial_mps,
        "equilibrium_shaft_rpm": equilibrium_rpm,
        "equilibrium_settle_s": settle_s if spec["kind"] == "constant_shaft" else None,
        "equilibrium_settle_stop": settle_result.stopping_reason if settle_result is not None else None,
        "stopping_reason": result.stopping_reason,
        "advanced_s": result.advanced_s,
        "warnings": warnings,
        "metrics": analysis,
        "trajectory": [asdict(sample) for sample in samples],
        "events": [asdict(event) for event in result.events],
    }
    return payload


def _summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "trajectory"}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Runtime actuator_model (default: {DEFAULT_MODEL})")
    parser.add_argument("--duration-s", type=float, default=900.0,
                        help="Measured trial duration after any equilibrium settle")
    parser.add_argument("--dt-s", type=float, default=0.05)
    parser.add_argument("--settle-s", type=float, default=60.0,
                        help="Straight self-propulsion settle before constant-shaft turns")
    parser.add_argument("--steady-window-s", type=float, default=120.0)
    parser.add_argument("--no-step-halving", action="store_true",
                        help="Skip the same primary trial at twice the time step")
    parser.add_argument("--reuse-existing", action="store_true",
                        help="Reuse complete trial JSON files already in the output directory")
    parser.add_argument("--output", type=Path,
                        default=Path("data/runs/turning-acceptance"))
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)

    status = integration_status(args.model)
    if status["status"] != "available":
        payload = {
            **status,
            "references": reference_payload(),
            "expected_trials": default_trial_specs(args.model),
            "scope": "No motion result is reported until the coordinated physical mode exists.",
        }
        destination = args.output / "pending-integration.json"
        destination.write_text(json.dumps(payload, indent=2) + "\n")
        print(json.dumps(payload, indent=2))
        return 2

    specs = default_trial_specs(args.model)
    if not specs:
        raise SystemExit(f"No supported trials for model {args.model!r}")

    trials: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, str]] = []
    for spec in specs:
        if spec["kind"] == "constant_shaft" and args.model == "teaching":
            skipped.append({"name": spec["name"],
                            "reason": "teaching mode has no physical shaft/RPM state"})
            continue
        trial_path = args.output / f"{spec['name']}.json"
        if args.reuse_existing and trial_path.exists():
            payload = json.loads(trial_path.read_text())
            if payload.get("model") != args.model or payload.get("name") != spec["name"]:
                raise ValueError(f"Cannot reuse mismatched trial file: {trial_path}")
            trials[payload["name"]] = _summary(payload)
            print(f"reused {payload['name']}", flush=True)
            continue
        payload = _run_trial(spec, model=args.model, dt_s=args.dt_s,
                             duration_s=args.duration_s, settle_s=args.settle_s,
                             steady_window_s=args.steady_window_s)
        trials[payload["name"]] = _summary(payload)
        trial_path.write_text(json.dumps(payload, indent=2) + "\n")
        print(f"completed {payload['name']}: {trials[payload['name']]['metrics']['final_turn_deg']:.2f} deg",
              flush=True)

    step_halving = None
    primary_name = "full_rudder_starboard_12p08kn_constant_shaft"
    if not args.no_step_halving and primary_name in trials:
        coarse_spec = next(spec for spec in specs if spec["name"] == primary_name)
        coarse_path = args.output / f"{primary_name}_dt{_slug(args.dt_s * 2)}.json"
        if args.reuse_existing and coarse_path.exists():
            coarse = json.loads(coarse_path.read_text())
        else:
            coarse = _run_trial(coarse_spec, model=args.model, dt_s=args.dt_s * 2,
                                duration_s=args.duration_s, settle_s=args.settle_s,
                                steady_window_s=args.steady_window_s)
            coarse_path.write_text(json.dumps(coarse, indent=2) + "\n")
        step_halving = compare_step_halving(
            flat_metrics(trials[primary_name]["metrics"]),
            flat_metrics(coarse["metrics"]),
        )
        step_halving["fine_dt_s"] = args.dt_s
        step_halving["coarse_dt_s"] = args.dt_s * 2

    symmetry = {}
    for speed in ("12p08kn", "10kn"):
        starboard = f"full_rudder_starboard_{speed}_constant_shaft"
        port = f"full_rudder_port_{speed}_constant_shaft"
        if starboard in trials and port in trials:
            symmetry[f"constant_shaft_{speed}"] = symmetry_comparison(
                trials[starboard]["metrics"], trials[port]["metrics"])
    for speed in ("12kn", "10kn"):
        starboard = f"full_rudder_starboard_{speed}_speed_pi"
        port = f"full_rudder_port_{speed}_speed_pi"
        if starboard in trials and port in trials:
            symmetry[f"speed_pi_{speed}"] = symmetry_comparison(
                trials[starboard]["metrics"], trials[port]["metrics"])

    normal_command_evidence = []
    for name, trial in trials.items():
        if trial["spec"]["kind"] != "speed_pi_rudder":
            continue
        metrics = trial["metrics"]
        normal_command_evidence.append({
            "trial": name,
            "rudder_deg": trial["spec"]["rudder_deg"],
            "ordered_speed_kn": trial["spec"]["speed_command_kn"],
            "initial_speed_mps": metrics["initial_speed_mps"],
            "speed_at_30_deg_mps": metrics["speed_at_30_deg_mps"],
            "speed_at_90_deg_mps": metrics["speed_at_90_deg_mps"],
            "speed_at_180_deg_mps": metrics["speed_at_180_deg_mps"],
            "final_speed_mps": metrics["final_speed_mps"],
            "steady_speed_mps": metrics["steady_speed_mps"],
            "steady_speed_kn": (None if metrics["steady_speed_mps"] is None
                                else metrics["steady_speed_mps"] * 3600 / 1852),
            "peak_heading_rate_deg_s": metrics["peak_heading_rate_deg_s"],
            "steady_heading_rate_deg_s": metrics["steady_heading_rate_deg_s"],
            "max_heel_deg": metrics["max_heel_deg"],
            "time_to_30_deg_s": metrics["time_to_30_deg_s"],
            "time_to_90_deg_s": metrics["time_to_90_deg_s"],
            "time_to_180_deg_s": metrics["time_to_180_deg_s"],
            "stopping_reason": trial["stopping_reason"],
        })

    warnings = [warning for trial in trials.values() for warning in trial["warnings"]]
    payload = {
        "status": "completed",
        "model": args.model,
        "algorithm": next(iter(trials.values()))["algorithm"],
        "settings": next(iter(trials.values()))["settings"],
        "scope": "Calm water, no wind/current; actual track/heading metrics; no IMO certification",
        "duration_s": args.duration_s,
        "dt_s": args.dt_s,
        "steady_window_s": args.steady_window_s,
        "references": reference_payload(),
        "trials": trials,
        "normal_command_evidence": normal_command_evidence,
        "skipped": skipped,
        "step_halving": step_halving,
        "symmetry": symmetry,
        "warnings": warnings,
    }
    (args.output / "report.json").write_text(json.dumps(payload, indent=2) + "\n")
    (args.output / "references.json").write_text(
        json.dumps(reference_payload(), indent=2) + "\n")
    print(json.dumps({"status": "completed", "model": args.model,
                      "trials": list(trials), "step_halving_passed":
                      None if step_halving is None else step_halving["passed"],
                      "warnings": warnings}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
