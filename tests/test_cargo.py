import copy
import json
from types import SimpleNamespace

import pytest

from game.cargo import CargoModel


def sample(
    time_s=0.0,
    roll_deg=0.0,
    pitch_deg=0.0,
    lateral_acceleration_mps2=0.0,
    angular_acceleration_x_rad_s2=0.0,
    angular_velocity_y_rad_s=0.0,
    angular_velocity_z_rad_s=0.0,
):
    return SimpleNamespace(
        time_s=time_s,
        roll_deg=roll_deg,
        pitch_deg=pitch_deg,
        lateral_acceleration_mps2=lateral_acceleration_mps2,
        angular_acceleration_x_rad_s2=angular_acceleration_x_rad_s2,
        angular_velocity_y_rad_s=angular_velocity_y_rad_s,
        angular_velocity_z_rad_s=angular_velocity_z_rad_s,
    )


def _single_stack_state(model, capacity=0.5, target_number=0):
    state = model.initial_state()
    others = [stack for stack in state["stack_order"] if stack != target_number]
    state["capacities_g"] = [capacity] + [2.0 + i / 100 for i in range(63)]
    state["stack_order"] = [target_number] + others
    return state, target_number


def _cargo_events(events):
    return [event for event in events if event.get("kind") == "cargo_lost"]


def _event_unit_ids(events):
    return {
        unit_id
        for event in _cargo_events(events)
        for unit_id in event.get("unit_ids", [])
    }


def test_initial_state_is_plain_json_and_contains_the_protected_template():
    state = CargoModel(seed=7).initial_state()

    assert state["capacities_g"] == sorted(state["capacities_g"])
    assert len(state["capacities_g"]) == 64
    assert len(state["stack_order"]) == 64
    assert state["overload_started_steps"] == [0] * 64
    assert state["overloaded_count"] == 0
    assert state["failed_count"] == 0
    assert state["dt_s"] is None
    assert state["required_steps"] == 0
    assert state["grade"] == 0
    assert state["lost_units"] == 0
    assert state["lost_teu"] == 0
    assert json.loads(json.dumps(state)) == state


