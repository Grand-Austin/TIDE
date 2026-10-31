from types import SimpleNamespace

import numpy as np
import pytest

from game.simulation.environment import SeaState
from game.simulation.waves import WaveModel, jonswap


def model(seed=1, force_rao=None):
    if force_rao is None:
        force_rao = np.ones((6, 40, 12), dtype=complex)
    return WaveModel(SimpleNamespace(main={'g':9.81}, freqs=np.linspace(.1, 4, 40),
                     headings=np.arange(0, 2*np.pi, np.pi/6),
                     force_rao=force_rao), seed=seed)


def test_calm_and_constant_rao_force():
    m = model()
    calm = m.evaluate(m.initial_state(), 0, SeaState(0,8,0), 0, [0,0])
    np.testing.assert_array_equal(calm.force, 0)
    result = m.evaluate(m.initial_state(), 1, SeaState(2,8,0), 0, [0,0])
    np.testing.assert_allclose(result.force, result.elevation)


def test_seed_phase_persistence_and_no_input_mutation():
    m = model()
    state = m.initial_state()
    original = state.phase.copy()
    sea = SeaState(2,8,0)
    a = m.evaluate(state, 1, sea, 0, [0,0])
    b = m.evaluate(state, 1, sea, 0, [0,0])
    np.testing.assert_array_equal(a.force, b.force)
    np.testing.assert_array_equal(state.phase, original)
    changed = m.evaluate(a.state, 2, SeaState(3,7,90), 1, [1,2])
    expected = a.state.phase + (a.state.previous_rate + changed.state.previous_rate)/2
    np.testing.assert_allclose(changed.state.phase, expected)
    other = model(2)
    assert not np.array_equal(a.force, other.evaluate(other.initial_state(),1,sea,0,[0,0]).force)


def test_following_sea_can_have_negative_encounter_rate():
    m = model()
    # Waves from south propagate north; fast northward motion overtakes components.
    result = m.evaluate(m.initial_state(), 1, SeaState(2,8,180), 0, [10,0])
    assert np.any(result.state.previous_rate < 0)


def test_full_spectrum_energy_and_truncation_reporting():
    w = np.geomspace(.001,100,10000)
    # DNV's analytic normalization is approximate, not exactly Hs^2/16.
    assert np.trapezoid(jonswap(w,2,2*np.pi/8),w) == pytest.approx(2**2/16, rel=.02)
    m=model()
    result=m.evaluate(m.initial_state(),0,SeaState(2,8,0),0,[0,0])
    assert result.retained_energy > .95
    with pytest.raises(ValueError,match='outside'):
        m.evaluate(m.initial_state(),0,SeaState(2,1,0),0,[0,0])


def test_tiny_negative_angle_preserves_directional_force_across_seam():
    directions = np.arange(12)
    force_rao = np.broadcast_to((1 + 0.5j) * (1 + 0.2j * directions), (6, 40, 12)).copy()
    m = model(force_rao=force_rao)
    sea = SeaState(1, 8, 0)
    seam_heading = np.nextafter(np.pi + m.mu[0], np.inf)
    nearby_heading = np.nextafter(np.pi + m.mu[0], -np.inf)

    seam = m.evaluate(m.initial_state(), 0, sea, seam_heading, [0, 0])
    nearby = m.evaluate(m.initial_state(), 0, sea, nearby_heading, [0, 0])

    assert np.all(np.isfinite(seam.force))
    assert np.all(np.isfinite(nearby.force))
    np.testing.assert_allclose(seam.force, nearby.force, rtol=1e-12, atol=1e-10)


def test_direction_wrap_and_time_order():
    m=model()
    a=m.evaluate(m.initial_state(),1,SeaState(2,8,359.9999),0,[1,0])
    b=m.evaluate(m.initial_state(),1,SeaState(2,8,.0001),0,[1,0])
    np.testing.assert_allclose(a.force,b.force,atol=1e-5)
    with pytest.raises(ValueError,match='decrease'):
        m.evaluate(a.state,0,SeaState(2,8,0),0,[0,0])


