"""Low-frequency MMG/Son--Nomoto hull force for the S175 six-DOF runtime.

This module supplies only the horizontal manoeuvring hull force. It does not
supply mass, added mass, Coriolis, hydrostatic restoring, wave damping or any
actuator force. The two-time-scale caller supplies native manoeuvring inertia
and S175 roll restoring, and keeps the prepared S175 matrices for wave-frequency
motion, so those physical terms are not duplicated.

The nondimensional coefficients are the Son--Nomoto four-DOF container-ship
coefficients used by MSS ``CRAFT/SHIP/models/container.m``. They also appear in
Table 2 of the S175 MMG study by J. Mar. Sci. Eng. 2025, 13(8), 1578. The
original source rudder limit is 10 degrees. ``RUDDER_LIMIT_RAD`` gives the
35-degree extension used by the cited full-rudder experiments. That extension
is a separate variant, not a reproduction of the original 10-degree source.

The source loading is about 21,222 m3 displacement, 8.5 m mean draft and 0.3 m
GM. The S175 prepared data uses 24,009 m3, 9.5 m draft and 0.996 m GM. This
module therefore keeps the source hull derivatives but applies them to the
S175 length and density. The caller supplies the S175 inertia and restoring
matrices. This loading mismatch is deliberate and remains an uncertainty.

Copyright for the source model: Trygve Lauvdal and Thor I. Fossen, MIT; see
``data/vessels/s175/MSS-LICENSE.txt``.
"""

from __future__ import annotations

import math

import numpy as np

SOURCE_COMMIT = "ce0b40ed101411fc4eebd064b35c5a96916106ad"
SOURCE_FILES = (
    "CRAFT/SHIP/models/container.m",
    "CRAFT/SHIP/SIMcontainer.m",
)
SOURCE_DOI = "10.3390/jmse13081578"
SOURCE_REFERENCE = (
    "Son and Nomoto (1982), coupled steering and rolling of a high-speed "
    "container ship; S175 MMG coefficient table in J. Mar. Sci. Eng. 2025, "
    "13(8), 1578, Table 2"
)

LENGTH_M = 175.0
BEAM_M = 25.4
DRAFT_M = 9.5
ORIGINAL_RUDDER_LIMIT_RAD = math.radians(10.0)
RUDDER_LIMIT_RAD = math.radians(35.0)
RUDDER_LIMIT_DEG = 35.0
RUDDER_RATE_RAD_S = math.radians(5.0)
RPM_LIMIT = 160.0

# Source nondimensional coefficients. Names follow container.m and the MMG
# table. They include the nonlinear sway/yaw/roll interaction terms and the
# rudder-free hull terms only.
COEFFICIENTS = {
    "Xuu": -0.0004226,
    "Xvr": -0.00311,
    "Xrr": 0.00020,
    "Xphiphi": -0.00020,
    "Xvv": -0.00386,
    "Kv": 0.0003026,
    "Kr": -0.000063,
    "Kp": -0.0000075,
    "Kphi": -0.000021,
    "Kvvv": 0.002843,
    "Krrr": -0.0000462,
    "Kvvr": -0.000588,
    "Kvrr": 0.0010565,
    "Kvvphi": -0.0012012,
    "Kvphiphi": -0.0000793,
    "Krrphi": -0.000243,
    "Krphiphi": 0.00003569,
    "Yv": -0.0116,
    "Yr": 0.00242,
    "Yp": 0.0,
    "Yphi": -0.000063,
    "Yvvv": -0.109,
    "Yrrr": 0.00177,
    "Yvvr": 0.0214,
    "Yvrr": -0.0405,
    "Yvvphi": 0.04605,
    "Yvphiphi": 0.00304,
    "Yrrphi": 0.009325,
    "Yrphiphi": -0.001368,
    "Nv": -0.0038545,
    "Nr": -0.00222,
    "Np": 0.000213,
    "Nphi": -0.0001424,
    "Nvvv": 0.001492,
    "Nrrr": -0.00229,
    "Nvvr": -0.0424,
    "Nvrr": 0.00156,
    "Nvvphi": -0.019058,
    "Nvphiphi": -0.0053766,
    "Nrrphi": -0.0038592,
    "Nrphiphi": 0.0024195,
}


def _main_scalar(main, key: str, default=None) -> float:
    value = main.get(key, default)
    if value is None or not math.isfinite(float(value)) or float(value) <= 0:
        raise ValueError(f"main[{key!r}] must be finite and positive")
    return float(value)


