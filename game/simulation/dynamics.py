"""S175 branch of MSS hydroVessel, in SI units and FSD/NED axes about CG.

Ported from MSS ce0b40ed101411fc4eebd064b35c5a96916106ad.
Copyright Thor I. Fossen, MIT; see data/vessels/s175/MSS-LICENSE.txt.
The horizontal current transformation follows the reference's yaw-only,
locally quasi-steady approximation, even when roll and pitch are nonzero.
"""

import math

import numpy as np


def _vector(value, size, name):
    value = np.asarray(value, dtype=float)
    if value.shape != (size,) or not np.all(np.isfinite(value)):
        raise ValueError(f"{name} must be a finite {size}-vector")
    return value


def skew(a):
    x, y, z = a
    return np.array([[0., -z, y], [z, 0., -x], [-y, x, 0.]])


def rotation(angles):
    """Body FSD to local NED, using the ZYX Euler convention."""
    phi, theta, psi = _vector(angles, 3, 'angles')
    cp, sp = math.cos(phi), math.sin(phi)
    ct, st = math.cos(theta), math.sin(theta)
    cy, sy = math.cos(psi), math.sin(psi)
    return np.array([[cy*ct, -sy*cp+cy*st*sp, sy*sp+cy*cp*st],
                     [sy*ct, cy*cp+sp*st*sy, -cy*sp+st*sy*cp],
                     [-st, ct*sp, ct*cp]])


def kinematics(eta, nu):
    eta = _vector(eta, 6, 'eta')
    nu = _vector(nu, 6, 'nu')
    phi, theta, _ = eta[3:]
    ct = math.cos(theta)
    if abs(ct) < 1e-8:
        raise ValueError('Euler kinematics are singular near pitch = +/-90 degrees')
    sp, cp, tt = math.sin(phi), math.cos(phi), math.tan(theta)
    transform = np.array([[1, sp*tt, cp*tt], [0, cp, -sp], [0, sp/ct, cp/ct]])
    return np.r_[rotation(eta[3:]) @ nu[:3], transform @ nu[3:]]


def added_coriolis(mass, nu):
    """MSS m2c's symmetric-mass, momentum-based six-DOF formulation."""
    mass = (mass + mass.T) / 2
    momentum = mass @ nu
    p, h = skew(momentum[:3]), skew(momentum[3:])
    return np.block([[np.zeros((3, 3)), -p], [-p, -h]])


def rigid_coriolis(main, angular_velocity):
    m = main['m']
    inertia = m * np.array([main['k44']**2, main['k55']**2, main['k66']**2])
    zero = np.zeros((3, 3))
    return np.block([[m*skew(angular_velocity), zero],
                     [zero, -skew(inertia*angular_velocity)]])


def _ittc_coefficient(speed, main):
    cb = main.get('C_B', 0)
    if cb <= 0:
        cb = min(main['nabla'] / (main['Lpp']*main['B']*main['T']), 1) if main.get('nabla', 0) > 0 else .65
    reynolds = max(main['Lpp'] * abs(speed) / 1e-6, 1e5)
    cf = .075 / (math.log10(reynolds)-2)**2
    wetted = 1.025 * main['Lpp'] * (cb*main['B'] + 1.7*main['T'])
    return .5 * main['rho'] * wetted * 1.1 * cf


def surge_drag(speed, vessel):
    """Signed ITTC resistance magnitude; positive for positive surge."""
    if not math.isfinite(speed):
        raise ValueError('Surge speed must be finite')
    return _ittc_coefficient(speed, vessel.main) * abs(speed) * speed


_HOERNER = np.array([
    [.0108623,1.96608],[.176606,1.96573],[.353025,1.89756],
    [.451863,1.78718],[.472838,1.58374],[.492877,1.27862],
    [.493252,1.21082],[.558473,1.08356],[.646401,.998631],
    [.833589,.87959],[.988002,.828415],[1.30807,.759941],
    [1.63918,.691442],[1.85998,.657076],[2.31288,.630693],
    [2.59998,.596186],[3.00877,.586846],[3.45075,.585909],
    [3.7379,.559877],[4.00309,.559315],
])


def crossflow(main, nu_relative):
    """Twenty midpoint strips, including the pinned reference's pitch sign."""
    length, breadth, draft = main['Lpp'], main['B'], main['T']
    coefficient = np.interp(breadth/(2*draft), _HOERNER[:, 0], _HOERNER[:, 1])
    strip = length/20
    positions = -length/2 + (np.arange(20)+.5)*strip
    horizontal = nu_relative[1] + positions*nu_relative[5]
    vertical = nu_relative[2] + positions*nu_relative[4]
    factor = -.5 * 1025 * draft * coefficient * strip
    lateral = factor * abs(horizontal)*horizontal
    heave = factor * abs(vertical)*vertical
    return np.array([0, lateral.sum(), heave.sum(), 0,
                     positions @ heave, positions @ lateral])


def derivative(x, tau, vessel, current_ne, *, wave_frequency=False):
    """Return [nu_dot, eta_dot]; nu is ground-relative body velocity.

    wave_frequency: x is the two-timescale wave-frequency layer, whose surge velocity is a small oscillation, not
    the ship's speed. Its surge damping stays the linear D11: the MSS fade to ITTC resistance is for forward speed,
    and applied to the oscillation it removed the damping as the oscillation grew, so the layer drifted ahead.
    """
    x = _vector(x, 12, 'state')
    tau = _vector(tau, 6, 'force')
    north, east = _vector(current_ne, 2, 'current')
    nu, eta = x[:6], x[6:]
    psi = eta[5]
    current_body = np.array([north*math.cos(psi)+east*math.sin(psi),
                             -north*math.sin(psi)+east*math.cos(psi), 0.])
    relative = nu - np.r_[current_body, np.zeros(3)]
    current_dot = np.r_[-skew(nu[3:]) @ current_body, np.zeros(3)]
    damping = vessel.D.copy()
    if not wave_frequency:
        damping[0,0] = vessel.D[0,0]*math.exp(-3*abs(relative[0])) + _ittc_coefficient(relative[0], vessel.main)*abs(relative[0])
    indices = np.arange(3,6)
    damping[indices,indices] = vessel.D[indices,indices] * (1+vessel.nonlinear_456*abs(nu[3:]))
    coriolis = rigid_coriolis(vessel.main, nu[3:]) + added_coriolis(vessel.MA, relative)
    acceleration = current_dot + vessel.Minv @ (tau + crossflow(vessel.main, relative)
                    - (coriolis + damping) @ relative - vessel.G @ eta)
    return np.r_[acceleration, kinematics(eta, nu)]
