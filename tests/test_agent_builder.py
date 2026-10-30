import copy
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest
from shapely.geometry import box

from agent.builder import Builder, planned_budget
from agent.voyage import VoyageUnavailable
from game.navigation import CoastGuard
from agent.models import (
    Candidate,
    digest_file,
    evidence_fingerprint,
    normalize_orders,
)


def definition(budget_s=3600, seed=1):
    return {
        "schema_version": 1,
        "id": "builder-test",
        "title": "Builder test",
        "start": "2026-01-01T00:00:00Z",
        "end": "2026-01-01T01:00:00Z",
        "seed": seed,
        "initial": {
            "lon": 0.0,
            "lat": 0.0,
            "heading_deg": 0.0,
            "speed_mps": 5.0,
        },
        "mission": {
            "budget_s": budget_s,
            "target_lon_lat": [0.0, 1.0],
            "radius_m": 1000,
            "review_interval_s": 300,
        },
        "vessel": {
            "path": "vessel",
            "sha256": "v" * 64,
            "source_commit": "c" * 40,
            "loading_condition": "Design waterline",
        },
        "settings": {},
        "wind_parameters": {},
        "environment": {
            "waves": {"path": "env/waves.nc", "sha256": "w" * 64},
            "currents": {"path": "env/currents.nc", "sha256": "c" * 64},
            "wind": {"path": "env/wind.nc", "sha256": "d" * 64},
        },
        "area_bbox": [-1.0, 1.0, -1.0, 1.0],
    }


class FakeScenario:
    def __init__(self, root: Path, value: dict):
        self.root = root
        self.definition = value


class FakeRouteSearch:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []

    def search(self, method, *, max_candidates=6):
        self.calls.append(method)
        return self.payloads.get(method, [])