def hull_force(nu, roll_rad: float, main) -> np.ndarray:
    """Return the rudder-free MMG hull force ``[X, Y, 0, K, 0, N]`` in SI.

    ``nu`` is ``[u, v, w, p, q, r]`` about the S175 CG in FSD axes. The force is
    positive forward/starboard/down and the moments are about the CG. Pass the
    water-relative horizontal velocity. ``main`` supplies ``Lpp`` and ``rho``.
    ``roll_rad`` is the Euler roll angle.

    The function excludes rigid-body and added-mass Coriolis, all damping
    matrices, hydrostatic ``G`` restoring (including the source ``W*GM*phi``
    roll term), wave/wind forces and propeller/rudder loads. It has no astern
    model: ``u < 0`` raises ``ValueError`` rather than extrapolating.
    """
    velocity = np.asarray(nu, dtype=float)
    if velocity.shape != (6,) or not np.all(np.isfinite(velocity)):
        raise ValueError("nu must be a finite six-vector")
    if not math.isfinite(roll_rad):
        raise ValueError("roll_rad must be finite")
    u, v, _w, p, _q, r = velocity
    if u < 0:
        raise ValueError("MMG/Son-Nomoto hull force has no validated astern model")
    length = _main_scalar(main, "Lpp", LENGTH_M)
    density = _main_scalar(main, "rho", 1025.0)
    speed = math.hypot(u, v)
    if speed < 1e-12:
        return np.zeros(6)

    un, vn = u / speed, v / speed
    pn, rn = p * length / speed, r * length / speed
    phi = float(roll_rad)
    c = COEFFICIENTS

    x_n = (
        c["Xuu"] * un**2
        + c["Xvr"] * vn * rn
        + c["Xvv"] * vn**2
        + c["Xrr"] * rn**2
        + c["Xphiphi"] * phi**2
    )
    y_n = (
        c["Yv"] * vn
        + c["Yr"] * rn
        + c["Yp"] * pn
        + c["Yphi"] * phi
        + c["Yvvv"] * vn**3
        + c["Yrrr"] * rn**3
        + c["Yvvr"] * vn**2 * rn
        + c["Yvrr"] * vn * rn**2
        + c["Yvvphi"] * vn**2 * phi
        + c["Yvphiphi"] * vn * phi**2
        + c["Yrrphi"] * rn**2 * phi
        + c["Yrphiphi"] * rn * phi**2
    )
    k_n = (
        c["Kv"] * vn
        + c["Kr"] * rn
        + c["Kp"] * pn
        + c["Kphi"] * phi
        + c["Kvvv"] * vn**3
        + c["Krrr"] * rn**3
        + c["Kvvr"] * vn**2 * rn
        + c["Kvrr"] * vn * rn**2
        + c["Kvvphi"] * vn**2 * phi
        + c["Kvphiphi"] * vn * phi**2
        + c["Krrphi"] * rn**2 * phi
        + c["Krphiphi"] * rn * phi**2
    )
    n_n = (
        c["Nv"] * vn
        + c["Nr"] * rn
        + c["Np"] * pn
        + c["Nphi"] * phi
        + c["Nvvv"] * vn**3
        + c["Nrrr"] * rn**3
        + c["Nvvr"] * vn**2 * rn
        + c["Nvrr"] * vn * rn**2
        + c["Nvvphi"] * vn**2 * phi
        + c["Nvphiphi"] * vn * phi**2
        + c["Nrrphi"] * rn**2 * phi
        + c["Nrphiphi"] * rn * phi**2
    )
    force_scale = 0.5 * density * length**2 * speed**2
    moment_scale = force_scale * length
    return np.array(
        [x_n * force_scale, y_n * force_scale, 0.0,
         k_n * moment_scale, 0.0, n_n * moment_scale],
        dtype=float,
    )


def servo_rates(rudder_rad: float, shaft_rpm: float, rudder_order_rad: float,
                shaft_order_rpm: float, *, rudder_limit_rad: float = RUDDER_LIMIT_RAD,
                rudder_rate_rad_s: float = RUDDER_RATE_RAD_S,
                rpm_limit: float = RPM_LIMIT) -> np.ndarray:
    """Return source-style rudder/RPM servo rates for a bounded 35-degree order.

    The equations are the original MSS first-order servos. Only the rudder
    limit differs from ``game.simulation.actuators``: this function exposes the
    separate 35-degree variant explicitly. It does not add force or yaw-rate
    limits.
    """
    values = (rudder_rad, shaft_rpm, rudder_order_rad, shaft_order_rpm,
              rudder_limit_rad, rudder_rate_rad_s, rpm_limit)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Servo inputs must be finite")
    if shaft_rpm < 0:
        raise ValueError("The source servo has no reverse-shaft model")
    if rudder_limit_rad <= 0 or rudder_rate_rad_s <= 0 or rpm_limit <= 0:
        raise ValueError("Servo limits must be positive")
    order = float(np.clip(rudder_order_rad, -rudder_limit_rad, rudder_limit_rad))
    shaft_order = float(np.clip(shaft_order_rpm, 0.0, rpm_limit))
    rudder_rate = float(np.clip(order - rudder_rad,
                                -rudder_rate_rad_s, rudder_rate_rad_s))
    revolutions = shaft_rpm / 60.0
    time_constant = 5.65 / revolutions if revolutions > 0.3 else 18.83
    return np.array([rudder_rate, (shaft_order - shaft_rpm) / time_constant])
