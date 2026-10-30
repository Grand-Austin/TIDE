from types import SimpleNamespace

import numpy as np
import pytest

from archive.pipeline.cargo_stacks import StackCargo as ReferenceStackCargo
from game.cargo import CargoModel, EFFECTIVE_HEIGHT_M, STACK_UNIT_INDICES
from pipeline.cargo_stacks import stack_loss


def test_runtime_matches_reference_and_batch_during_changing_overload_prefixes():
    dt = 0.05
    rng = np.random.default_rng(7)
    demand = np.repeat(rng.uniform(0.05, 0.95, 100), rng.integers(1, 60, 100))
    reference = ReferenceStackCargo(dt, cargo_seed=101)
    model = CargoModel(seed=101)
    state = model.initial_state()
    sample = SimpleNamespace(time_s=0.0, roll_deg=0.0, pitch_deg=0.0,
                             lateral_acceleration_mps2=0.0,
                             angular_acceleration_x_rad_s2=0.0,
                             angular_velocity_y_rad_s=0.0, angular_velocity_z_rad_s=0.0)
    for tick, value in enumerate(demand, 1):
        reference.advance(value)
        sample.time_s = tick * dt
        sample.lateral_acceleration_mps2 = float(value * 9.81)
        model.advance(state, sample, dt)
        assert sorted(state["stack_order"][:state["failed_count"]]) == reference.summary()["failed_stack_ids"]
    expected = stack_loss(demand, dt)
    assert expected == reference.summary()
    assert model.summary(state)["lost_units"] == expected["lost_units"]
    assert model.summary(state)["lost_teu"] == expected["lost_teu"]


def test_stowage_preserves_existing_deck_ids_and_box_sizes():
    assert EFFECTIVE_HEIGHT_M == pytest.approx(11.25)
    assert sorted(index for stack in STACK_UNIT_INDICES for index in stack) == list(range(320))
    assert all(len(stack) == 5 and len({i % 2 for i in stack}) == 1 for stack in STACK_UNIT_INDICES)


@pytest.mark.parametrize("demand", [[], [0.1, -0.1], [float("nan")], [float("inf")]])
def test_batch_rejects_invalid_histories(demand):
    with pytest.raises(ValueError):
        stack_loss(demand, 0.05)
