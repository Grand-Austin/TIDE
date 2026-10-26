import math

import pytest

from pipeline.simulate import (
    Limits,
    SeaState,
    best_route,
    encounter,
    legs_of,
    simulate,
    tipping_points,
)

LIMITS = Limits(beam_hs=1.5, max_hs=2.5, arrive_by=20.5)
EAST = [(3.0, 40.0), (3.5, 40.0)]  # about 23 nm due east
NORTH = [(3.0, 40.0), (3.0, 40.4)]  # 24 nm due north


def calm(t, lon, lat):
    return SeaState(hs=0.5, wave_from=0, period=5)


def test_legs_heading_and_distance():
    (east,) = legs_of(EAST)
    (north,) = legs_of(NORTH)
    assert east.heading == pytest.approx(90)
    assert east.nm == pytest.approx(0.5 * 60 * math.cos(math.radians(40)))
    assert north.heading == pytest.approx(0)
    assert north.nm == pytest.approx(24)


@pytest.mark.parametrize(
    "heading, wave_from, category",
    [(0, 0, "head"), (90, 0, "beam"), (180, 0, "following"), (350, 20, "head"), (75, 345, "beam")],
)
def test_encounter_category(heading, wave_from, category):
    assert encounter(heading, wave_from)[1] == category


def test_beam_sector_boundary_is_configurable():
    assert encounter(65, 0)[1] == "beam"
    assert encounter(65, 0, beam_from=70)[1] == "head"
    assert encounter(112, 0, beam_from=70)[1] == "following"


def test_calm_passage_is_safe_and_takes_distance_over_speed():
    r = simulate(NORTH, departure=8, sea_at=calm, limits=LIMITS, speed_kn=6)
    assert r.safe
    assert r.eta_hours == pytest.approx(4, abs=1 / 12)


def test_beam_seas_above_limit_flag_the_passage():
    rough_from_north = lambda t, lon, lat: SeaState(hs=1.8, wave_from=0, period=6)
    east = simulate(EAST, 8, rough_from_north, LIMITS)
    north = simulate(NORTH, 8, rough_from_north, LIMITS)
    assert east.beam_hours > 0 and not east.safe
    assert north.beam_hours == 0 and north.safe


def test_contrary_current_slows_the_vessel():
    against = lambda t, lon, lat: SeaState(0.5, 0, 5, current_kn=1.0, current_to=180)
    assert simulate(NORTH, 8, against, LIMITS).eta_hours > simulate(NORTH, 8, calm, LIMITS).eta_hours


def test_late_arrival_is_unsafe():
    r = simulate(NORTH, departure=18, sea_at=calm, limits=LIMITS)
    assert r.late and not r.safe


def test_best_route_and_tipping_points():
    morning = {"A": simulate(EAST, 6, calm, LIMITS), "B": simulate(NORTH, 6, calm, LIMITS)}
    assert best_route(morning) == "A"
    assert best_route({"B": simulate(NORTH, 18, calm, LIMITS)}) is None
    assert tipping_points([(6, "A"), (7, "A"), (8, "C"), (14, None)]) == [(8, "A", "C"), (14, "C", None)]
