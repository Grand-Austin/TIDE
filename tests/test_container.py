import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pytest

from game.simulation.container import (
    SOURCE_SHA256,
    actuator_forces,
    derivative,
)
from pipeline.validate_container import compare, prepare

FIXTURE = Path("tests/fixtures/mss/container-reference.json")


def _reference():
    return json.loads(FIXTURE.read_text())


def test_port_matches_executed_octave_fixture(tmp_path):
    reference = _reference()
    expected = prepare(tmp_path)

    np.testing.assert_allclose(
        expected["derivative_states"],
        reference["derivative_states"],
        rtol=0,
        atol=0,
    )
    np.testing.assert_allclose(
        expected["derivative_orders"],
        reference["derivative_orders"],
        rtol=0,
        atol=0,
    )
    np.testing.assert_allclose(
        expected["short_x0"], reference["short_x0"], rtol=0, atol=0
    )
    np.testing.assert_allclose(
        expected["short_orders"], reference["short_orders"], rtol=0, atol=0
    )
    assert expected["short_h"] == reference["short_h"]
    assert expected["sustained_h"] == reference["sustained_h"]
    np.testing.assert_allclose(
        expected["sustained_x0"], reference["sustained_x0"], rtol=0, atol=0
    )
    np.testing.assert_allclose(
        expected["sustained_orders"][0],
        reference["sustained_command"],
        rtol=0,
        atol=0,
    )

    indices = np.asarray(reference["sustained_indices"])
    checks = compare(
        {
            "derivatives": expected["derivatives"],
            "short": expected["short"][:, reference["short_indices"]],
            "sustained": expected["sustained"][indices],
            "actuator_differences": expected["actuator_differences"],
        },
        {
            "derivatives": np.asarray(reference["derivatives"]),
            "short": np.asarray(reference["short"]),
            "sustained": np.asarray(reference["sustained"]),
            "actuator_differences": np.asarray(reference["actuator_differences"]),
        },
    )
    assert all(check["passed"] for check in checks.values()), checks


def test_fixture_records_pinned_source_hashes():
    reference = _reference()
    assert reference["source_commit"] == (
        "ce0b40ed101411fc4eebd064b35c5a96916106ad"
    )
    assert reference["source_license"] == "MIT"
    assert reference["source_sha256"] == SOURCE_SHA256

    root = Path("data/raw/mss")
    for name, expected in SOURCE_SHA256.items():
        path = root / name
        if not path.is_file():
            pytest.skip(f"Pinned MSS source is not present: {path}")
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected


def test_sustained_rudder_trial_exercises_a_turn():
    response = _reference()["turn_response"]
    assert response["max_abs_roll_deg"] > 1.0
    assert abs(response["net_heading_change_deg"]) > 180.0
    assert response["final_speed_mps"] < 7.0


def test_saturation_limits_and_signed_rudder():
    x = [7.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 70.0]
    positive = derivative(x, [10.0, 1000.0])
    negative = derivative(x, [-10.0, 1000.0])
    assert positive[8] == pytest.approx(math.radians(5.0))
    assert negative[8] == pytest.approx(-math.radians(5.0))
    assert np.all(np.isfinite(positive))
    assert np.all(np.isfinite(negative))


def test_actuator_helper_returns_si_components():
    right = actuator_forces([7.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.1, 70.0],
                            [0.1, 70.0])
    left = actuator_forces([7.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, -0.1, 70.0],
                           [-0.1, 70.0])
    assert right.keys() == left.keys()
    assert all(np.isfinite(value) for value in right.values())
    assert right["propeller_thrust_n"] > 0
    assert right["rudder_normal_force_n"] == pytest.approx(
        -left["rudder_normal_force_n"]
    )
    assert right["rudder_sway_force_n"] == pytest.approx(
        -left["rudder_sway_force_n"]
    )
    assert right["rudder_yaw_moment_nm"] == pytest.approx(
        -left["rudder_yaw_moment_nm"]
    )


@pytest.mark.parametrize(
    "state, orders",
    [
        ([7.0] * 9, [0.0, 70.0]),
        ([7.0] * 11, [0.0, 70.0]),
        ([7.0] * 10, [70.0]),
        ([7.0] * 10, [0.0, 70.0, 1.0]),
        ([7.0, np.nan] + [0.0] * 8, [0.0, 70.0]),
        ([7.0] * 10, [np.inf, 70.0]),
        ([0.0, 0.0] + [0.0] * 7 + [70.0], [0.0, 70.0]),
        ([0.0, 7.0] + [0.0] * 7 + [70.0], [0.0, 70.0]),
        ([7.0, 0.0] + [0.0] * 7 + [0.0], [0.0, 70.0]),
        ([7.0, 0.0] + [0.0] * 7 + [-1.0], [0.0, 70.0]),
    ],
)
def test_invalid_inputs_raise_value_error(state, orders):
    with pytest.raises(ValueError):
        derivative(state, orders)


def test_comparison_rejects_a_changed_reference():
    expected = {
        "derivatives": np.zeros((7, 10)),
        "short": np.zeros((2, 401, 10)),
        "sustained": np.zeros((601, 10)),
        "actuator_differences": np.zeros((7, 6)),
    }
    actual = {key: value.copy() for key, value in expected.items()}
    assert all(check["passed"] for check in compare(expected, actual).values())
    actual["derivatives"][2, 0] = 0.01
    assert not compare(expected, actual)["derivatives"]["passed"]
