"""Kernel tests for the approved two-time-scale LF/WF composition."""

import math
from types import SimpleNamespace

import numpy as np
import pytest

from game.simulation import actuators, container
from game.simulation.dynamics import kinematics, rotation
from game.simulation.maneuvering import hull_force
from game.simulation.two_timescale import (
    SOURCE_ADDED_MASS,
    compose,
    lf_derivative,
    lf_mass,
)
from game.simulation.vessel import load_s175


def source_vessel():
    """Vessel-like source loading used by the original container derivative."""
    length, density, gravity, displacement = 175.0, 1025.0, 9.81, 21222.0
    mass_scale = 0.5 * density * length**3
    inertia_scale = 0.5 * density * length**5
    mass = 0.00792 * mass_scale
    ixx = 0.0000176 * inertia_scale
    izz = 0.000456 * inertia_scale
    main = {"Lpp": length, "rho": density, "m": mass,
            "k44": 1.0, "k55": 1.0, "k66": 1.0}
    mrb = np.diag([mass, mass, mass, ixx, 1.0e12, izz])
    restoring = np.zeros((6, 6))
    restoring[3, 3] = 0.3 * density * gravity * displacement
    return SimpleNamespace(main=main, MRB=mrb, G=restoring)


def source_identity_state(state):
    u, v, r, north, east, psi, p, phi, delta, rpm = state
    external = actuators.loads([u, v, 0.0, p, 0.0, r], delta, rpm)
    lf = np.array([u, v, p, r, north, east, phi, psi])
    expected = container.derivative(state, [delta, rpm])
    expected_lf = np.array([
        expected[0], expected[1], expected[6], expected[2],
        expected[3], expected[4], expected[7], expected[5],
    ])
    return lf, external, expected_lf


def finite_difference_velocity(lf, wf, lf_dot, wf_dot, current, step):
    x0, dx0 = compose(lf, wf, lf_dot, wf_dot, current)
    x1, _ = compose(lf + step*lf_dot, wf + step*wf_dot,
                    lf_dot, wf_dot, current)
    velocity_n0 = rotation(x0[9:12]) @ x0[:3]
    velocity_n1 = rotation(x1[9:12]) @ x1[:3]
    acceleration_n = (velocity_n1 - velocity_n0) / step
    return x0, dx0, x1, acceleration_n


def test_source_loading_matches_the_original_container_derivative():
    vessel = source_vessel()
    states = [
        [5.0, 0.3, 0.01, 0.0, 0.0, 0.2, 0.02, 0.01, 0.1, 70.0],
        [6.0, -0.4, -0.02, 0.0, 0.0, -0.3, 0.03, -0.02, -0.15, 90.0],
        [4.5, 0.8, 0.03, 0.0, 0.0, 1.0, -0.01, 0.04, 0.2, 60.0],
    ]
    for state in states:
        lf, external, expected = source_identity_state(state)
        actual = lf_derivative(lf, external, vessel, current_ne=(0.0, 0.0))
        np.testing.assert_allclose(actual, expected, rtol=0.0, atol=2e-12)


def test_lf_mass_is_symmetric_positive_and_uses_the_contract_entries():
    vessel = source_vessel()
    matrix = lf_mass(vessel)
    np.testing.assert_array_equal(matrix, matrix.T)
    assert np.linalg.eigvalsh(matrix).min() > 0
    length, density = 175.0, 1025.0
    mass_scale = 0.5 * density * length**3
    my = SOURCE_ADDED_MASS["my"] * mass_scale
    jz = SOURCE_ADDED_MASS["Jz"] * 0.5 * density * length**5
    assert matrix[1, 1] == pytest.approx(vessel.main["m"] + my)
    assert matrix[1, 2] == pytest.approx(-my * SOURCE_ADDED_MASS["ly"] * length)
    assert matrix[3, 3] == pytest.approx(vessel.MRB[5, 5] + jz)

    s175 = lf_mass(load_s175())
    assert np.linalg.eigvalsh(s175).min() > 0


def test_lf_derivative_advects_current_once_and_zero_velocity_is_rest():
    vessel = load_s175()
    rest = lf_derivative(np.zeros(8), np.zeros(6), vessel)
    np.testing.assert_array_equal(rest, np.zeros(8))
    drift = lf_derivative(np.zeros(8), np.zeros(6), vessel,
                          current_ne=(0.2, -0.3))
    assert drift[0:4] == pytest.approx(0.0)
    assert drift[4] == pytest.approx(0.2)
    assert drift[5] == pytest.approx(-0.3)
    assert drift[6] == pytest.approx(0.0)
    assert drift[7] == pytest.approx(0.0)

    lf = np.array([4.0, 0.2, 0.01, 0.02, 10.0, -20.0, 0.03, 0.7])
    derivative = lf_derivative(lf, np.zeros(6), vessel, current_ne=(0.1, -0.2))
    u, v, _, _, _, _, phi, psi = lf
    assert derivative[4] == pytest.approx(
        math.cos(psi)*u - math.sin(psi)*math.cos(phi)*v + 0.1)
    assert derivative[5] == pytest.approx(
        math.sin(psi)*u + math.cos(psi)*math.cos(phi)*v - 0.2)