class FakeSimulator:
    """A deterministic stand-in for agent.simulator.SimulatorService."""

    def __init__(self, outcomes, *, fail_final=False, fail_headings=()):
        self.outcomes = outcomes
        self.calls = []
        self.fail_final = fail_final
        self.fail_headings = set(fail_headings)

    @staticmethod
    def _fingerprint(view):
        payload = {
            key: value
            for key, value in view.items()
            if key != "state_fingerprint"
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()
        return "runtime-" + digest

    def run_plan(self, path, orders):
        location = str(path)
        self.calls.append((location, [dict(o) for o in orders]))
        heading = round(float(orders[0]["heading_deg"])) % 360
        if self.fail_final and "/package/" in location:
            raise RuntimeError("simulated final session failure")
        if "/package/" in location and heading in self.fail_headings:
            raise RuntimeError("simulated final alternative failure")
        spec = self.outcomes.get(heading, {"outcome": "timeout", "elapsed_s": 3600})
        view = {
            "elapsed_s": spec.get("elapsed_s", 3600),
            "outcome": spec.get("outcome"),
            "remaining_s": max(0, 4500 - spec.get("elapsed_s", 3600)),
            "cargo": {
                "lost_teu": spec.get("lost_teu", 0),
                "total_teu": 1200,
                "grade": spec.get("grade", 0),
            },
            "ship": {"lost": spec.get("ship_lost", False)},
            "vessel": {
                "lon": 0.1,
                "lat": 0.2,
                "heading_deg": heading,
                "time_s": spec.get("elapsed_s", 3600),
                "speed_through_water_mps": 5.0,
            },
            "revision": 1,
        }
        return {
            "trial_id": f"trial-{len(self.calls)}",
            "outcome": spec.get("outcome"),
            "elapsed_s": spec.get("elapsed_s", 3600),
            "lost_teu": spec.get("lost_teu", 0),
            "total_teu": 1200,
            "ship_lost": spec.get("ship_lost", False),
            "orders": [dict(o) for o in orders],
            "session": f"sessions/{len(self.calls)}",
            "view": dict(view, state_fingerprint=self._fingerprint(view)),
        }


class FlakySimulator(FakeSimulator):
    """Fail once for one heading, then work normally."""

    def __init__(self, outcomes, *, fail_heading, failures=1, **kwargs):
        super().__init__(outcomes, **kwargs)
        self.fail_heading = fail_heading
        self.remaining_failures = failures

    def run_plan(self, path, orders):
        heading = round(float(orders[0]["heading_deg"])) % 360
        if heading == self.fail_heading and self.remaining_failures:
            self.remaining_failures -= 1
            self.calls.append((str(path), [dict(o) for o in orders]))
            raise RuntimeError("simulated transient trial failure")
        return super().run_plan(path, orders)


class MutatingSimulator(FakeSimulator):
    def __init__(self, outcomes, mutation):
        super().__init__(outcomes)
        self.mutation = mutation

    def run_plan(self, path, orders):
        result = super().run_plan(path, orders)
        self.mutation(result)
        return result


class FakeVoyagePlanner:
    """A deterministic stand-in for agent.voyage.VoyagePlanner."""

    def __init__(self, *, error=None):
        self.calls = []
        self.error = error

    def build(self, initial):
        self.calls.append(copy.deepcopy(initial))
        if self.error is not None:
            raise self.error
        start = [float(initial["lon"]), float(initial["lat"])]
        return {
            "version": 1,
            "origin": {
                "name": "Test West",
                "country": "Test",
                "lon": start[0] - 0.6,
                "lat": start[1],
            },
            "destination": {
                "name": "Test North",
                "country": "Test",
                "lon": start[0],
                "lat": start[1] + 0.8,
            },
            "track": [
                [start[0] - 0.6, start[1]],
                start,
                [start[0], start[1] + 0.8],
            ],
            "distance_nm": 84.0,
            "snapped_position": start,
            "snap_distance_nm": 0.0,
            "provenance": {
                "method": "synthetic_test_network",
                "authored": True,
                "catalogue_sha256": "synthetic-catalogue",
                "searoute_version": "test",
                "initial": copy.deepcopy(initial),
            },
            "authored": True,
        }


def synthetic_coast_guard(bbox):
    """A fixed 5 NM guard with synthetic land away from the test start."""
    west, east, south, north = bbox
    land = box(
        max(west, 0.8),
        max(south, 0.8),
        min(east, 1.0),
        min(north, 1.0),
    )
    return CoastGuard(bbox, land=land)


def synthetic_voyage(start=(0.0, 0.0)):
    return FakeVoyagePlanner().build(
        {"lon": start[0], "lat": start[1], "heading_deg": 0.0}
    )


def write_weather(root, last_s):
    (root / "input" / "weather.json").write_text(
        json.dumps(
            {
                "source": "test",
                "model": "test",
                "hourly": [
                    {"t_s": 0, "cloud": 50, "precip": 0},
                    {"t_s": last_s, "cloud": 60, "precip": 0.1},
                ],
            }
        )
        + "\n"
    )


MUTATIONS = {
    "missing_lost": lambda result: result.pop("lost_teu"),
    "missing_total": lambda result: result.pop("total_teu"),
    "zero_total": lambda result: result.__setitem__("total_teu", 0),
    "lost_above_total": lambda result: result.__setitem__("lost_teu", 1300),
    "nonfinite_lost": lambda result: result.__setitem__("lost_teu", float("nan")),
    "alias_outcome": lambda result: result.__setitem__("outcome", "escape"),
    "missing_orders": lambda result: result.__setitem__("orders", []),
    "view_lost_mismatch": lambda result: result["view"]["cargo"].__setitem__(
        "lost_teu", 1200
    ),
    "view_elapsed_mismatch": lambda result: result["view"].__setitem__(
        "elapsed_s", 0.0
    ),
}


def gameplay_for(scenario):
    budget = int(scenario.definition["mission"]["budget_s"])
    return {
        "version": 1,
        "region_method": "test envelope",
        "source_sha256": scenario.definition["environment"]["waves"]["sha256"],
        "regions": [
            {"time_s": 0, "polygon": []},
            {"time_s": budget, "polygon": []},
        ],
        "reports": [
            {
                "id": "forecast-test",
                "sector": [0, 0],
                "issued_s": 0,
                "valid_to_s": budget,
            }
        ],
        "cargo": {},
    }


def make_input(tmp_path, budget_s=3600, with_gameplay=True):
    root = tmp_path
    (root / "env").mkdir(parents=True, exist_ok=True)
    for name in ("waves", "currents", "wind"):
        (root / "env" / f"{name}.nc").write_bytes(f"{name}-data".encode())
    value = definition(budget_s)
    if with_gameplay:
        (root / "input").mkdir(exist_ok=True)
        (root / "input" / "gameplay.json").write_text('{"version": 1}\n')
        value["gameplay"] = {"path": "input/gameplay.json", "sha256": "g" * 64}
    path = root / "input" / "scenario.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")
    return path, FakeScenario(root, json.loads(path.read_text()))


def coverage_ok(_definition, _root):
    return {"ok": True, "errors": [], "layers": {}, "intersection": "test"}


def route_search_for(extra=None):
    payloads = {
        "simroute": [
            {
                "name": "lossy",
                "solver": "simroute",
                "orders": [{"t": 0, "heading_deg": 100, "speed_knots": 9}],
                "estimated_s": 900,
            },
            {
                "name": "ok",
                "solver": "simroute",
                "orders": [{"t": 0, "heading_deg": 90, "speed_knots": 9}],
                "estimated_s": 5200,
            },
        ],
        "visir2": [
            {
                "name": "slow",
                "solver": "visir2",
                "orders": [{"t": 0, "heading_deg": 270, "speed_knots": 9}],
                "estimated_s": 3600,
            },
            {
                "name": "lost-ship",
                "solver": "visir2",
                "orders": [{"t": 0, "heading_deg": 0, "speed_knots": 9}],
                "estimated_s": 500,
            },
        ],
    }
    if extra:
        payloads.update(extra)
    return FakeRouteSearch(payloads)


OUTCOMES = {
    90: {"outcome": "escaped", "elapsed_s": 2400, "lost_teu": 12, "grade": 3},
    100: {"outcome": "escaped", "elapsed_s": 1000, "lost_teu": 13, "grade": 3},
    270: {"outcome": "timeout", "elapsed_s": 3000, "lost_teu": 0},
    0: {"outcome": "ship_lost", "elapsed_s": 500, "lost_teu": 1200, "ship_lost": True},
}


