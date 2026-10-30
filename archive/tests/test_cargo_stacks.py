import numpy as np
import pytest

from archive.pipeline.cargo_stacks import (
    EFFECTIVE_HEIGHT_M,
    STACK_UNIT_INDICES,
    StackCargo,
    stack_loss,
)


def test_stack_failure_loses_whole_stacks_and_respects_protected_hold_ceiling():
    model = StackCargo(1.0, median_g=0.5, sigma=0.0, exposure_s=1.0)

    newly_failed = model.advance(0.5)

    np.testing.assert_array_equal(newly_failed, np.arange(64))
    summary = model.summary()
    assert summary["failed_stacks"] == 64
    assert summary["lost_units"] == 320
    assert summary["lost_teu"] == 480
    assert summary["loss_fraction_units"] == 320 / 800
    assert summary["loss_fraction_teu"] == 480 / 1200
    assert summary["lost_units"] <= 800 - 480
    assert summary["lost_teu"] <= 1200 - 720
    single = StackCargo(1.0, sigma=0.0)
    single.capacities_g[:] = 1.0
    single.capacities_g[1] = 0.1
    np.testing.assert_array_equal(single.advance(0.2), [1])
    assert single.summary()["lost_units"] == 5
    assert single.summary()["lost_teu"] == 10


def test_transient_rejection_resets_counter_between_separate_excursions():
    model = StackCargo(1.0, median_g=0.5, sigma=0.0, exposure_s=2.0)

    assert model.advance(0.5).size == 0
    model.advance(0.49)
    assert model.advance(0.5).size == 0
    assert model.advance(0.5).size == 64


def test_capacity_and_required_duration_are_inclusive():
    model = StackCargo(0.5, median_g=0.5, sigma=0.0, exposure_s=1.0)

    assert model.advance(0.5).size == 0
    assert model.advance(0.5).size == 64


def test_failed_stack_cannot_fail_again():
    model = StackCargo(1.0, median_g=0.5, sigma=0.0, exposure_s=1.0)

    first = model.advance(0.5)
    second = model.advance(0.5)

    assert first.size == 64
    assert second.size == 0
    assert model.summary()["failed_stacks"] == 64


def test_batch_and_streaming_summaries_match_on_seeded_varying_trace():
    rng = np.random.default_rng(7)
    demand = rng.uniform(0.25, 0.75, size=40)
    config = {
        "median_g": 0.5,
        "sigma": 0.35,
        "exposure_s": 1.5,
        "strength": 1.0,
        "cargo_seed": 101,
    }
    model = StackCargo(0.5, **config)
    for value in demand:
        model.advance(value)

    assert stack_loss(demand, 0.5, **config) == model.summary()


def test_fixed_clock_segmentation_does_not_change_results():
    rng = np.random.default_rng(11)
    demand = rng.uniform(0.2, 0.8, size=24)
    config = {"median_g": 0.5, "sigma": 0.2, "exposure_s": 1.0, "cargo_seed": 3}
    whole = StackCargo(0.5, **config)
    segmented = StackCargo(0.5, **config)
    for value in demand:
        whole.advance(value)
    for segment in np.array_split(demand, 4):
        for value in segment:
            segmented.advance(value)

    assert segmented.summary() == whole.summary()


def test_results_are_reproducible_and_strength_reduces_loss():
    rng = np.random.default_rng(13)
    demand = rng.uniform(0.35, 0.65, size=30)
    weak = stack_loss(demand, 0.5, sigma=0.35, exposure_s=1.0, strength=0.8)
    repeated = stack_loss(demand, 0.5, sigma=0.35, exposure_s=1.0, strength=0.8)
    strong = stack_loss(demand, 0.5, sigma=0.35, exposure_s=1.0, strength=1.2)

    assert weak == repeated
    assert weak["failed_stacks"] >= strong["failed_stacks"]


def test_stowage_mapping_partitions_units_and_keeps_one_size_per_stack():
    assert EFFECTIVE_HEIGHT_M == pytest.approx(11.25)
    assert STACK_UNIT_INDICES.shape == (64, 5)
    assert sorted(STACK_UNIT_INDICES.ravel().tolist()) == list(range(320))
    for stack in STACK_UNIT_INDICES:
        assert len(set((stack % 2) + 1)) == 1


@pytest.mark.parametrize("kwargs", [
    {"dt_s": 0},
    {"dt_s": float("nan")},
    {"median_g": 0},
    {"exposure_s": -1},
    {"strength": 0},
    {"sigma": -0.1},
])
def test_invalid_scales_are_rejected(kwargs):
    parameters = {"dt_s": 1.0}
    parameters.update(kwargs)
    with pytest.raises(ValueError):
        StackCargo(**parameters)


@pytest.mark.parametrize("demand", [-0.1, float("nan"), float("inf")])
def test_invalid_streaming_demands_are_rejected(demand):
    model = StackCargo(1.0)
    with pytest.raises(ValueError):
        model.advance(demand)


@pytest.mark.parametrize("demand", [[], [0.1, -0.1], [0.1, float("nan")]])
def test_invalid_batch_demands_are_rejected(demand):
    with pytest.raises(ValueError):
        stack_loss(demand, 1.0)
