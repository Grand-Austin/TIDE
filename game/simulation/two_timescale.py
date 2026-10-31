"""Two-time-scale LF manoeuvring / WF seakeeping composition.

The LF state is the native four-DOF Son--Nomoto state
``[u, v, p, r, N, E, phi, psi]``. It uses the source kinetic convention:
the supplied mass matrix plus the source convective vector ``Fc`` and the pure
hull force from :mod:`game.simulation.maneuvering`. The prepared S175 damping
matrix and cross-flow are not applied on the LF state.

The WF state is the original twelve-state S175 perturbation in the LF body
frame. Its derivative is supplied by :func:`game.simulation.dynamics.derivative`.
This module only composes the two states. It does not alter or replace the
original WF matrices.

All functions are pure and read-only. Angles and headings are radians. The LF
heading is unbounded; the composed yaw is selected continuously near it.
"""

from __future__ import annotations

import math

import numpy as np

from .dynamics import rotation
from .maneuvering import hull_force

SOURCE_ADDED_MASS = {
    "mx": 0.000238,
    "my": 0.007049,
    "Jx": 0.0000034,
    "Jz": 0.000419,
    "alphay": 0.05,
    "lx": 0.0313,
    "ly": 0.0313,
}


def _vector(value, size: int, name: str) -> np.ndarray:
    array = np.asarray(value, dtype=float)
    if array.shape != (size,) or not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must be a finite {size}-vector")
    return array


def _main(main, key: str) -> float:
    value = main.get(key)
    if value is None or not math.isfinite(float(value)) or float(value) <= 0:
        raise ValueError(f"main[{key!r}] must be finite and positive")
    return float(value)


def _rigid_roll_yaw_inertia(vessel, mass: float) -> tuple[float, float]:
    matrix = getattr(vessel, "MRB", None)
    if matrix is not None:
        matrix = np.asarray(matrix, dtype=float)
        if matrix.shape != (6, 6) or not np.all(np.isfinite(matrix)):
            raise ValueError("vessel.MRB must be a finite 6x6 matrix")
        ixx, izz = float(matrix[3, 3]), float(matrix[5, 5])
    else:
        main = vessel.main
        ixx = mass * _main(main, "k44") ** 2
        izz = mass * _main(main, "k66") ** 2
    if not all(math.isfinite(value) and value > 0 for value in (ixx, izz)):
        raise ValueError("Rigid roll and yaw inertia must be finite and positive")
    return ixx, izz


def lf_mass(vessel) -> np.ndarray:
    """Return the native LF 4x4 mass matrix in ``[u, v, p, r]`` order.

    The rigid mass and moments come from ``vessel.main['m']`` and
    ``vessel.MRB``. The added masses and coupling terms are the Son--Nomoto
    source values specified by the integration contract. The matrix is SI.
    """
    main = vessel.main
    length = _main(main, "Lpp")
    density = _main(main, "rho")
    mass = _main(main, "m")
    ixx, izz = _rigid_roll_yaw_inertia(vessel, mass)
    mass_scale = 0.5 * density * length**3
    inertia_scale = 0.5 * density * length**5
    mx = SOURCE_ADDED_MASS["mx"] * mass_scale
    my = SOURCE_ADDED_MASS["my"] * mass_scale
    jx = SOURCE_ADDED_MASS["Jx"] * inertia_scale
    jz = SOURCE_ADDED_MASS["Jz"] * inertia_scale
    ly_l = SOURCE_ADDED_MASS["ly"] * length
    alpha_l = SOURCE_ADDED_MASS["alphay"] * length
    return np.array(
        [
            [mass + mx, 0.0, 0.0, 0.0],
            [0.0, mass + my, -my * ly_l, my * alpha_l],
            [0.0, -my * ly_l, ixx + jx, 0.0],
            [0.0, my * alpha_l, 0.0, izz + jz],
        ],
        dtype=float,
    )


def lf_derivative(lf, external6, vessel, current_ne=(0.0, 0.0)) -> np.ndarray:
    """Return the native LF derivative ``[udot,vdot,pdot,rdot,Ndot,Edot,phidot,psidot]``.

    ``external6`` is ``[X,Y,Z,K,M,N]`` in SI. It must contain the actual
    actuator, wind and mean-surge-drift loads only. This function adds the
    source hull force, the source convective vector and the single S175 roll
    restoring term. It does not apply the prepared S175 ``D`` matrix or
    cross-flow.
    """
    state = _vector(lf, 8, "lf")
    external = _vector(external6, 6, "external6")
    current = _vector(current_ne, 2, "current_ne")
    u, v, p, r, north, east, phi, psi = state
    main = vessel.main
    length = _main(main, "Lpp")
    density = _main(main, "rho")
    mass = _main(main, "m")
    mass_scale = 0.5 * density * length**3
    mx = SOURCE_ADDED_MASS["mx"] * mass_scale
    my = SOURCE_ADDED_MASS["my"] * mass_scale
    lx_l = SOURCE_ADDED_MASS["lx"] * length
    hull = hull_force([u, v, 0.0, p, 0.0, r], phi, main)
    convective = np.array(
        [
            (mass + my) * v * r,
            -(mass + mx) * u * r,
            mx * lx_l * u * r,
            0.0,
        ]
    )
    force = external[[0, 1, 3, 5]] + hull[[0, 1, 3, 5]] + convective
    restoring = np.asarray(vessel.G, dtype=float)
    if restoring.shape != (6, 6) or not np.all(np.isfinite(restoring)):
        raise ValueError("vessel.G must be a finite 6x6 matrix")
    force[2] -= float(restoring[3, 3]) * phi
    acceleration = np.linalg.solve(lf_mass(vessel), force)
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)
    cos_psi, sin_psi = math.cos(psi), math.sin(psi)
    return np.array(
        [
            acceleration[0],
            acceleration[1],
            acceleration[2],
            acceleration[3],
            cos_psi*u - sin_psi*cos_phi*v + current[0],
            sin_psi*u + cos_psi*cos_phi*v + current[1],
            p,
            cos_phi*r,
        ],
        dtype=float,
    )