def builder(tmp_path, **kwargs):
    path, scenario = make_input(tmp_path, kwargs.pop("budget_s", 3600))
    values = dict(
        scenario_path=path,
        out=kwargs.pop("out", tmp_path / "run"),
        project_root=tmp_path,
        scenario=scenario,
        simulator=kwargs.pop("simulator", FakeSimulator(OUTCOMES)),
        route_search=kwargs.pop("route_search", route_search_for()),
        coverage_check=coverage_ok,
        gameplay_builder=gameplay_for,
        voyage_planner=kwargs.pop("voyage_planner", None) or FakeVoyagePlanner(),
        coast_guard=kwargs.pop("coast_guard", synthetic_coast_guard),
        expert_limit=0,
        max_revisions=0,
        max_trials=12,
        max_candidates=4,
        min_solutions=1,
    )
    values.update(kwargs)
    return Builder(**values)


def test_budget_formula_uses_verified_winner_time(tmp_path):
    assert planned_budget(2400) == 3600
    assert planned_budget(4200) == 5400
    assert planned_budget(900) == 1800
    assert planned_budget(100) == 900
    b = builder(tmp_path, budget_s=3600)
    result = b.build("local")
    assert result["status"] == "review_ready"
    assert result["elapsed_s"] == 2400
    budget = 3600
    assert budget == 900 * math.ceil(1.2 * result["elapsed_s"] / 900)
    value = json.loads((tmp_path / "run" / "package" / "scenario.json").read_text())
    assert value["mission"]["budget_s"] == budget
    assert value["end"] == "2026-01-01T01:00:00Z"
    assert value["start"] == "2026-01-01T00:00:00Z"
    gameplay = json.loads((tmp_path / "run" / "package" / "gameplay.json").read_text())
    assert gameplay["regions"][-1]["time_s"] == budget
    assert max(r["valid_to_s"] for r in gameplay["reports"]) == budget
    report = json.loads((tmp_path / "run" / "package" / "build-report.json").read_text())
    assert report["search_horizon_s"] == 3600
    assert report["budget"]["budget_s"] == budget


def test_search_horizon_shrinks_to_available_coverage(tmp_path):
    def limited_coverage(definition, _root):
        layers = {
            "waves": {
                "time": ["2026-01-01T00:00:00", "2026-01-01T01:00:00"],
            }
        }
        budget = int(definition["mission"]["budget_s"])
        return {
            "ok": budget <= 3600,
            "errors": [] if budget <= 3600 else ["source ends earlier"],
            "layers": layers,
        }

    path, scenario = make_input(tmp_path, budget_s=7200)
    sim = FakeSimulator(OUTCOMES)
    b = Builder(
        scenario_path=path,
        out=tmp_path / "run",
        project_root=tmp_path,
        scenario=scenario,
        simulator=sim,
        route_search=route_search_for(),
        coverage_check=limited_coverage,
        gameplay_builder=gameplay_for,
        voyage_planner=FakeVoyagePlanner(),
        coast_guard=synthetic_coast_guard,
        expert_limit=0,
        max_revisions=0,
        max_trials=8,
        min_solutions=1,
    )
    result = b.build("local")
    assert result["status"] == "review_ready"
    assert result["budget_s"] == 3600
    report = json.loads((b.package / "build-report.json").read_text())
    assert report["search_horizon_s"] == 3600
    assert report["coverage"]["adjusted_from_s"] == 7200
    assert report["final_coverage"]["ok"] is True


def test_boundary_seventy_minute_route_uses_ninety_minute_budget(tmp_path):
    outcomes = dict(OUTCOMES)
    outcomes[90] = {
        "outcome": "escaped",
        "elapsed_s": 4200,
        "lost_teu": 12,
        "grade": 3,
    }
    path, scenario = make_input(tmp_path, budget_s=7200)
    sim = FakeSimulator(outcomes)
    b = Builder(
        scenario_path=path,
        out=tmp_path / "run",
        project_root=tmp_path,
        scenario=scenario,
        simulator=sim,
        route_search=route_search_for(),
        coverage_check=coverage_ok,
        gameplay_builder=gameplay_for,
        voyage_planner=FakeVoyagePlanner(),
        coast_guard=synthetic_coast_guard,
        expert_limit=0,
        max_revisions=0,
        max_trials=8,
        min_solutions=1,
    )
    result = b.build("local")
    assert result["status"] == "review_ready"
    assert result["elapsed_s"] == 4200
    assert result["budget_s"] == 5400
    packaged = json.loads((b.package / "scenario.json").read_text())
    assert packaged["mission"]["budget_s"] == 5400
    assert packaged["end"] == "2026-01-01T01:30:00Z"
    original = json.loads(path.read_text())
    assert original["mission"]["budget_s"] == 7200


