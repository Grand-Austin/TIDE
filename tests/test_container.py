import math

import numpy as np
import pytest

from game.simulation.container import actuator_forces, derivative


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
