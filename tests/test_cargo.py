import json
from types import SimpleNamespace

from game.cargo import CargoModel


def sample(time_s=0.0, roll_deg=0.0, pitch_deg=0.0, lateral_acceleration_mps2=0.0):
    return SimpleNamespace(
        time_s=time_s,
        roll_deg=roll_deg,
        pitch_deg=pitch_deg,
        lateral_acceleration_mps2=lateral_acceleration_mps2,
    )


def test_cargo_template_contains_800_units_and_1200_teu():
    state = CargoModel(seed=7).initial_state()
    summary = CargoModel().summary(state)

    assert len(state["units"]) == 800
    assert summary["total_units"] == 800
    assert summary["deck_units"] == 320
    assert summary["hold_units"] == 480
    assert summary["total_teu"] == 1200
    assert sum(unit["teu"] for unit in state["units"]) == 1200


def test_mild_motion_causes_no_damage_or_loss():
    model = CargoModel(seed=3)
    state = model.initial_state()

    events = model.advance(state, sample(lateral_acceleration_mps2=0.2), dt_s=600.0)

    assert events == []
    assert state["damage"] == [0.0, 0.0, 0.0]
    assert state["lost_units"] == 0
    assert state["lost_teu"] == 0
    assert state["grade"] == 0


def test_severe_loss_removes_deck_cargo_but_protects_hold():
    model = CargoModel(seed=11)
    state = model.initial_state()

    events = model.advance(
        state,
        sample(time_s=60.0, roll_deg=20.0, lateral_acceleration_mps2=30.0),
        dt_s=120.0,
    )

    lost_ids = {unit_id for event in events for unit_id in event.get("unit_ids", [])}
    assert lost_ids
    assert state["lost_units"] == len(lost_ids)
    assert state["lost_teu"] > 0
    assert state["grade"] > 0
    assert all(
        state["units"][int(unit_id.split("-")[1]) - 1]["category"] is not None
        for unit_id in lost_ids
    )
    assert all(not unit["overboard"] for unit in state["units"][320:])


def test_overboard_units_are_not_lost_again():
    model = CargoModel(seed=11)
    state = model.initial_state()
    severe = sample(time_s=60.0, roll_deg=20.0, lateral_acceleration_mps2=30.0)

    first = model.advance(state, severe, dt_s=120.0)
    second = model.advance(state, severe, dt_s=120.0)
    first_ids = {unit_id for event in first for unit_id in event.get("unit_ids", [])}
    second_ids = {unit_id for event in second for unit_id in event.get("unit_ids", [])}

    assert first_ids
    assert second_ids.isdisjoint(first_ids)
    assert state["lost_units"] == len(first_ids)


def test_seed_reproducibility_is_exact():
    first = CargoModel(seed=42).initial_state()
    second = CargoModel(seed=42).initial_state()
    different = CargoModel(seed=43).initial_state()

    assert first == second
    assert first != different


def test_split_run_json_round_trip_preserves_cargo_state():
    model = CargoModel(seed=5)
    state = model.initial_state()
    model.advance(state, sample(lateral_acceleration_mps2=30.0), dt_s=90.0)

    restored = json.loads(json.dumps(state))
    before_lost = {unit["id"] for unit in restored["units"] if unit["overboard"]}
    events = model.advance(restored, sample(lateral_acceleration_mps2=30.0), dt_s=90.0)

    after_lost = {unit["id"] for unit in restored["units"] if unit["overboard"]}
    newly_lost = {unit_id for event in events for unit_id in event.get("unit_ids", [])}
    assert before_lost <= after_lost
    assert newly_lost.isdisjoint(before_lost)
    assert restored["lost_units"] == len(after_lost)


def test_cargo_grade_boundaries_are_strict():
    model = CargoModel(seed=1)
    # These values are the exact boundaries for grades 1 through 4.
    cases = [
        (0, 0),
        (6, 1),
        (7, 2),
        (12, 2),
        (13, 3),
        (36, 3),
        (37, 4),
        (120, 4),
        (121, 5),
    ]

    for lost_teu, expected_grade in cases:
        state = model.initial_state()
        state["lost_teu"] = lost_teu
        state["next_threshold"] = [float("inf")] * 3
        model.advance(state, sample(), dt_s=1.0)
        assert state["grade"] == expected_grade, lost_teu