def test_inputs_are_immutable_and_package_is_complete(tmp_path):
    path, scenario = make_input(tmp_path)
    before = {
        p: digest_file(p)
        for p in [path, tmp_path / "input" / "gameplay.json", *sorted((tmp_path / "env").glob("*"))]
    }
    b = builder(tmp_path, route_search=route_search_for())
    result = b.build("local")
    assert result["status"] == "review_ready"
    after = {p: digest_file(p) for p in before}
    assert before == after
    package = tmp_path / "run" / "package"
    for name in (
        "scenario.json",
        "gameplay.json",
        "validation.json",
        "solutions.json",
        "build-report.json",
    ):
        assert (package / name).is_file()
    assert not (package / "hints.json").exists()
    assert not (package / "hints.md").exists()
    value = json.loads((path).read_text())
    assert value["mission"]["budget_s"] == 3600
    assert value["end"] == "2026-01-01T01:00:00Z"


def test_99_percent_threshold_and_fastest_qualified(tmp_path):
    search = route_search_for()
    b = builder(tmp_path, route_search=search, max_revisions=0)
    result = b.build("local")
    assert search.calls == ["simroute", "visir2"]
    assert result["status"] == "review_ready"
    assert result["reference"] == "ok"
    validation = json.loads(
        (tmp_path / "run" / "package" / "validation.json").read_text()
    )
    assert validation["version"] == 1
    assert validation["passed"] is True
    assert validation["status"] == "review_ready"
    assert validation["reference"] == "ok"
    by_name = {s["name"]: s for s in validation["strategies"]}
    assert by_name["ok"]["qualified"] is True
    assert by_name["ok"]["lost_teu"] / by_name["ok"]["total_teu"] == pytest.approx(0.01)
    assert by_name["lossy"]["qualified"] is False
    assert by_name["lossy"]["elapsed_s"] < by_name["ok"]["elapsed_s"]
    assert by_name["slow"]["qualified"] is False
    assert by_name["lost-ship"]["qualified"] is False
    assert validation["metadata"]["builder"]["evidence_fingerprint"]["fingerprint"]
    assert validation["metadata"]["builder"]["approval"] == "none"
    assert validation["metadata"]["builder"]["published"] is False


def test_environment_coverage_pending_does_not_run_trials(tmp_path):
    sim = FakeSimulator(OUTCOMES)
    b = builder(
        tmp_path,
        simulator=sim,
        coverage_check=lambda *_: {
            "ok": False,
            "errors": ["wind: mission window is outside source time coverage"],
        },
    )
    result = b.build("local")
    assert result["status"] == "pending"
    assert sim.calls == []
    validation = json.loads(
        (tmp_path / "run" / "package" / "validation.json").read_text()
    )
    assert validation["status"] == "pending"
    assert validation["passed"] is False
    assert any(
        reason.startswith("inadequate_environmental_time_coverage")
        for reason in validation["metadata"]["pending_reasons"]
    )
    solutions = json.loads(
        (tmp_path / "run" / "package" / "solutions.json").read_text()
    )
    assert solutions["status"] == "pending"
    assert solutions["solutions"] == []


def test_final_definition_is_revalidated_and_can_fail(tmp_path):
    sim = FakeSimulator(OUTCOMES, fail_final=True)
    b = builder(tmp_path, simulator=sim)
    result = b.build("local")
    assert result["status"] == "pending"
    assert any(reason.startswith("final_revalidation_failed") for reason in result["pending_reasons"])
    assert sim.calls[-1][0].endswith("package/scenario.json")
    validation = json.loads(
        (tmp_path / "run" / "package" / "validation.json").read_text()
    )
    assert validation["passed"] is False
    assert validation["status"] == "pending"


def test_final_revalidation_uses_final_definition(tmp_path):
    sim = FakeSimulator(OUTCOMES)
    b = builder(tmp_path, simulator=sim)
    result = b.build("local")
    assert result["status"] == "review_ready"
    assert result["final_revalidated"] is True
    assert sim.calls[-1][0].endswith("package/scenario.json")
    validation = json.loads(
        (tmp_path / "run" / "package" / "validation.json").read_text()
    )
    final = [s for s in validation["strategies"] if s["final_validation"]]
    assert final
    assert all(s["outcome"] == "escaped" for s in final)
    assert all(s["qualified"] is True for s in final)
    assert result["solution_count"] == 1


def solutions_for(build):
    return json.loads((build.package / "solutions.json").read_text())


def route(name, heading):
    return {
        "name": name,
        "orders": [{"t": 0, "heading_deg": heading, "speed_knots": 9}],
    }


def route_outcomes(extra=None):
    outcomes = dict(OUTCOMES)
    outcomes.update(
        {
            80: {"outcome": "escaped", "elapsed_s": 2200, "lost_teu": 6, "grade": 3},
            70: {"outcome": "escaped", "elapsed_s": 2600, "lost_teu": 2, "grade": 3},
        }
    )
    if extra:
        outcomes.update(extra)
    return outcomes


