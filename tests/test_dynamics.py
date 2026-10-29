"""Physical invariants for the six-DOF port, independent of MATLAB runtime."""

from types import SimpleNamespace

import numpy as np
import pytest

from game.simulation.dynamics import (
    added_coriolis, crossflow, derivative, kinematics, rigid_coriolis,
    rotation, surge_drag,
)


@pytest.fixture
def vessel():
    main = dict(m=1e6,k44=10,k55=10,k66=10,Lpp=50,B=10,T=3,rho=1025,C_B=.6)
    mass = np.diag([1e6]*3 + [1e8]*3)
    return SimpleNamespace(main=main, MA=mass*.1, Minv=np.linalg.inv(mass*1.1),
        D=np.diag([1e4]*3+[1e6]*3), G=np.diag([0,0,1e6,1e8,1e8,0]),
        nonlinear_456=np.zeros(3))


def test_rotation_is_proper_and_matches_cardinal_heading():
    angles = [.2,-.1,.7]
    r = rotation(angles)
    np.testing.assert_allclose(r.T@r,np.eye(3),atol=1e-15)
    assert np.linalg.det(r) == pytest.approx(1)
    np.testing.assert_allclose(rotation([0,0,np.pi/2])@[1,0,0],[0,1,0],atol=1e-15)


def test_kinematics_euler_singularity_and_finite_inputs():
    with pytest.raises(ValueError,match='singular'):
        kinematics([0,0,0,0,np.pi/2,0],np.zeros(6))
    with pytest.raises(ValueError,match='finite'):
        rotation([0,np.nan,0])


def test_coriolis_matrices_do_no_work(vessel):
    nu=np.array([1,2,3,.1,.2,.3])
    for c in (added_coriolis(vessel.MA,nu),rigid_coriolis(vessel.main,nu[3:])):
        np.testing.assert_array_equal(c+c.T,0)
        assert abs(nu@c@nu) < 1e-7


def test_zero_state_and_uniform_current_equilibrium(vessel):
    np.testing.assert_array_equal(derivative(np.zeros(12),np.zeros(6),vessel,[0,0]),0)
    x=np.zeros(12); x[11]=.7
    x[:3]=rotation([0,0,.7]).T@np.array([.3,.4,0])
    dx=derivative(x,np.zeros(6),vessel,[.3,.4])
    np.testing.assert_allclose(dx[:6],0,atol=1e-15)
    np.testing.assert_allclose(dx[6:9],[.3,.4,0],atol=1e-15)


def test_independent_surge_resistance_balance(vessel):
    x=np.zeros(12);x[0]=2
    force=surge_drag(2,vessel)+vessel.D[0,0]*np.exp(-6)*2
    assert force>0
    assert derivative(x,np.zeros(6),vessel,[0,0])[0] == pytest.approx(-force/1.1e6)
    assert surge_drag(-2,vessel)==pytest.approx(-surge_drag(2,vessel))


def test_restoring_roll_and_heave_accelerate_toward_equilibrium(vessel):
    x=np.zeros(12);x[8]=.2;x[9]=.01
    dx=derivative(x,np.zeros(6),vessel,[0,0])
    assert dx[2] == pytest.approx(-1e6*.2/1.1e6)
    assert dx[3] == pytest.approx(-1e8*.01/1.1e8)


def test_crossflow_has_reference_symmetry_and_opposes_sway(vessel):
    nu=np.array([0,2,0,0,0,0])
    tau=crossflow(vessel.main,nu)
    assert tau[1]<0
    assert tau[5]==pytest.approx(0,abs=1e-6)
    np.testing.assert_allclose(crossflow(vessel.main,-nu),-tau)


def test_small_unforced_roll_energy_decays(vessel):
    from scipy.integrate import solve_ivp
    x=np.zeros(12);x[9]=.01
    result=solve_ivp(lambda t,x: derivative(x,np.zeros(6),vessel,[0,0]),[0,10],x,rtol=1e-9,atol=1e-12)
    def energy(x):
        return .5*1.1e8*x[3]**2+.5*1e8*x[9]**2
    assert energy(result.y[:,-1]) < energy(x)
