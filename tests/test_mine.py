from pipeline.mine import Flip, day_flips, reason
from pipeline.simulate import Limits, SeaState, simulate

NORTH = [(3.0, 40.0), (3.0, 40.4)]


def calm(t, lon, lat):
    return SeaState(hs=0.5, wave_from=0, period=5)


def test_reason_follows_schema_order():
    rough = lambda t, lon, lat: SeaState(hs=3.0, wave_from=90, period=7)
    assert reason(simulate(NORTH, 8, rough, Limits(1.5, 2.5, 20))) == "beam_hours"
    assert reason(simulate(NORTH, 8, rough, Limits(1.5, 2.5, 20, beam_from=100))) == "over_hours"
    assert reason(simulate(NORTH, 18, calm, Limits(1.5, 2.5, 20))) == "late"
    assert reason(simulate(NORTH, 8, calm, Limits(1.5, 2.5, 20))) == "slower"


def test_deadline_flips_are_not_sea_state_flips():
    flips = day_flips(calm, Limits(1.5, 2.5, arrive_by=12.0))
    assert flips and all(f.reason == "late" and not f.sea_state for f in flips)
    assert day_flips(calm, Limits(1.5, 2.5, arrive_by=99.0)) == []


def test_waves_building_on_the_beam_flip_the_route():
    building = lambda t, lon, lat: SeaState(hs=0.5 if t < 7 else 2.0, wave_from=0, period=6)
    flips = day_flips(building, Limits(1.5, 2.5, arrive_by=99.0))
    assert any(f.sea_state and f.reason in ("beam_hours", "over_hours") for f in flips)
    assert Flip(7.0, "A", "C", "beam_hours").route_to_route and not Flip(7.0, "A", None, "late").sea_state