def assert_concrete_solutions(value):
    assert value["version"] == 1
    assert value["kind"] == "solution_routes"
    assert value["solution_count"] == len(value["solutions"])
    assert value["required_minimum"] == 3
    assert value["source_fingerprint"]
    assert "levels" not in value
    orders = []
    for solution in value["solutions"]:
        assert solution["total_teu"] == 1200
        assert solution["lost_teu"] / solution["total_teu"] <= 0.01
        assert solution["elapsed_s"] <= value["budget_s"]
        assert solution["track_status"] in {"sampled", "endpoints_only"}
        assert solution["provenance"]["final_validation"] is True
        assert solution["provenance"]["fingerprint"] == value["source_fingerprint"]
        assert solution["orders"]
        for order in solution["orders"]:
            assert {"t", "heading_deg", "speed_knots"} <= set(order)
        orders.append(
            tuple(
                (order["t"], order["heading_deg"], order["speed_knots"])
                for order in solution["orders"]
            )
        )
    assert len(set(orders)) == len(orders)
    return orders


def test_three_distinct_final_routes_are_review_ready(tmp_path):
    search = FakeRouteSearch(
        {
            "simroute": [route("fast", 80), route("ok", 90), route("slower", 70)],
            "visir2": [route("slow", 270), route("lost-ship", 0)],
        }
    )
    sim = FakeSimulator(route_outcomes())
    build = builder(
        tmp_path,
        route_search=search,
        simulator=sim,
        min_solutions=3,
    )
    result = build.build("local")

    assert result["status"] == "review_ready"
    assert result["reference"] == "fast"
    assert result["min_solutions"] == 3
    assert result["solution_count"] == 3
    assert result["budget_s"] == 2700
    value = solutions_for(build)
    assert value["status"] == "ready"
    assert value["budget_s"] == 2700
    orders = assert_concrete_solutions(value)
    assert value["solution_count"] == 3
    assert [solution["elapsed_s"] for solution in value["solutions"]] == [
        2200,
        2400,
        2600,
    ]
    assert {
        heading
        for solution_orders in orders
        for _, heading, _ in solution_orders
    } == {80, 90, 70}


def test_two_final_routes_are_insufficient_pending(tmp_path):
    search = FakeRouteSearch(
        {
            "simroute": [route("fast", 80), route("ok", 90)],
            "visir2": [route("slow", 270), route("lost-ship", 0)],
        }
    )
    build = builder(
        tmp_path,
        route_search=search,
        simulator=FakeSimulator(route_outcomes()),
        min_solutions=3,
    )
    result = build.build("local")

    assert result["status"] == "pending"
    assert result["final_revalidated"] is True
    assert result["reference"] is None
    assert result["solution_count"] == 2
    assert "insufficient_qualified_solutions" in result["pending_reasons"]
    value = solutions_for(build)
    assert value["status"] == "insufficient"
    assert value["required_minimum"] == 3
    assert value["solution_count"] == len(value["solutions"]) == 2


def test_no_final_routes_are_pending_with_no_solutions(tmp_path):
    search = FakeRouteSearch(
        {
            "simroute": [route("slow", 270)],
            "visir2": [route("lost-ship", 0)],
        }
    )
    build = builder(
        tmp_path,
        route_search=search,
        simulator=FakeSimulator(OUTCOMES),
        min_solutions=3,
    )
    result = build.build("local")

    assert result["status"] == "pending"
    assert result["solution_count"] == 0
    value = solutions_for(build)
    assert value["status"] == "pending"
    assert value["solutions"] == []


def test_all_qualified_final_routes_are_revalidated_without_cap(tmp_path):
    outcomes = route_outcomes(
        {
            40: {"outcome": "escaped", "elapsed_s": 2300, "lost_teu": 8, "grade": 3},
            60: {"outcome": "escaped", "elapsed_s": 3000, "lost_teu": 1, "grade": 3},
        }
    )
    search = FakeRouteSearch(
        {
            "simroute": [
                route("fast", 80),
                route("ok", 90),
                route("slower", 70),
                route("fourth", 40),
                route("too-slow", 60),
            ],
            "visir2": [
                route("lossy", 100),
                route("slow", 270),
                route("lost-ship", 0),
            ],
        }
    )
    sim = FakeSimulator(outcomes)
    build = builder(
        tmp_path,
        route_search=search,
        simulator=sim,
        min_solutions=3,
    )
    result = build.build("local")

    assert result["status"] == "review_ready"
    assert result["budget_s"] == 2700
    assert result["solution_count"] == 4
    value = solutions_for(build)
    assert value["status"] == "ready"
    orders = assert_concrete_solutions(value)
    stored = {
        heading
        for solution_orders in orders
        for _, heading, _ in solution_orders
    }
    assert stored == {80, 90, 70, 40}

    package_headings = [
        round(call_orders[0]["heading_deg"])
        for path, call_orders in sim.calls
        if "/package/" in path
    ]
    for heading in (80, 90, 70, 40):
        assert package_headings.count(heading) == 1
    # The slow route is revalidated but falls outside the final budget.
    assert package_headings.count(60) == 1
    assert 60 not in stored
    for heading in (100, 270, 0):
        assert heading not in package_headings


