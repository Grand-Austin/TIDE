"""Physical invariants for the low-frequency S175 manoeuvring hull force."""

import math

import numpy as np
import pytest

from game.simulation import maneuvering
from game.simulation.vessel import load_s175


MAIN = {"Lpp": 175.0, "rho": 1025.0, "m": 24_609_620.0,
        "B": 25.4, "T": 9.5}


def test_source_provenance_and_35_degree_variant_are_explicit():
    assert maneuvering.SOURCE_COMMIT == (
        "ce0b40ed101411fc4eebd064b35c5a96916106ad"
    )
    assert "container.m" in maneuvering.SOURCE_FILES[0]
    assert maneuvering.SOURCE_DOI == "10.3390/jmse13081578"
    assert maneuvering.ORIGINAL_RUDDER_LIMIT_RAD == pytest.approx(
        math.radians(10.0)
    )
    assert maneuvering.RUDDER_LIMIT_RAD == pytest.approx(math.radians(35.0))
    assert maneuvering.RUDDER_LIMIT_DEG == pytest.approx(35.0)


def test_hull_force_is_zero_at_rest_and_rejects_astern_extrapolation():
    np.testing.assert_array_equal(
        maneuvering.hull_force(np.zeros(6), 0.0, MAIN), np.zeros(6)
    )
    with pytest.raises(ValueError, match="astern"):
        maneuvering.hull_force([-0.1, 0, 0, 0, 0, 0], 0.0, MAIN)


def test_hull_force_matches_the_source_straight_ahead_Xuu_term():
    speed = 5.0
    force = maneuvering.hull_force([speed, 0, 0, 0, 0, 0], 0.0, MAIN)
    expected = (maneuvering.COEFFICIENTS["Xuu"]
                * 0.5 * MAIN["rho"] * MAIN["Lpp"]**2 * speed**2)
    assert force[0] == pytest.approx(expected, rel=1e-15)
    assert force[1] == pytest.approx(0.0)
    assert force[3] == pytest.approx(0.0)
    assert force[5] == pytest.approx(0.0)


def test_hull_force_does_not_use_vessel_mass_or_added_mass():
    state = np.array([5.0, 0.25, 0.0, 0.01, 0.0, 0.02])
    light = dict(MAIN, m=1.0)
    heavy = dict(MAIN, m=1e12)
    np.testing.assert_array_equal(
        maneuvering.hull_force(state, 0.01, light),
        maneuvering.hull_force(state, 0.01, heavy),
    )


def test_hull_force_does_not_duplicate_hydrostatic_roll_restoring():
    vessel = load_s175()
    roll = 0.01
    force = maneuvering.hull_force(
        [5.0, 0, 0, 0, 0, 0], roll, vessel.main
    )
    # The source Kphi term is a small hydrodynamic roll moment. The large S175
    # hydrostatic G33*phi term belongs to the caller and must be absent here.
    assert force[3] < 0
    assert abs(force[3]) < abs(vessel.G[3, 3] * roll) / 10


def test_hull_force_has_reference_port_starboard_symmetry():
    positive = maneuvering.hull_force(
        [5.0, 0.3, 0.1, 0.02, -0.01, 0.03], 0.004, MAIN
    )
    negative = maneuvering.hull_force(
        [5.0, -0.3, -0.1, -0.02, 0.01, -0.03], -0.004, MAIN
    )
    assert positive[0] == pytest.approx(negative[0])
    np.testing.assert_allclose(positive[[1, 3, 5]], -negative[[1, 3, 5]])


def test_positive_yaw_is_damped_by_the_source_hull_moment():
    force = maneuvering.hull_force([5.0, 0, 0, 0, 0, 0.01], 0.0, MAIN)
    assert force[5] < 0
    assert force[1] > 0


def test_servo_uses_35_degree_limit_with_source_rate_and_time_constant():
    rates = maneuvering.servo_rates(0.0, 70.0, math.pi, 1000.0)
    assert rates[0] == pytest.approx(math.radians(5.0))
    assert rates[1] == pytest.approx((160.0 - 70.0) / (5.65 / (70.0 / 60.0)))
    assert maneuvering.servo_rates(0.0, 70.0, 1.0, 70.0)[0] == pytest.approx(
        math.radians(5.0)
    )
    assert maneuvering.servo_rates(
        maneuvering.RUDDER_LIMIT_RAD, 70.0, 1.0, 70.0
    )[0] == pytest.approx(0.0)
    with pytest.raises(ValueError, match="reverse"):
        maneuvering.servo_rates(0.0, -1.0, 0.0, 70.0)


def test_froude_scaled_primary_references_are_not_merged():
    # Shang, Zhan and Liu (2021), Table 6, 1:42.8 model.
    scale_2021 = 42.8
    assert 0.95 * math.sqrt(scale_2021) == pytest.approx(6.2146, rel=1e-4)
    assert 23.06 * math.sqrt(scale_2021) == pytest.approx(150.862, rel=1e-4)
    assert 44.6 * math.sqrt(scale_2021) == pytest.approx(291.781, rel=1e-4)
    assert 16.1718 * scale_2021 == pytest.approx(692.153, rel=1e-4)
    assert 90.0 / (23.06 * math.sqrt(scale_2021)) == pytest.approx(
        0.5966, rel=1e-3
    )

    # J. Mar. Sci. Eng. (2025), 13(8), 1578, different 1:57.689 model.
    scale_2025 = 57.689
    assert 35.27 * math.sqrt(scale_2025) == pytest.approx(267.887, rel=1e-4)
    assert 180.0 / (35.27 * math.sqrt(scale_2025)) == pytest.approx(
        0.6719, rel=1e-3
    )
    assert 0.090 / math.sqrt(scale_2025) == pytest.approx(
        math.radians(0.6793), rel=1e-3
    )
    assert scale_2021 != scale_2025