def _rz(angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def _rx(angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


def _wrap(angle: float) -> float:
    return (angle + math.pi) % (2.0*math.pi) - math.pi


def _total_angles(rotation_total: np.ndarray, lf_psi: float) -> np.ndarray:
    sin_theta = float(np.clip(-rotation_total[2, 0], -1.0, 1.0))
    theta = math.asin(sin_theta)
    if abs(math.cos(theta)) < 1e-10:
        raise ValueError("Composed Euler angles are singular near pitch = +/-90 degrees")
    phi = math.atan2(rotation_total[2, 1], rotation_total[2, 2])
    psi_raw = math.atan2(rotation_total[1, 0], rotation_total[0, 0])
    psi = lf_psi + _wrap(psi_raw - lf_psi)
    return np.array([phi, theta, psi], dtype=float)


def _euler_rates(angles: np.ndarray, omega: np.ndarray) -> np.ndarray:
    phi, theta, _ = angles
    cos_theta = math.cos(theta)
    if abs(cos_theta) < 1e-10:
        raise ValueError("Composed Euler kinematics are singular near pitch = +/-90 degrees")
    sin_phi, cos_phi = math.sin(phi), math.cos(phi)
    tan_theta = math.tan(theta)
    transform = np.array(
        [
            [1.0, sin_phi*tan_theta, cos_phi*tan_theta],
            [0.0, cos_phi, -sin_phi],
            [0.0, sin_phi/cos_theta, cos_phi/cos_theta],
        ]
    )
    return transform @ omega


def compose(lf, wf, lf_dot, wf_dot, current_ne=(0.0, 0.0)):
    """Compose LF motion and WF perturbation into a twelve-state legacy pose.

    Returns ``(total_x, total_derivative)``. ``total_x`` uses the original
    ordering ``[u,v,w,p,q,r,N,E,D,phi,theta,psi]``. The derivative is the
    legacy-shaped derivative used by samples and cargo. The total yaw is
    continuous around ``lf[7]``, not wrapped to a fixed interval.
    """
    lf = _vector(lf, 8, "lf")
    wf = _vector(wf, 12, "wf")
    lf_dot = _vector(lf_dot, 8, "lf_dot")
    wf_dot = _vector(wf_dot, 12, "wf_dot")
    current = _vector(current_ne, 2, "current_ne")

    u, v, p, r, north, east, phi, psi = lf
    udot, vdot, pdot, rdot = lf_dot[:4]
    xi = wf[6:9]
    omega_w = wf[3:6]
    alpha_w = wf_dot[3:6]

    rotation_lf = _rz(psi) @ _rx(phi)
    rotation_wf = rotation(wf[9:12])
    rotation_total = rotation_lf @ rotation_wf

    cos_phi, sin_phi = math.cos(phi), math.sin(phi)
    heading_rate = cos_phi * r
    heading_accel = cos_phi*rdot - sin_phi*p*r

    velocity_lf_n = _rz(psi) @ np.array([u, v*cos_phi, 0.0])
    velocity_lf_n[0] += current[0]
    velocity_lf_n[1] += current[1]
    acceleration_lf_n = _rz(psi) @ (
        np.array([udot, vdot*cos_phi - v*sin_phi*p, 0.0])
        + np.cross(np.array([0.0, 0.0, heading_rate]),
                   np.array([u, v*cos_phi, 0.0]))
    )
    omega_lf = np.array(
        [p, sin_phi*heading_rate, cos_phi*heading_rate]
    )
    alpha_lf = np.array(
        [
            pdot,
            cos_phi*p*heading_rate + sin_phi*heading_accel,
            -sin_phi*p*heading_rate + cos_phi*heading_accel,
        ]
    )

    xi_dot_l = rotation_wf @ wf[:3]
    xi_ddot_l = rotation_wf @ (wf_dot[:3] + np.cross(omega_w, wf[:3]))
    velocity_n = velocity_lf_n + rotation_lf @ (
        xi_dot_l + np.cross(omega_lf, xi)
    )
    acceleration_n = acceleration_lf_n + rotation_lf @ (
        xi_ddot_l
        + 2.0*np.cross(omega_lf, xi_dot_l)
        + np.cross(alpha_lf, xi)
        + np.cross(omega_lf, np.cross(omega_lf, xi))
    )

    velocity_b = rotation_total.T @ velocity_n
    omega_b = rotation_wf.T @ omega_lf + omega_w
    alpha_b = (
        rotation_wf.T @ alpha_lf + alpha_w
        + np.cross(rotation_wf.T @ omega_lf, omega_w)
    )
    acceleration_b = rotation_total.T @ acceleration_n
    position = np.array([north, east, 0.0]) + rotation_lf @ xi
    angles = _total_angles(rotation_total, psi)

    total_x = np.r_[
        velocity_b,
        omega_b,
        position,
        angles,
    ]
    total_derivative = np.r_[
        acceleration_b - np.cross(omega_b, velocity_b),
        alpha_b,
        velocity_n,
        _euler_rates(angles, omega_b),
    ]
    return total_x, total_derivative