def test_failed_final_alternative_is_omitted(tmp_path):
    outcomes = route_outcomes(
        {60: {"outcome": "escaped", "elapsed_s": 2300, "lost_teu": 1, "grade": 3}}
    )
    search = FakeRouteSearch(
        {
            "simroute": [
                route("fast", 80),
                route("ok", 90),
                route("slower", 70),
                route("failing", 60),
            ],
            "visir2": [route("slow", 270), route("lost-ship", 0)],
        }
    )
    sim = FakeSimulator(outcomes, fail_headings={60})
    build = builder(
        tmp_path,
        route_search=search,
        simulator=sim,
        min_solutions=3,
    )
    result = build.build("local")

    assert result["status"] == "review_ready"
    assert result["solution_count"] == 3
    value = solutions_for(build)
    assert value["status"] == "ready"
    orders = assert_concrete_solutions(value)
    stored = {
        heading
        for solution_orders in orders
        for _, heading, _ in solution_orders
    }
    assert stored == {80, 90, 70}
    package_headings = [
        round(call_orders[0]["heading_deg"])
        for path, call_orders in sim.calls
        if "/package/" in path
    ]
    assert package_headings.count(60) == 1


def test_resume_returns_terminal_result_without_duplicate_cost(tmp_path):
    sim = FakeSimulator(OUTCOMES)
    first = builder(tmp_path, simulator=sim)
    result = first.build("local")
    assert result["status"] == "review_ready"
    calls_after_first = len(sim.calls)
    second = builder(tmp_path, simulator=sim)
    resumed = second.build("local", resume=True)
    assert resumed == result
    assert len(sim.calls) == calls_after_first


def test_repeated_run_directory_requires_explicit_resume(tmp_path):
    sim = FakeSimulator(OUTCOMES)
    first = builder(tmp_path, simulator=sim)
    first.build("local")
    second = builder(tmp_path, simulator=sim)
    with pytest.raises(ValueError, match="resume"):
        second.build("local")


def test_resume_rejects_changed_input_context(tmp_path):
    sim = FakeSimulator(OUTCOMES)
    first = builder(tmp_path, simulator=sim)
    first.build("local")
    changed = builder(tmp_path, simulator=sim, max_revisions=1)
    with pytest.raises(ValueError, match="context|configuration"):
        changed.build("local", resume=True)


def test_resume_rejects_changed_algorithm_evidence(tmp_path):
    algorithm = tmp_path / "game" / "simulation" / "dynamics.py"
    algorithm.parent.mkdir(parents=True)
    algorithm.write_text("motion = 1\n")
    first = builder(tmp_path)
    result = first.build("local")
    assert result["status"] == "review_ready"
    algorithm.write_text("motion = 2\n")
    second = builder(tmp_path)
    with pytest.raises(ValueError, match="context|configuration"):
        second.build("local", resume=True)


def test_resume_rejects_changed_final_package_evidence(tmp_path):
    first = builder(tmp_path)
    result = first.build("local")
    assert result["status"] == "review_ready"
    gameplay_path = tmp_path / "run" / "package" / "gameplay.json"
    gameplay = json.loads(gameplay_path.read_text())
    gameplay["cargo"] = {"edited": True}
    gameplay_path.write_text(json.dumps(gameplay))
    second = builder(tmp_path)
    with pytest.raises(ValueError, match="final package evidence"):
        second.build("local", resume=True)


def test_local_revision_runs_after_failure(tmp_path):
    search = FakeRouteSearch(
        {
            "simroute": [
                {
                    "name": "only-timeout",
                    "orders": [{"t": 0, "heading_deg": 270, "speed_knots": 9}],
                }
            ],
            "visir2": [],
        }
    )
    b = builder(
        tmp_path,
        route_search=search,
        max_revisions=1,
        max_trials=4,
    )
    b.source_definition["mission"]["target_lon_lat"] = [1.0, 0.0]
    result = b.build("local")
    assert result["status"] == "review_ready"
    assert result["reference"].endswith("-rev1")
    validation = json.loads(
        (tmp_path / "run" / "package" / "validation.json").read_text()
    )
    assert any("rev1" in s["name"] for s in validation["strategies"])