def test_lf_hull_force_and_acceleration_are_port_starboard_odd():
    vessel = load_s175()
    positive = np.array([5.0, 0.3, 0.012, 0.02, 0.0, 0.0, 0.01, 0.0])
    negative = np.array([5.0, -0.3, -0.012, -0.02, 0.0, 0.0, -0.01, 0.0])
    p = lf_derivative(positive, np.zeros(6), vessel)
    n = lf_derivative(negative, np.zeros(6), vessel)
    assert p[0] == pytest.approx(n[0])
    np.testing.assert_allclose(p[1:4], -n[1:4])


def test_wf_zero_identity_preserves_lf_pose_velocity_and_heading_kinematics():
    lf = np.array([4.0, 0.25, 0.02, 0.03, 12.0, -4.0, 0.06, 1.1])
    current = np.array([0.1, -0.2])
    u, v, p, r, north, east, phi, psi = lf
    lf_dot = np.array([
        0.1, -0.04, 0.01, -0.02,
        math.cos(psi)*u - math.sin(psi)*math.cos(phi)*v + current[0],
        math.sin(psi)*u + math.cos(psi)*math.cos(phi)*v + current[1],
        p, math.cos(phi)*r,
    ])
    x, dx = compose(lf, np.zeros(12), lf_dot, np.zeros(12), current)
    assert x[6] == pytest.approx(north)
    assert x[7] == pytest.approx(east)
    assert x[8] == pytest.approx(0.0)
    np.testing.assert_allclose(x[9:12], [phi, 0.0, psi])
    np.testing.assert_allclose(dx[6:9], lf_dot[4:6].tolist() + [0.0], atol=1e-15)
    np.testing.assert_allclose(dx[9:12], [p, 0.0, math.cos(phi)*r], atol=1e-15)


def test_compose_acceleration_matches_independent_finite_differences():
    lf = np.array([4.2, 0.35, 0.03, 0.045, 12.3, -4.5, 0.07, 1.2])
    wf = np.array([0.4, -0.2, 0.15, 0.02, -0.03, 0.05,
                   0.6, -0.4, 0.1, 0.04, -0.02, 0.09])
    current = np.array([0.1, -0.2])
    u, v, p, r, north, east, phi, psi = lf
    lf_dot = np.array([
        0.15, -0.08, 0.02, -0.01,
        math.cos(psi)*u - math.sin(psi)*math.cos(phi)*v + current[0],
        math.sin(psi)*u + math.cos(psi)*math.cos(phi)*v + current[1],
        p, math.cos(phi)*r,
    ])
    wf_dot = np.r_[
        [0.04, -0.03, 0.02, 0.005, -0.004, 0.006],
        kinematics(wf[6:12], wf[:6]),
    ]
    step = 1e-6
    x0, dx0, x1, acceleration_n = finite_difference_velocity(
        lf, wf, lf_dot, wf_dot, current, step)
    # Position rate and inertial acceleration are both checked independently.
    np.testing.assert_allclose(
        (x1[6:9] - x0[6:9]) / step, dx0[6:9], rtol=2e-9, atol=2e-8)
    omega_b, velocity_b = x0[3:6], x0[:3]
    accel_b = dx0[:3] + np.cross(omega_b, velocity_b)
    np.testing.assert_allclose(
        acceleration_n, rotation(x0[9:12]) @ accel_b, rtol=2e-9, atol=2e-8)


def test_true_heading_rate_matches_the_euler_transform_of_total_omega():
    lf = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 4.0*math.pi + 0.3])
    wf = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                   0.0, 0.0, 0.0, 0.08, -0.03, 0.05])
    x, dx = compose(lf, wf, np.zeros(8), np.zeros(12))
    phi, theta, _ = x[9:12]
    omega_b = x[3:6]
    expected = ((math.sin(phi)*omega_b[1] + math.cos(phi)*omega_b[2])
                / math.cos(theta))
    assert dx[11] == pytest.approx(expected, rel=1e-15, abs=1e-15)
    assert abs(x[11] - lf[7]) < math.pi
    assert x[11] == pytest.approx(lf[7], abs=0.1)


def test_compose_does_not_mutate_inputs_and_rejects_bad_shapes():
    lf = np.zeros(8)
    wf = np.zeros(12)
    lf_dot = np.ones(8)
    wf_dot = np.ones(12)
    current = np.array([0.1, 0.2])
    copies = [lf.copy(), wf.copy(), lf_dot.copy(), wf_dot.copy(), current.copy()]
    compose(lf, wf, lf_dot, wf_dot, current)
    for before, after in zip(copies, (lf, wf, lf_dot, wf_dot, current)):
        np.testing.assert_array_equal(before, after)
    with pytest.raises(ValueError, match="8-vector"):
        compose(np.zeros(7), wf, lf_dot, wf_dot)
    with pytest.raises(ValueError, match="finite"):
        compose(lf, wf, lf_dot, np.full(12, np.nan))
    with pytest.raises(ValueError, match="finite"):
        lf_derivative(lf, np.zeros(6), load_s175(), current_ne=(0.0, np.inf))


def test_hull_force_mirror_is_the_source_port_starboard_symmetry():
    vessel = load_s175()
    positive = hull_force([5.0, 0.3, 0.0, 0.01, 0.0, 0.02], 0.01, vessel.main)
    negative = hull_force([5.0, -0.3, 0.0, -0.01, 0.0, -0.02], -0.01, vessel.main)
    assert positive[0] == pytest.approx(negative[0])
    np.testing.assert_allclose(positive[[1, 3, 5]], -negative[[1, 3, 5]])
