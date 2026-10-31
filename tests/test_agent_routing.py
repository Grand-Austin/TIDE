import json
import math
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

import pytest

from agent.routing import RouteSearch, _Sea as _RouteSea, _bowditch_loss_kn, _current_composition

REFERENCES = Path(__file__).resolve().parents[1] / "agent" / "references" / "benchmarks.json"


@dataclass(frozen=True)
class _SyntheticSea:
    hs: float
    tp: float
    wave_from_deg: float
    current_east_mps: float = 0.0
    current_north_mps: float = 0.0


class _Environment:
    def __init__(self, start, function):
        self.start, self.function = start, function

    def sample(self, time, lon, lat):
        result = self.function((time - self.start).total_seconds(), lon, lat)
        if result is None:
            raise ValueError("synthetic land or missing data")
        return result


class _Scenario:
    def __init__(self, definition, environment):
        self.definition, self.environment_source = definition, environment

    def environment(self):
        return nullcontext(self.environment_source)


def _rectangle(west, east, south, north):
    return [[west, south], [west, north], [east, north], [east, south]]


def _region_at(gameplay, elapsed):
    chosen = gameplay["regions"][0]
    for region in gameplay["regions"]:
        if region["time_s"] <= elapsed:
            chosen = region
    return chosen["polygon"]


def _inside(polygon, lon, lat):
    signs = []
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        cross = (x2 - x1) * (lat - y1) - (y2 - y1) * (lon - x1)
        if abs(cross) > 1e-12:
            signs.append(cross > 0)
    return bool(signs) and all(sign == signs[0] for sign in signs)


def _make_scenario(environment_function, *, regions, bbox=(0.0, 1.0, 0.0, 0.6),
                   start=(0.5, 0.3), budget=7200, speed_mps=5.0):
    definition = {
        "start": "2025-01-01T00:00:00Z",
        "end": "2025-01-01T00:30:00Z",
        "initial": {"lon": start[0], "lat": start[1], "heading_deg": 90.0,
                    "speed_mps": speed_mps},
        "mission": {"budget_s": budget, "target_lon_lat": [1.0, 0.3], "radius_m": 1000},
        "area_bbox": list(bbox),
        "wind_parameters": {"loa_m": 175.0},
    }
    from game.scenario import utc
    environment = _Environment(utc(definition["start"]), environment_function)
    gameplay = {"version": 1,
                "regions": [{"time_s": t, "polygon": polygon} for t, polygon in regions]}
    return _Scenario(definition, environment), gameplay


def _quiet_sea(elapsed, lon, lat):
    return _SyntheticSea(1.0, 8.0, 270.0, 0.2, 0.0)


def _schema_assertions(candidate, method):
    assert {"name", "solver", "orders", "estimated_s"} <= set(candidate)
    assert candidate["solver"].startswith(method + ":")
    assert candidate["orders"]
    times = [order["t"] for order in candidate["orders"]]
    assert times == sorted(times)
    assert times[0] == 0.0
    assert times[-1] <= candidate["estimated_s"] + 0.2
    for order in candidate["orders"]:
        assert 0.0 <= order["heading_deg"] < 360.0
        assert order["speed_knots"] > 0.0


def test_route_candidates_are_deterministic_and_have_persistent_commands():
    regions = [(0, _rectangle(0.2, 0.8, 0.15, 0.45))]
    scenario, gameplay = _make_scenario(_quiet_sea, regions=regions)
    for method in ("simroute", "visir2"):
        first = RouteSearch(scenario, gameplay).search(method, max_candidates=4)
        second = RouteSearch(scenario, gameplay).search(method, max_candidates=4)
        assert first == second
        assert first and len(first) <= 4
        assert len({candidate["name"] for candidate in first}) == len(first)
        for candidate in first:
            _schema_assertions(candidate, method)
            exit_point = candidate["exit"]
            assert not _inside(_region_at(gameplay, exit_point["time_s"]),
                               exit_point["lon"], exit_point["lat"])
            assert all(0.0 <= point["lon"] <= 1.0 and 0.0 <= point["lat"] <= 0.6
                       for point in candidate["path"])


def test_missing_data_barrier_forces_a_detour_on_water():
    def barrier(elapsed, lon, lat):
        if 0.30 <= lon <= 0.40 and 0.12 <= lat <= 0.48:
            return None
        return _SyntheticSea(1.0, 8.0, 270.0)

    regions = [(0, _rectangle(0.0, 0.5, 0.0, 0.6))]
    scenario, gameplay = _make_scenario(barrier, regions=regions, start=(0.2, 0.3),
                                       bbox=(0.0, 0.6, 0.0, 0.6), budget=10800)
    for method in ("simroute", "visir2"):
        candidates = RouteSearch(scenario, gameplay).search(method, max_candidates=3)
        assert candidates
        fastest = candidates[0]
        direct = math.hypot(fastest["exit"]["lon"] - 0.2,
                            (fastest["exit"]["lat"] - 0.3) * 0.6) * 60 * 1.15
        assert fastest["distance_nm"] > direct
        for point in fastest["path"]:
            assert not (0.30 <= point["lon"] <= 0.40 and 0.12 <= point["lat"] <= 0.48)


