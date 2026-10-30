"""Independent 4-DOF reference port of pinned MSS container.m (MIT).

Son–Nomoto (1982); source authors Trygve Lauvdal and Thor I. Fossen.
State: [u,v,r,N,E,psi,p,phi,delta,RPM]; orders: [rudder radians,RPM].
Source coefficients, signs and servo limits are unchanged. Positive speed and
actual RPM are required. Heave, pitch, wind, waves and current are absent.
This reference is not the six-DOF runtime actuator; source hashes are below.
"""

from __future__ import annotations

import math

import numpy as np

SOURCE_COMMIT = "ce0b40ed101411fc4eebd064b35c5a96916106ad"
SOURCE_SHA256 = {
    "CRAFT/SHIP/models/container.m": (
        "c2de8f29268ae239b9856c27ee8da6901329e449e809d81c9763047f843d3df7"
    ),
    "LIBRARY/mssConstants.m": (
        "6855f41a56d9da49cb26a0c81fce7f07eea9e94882afc75fd7ac27b37450a4ce"
    ),
    "CRAFT/SHIP/SIMcontainer.m": (
        "c03c7d60e6b4f2ef8058a9f003d11ee0e72800b5e7211f861c3fa0ef5fbfb8be"
    ),
    "LICENSE": "922bb433450a9e39368009c29db7b07857520b8e3aad20797f5dc082df6dcce1",
}
SOURCE_LICENSE = "MIT"


def _validated(x, orders) -> tuple[np.ndarray, np.ndarray]:
    state = np.asarray(x, dtype=float)
    controls = np.asarray(orders, dtype=float)
    if state.shape != (10,):
        raise ValueError("x must be a 10-vector [u, v, r, N, E, psi, p, phi, delta, n]")
    if controls.shape != (2,):
        raise ValueError("orders must be a 2-vector [delta_c, n_c]")
    if not np.all(np.isfinite(state)):
        raise ValueError("x must contain only finite values")
    if not np.all(np.isfinite(controls)):
        raise ValueError("orders must contain only finite values")
    return state, controls


def _saturate(value: float, limit: float) -> float:
    """Apply the source's inclusive absolute-value saturation."""
    if abs(value) >= limit:
        return math.copysign(limit, value)
    return value


