from dataclasses import replace
import math
from types import SimpleNamespace

import numpy as np
import pytest

from game.simulation.wind import WindParameters, isherwood72, wind_load


def sea(north=0., east=0.):
    return SimpleNamespace(wind_north_mps=north, wind_east_mps=east)


def test_zero_relative_wind_and_moving_ship_in_calm_air():
    x = np.zeros(12)
    np.testing.assert_array_equal(wind_load(x, sea()), 0)
    x[0] = 5
    force = wind_load(x, sea())
    assert force[0] < 0
    np.testing.assert_array_equal(force[1:], 0)
    np.testing.assert_array_equal(wind_load(x, sea(north=5)), 0)


def test_head_tail_and_sidewind_signs():
    x = np.zeros(12)
    assert wind_load(x, sea(north=-10))[0] < 0
    assert wind_load(x, sea(north=10))[0] > 0
    port = wind_load(x, sea(east=10))
    starboard = wind_load(x, sea(east=-10))
    assert port[1] > 0 and starboard[1] < 0
    np.testing.assert_allclose(port[[0,1,5]], starboard[[0,1,5]] * [1,-1,-1])
    np.testing.assert_array_equal(port[2:5], 0)


def test_wind_force_scales_with_speed_squared_and_angle_is_periodic():
    a = isherwood72(.7, 10)
    np.testing.assert_allclose(isherwood72(.7, 20), 4*a)
    np.testing.assert_allclose(isherwood72(.7 + 2*math.pi, 10), a)
    np.testing.assert_allclose(isherwood72(-.7, 10), a*[1,-1,-1])


def test_earth_wind_rotates_into_body_heading():
    northbound = np.zeros(12)
    northbound[0] = 4
    eastbound = northbound.copy()
    eastbound[11] = math.pi/2
    a = wind_load(northbound, sea(north=-6, east=3))
    b = wind_load(eastbound, sea(north=-3, east=-6))
    np.testing.assert_allclose(a, b, atol=1e-9)


def test_relative_wind_uses_ground_velocity_not_water_velocity():
    x = np.zeros(12)
    x[0] = 3
    a = sea(north=-4)
    a.current_north_mps = 100
    b = sea(north=-4)
    b.current_north_mps = -100
    np.testing.assert_array_equal(wind_load(x,a), wind_load(x,b))


def test_exact_beam_coefficients_against_independent_scalar_formula():
    p = WindParameters()
    q = .5*1.224*10**2
    expected = q*np.array([
        -(.355-.247*130/175)*100,
        (.889+.138*2*100/25.4**2-.66*30/175)*2000,
        (.2627-.508*30/175)*2000*175,
    ])
    np.testing.assert_allclose(isherwood72(math.pi/2,10), expected)


@pytest.mark.parametrize('changes', [dict(loa_m=0),dict(air_density_kg_m3=float('nan')),
    dict(frontal_area_m2=-1),dict(superstructure_area_m2=2001),
    dict(centroid_from_bow_m=176),dict(mast_groups=.5)])
def test_invalid_geometry_is_rejected(changes):
    with pytest.raises(ValueError):
        replace(WindParameters(), **changes)


def test_invalid_wind_or_state_is_rejected():
    for angle,speed in [(float('nan'),10),(0,-1),(0,float('inf'))]:
        with pytest.raises(ValueError):
            isherwood72(angle,speed)
    with pytest.raises(ValueError):
        wind_load(np.zeros(11),sea())
