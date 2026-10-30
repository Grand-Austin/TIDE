from types import SimpleNamespace

import numpy as np
import pytest

from archive.game.cargo import CargoModel
from game.simulation.dynamics import rotation
from archive.pipeline.cargo_stacks import EFFECTIVE_HEIGHT_M
from archive.pipeline.cargo_study import candidate_loss, demands, legacy_loss


def test_legacy_replay_matches_the_live_algorithm():
    demand = np.array([0.0, 0.12, 0.2, 0.3, 0.05, 0.4])
    model = CargoModel(seed=3)
    state = model.initial_state()
    for i, value in enumerate(demand):
        model.advance(state, SimpleNamespace(time_s=i * 4, roll_deg=0, pitch_deg=0,
                                            lateral_acceleration_mps2=value * 9.81), 4)
    replay = legacy_loss(demand, 4, seed=3)
    assert replay["lost_units"] == state["lost_units"]
    assert replay["lost_teu"] == state["lost_teu"]
    np.testing.assert_allclose(replay["dose"], state["damage"])


def test_deck_acceleration_includes_correct_fsd_height_and_gravity_signs():
    t = np.arange(0.05, 20, 0.05)
    amplitude, omega = np.radians(25), 2 * np.pi / 15
    phi = amplitude * np.sin(omega * t)
    p = amplitude * omega * np.cos(omega * t)
    trace = np.zeros((len(t), 8))
    trace[:, 0], trace[:, 1], trace[:, 4] = t, p, phi
    cg, deck = demands(trace)
    expected = np.abs(-9.81 * np.sin(phi) - 15 * amplitude * omega**2 * np.sin(omega * t)) / 9.81
    np.testing.assert_allclose(cg, np.abs(np.sin(phi)), atol=1e-14)
    np.testing.assert_allclose(deck[1:-1, 2], expected[1:-1], atol=1e-5)
    assert deck[:, 2].max() > deck[:, 0].max() > cg.max()
    _, stack_demand = demands(trace, [EFFECTIVE_HEIGHT_M])
    expected_stack = np.abs(-9.81 * np.sin(phi)
                            - EFFECTIVE_HEIGHT_M * amplitude * omega**2 * np.sin(omega * t)) / 9.81
    np.testing.assert_allclose(stack_demand[1:-1, 0], expected_stack[1:-1], atol=1e-5)
    trace[:, 1:6] = [0, 0.2, 0.3, 0, 0]
    _, deck = demands(trace)
    expected = np.broadcast_to(np.array([5, 10, 15]) * 0.2 * 0.3 / 9.81, deck.shape)
    np.testing.assert_allclose(deck, expected, atol=1e-14)


def test_body_gravity_projection_cancels_free_fall_specific_force():
    trace = np.zeros((3, 8))
    trace[:, 0] = [0.05, 0.1, 0.15]
    trace[:, 4:6] = np.radians([25, 7])
    gravity_body = rotation([*trace[0, 4:6], 0.3]).T @ [0, 0, 9.81]
    assert gravity_body[1] > 0
    trace[:, 6] = gravity_body[1]
    cg, deck = demands(trace)
    np.testing.assert_allclose(cg, 0, atol=1e-14)
    np.testing.assert_allclose(deck, 0, atol=1e-14)


def test_calm_and_peak_failure_are_distinct_from_cumulative_failure():
    assert candidate_loss(np.zeros((10, 3)), 1)["lost_units"] == 0
    assert candidate_loss(np.full((1, 3), 1.0), 0.05, sigma=0)["lost_units"] == 320
    demand = np.full((100, 3), 0.65)
    short = candidate_loss(demand, 1, sigma=0, fatigue_s=60)
    long = candidate_loss(demand, 10, sigma=0, fatigue_s=60)
    assert short["lost_units"] == 0
    assert long["peak_lost_units"] == 0
    assert long["fatigue_only_lost_units"] == 320


def test_stronger_securing_cannot_increase_loss_and_seed_is_fixed():
    demand = np.full((30, 3), 0.45)
    a = candidate_loss(demand, 1)
    assert a == candidate_loss(demand, 1)
    weak = candidate_loss(demand, 1, strength=0.75)
    strong = candidate_loss(demand, 1, strength=1.25)
    assert weak["lost_units"] >= a["lost_units"] >= strong["lost_units"]
    assert 0 < a["lost_units"] < 320


@pytest.mark.parametrize("kwargs", [{"dt": 0}, {"sigma": -1}, {"strength": float("nan")}])
def test_candidate_rejects_invalid_scales(kwargs):
    config = {"dt": 1, **kwargs}
    with pytest.raises(ValueError):
        candidate_loss(np.zeros((3, 3)), **config)