def test_ai_mode_can_inspect_and_revise_without_external_calls(tmp_path):
    search = FakeRouteSearch(
        {
            "simroute": [
                {
                    "name": "only-timeout",
                    "orders": [{"t": 0, "heading_deg": 270, "speed_knots": 9}],
                }
            ],
            "visir2": [],
        }
    )
    responses = iter(
        [
            ai_call("record_plan", {"steps": ["trial", "revise", "finalize"]}),
            ai_call("run_trial", {"candidate_index": 0}),
            ai_call(
                "submit_revision",
                {
                    "base_index": 0,
                    "rationale": "The timeout trial needs a direct faster order.",
                    "orders": [{"t": 0, "heading_deg": 90, "speed_knots": 10}],
                },
            ),
            ai_call("run_trial", {"candidate_index": 1}),
            ai_call("finalize_build", {}),
        ]
    )
    seen = []

    def transport(payload):
        seen.append(payload)
        return next(responses)

    sim = FakeSimulator(OUTCOMES)
    b = builder(
        tmp_path,
        route_search=search,
        simulator=sim,
        max_revisions=1,
        max_trials=4,
        transport=transport,
    )
    result = b.build("ai")
    assert result["status"] == "review_ready"
    assert result["reference"] == "ai-revision-1"
    assert seen[0]["tool_choice"]["function"]["name"] == "record_plan"
    assert "Scenario Builder" in seen[0]["messages"][0]["content"]
    assert any(
        tool["function"]["name"] == "run_trial" for tool in seen[2]["tools"]
    )
    # The model saw the failed trial observation before it revised.
    assert seen[2]["messages"][-1]["role"] == "tool"
    assert '"outcome": "timeout"' in seen[2]["messages"][-1]["content"]
    validation = json.loads(
        (tmp_path / "run" / "package" / "validation.json").read_text()
    )
    assert validation["metadata"]["builder"]["mode"] == "ai"
    assert validation["metadata"]["builder"]["approval"] == "none"
    packaged = json.loads(
        (tmp_path / "run" / "package" / "scenario.json").read_text()
    )
    assert packaged["voyage"] == b.voyage
    assert packaged["voyage"]["authored"] is True
    assert (tmp_path / "run" / "audit.jsonl").is_file()
    assert "Authorization" not in (tmp_path / "run" / "audit.jsonl").read_text()


def test_search_failure_is_pending_not_impossible_claim(tmp_path):
    class FailingSearch:
        def search(self, method, *, max_candidates=6):
            raise RuntimeError("solver unavailable")

    b = builder(tmp_path, route_search=FailingSearch())
    result = b.build("local")
    assert result["status"] == "pending"
    report = json.loads(
        (tmp_path / "run" / "package" / "build-report.json").read_text()
    )
    assert any("solver unavailable" in value for value in report["checks"]["search_errors"])
    assert "does not prove the mission impossible" in " ".join(report["limitations"])
    solutions = json.loads(
        (tmp_path / "run" / "package" / "solutions.json").read_text()
    )
    assert solutions["status"] == "pending"


@pytest.mark.parametrize("name", list(MUTATIONS))
def test_invalid_result_metrics_never_qualify(tmp_path, name):
    root = tmp_path / name
    root.mkdir()
    search = FakeRouteSearch(
        {
            "simroute": [
                {
                    "name": "candidate",
                    "orders": [{"t": 0, "heading_deg": 90, "speed_knots": 9}],
                }
            ],
            "visir2": [],
        }
    )
    sim = MutatingSimulator(OUTCOMES, MUTATIONS[name])
    b = builder(root, route_search=search, simulator=sim)
    result = b.build("local")
    assert result["status"] == "pending"
    validation = json.loads((b.package / "validation.json").read_text())
    assert validation["passed"] is False
    assert validation["strategies"]
    assert all(not strategy["qualified"] for strategy in validation["strategies"])
    solutions = json.loads((b.package / "solutions.json").read_text())
    assert solutions["status"] == "pending"
    assert solutions["solutions"] == []


@pytest.mark.parametrize("kind", ["input", "symlink", "scenarios"])
def test_output_overlap_is_rejected_before_any_write(tmp_path, kind):
    root = tmp_path / "root"
    root.mkdir()
    path, scenario = make_input(root)
    if kind == "input":
        out = path.parent / "builder-output"
    elif kind == "symlink":
        link = tmp_path / "link"
        link.symlink_to(path.parent, target_is_directory=True)
        out = link / "builder-output"
    else:
        (root / "scenarios").mkdir()
        out = root / "scenarios" / "builder-output"
    with pytest.raises(ValueError, match="overlap"):
        Builder(
            scenario_path=path,
            out=out,
            project_root=root,
            scenario=scenario,
            simulator=FakeSimulator(OUTCOMES),
            route_search=route_search_for(),
            coverage_check=coverage_ok,
            gameplay_builder=gameplay_for,
            expert_limit=0,
            max_revisions=0,
        )
    assert not (out / "run.json").exists()
    assert not (out / "package" / "scenario.json").exists()


def test_weather_is_copied_only_inside_source_coverage(tmp_path):
    path, scenario = make_input(tmp_path, budget_s=7200)
    write_weather(tmp_path, 7200)
    b = builder(tmp_path, budget_s=7200)
    result = b.build("local")
    assert result["status"] == "review_ready"
    assert (b.package / "weather.json").is_file()
    weather = json.loads((b.package / "weather.json").read_text())
    assert weather["builder_budget_s"] == 3600
    assert weather["source_last_valid_s"] == 7200
    report = json.loads((b.package / "build-report.json").read_text())
    assert report["weather"]["status"] == "copied"