def test_moving_region_and_time_varying_current_use_actual_time():
    def current(elapsed, lon, lat):
        return _SyntheticSea(0.8, 8.0, 270.0, 1.0 + 0.4 * math.sin(2 * math.pi * elapsed / 1800), 0.0)

    regions = [(0, _rectangle(0.15, 0.75, 0.10, 0.40)),
               (900, _rectangle(0.15, 0.75, 0.25, 0.55))]
    scenario, gameplay = _make_scenario(current, regions=regions, start=(0.45, 0.22))
    search = RouteSearch(scenario, gameplay)
    candidates = search.search("visir2", max_candidates=4)
    assert candidates
    assert len(search.diagnostic["goal_times"]) > 1
    assert any(candidate["target_time_s"] > 0 for candidate in candidates)
    for candidate in candidates:
        _schema_assertions(candidate, "visir2")
        assert not _inside(_region_at(gameplay, candidate["exit"]["time_s"]),
                           candidate["exit"]["lon"], candidate["exit"]["lat"])
    assert any(abs((point["heading_deg"] - point["course_deg"] + 540) % 360 - 180) > 1.0
               for candidate in candidates for point in candidate["path"])


def test_exhausted_search_returns_empty_list_with_diagnostic():
    regions = [(0, _rectangle(0.0, 1.0, 0.0, 0.6))]  # no exit cell remains inside the domain
    scenario, gameplay = _make_scenario(_quiet_sea, regions=regions)
    search = RouteSearch(scenario, gameplay)
    assert search.search("simroute") == []
    assert search.diagnostic["status"] == "exhausted"
    assert "not a proof of no solution" in search.diagnostic["reason"]

    missing, gameplay = _make_scenario(lambda *args: None, regions=regions)
    search = RouteSearch(missing, gameplay)
    assert search.search("visir2") == []
    assert search.diagnostic["status"] == "exhausted"


def test_method_and_candidate_count_validation():
    scenario, gameplay = _make_scenario(_quiet_sea, regions=[(0, _rectangle(0.2, 0.8, 0.1, 0.5))])
    search = RouteSearch(scenario, gameplay)
    with pytest.raises(ValueError):
        search.search("dijkstra")
    with pytest.raises(ValueError):
        search.search("simroute", max_candidates=0)


def test_simroute_bowditch_reference_edge_matches_published_case():
    # First edge of the official Palma-Barcelona case (SIMROUTE commit 7d9a338).
    hs_a, hs_b = 1.4656864633867295, 1.1940021984543945
    wave_from, course, distance_nm = 112.51947885022184, 72.07953735326944, 4.8832689428539195
    speed = 16.1 - _bowditch_loss_kn((hs_a + hs_b) / 2, wave_from, course)
    assert speed == pytest.approx(15.627923038986786, rel=1e-13)
    assert distance_nm / speed == pytest.approx(0.31247075703352833, rel=1e-13)


def test_published_benchmark_provenance_is_not_mislabelled():
    reference = json.loads(REFERENCES.read_text())
    simroute = reference["simroute_palma_barna"]
    assert simroute["license"] == "No license file in repository; no upstream code copied"
    assert simroute["status"] == "independent_reproduction"
    assert simroute["independent_reproduction"]["optimized_with_waves_h"] == pytest.approx(14.973725552938715)
    visir = reference["visir2"]
    assert visir["license"] == "GPL-3.0-or-later"
    assert visir["status"] == "official_analytic_current_reproduced"
    assert visir["alboran_full_numerical_status"] == "not_run"
    assert visir["repository"] == "https://doi.org/10.5281/zenodo.10960842"
    assert visir["available_reference_benchmarks"]["analytic_current"]["status"] == (
        "reproduced_official"
    )
    assert visir["available_reference_benchmarks"]["alboran_current_wind"]["status"] == (
        "not_run"
    )
    reproduction = visir["reproduction"]
    assert reproduction["case"] == "official_analytic_current_techy"
    assert reproduction["official_verdict"] == "Curr analytic PASSED"
    assert reproduction["comparison"]["passed"] is True
    assert reproduction["comparison"]["tolerance_percent"] == 3.0
    assert reproduction["measured"]["L_over_L0"] == pytest.approx(1.017587668235335)
    assert reproduction["measured"]["T_over_T0"] == pytest.approx(1.0568394582308234)
    assert reproduction["reference"] == {"L_over_L0": 1.00480754, "T_over_T0": 1.03038814}


def test_visir_current_composition_distinguishes_heading_from_course():
    sea = _RouteSea(0.0, 8.0, 0.0, 1.0, 1.0, 0.0)
    stw, sog, heading = _current_composition(10.0, sea, 0.0, 175.0)
    assert stw == pytest.approx(10.0)
    assert sog == pytest.approx(math.sqrt(100.0 - (1.0 * 1.9438444924406) ** 2), rel=1e-12)
    assert abs((heading - 0.0 + 540) % 360 - 180) > 1.0
