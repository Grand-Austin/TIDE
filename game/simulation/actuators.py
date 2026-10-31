"""Son–Nomoto propeller/rudder loads from pinned MSS container.m, in SI/FSD.

Only the actuator terms are reused; the source's 4-DOF hull derivatives, mass
and GM are deliberately excluded. The source geometry and effective lever arms
are retained about CG. Their applicability to S175 design loading is uncalibrated.
Copyright Trygve Lauvdal / Thor I. Fossen, MIT; see data/vessels/s175/MSS-LICENSE.txt.
"""

import math

import numpy as np

LENGTH_M = 175.0
RUDDER_LIMIT_RAD = math.radians(10)
RUDDER_RATE_RAD_S = math.radians(5)
RPM_LIMIT = 160.0
PROPELLER_DIAMETER_M = 6.533
MAX_NORMALIZED_YAW = 1.5


def _inflow(velocity):
    velocity = np.asarray(velocity, dtype=float)
    if velocity.shape != (6,) or not np.all(np.isfinite(velocity)):
        raise ValueError("Actuator velocity must be a finite six-vector")
    u, v, _, _, _, r = velocity
    speed = math.hypot(u, v)
    if u < 0:
        raise ValueError("Son–Nomoto actuator has no validated astern-flow model")
    if speed == 0:
        if r != 0:
            raise ValueError("Son–Nomoto actuator is undefined at zero speed with yaw")
        return 0.0, 0.0
    r_scaled = r * LENGTH_M
    # shortcut: a numerical envelope bounds the source's 1/U² polynomial;
    # replace with a validated low-speed inflow model before extending this range.
    if abs(r_scaled) > MAX_NORMALIZED_YAW*speed:
        raise ValueError("Son–Nomoto inflow outside numerical envelope: abs(r)*L/U > 1.5")
    propeller = u * (0.816 + 1.09 * ((v - 0.526 * r_scaled) / speed)**2)
    rudder = 0.088*v - 0.156*r_scaled + (
        -0.275*r_scaled**3 + 1.96*r_scaled**2*v) / speed**2
    return propeller, rudder


def loads(velocity, rudder_rad, shaft_rpm, *, rho=1025.0):
    """Return [X,Y,Z,K,M,N] about CG, excluding all hull forces.

    Algebraically cancelling advance-ratio divisions gives the continuous
    straight-ahead limits at zero RPM/speed. Those limits extend the original
    positive-speed/RPM source; they are not an experimentally validated model.
    """
    if not all(math.isfinite(x) for x in (rudder_rad, shaft_rpm, rho)) or rho <= 0:
        raise ValueError("Actuator inputs must be finite with positive density")
    if shaft_rpm < 0:
        raise ValueError("Son–Nomoto actuator does not model reverse shaft rotation")
    propeller, lateral = _inflow(velocity)
    n_d = shaft_rpm / 60 * PROPELLER_DIAMETER_M
    # n^2*KT and uR^2 remain finite when the source's J tends to zero/infinity.
    thrust = rho * PROPELLER_DIAMETER_M**2 * (
        0.527*n_d**2 - 0.455*propeller*n_d)
    axial_squared = 0.921**2 * (propeller**2 + 8*0.631/math.pi * (
        0.527*n_d**2 - 0.455*propeller*n_d))
    axial = math.sqrt(axial_squared)
    angle = rudder_rad + math.atan2(lateral, axial)
    normal = -0.5*rho * (6.13*1.8219/(1.8219+2.25)) * 33.0376 * (
        axial_squared + lateral**2) * math.sin(angle)
    side = normal * math.cos(rudder_rad)
    return np.array([
        0.825*thrust + 0.71*normal*math.sin(rudder_rad),
        1.237*side, 0.0, -1.237*0.033*LENGTH_M*side, 0.0,
        (-0.5 - 0.237*0.48)*LENGTH_M*side,
    ])


def rpm_for_thrust(thrust_n, velocity, *, rho=1025.0):
    """Ahead RPM for a requested propeller thrust after thrust deduction.

    Rudder drag is left to speed feedback. A non-positive request coasts at
    zero RPM; there is no invented reverse thrust or constant-force brake.
    Returns an unclipped order so the controller can detect saturation.
    """
    if not all(math.isfinite(x) for x in (thrust_n, rho)) or rho <= 0:
        raise ValueError("Requested thrust must be finite with positive density")
    propeller, _ = _inflow(velocity)
    if thrust_n <= 0:
        return 0.0
    b = 0.455*propeller/PROPELLER_DIAMETER_M
    c = thrust_n/(0.825*rho*PROPELLER_DIAMETER_M**4)
    return 60*(b + math.sqrt(b*b + 4*0.527*c))/(2*0.527)


def rates(rudder_rad, shaft_rpm, rudder_order_rad, rpm_order):
    """Original MSS rudder/shaft servo equations; rates are rad/s and RPM/s."""
    if not all(math.isfinite(x) for x in (rudder_rad, shaft_rpm, rudder_order_rad, rpm_order)):
        raise ValueError("Actuator servo inputs must be finite")
    if shaft_rpm < 0:
        raise ValueError("Son–Nomoto actuator does not model reverse shaft rotation")
    rudder_order_rad = float(np.clip(rudder_order_rad, -RUDDER_LIMIT_RAD, RUDDER_LIMIT_RAD))
    rpm_order = float(np.clip(rpm_order, 0, RPM_LIMIT))
    rudder_rate = float(np.clip(rudder_order_rad-rudder_rad, -RUDDER_RATE_RAD_S, RUDDER_RATE_RAD_S))
    n = shaft_rpm / 60
    time_constant = 5.65/n if n > 0.3 else 18.83
    return np.array([rudder_rate, (rpm_order-shaft_rpm)/time_constant])