def test_weather_is_omitted_when_final_budget_exceeds_coverage(tmp_path):
    outcomes = dict(OUTCOMES)
    outcomes[90] = {
        "outcome": "escaped",
        "elapsed_s": 4200,
        "lost_teu": 12,
        "grade": 3,
    }
    path, scenario = make_input(tmp_path, budget_s=7200)
    write_weather(tmp_path, 3600)
    sim = FakeSimulator(outcomes)
    b = Builder(
        scenario_path=path,
        out=tmp_path / "run",
        project_root=tmp_path,
        scenario=scenario,
        simulator=sim,
        route_search=route_search_for(),
        coverage_check=coverage_ok,
        gameplay_builder=gameplay_for,
        voyage_planner=FakeVoyagePlanner(),
        coast_guard=synthetic_coast_guard,
        expert_limit=0,
        max_revisions=0,
        max_trials=8,
        min_solutions=1,
    )
    result = b.build("local")
    assert result["status"] == "review_ready"
    assert result["budget_s"] == 5400
    assert not (b.package / "weather.json").exists()
    report = json.loads((b.package / "build-report.json").read_text())
    assert report["weather"]["status"] == "omitted_beyond_coverage"
    assert report["weather"]["source_last_valid_s"] == 3600


def test_resume_restores_trials_and_revisions_after_tool_failure(tmp_path):
    search = FakeRouteSearch(
        {
            "simroute": [
                {
                    "name": "first",
                    "orders": [{"t": 0, "heading_deg": 270, "speed_knots": 9}],
                },
                {
                    "name": "second",
                    "orders": [{"t": 0, "heading_deg": 90, "speed_knots": 9}],
                },
            ],
            "visir2": [],
        }
    )
    sim = FlakySimulator(OUTCOMES, fail_heading=90)
    first = builder(tmp_path, route_search=search, simulator=sim, max_trials=4)
    result = first.build("local")
    assert result["status"] == "pending"
    assert any("simulator trial failed" in reason for reason in result["pending_reasons"])
    headings = [round(o[0]["heading_deg"]) for _, o in sim.calls]
    assert headings.count(270) == 1
    assert headings.count(90) == 1

    resumed = builder(tmp_path, route_search=search, simulator=sim, max_trials=4)
    result = resumed.build("local", resume=True)
    assert result["status"] == "review_ready"
    headings = [round(o[0]["heading_deg"]) for _, o in sim.calls]
    # The first candidate was restored from the checkpoint and was not re-run.
    assert headings.count(270) == 1
    assert headings.count(90) == 3  # one failed attempt, one retry, one final revalidation
    validation = json.loads((resumed.package / "validation.json").read_text())
    assert any(s["name"] == "first" for s in validation["strategies"])
    assert any(s["name"] == "second" for s in validation["strategies"])


def test_evidence_fingerprint_covers_the_runtime_kernel(tmp_path):
    kernel = tmp_path / "game" / "simulation" / "two_timescale.py"
    kernel.parent.mkdir(parents=True)
    kernel.write_text("kernel = 1\n")
    first = evidence_fingerprint(definition(), tmp_path)
    kernel.write_text("kernel = 2\n")
    second = evidence_fingerprint(definition(), tmp_path)
    key = "game/simulation/two_timescale.py"
    assert first["algorithms"][key] != second["algorithms"][key]
    assert first["fingerprint"] != second["fingerprint"]


def test_resume_keeps_a_submitted_revision_before_its_trial(tmp_path):
    search = FakeRouteSearch(
        {
            "simroute": [
                {
                    "name": "only-timeout",
                    "orders": [{"t": 0, "heading_deg": 270, "speed_knots": 9}],
                }
            ],
            "visir2": [],
        }
    )
    first = builder(
        tmp_path,
        route_search=search,
        simulator=FlakySimulator(OUTCOMES, fail_heading=270),
        max_trials=4,
        max_revisions=1,
    )
    result = first.build("local")
    assert result["status"] == "pending"
    first.submit_revision(
        0,
        [{"t": 0, "heading_deg": 90, "speed_knots": 9}],
        "The timeout trial needs the clear heading.",
    )

    resumed = builder(
        tmp_path,
        route_search=search,
        simulator=FakeSimulator(OUTCOMES),
        max_trials=4,
        max_revisions=1,
    )
    result = resumed.build("local", resume=True)
    assert result["status"] == "review_ready"
    assert result["reference"] == "ai-revision-1"
    validation = json.loads((resumed.package / "validation.json").read_text())
    assert any(s["name"] == "ai-revision-1" for s in validation["strategies"])


def test_ai_setup_error_is_pending_instead_of_raising(tmp_path):
    models = {
        "version": 1,
        "providers": {"test": {"base_url": "https://example.invalid/v1"}},
        "profiles": {
            "builder_test": {
                "provider": "test",
                "model": "tool-model",
                "temperature": 0.0,
                "top_p": 1.0,
                "max_tokens": 128,
                "timeout_s": 5,
                "tool_round_limit": 4,
                "request_options": {},
            }
        },
        "roles": {"builder": "builder_test"},
    }
    models_path = tmp_path / "models.json"
    models_path.write_text(json.dumps(models))
    b = builder(
        tmp_path,
        models_path=models_path,
        keys_path=tmp_path / "missing-keys.json",
    )
    result = b.build("ai")
    assert result["status"] == "pending"
    assert any(
        reason.startswith("builder_harness_setup_failed")
        for reason in result["pending_reasons"]
    )


def ai_call(name, args):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call-" + name,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ],
    }