@pytest.mark.parametrize('tp', [5, 8.39, 11.5, 15, 19])
def test_long_period_spectrum_has_resolved_energy_without_renormalization(tp):
    m = model()
    result = m.evaluate(m.initial_state(), 0, SeaState(8.2, tp, 0), 0, [0, 0])
    assert 'spectrum_underresolved' not in result.flags
    fine = np.geomspace(m.edges[0], m.edges[-1], 100000)
    expected = np.trapezoid(jonswap(fine, 1, 2*np.pi/tp), fine)
    actual = np.sum(jonswap(m.omega, 1, 2*np.pi/tp) * m.dw)
    assert actual == pytest.approx(expected, rel=0.001)


def test_coarse_grid_still_reports_underresolution():
    m = WaveModel(SimpleNamespace(main={'g': 9.81}, freqs=np.linspace(.1, 4, 40),
                                  headings=np.arange(0, 2*np.pi, np.pi/6),
                                  force_rao=np.ones((6, 40, 12), complex)), n_frequencies=8)
    result = m.evaluate(m.initial_state(), 0, SeaState(8.2, 11.5, 0), 0, [0, 0])
    assert 'spectrum_underresolved' in result.flags


def test_mean_surge_uses_amplitude_squared_and_tabulated_speed():
    vessel = SimpleNamespace(main={'g':9.81}, freqs=np.linspace(.1,4,40),
        headings=np.arange(0,2*np.pi,np.pi/6), force_rao=np.ones((6,40,12),complex),
        drift_freqs=np.linspace(.1,4,40), drift_speeds=np.array([0.,5.]),
        surge_drift=np.broadcast_to([-100.,-200.],(40,12,2)).copy())
    m = WaveModel(vessel)
    sea = SeaState(2,11.5,0)
    result = m.evaluate(m.initial_state(),1,sea,0,[2.5,0])
    amplitude_squared = 2*np.sum(jonswap(m.omega,2,2*np.pi/11.5)*m.dw)
    assert result.mean_surge_n == pytest.approx(-150*amplitude_squared)
    doubled = m.evaluate(m.initial_state(),1,SeaState(4,11.5,0),0,[2.5,0])
    assert doubled.mean_surge_n == pytest.approx(4*result.mean_surge_n)
    clipped = m.evaluate(m.initial_state(),1,sea,0,[-1,0])
    assert 'mean_drift_speed_clipped' in clipped.flags
    assert clipped.mean_surge_n == pytest.approx(-100*amplitude_squared)


def test_s175_mean_wave_surge_has_sourced_head_and_following_signs():
    from game.simulation.vessel import load_s175
    m = WaveModel(load_s175())
    head = m.evaluate(m.initial_state(),0,SeaState(8.2,11.5,0),0,[3,0])
    following = m.evaluate(m.initial_state(),0,SeaState(8.2,11.5,180),0,[3,0])
    assert head.mean_surge_n < 0 < following.mean_surge_n
    calm = m.evaluate(m.initial_state(),0,SeaState(0,11.5,0),0,[3,0])
    assert calm.mean_surge_n == 0


@pytest.mark.parametrize('tp',[5.,8.39,11.5,15.,19.])
def test_s175_force_rms_quadrature_agrees_with_dense_reference(tp):
    from scipy.interpolate import interp1d
    from game.simulation.vessel import load_s175
    vessel = load_s175()
    m = WaveModel(vessel)
    dense = np.geomspace(vessel.freqs[0],vessel.freqs[-1],20000)
    coefficients = interp1d(vessel.freqs,vessel.force_rao,axis=1)(dense)
    expected = np.trapezoid(abs(coefficients)**2*jonswap(dense,1,2*np.pi/tp)[None,:,None],dense,axis=1)
    actual = np.sum(abs(m.rao[:,:,:-1])**2*jonswap(m.omega,1,2*np.pi/tp)[None,:,None]*m.dw[None,:,None],axis=1)
    assert np.max(abs(np.sqrt(actual/expected)-1)) < .03