def _evaluate(x, orders) -> tuple[np.ndarray, dict[str, float]]:
    """Evaluate the source derivative and the SI actuator components."""
    state, controls = _validated(x, orders)
    u, v, r, _, _, psi, p, phi, delta, rpm = state
    delta_c, rpm_c = controls

    # Normalization variables and source applicability checks.
    L = 175.0
    U = math.sqrt(u**2 + v**2)
    if U <= 0:
        raise ValueError("the ship must have speed greater than zero")
    if rpm <= 0:
        raise ValueError("the propeller RPM must be greater than zero")

    delta_max = math.radians(10.0)
    delta_rate_max = math.radians(5.0)
    n_max = 160.0

    # Nondimensional states and commanded shaft speed.
    n_c = rpm_c / 60.0 * L / U
    un = u / U
    vn = v / U
    pn = p * L / U
    rn = r * L / U
    phi_n = phi
    psi_n = psi
    delta_n = delta
    n = rpm / 60.0 * L / U

    # Parameters, hydrodynamic derivatives and main dimensions.
    m = 0.00792
    mx = 0.000238
    my = 0.007049
    alphay = 0.05
    lx = 0.0313
    ly = 0.0313
    Ix = 0.0000176
    Iz = 0.000456
    Jx = 0.0000034
    Jz = 0.000419
    g = 9.81
    rho = 1025.0

    nabla = 21222
    AR = 33.0376
    Delta = 1.8219
    D = 6.533
    GM = 0.3 / L
    t = 0.175

    W = rho * g * nabla / (rho * L**2 * U**2 / 2)

    Xuu = -0.0004226
    Xvr = -0.00311
    Xrr = 0.00020
    Xphiphi = -0.00020
    Xvv = -0.00386

    Kv = 0.0003026
    Kr = -0.000063
    Kp = -0.0000075
    Kphi = -0.000021
    Kvvv = 0.002843
    Krrr = -0.0000462
    Kvvr = -0.000588
    Kvrr = 0.0010565
    Kvvphi = -0.0012012
    Kvphiphi = -0.0000793
    Krrphi = -0.000243
    Krphiphi = 0.00003569

    Yv = -0.0116
    Yr = 0.00242
    Yp = 0
    Yphi = -0.000063
    Yvvv = -0.109
    Yrrr = 0.00177
    Yvvr = 0.0214
    Yvrr = -0.0405
    Yvvphi = 0.04605
    Yvphiphi = 0.00304
    Yrrphi = 0.009325
    Yrphiphi = -0.001368

    Nv = -0.0038545
    Nr = -0.00222
    Np = 0.000213
    Nphi = -0.0001424
    Nvvv = 0.001492
    Nrrr = -0.00229
    Nvvr = -0.0424
    Nvrr = 0.00156
    Nvvphi = -0.019058
    Nvphiphi = -0.0053766
    Nrrphi = -0.0038592
    Nrphiphi = 0.0024195

    kk = 0.631
    epsilon = 0.921
    xR = -0.5
    wp = 0.184
    tau = 1.09
    xp = -0.526
    cpv = 0.0
    cpr = 0.0
    ga = 0.088
    cRr = -0.156
    cRrrr = -0.275
    cRrrv = 1.96
    cRX = 0.71
    aH = 0.237
    zR = 0.033
    xH = -0.48

    # Masses and moments of inertia.
    m11 = m + mx
    m22 = m + my
    m32 = -my * ly
    m42 = my * alphay
    m33 = Ix + Jx
    m44 = Iz + Jz

    # Rudder saturation and dynamics.
    delta_c = _saturate(delta_c, delta_max)
    delta_dot = _saturate(delta_c - delta_n, delta_rate_max)

    # Shaft-speed saturation and dynamics.  The source restores rev/s here.
    n_c = n_c * U / L
    n = n * U / L
    n_c = _saturate(n_c, n_max / 60.0)
    if n > 0.3:
        Tm = 5.65 / n
    else:
        Tm = 18.83
    n_dot = 1.0 / Tm * (n_c - n) * 60.0

    # Propeller and rudder flow state.
    vR = ga * vn + cRr * rn + cRrrr * rn**3 + cRrrv * rn**2 * vn
    uP = un * (
        (1.0 - wp) + tau * ((vn + xp * rn) ** 2 + cpv * vn + cpr * rn)
    )
    if uP == 0:
        raise ValueError("source propeller advance ratio is undefined at zero axial inflow")
    J = uP * U / (n * D)
    KT = 0.527 - 0.455 * J
    uR = uP * epsilon * math.sqrt(1.0 + 8.0 * kk * KT / (math.pi * J**2))
    alphaR = delta_n + math.atan(vR / uR)
    FN = (
        -((6.13 * Delta) / (Delta + 2.25))
        * (AR / L**2)
        * (uR**2 + vR**2)
        * math.sin(alphaR)
    )
    thrust = 2.0 * rho * D**4 / (U**2 * L**2 * rho) * KT * n * abs(n)

    # Forces and moments.
    X = (
        Xuu * un**2
        + (1.0 - t) * thrust
        + Xvr * vn * rn
        + Xvv * vn**2
        + Xrr * rn**2
        + Xphiphi * phi_n**2
        + cRX * FN * math.sin(delta_n)
        + (m + my) * vn * rn
    )
    Y = (
        Yv * vn
        + Yr * rn
        + Yp * pn
        + Yphi * phi_n
        + Yvvv * vn**3
        + Yrrr * rn**3
        + Yvvr * vn**2 * rn
        + Yvrr * vn * rn**2
        + Yvvphi * vn**2 * phi_n
        + Yvphiphi * vn * phi_n**2
        + Yrrphi * rn**2 * phi_n
        + Yrphiphi * rn * phi_n**2
        + (1.0 + aH) * FN * math.cos(delta_n)
        - (m + mx) * un * rn
    )
    K = (
        Kv * vn
        + Kr * rn
        + Kp * pn
        + Kphi * phi_n
        + Kvvv * vn**3
        + Krrr * rn**3
        + Kvvr * vn**2 * rn
        + Kvrr * vn * rn**2
        + Kvvphi * vn**2 * phi_n
        + Kvphiphi * vn * phi_n**2
        + Krrphi * rn**2 * phi_n
        + Krphiphi * rn * phi_n**2
        - (1.0 + aH) * zR * FN * math.cos(delta_n)
        + mx * lx * un * rn
        - W * GM * phi_n
    )
    N = (
        Nv * vn
        + Nr * rn
        + Np * pn
        + Nphi * phi_n
        + Nvvv * vn**3
        + Nrrr * rn**3
        + Nvvr * vn**2 * rn
        + Nvrr * vn * rn**2
        + Nvvphi * vn**2 * phi_n
        + Nvphiphi * vn * phi_n**2
        + Nrrphi * rn**2 * phi_n
        + Nrphiphi * rn * phi_n**2
        + (xR + aH * xH) * FN * math.cos(delta_n)
    )

    detM = m22 * m33 * m44 - m32**2 * m44 - m42**2 * m33
    xdot = np.array(
        [
            X * (U**2 / L) / m11,
            -(
                (-m33 * m44 * Y + m32 * m44 * K + m42 * m33 * N)
                / detM
            )
            * (U**2 / L),
            (
                (-m42 * m33 * Y + m32 * m42 * K + N * m22 * m33 - N * m32**2)
                / detM
            )
            * (U**2 / L**2),
            (math.cos(psi_n) * un - math.sin(psi_n) * math.cos(phi_n) * vn) * U,
            (math.sin(psi_n) * un + math.cos(psi_n) * math.cos(phi_n) * vn) * U,
            math.cos(phi_n) * rn * (U / L),
            (
                (-m32 * m44 * Y + K * m22 * m44 - K * m42**2 + m32 * m42 * N)
                / detM
            )
            * (U**2 / L**2),
            pn * (U / L),
            delta_dot,
            n_dot,
        ],
        dtype=float,
    )

    # Source normalized-force convention: force / (0.5*rho*L^2*U^2) and
    # moment / (0.5*rho*L^3*U^2).  The rudder normal force is FN.
    scale_force = 0.5 * rho * L**2 * U**2
    scale_moment = scale_force * L
    actuator = {
        "propeller_thrust_n": thrust * scale_force,
        "rudder_normal_force_n": FN * scale_force,
        "rudder_surge_force_n": cRX * FN * math.sin(delta_n) * scale_force,
        "rudder_sway_force_n": (1.0 + aH) * FN * math.cos(delta_n) * scale_force,
        "rudder_roll_moment_nm": (
            -(1.0 + aH) * zR * FN * math.cos(delta_n) * scale_moment
        ),
        "rudder_yaw_moment_nm": (
            (xR + aH * xH) * FN * math.cos(delta_n) * scale_moment
        ),
    }
    return xdot, actuator


def derivative(x, orders) -> np.ndarray:
    """Return the original 10-state container-ship derivative.

    ``x`` is ``[u, v, r, N, E, psi, p, phi, delta, n]``.  ``orders`` is
    ``[delta_c, n_c]``.  The function raises ``ValueError`` for non-finite
    inputs, wrong vector lengths, or values outside the source's positive
    speed and positive actual-RPM applicability.
    """
    return _evaluate(x, orders)[0]


def actuator_forces(x, orders) -> dict[str, float]:
    """Return source propeller and rudder force components in SI units.

    Keys are ``propeller_thrust_n``, ``rudder_normal_force_n``,
    ``rudder_surge_force_n``, ``rudder_sway_force_n``,
    ``rudder_roll_moment_nm`` and ``rudder_yaw_moment_nm``.  The helper uses
    the identical coefficients, saturation and flow calculation as
    :func:`derivative`; it does not define a runtime actuator.
    """
    return _evaluate(x, orders)[1]
