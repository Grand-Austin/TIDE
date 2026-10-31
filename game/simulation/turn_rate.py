"""Signed actual heading rate from six-DOF sample fields.

Body angular rates use the FSD axes: q is pitch rate and r is yaw rate. The
Euler heading rate is

    psi_dot = (sin(phi) * q + cos(phi) * r) / cos(theta)

where phi is roll, theta is pitch and positive psi_dot turns to starboard.
This is the actual heading rate, not a reference/order rate or raw body r.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence


def _field(sample, name: str, default=None):
    if isinstance(sample, Mapping):
        return sample.get(name, default)
    return getattr(sample, name, default)


def turn_rate_deg_s(roll_deg: float, pitch_deg: float,
                    angular_velocity_y_rad_s: float,
                    angular_velocity_z_rad_s: float) -> float:
    """Actual Euler heading rate in degrees/second (starboard positive)."""
    phi = math.radians(float(roll_deg))
    theta = math.radians(float(pitch_deg))
    q = float(angular_velocity_y_rad_s)
    r = float(angular_velocity_z_rad_s)
    cosine = math.cos(theta)
    if abs(cosine) < 1e-9:
        raise ValueError("Heading rate is singular near pitch = +/-90 degrees")
    return math.degrees((math.sin(phi) * q + math.cos(phi) * r) / cosine)


def sample_turn_rate_deg_s(sample) -> float | None:
    """Turn rate for one Sample (or mapping), or None if body rates are absent."""
    q = _field(sample, "angular_velocity_y_rad_s")
    r = _field(sample, "angular_velocity_z_rad_s")
    if q is None or r is None:
        return None
    roll = _field(sample, "roll_deg", 0.0)
    pitch = _field(sample, "pitch_deg", 0.0)
    return turn_rate_deg_s(0.0 if roll is None else roll,
                           0.0 if pitch is None else pitch, q, r)


def unwrap_headings_deg(headings: Sequence[float]) -> list[float]:
    """Unwrap a heading sequence into a continuous angle, crossing north cleanly."""
    if not headings:
        return []
    unwrapped = [float(headings[0])]
    for heading in headings[1:]:
        heading = float(heading)
        unwrapped.append(unwrapped[-1] + (heading - unwrapped[-1] + 180.0) % 360.0 - 180.0)
    return unwrapped


def heading_turn_rates_deg_s(samples: Sequence) -> list[float | None]:
    """Historical fallback: derive rates from heading/time with angle unwrap."""
    samples = list(samples)
    if len(samples) < 2:
        return [None] * len(samples)
    try:
        times = [float(_field(sample, "time_s")) for sample in samples]
        headings = [float(_field(sample, "heading_deg")) for sample in samples]
    except (TypeError, ValueError):
        return [None] * len(samples)
    if not all(map(math.isfinite, times + headings)):
        return [None] * len(samples)
    unwrapped = unwrap_headings_deg(headings)
    rates: list[float | None] = []
    for i in range(len(samples)):
        left = i - 1 if i > 0 else i
        right = i + 1 if i + 1 < len(samples) else i
        dt = times[right] - times[left]
        rates.append((unwrapped[right] - unwrapped[left]) / dt if dt > 0 else None)
    return rates


def turn_rate_series(samples: Sequence) -> list[float | None]:
    """Per-sample body-rate values, filling historical gaps from the headings."""
    samples = list(samples)
    rates = [sample_turn_rate_deg_s(sample) for sample in samples]
    if any(rate is None for rate in rates):
        fallback = heading_turn_rates_deg_s(samples)
        rates = [fallback[i] if rate is None else rate for i, rate in enumerate(rates)]
    return rates


# Readable aliases for callers that name the quantity rather than the module.
heading_rate_deg_s = turn_rate_deg_s
sample_turn_rate = sample_turn_rate_deg_s
turn_rates_from_samples = turn_rate_series
