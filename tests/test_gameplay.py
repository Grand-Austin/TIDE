import json
from types import SimpleNamespace

from game.rules.mission import MissionRules, inside


def gameplay_fixture():
    return {
        "version": 1,
        "regions": [
            {
                "time_s": 0,
                "polygon": [[0.0, 0.0], [3.0, 0.0], [3.0, 3.0], [0.0, 3.0]],
            },
            {
                "time_s": 60,
                # An empty polygon represents disappearance of the entire adverse region.
                "polygon": [],
            },
        ],
        "reports": [
            {
                "id": "forecast-early",
                "sector": [1, 1],
                "issued_s": 0,
                "valid_to_s": 30,
                "important": False,
            },
            {
                "id": "forecast-important",
                "sector": [1, 1],
                "issued_s": 10,
                "valid_to_s": 40,
                "important": True,
            },
            {
                "id": "forecast-neighbor",
                "sector": [1, 0],
                "issued_s": 0,
                "valid_to_s": 60,
                "important": False,
            },
            {
                "id": "forecast-far",
                "sector": [2, 2],
                "issued_s": 0,
                "valid_to_s": 60,
                "important": False,
            },
        ],
    }


def rules_fixture():
    return MissionRules(gameplay_fixture(), budget_s=50, bbox=(0.0, 9.0, 0.0, 9.0))


def sample(lon, lat, time_s):
    return SimpleNamespace(lon=lon, lat=lat, time_s=time_s)


def test_convex_geometry_includes_interior_and_boundary_points():
    polygon = [[0.0, 0.0], [3.0, 0.0], [3.0, 3.0], [0.0, 3.0]]

    assert inside(polygon, 1.5, 1.5)
    assert inside(polygon, 0.0, 1.5)
    assert inside(polygon, 3.0, 3.0)
    assert not inside(polygon, 3.01, 1.5)
    assert not inside(polygon, 1.5, -0.01)
    assert not inside([], 1.5, 1.5)


def test_disappearance_of_whole_region_allows_escape():
    rules = rules_fixture()

    assert rules.contains(1.5, 1.5, 59.999)
    assert not rules.contains(1.5, 1.5, 60.0)


def test_region_schedule_uses_the_new_region_at_its_exact_start_time():
    rules = rules_fixture()

    assert rules.contains(2.9, 2.9, 59.999)
    assert not rules.contains(2.9, 2.9, 60.0)


def test_escape_takes_priority_over_timeout():
    rules = rules_fixture()

    status, events = rules.evaluate(sample(8.0, 8.0, 50.0), set())

    assert status == "escaped"
    assert [event["kind"] for event in events] == ["escaped"]


def test_domain_boundary_takes_priority_over_escape_and_timeout():
    rules = rules_fixture()

    status, events = rules.evaluate(sample(-0.1, 1.0, 50.0), set())

    assert status == "domain_boundary"
    assert events == []


def test_inside_domain_timeout_is_reported_at_the_budget():
    rules = rules_fixture()

    status, events = rules.evaluate(sample(1.5, 1.5, 50.0), set())

    assert status == "timeout"
    assert [event["kind"] for event in events] == ["timeout"]


def test_reports_are_released_once_and_only_while_valid():
    rules = rules_fixture()
    consumed = set()

    before_release, _ = rules.evaluate(sample(1.5, 1.5, 9.0), consumed)
    at_release, release_events = rules.evaluate(sample(1.5, 1.5, 10.0), consumed)
    duplicate_status, duplicate_events = rules.evaluate(
        sample(1.5, 1.5, 11.0), consumed
    )
    at_expiry, expiry_events = rules.evaluate(sample(1.5, 1.5, 40.0), consumed)

    assert before_release is None
    assert at_release == "weather_report"
    assert any(
        event["kind"] == "report_released" and event["detail"] == "forecast-important"
        for event in release_events
    )
    assert duplicate_status is None
    assert duplicate_events == []
    assert at_expiry is None
    assert expiry_events == []
    assert consumed == {"forecast-early", "forecast-important", "forecast-neighbor"}


def test_report_sector_visibility_includes_neighbours_but_excludes_distant_sectors():
    rules = rules_fixture()

    visible_ids = {report["id"] for report in rules.visible_reports(5.0, 1.5, 1.5)}

    assert "forecast-neighbor" in visible_ids
    assert "forecast-far" not in visible_ids
    assert "forecast-important" not in visible_ids


def test_split_run_json_round_trip_preserves_mission_behavior():
    gameplay = gameplay_fixture()
    restored = json.loads(json.dumps(gameplay))
    rules = MissionRules(restored, budget_s=50, bbox=(0.0, 9.0, 0.0, 9.0))
    consumed = set()

    status, events = rules.evaluate(sample(1.5, 1.5, 10.0), consumed)

    assert status == "weather_report"
    released = {
        event["detail"] for event in events if event["kind"] == "report_released"
    }
    assert released == {"forecast-early", "forecast-important", "forecast-neighbor"}
    assert consumed == released


def test_low_wave_pocket_inside_envelope_is_not_escape():
    rules = rules_fixture()
    point = sample(1.5, 1.5, 5)
    point.hs_m = 0.5
    reason, _ = rules.evaluate(point, set())
    assert rules.contains(point.lon, point.lat, point.time_s)
    assert reason is None


def test_forecast_directions_use_16_points_and_report_a_turning_wind():
    from types import SimpleNamespace
    from pipeline.gameplay import _point, _shift, _wind_from

    assert [_point(d) for d in (258.0, 265.0, 340.0, 359.0)] == [247.5, 270.0, 337.5, 0.0]
    westerly = SimpleNamespace(wind_east_mps=10.0, wind_north_mps=0.0)  # blowing east: from the west
    assert _wind_from(westerly) == 270.0
    north_westerly = SimpleNamespace(wind_east_mps=7.0, wind_north_mps=-7.0)
    assert _shift([westerly, north_westerly]) == "veering"  # 270 -> 315, clockwise
    assert _shift([north_westerly, westerly]) == "backing"
    assert _shift([westerly, westerly]) == "steady"