def test_failure_loses_five_boxes_jointly_and_maps_the_original_deck_indices():
    model = CargoModel(config={"exposure_s": 0.05}, seed=1)
    state, target = _single_stack_state(model, capacity=0.1)

    events = model.advance(state, sample(lateral_acceleration_mps2=9.81), dt_s=0.05)

    cargo_events = _cargo_events(events)
    assert len(cargo_events) == 1
    assert cargo_events[0]["stack_ids"] == [target]
    assert cargo_events[0]["teu"] == 5 * (1 + target % 2)
    assert cargo_events[0]["demand_g"] > 0
    assert len(cargo_events[0]["unit_ids"]) == 5
    assert len(set(cargo_events[0]["unit_ids"])) == 5
    assert state["failed_count"] == 1
    assert state["lost_units"] == 5
    number = target
    original_indices = [10 * (number // 2) + number % 2 + 2 * tier for tier in range(5)]
    assert set(cargo_events[0]["unit_ids"]) == {f"deck-{index + 1:03}" for index in original_indices}
    expected_teu = sum(1 if index % 2 == 0 else 2 for index in original_indices)
    assert state["lost_teu"] == expected_teu
    assert all(0 <= index < 320 for index in original_indices)


def test_hold_boxes_remain_protected():
    model = CargoModel(config={"exposure_s": 0.05}, seed=1)
    state = model.initial_state()

    events = model.advance(state, sample(lateral_acceleration_mps2=100.0), dt_s=0.05)

    assert _event_unit_ids(events)
    assert state["lost_units"] == 5 * state["failed_count"]
    assert state["lost_units"] <= 320
    assert state["lost_teu"] <= 480


def test_ship_loss_includes_hold_cargo_and_only_counts_remaining_units():
    model = CargoModel(config={"exposure_s": 0.05}, seed=1)
    state = model.initial_state()
    model.advance(state, sample(lateral_acceleration_mps2=100.0), dt_s=0.05)
    assert state["lost_units"] == 320
    events = model.lose_all(state, 10.0)
    assert events[0]["kind"] == "cargo_total_loss"
    assert events[0]["lost_units"] == 480
    assert events[0]["lost_teu"] == 720
    summary = model.summary(state)
    assert summary["remaining_units"] == 0
    assert summary["loss_fraction"] == 1
    assert summary["grade"] == 5
    assert model.lose_all(state, 11.0) == []
    assert model.advance(state, sample(lateral_acceleration_mps2=100.0), 0.05) == []


def test_transient_underload_resets_the_consecutive_overload_counter():
    model = CargoModel(config={"exposure_s": 0.1}, seed=1)
    state, _ = _single_stack_state(model, capacity=0.5)
    high = sample(lateral_acceleration_mps2=0.5 * 9.81)
    low = sample(lateral_acceleration_mps2=0.0)

    assert not _cargo_events(model.advance(state, high, dt_s=0.05))
    assert not _cargo_events(model.advance(state, low, dt_s=0.05))
    assert not _cargo_events(model.advance(state, high, dt_s=0.05))
    events = model.advance(state, high, dt_s=0.05)

    assert _cargo_events(events)
    assert state["failed_count"] == 1


def test_exact_one_second_boundary_fails_on_the_twentieth_sample():
    model = CargoModel(config={"exposure_s": 1.0}, seed=1)
    state, _ = _single_stack_state(model, capacity=0.1)
    high = sample(lateral_acceleration_mps2=9.81)

    for _ in range(19):
        assert not _cargo_events(model.advance(state, high, dt_s=0.05))

    events = model.advance(state, high, dt_s=0.05)
    assert _cargo_events(events)
    assert state["failed_count"] == 1


def test_a_failed_stack_does_not_report_duplicate_loss():
    model = CargoModel(config={"exposure_s": 0.05}, seed=1)
    state, _ = _single_stack_state(model, capacity=0.1)
    high = sample(lateral_acceleration_mps2=9.81)

    first = model.advance(state, high, dt_s=0.05)
    second = model.advance(state, high, dt_s=0.05)

    first_units = _event_unit_ids(first)
    assert first_units
    assert _event_unit_ids(second).isdisjoint(first_units)
    assert state["lost_units"] == 5


def test_seed_reproducibility_is_exact():
    first = CargoModel(seed=42).initial_state()
    second = CargoModel(seed=42).initial_state()
    different = CargoModel(seed=43).initial_state()

    assert first == second
    assert first != different


def test_json_restore_during_an_unfinished_overload_preserves_the_exact_boundary():
    model = CargoModel(config={"exposure_s": 0.1}, seed=1)
    state, _ = _single_stack_state(model, capacity=0.5)
    high = sample(lateral_acceleration_mps2=0.5 * 9.81)

    assert not _cargo_events(model.advance(state, high, dt_s=0.05))
    restored = json.loads(json.dumps(state))
    assert restored == state

    uninterrupted = copy.deepcopy(state)
    restored_events = model.advance(restored, high, dt_s=0.05)
    uninterrupted_events = model.advance(uninterrupted, high, dt_s=0.05)

    assert _cargo_events(restored_events)
    assert restored_events == uninterrupted_events
    assert restored == uninterrupted


def test_independent_states_advanced_by_one_model_do_not_share_counters():
    model = CargoModel(config={"exposure_s": 0.1}, seed=1)
    first, _ = _single_stack_state(model, capacity=0.5)
    second, _ = _single_stack_state(model, capacity=0.5)
    high = sample(lateral_acceleration_mps2=0.5 * 9.81)

    model.advance(first, high, dt_s=0.05)
    assert first["overload_started_steps"] != second["overload_started_steps"]
    assert not _cargo_events(model.advance(second, high, dt_s=0.05))
    assert _cargo_events(model.advance(first, high, dt_s=0.05))
    assert second["failed_count"] == 0


def test_angular_point_load_uses_the_p_dot_minus_q_r_signs():
    model = CargoModel(config={"exposure_s": 0.05}, seed=1)

    canceling = sample(
        lateral_acceleration_mps2=-11.25,
        angular_acceleration_x_rad_s2=1.0,
        angular_velocity_y_rad_s=0.0,
        angular_velocity_z_rad_s=0.0,
    )
    state, _ = _single_stack_state(model, capacity=0.5)
    assert not _cargo_events(model.advance(state, canceling, dt_s=0.05))

    reinforcing = sample(
        lateral_acceleration_mps2=-11.25,
        angular_acceleration_x_rad_s2=-1.0,
        angular_velocity_y_rad_s=0.0,
        angular_velocity_z_rad_s=0.0,
    )
    state, _ = _single_stack_state(model, capacity=0.5)
    assert _cargo_events(model.advance(state, reinforcing, dt_s=0.05))

    yaw_product = sample(
        lateral_acceleration_mps2=11.25,
        angular_acceleration_x_rad_s2=0.0,
        angular_velocity_y_rad_s=1.0,
        angular_velocity_z_rad_s=1.0,
    )
    state, _ = _single_stack_state(model, capacity=0.5)
    assert not _cargo_events(model.advance(state, yaw_product, dt_s=0.05))


def test_finite_input_rejection_happens_before_state_mutation():
    model = CargoModel(config={"exposure_s": 0.1}, seed=1)
    state, _ = _single_stack_state(model, capacity=0.5)
    before = copy.deepcopy(state)

    with pytest.raises(ValueError):
        model.advance(state, sample(lateral_acceleration_mps2=float("nan")), dt_s=0.05)
    assert state == before


def test_dt_is_fixed_after_the_first_step():
    model = CargoModel(config={"exposure_s": 0.1}, seed=1)
    state, _ = _single_stack_state(model, capacity=0.5)
    model.advance(state, sample(lateral_acceleration_mps2=0.0), dt_s=0.05)
    before = copy.deepcopy(state)

    with pytest.raises(ValueError):
        model.advance(state, sample(lateral_acceleration_mps2=0.0), dt_s=0.1)
    assert state == before


def test_grade_boundaries_use_strict_teu_percentages():
    model = CargoModel(config={"exposure_s": 0.05}, seed=1)
    cases = [
        (6, 1),
        (7, 2),
        (12, 2),
        (13, 3),
        (36, 3),
        (37, 4),
        (120, 4),
        (121, 5),
    ]

    for final_lost_teu, expected_grade in cases:
        state, _ = _single_stack_state(model, capacity=0.1, target_number=0)
        state["lost_teu"] = final_lost_teu - 5
        model.advance(
            state, sample(lateral_acceleration_mps2=9.81), dt_s=0.05
        )
        assert state["lost_teu"] == final_lost_teu
        assert state["grade"] == expected_grade, final_lost_teu
