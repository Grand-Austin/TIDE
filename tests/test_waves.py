from types import SimpleNamespace

import numpy as np
import pytest

from game.simulation.environment import SeaState
from game.simulation.waves import WaveModel, jonswap


def model(seed=1):
    return WaveModel(SimpleNamespace(main={'g':9.81}, freqs=np.linspace(.1, 4, 40),
                     headings=np.arange(0, 2*np.pi, np.pi/6),
                     force_rao=np.ones((6,40,12), dtype=complex)), seed=seed)


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


def test_direction_wrap_and_time_order():
    m=model()
    a=m.evaluate(m.initial_state(),1,SeaState(2,8,359.9999),0,[1,0])
    b=m.evaluate(m.initial_state(),1,SeaState(2,8,.0001),0,[1,0])
    np.testing.assert_allclose(a.force,b.force,atol=1e-5)
    with pytest.raises(ValueError,match='decrease'):
        m.evaluate(a.state,0,SeaState(2,8,0),0,[0,0])
