"""Builder integration checks for authored voyage and fixed coast policy."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest
from shapely.geometry import box

from agent.builder import HARNESS_DIR, Builder, validate_voyage
from agent.harness import ToolError, load_tool_specs
from agent.voyage import VoyagePlanner, VoyageUnavailable
from game.navigation import CoastGuard
from test_agent_builder import (
    OUTCOMES,
    FakeRouteSearch,
    FakeSimulator,
    FakeVoyagePlanner,
    FlakySimulator,
    builder,
    coverage_ok,
    definition,
    gameplay_for,
    make_input,
    route_search_for,
    synthetic_coast_guard,
    synthetic_voyage,
)

ROOT = Path(__file__).resolve().parents[1]
IMPLEMENTATION_FILES = (
    "agent/solutions.py",
    "agent/models.py",
    "agent/voyage.py",
    "agent/builder_harness/tools.json",
    "game/navigation.py",
)


def packaged(build):
    return json.loads((build.package / "scenario.json").read_text())


def report(build):
    return json.loads((build.package / "build-report.json").read_text())


def test_output_package_preserves_start_and_authored_voyage(tmp_path):
    build = builder(tmp_path)
    result = build.build("local")

    assert result["status"] == "review_ready"
    scenario = packaged(build)
    assert scenario["initial"] == definition()["initial"]
    assert scenario["voyage"] == build.voyage
    assert scenario["voyage"]["authored"] is True
    assert scenario["voyage"]["track"][0] != scenario["voyage"]["track"][-1]

    value = report(build)
    assert value["voyage"]["origin"]["name"] == "Test West"
    assert value["voyage"]["destination"]["name"] == "Test North"
    assert value["voyage"]["snap_offset_nm"] == build.voyage["snap_distance_nm"]
    assert value["voyage"]["historical_track"] is False
    assert value["voyage"]["harbour_safety_claimed"] is False
    assert value["navigation"]["clearance_nm"] == 5.0
    assert value["navigation"]["override_allowed"] is False
    assert value["briefing"]["labels"]["port_arrival_is_escape"] is False
    assert (build.package / "briefing.md").is_file()
    text = (build.package / "briefing.md").read_text()
    assert "not a historical ship log" in text
    assert "5 NM" in text

    evidence = value["evidence_fingerprint"]
    assert evidence["voyage"]["present"] is True
    assert evidence["voyage"]["sha256"]
    assert evidence["navigation"]["clearance_nm"] == 5.0
    for name in IMPLEMENTATION_FILES:
        assert name in evidence["algorithms"]
    assert "web/game/ports.json" in evidence["algorithms"]
    assert "data/navigation/manifest.json" in evidence["algorithms"]


def test_voyage_planner_failure_is_pending_without_a_fallback(tmp_path):
    planner = FakeVoyagePlanner(
        error=VoyageUnavailable("no_plausible_port_pair", "No opposite branches")
    )
    search = route_search_for()
    simulator = FakeSimulator(OUTCOMES)
    build = builder(
        tmp_path,
        voyage_planner=planner,
        route_search=search,
        simulator=simulator,
    )
    result = build.build("local")

    assert result["status"] == "pending"
    assert any(
        reason.startswith("voyage_unavailable: no_plausible_port_pair")
        for reason in result["pending_reasons"]
    )
    assert planner.calls and planner.calls[0]["time"] == definition()["start"]
    assert search.calls == []
    assert simulator.calls == []
    assert not (build.package / "scenario.json").exists()
    value = report(build)
    assert value["voyage"] is None
    assert "Halifax" not in json.dumps(value)


def test_invalid_designer_voyage_is_pending_not_replaced(tmp_path):
    path, scenario = make_input(tmp_path)
    value = json.loads(path.read_text())
    value["voyage"] = {"version": 2, "track": [], "authored": True}
    path.write_text(json.dumps(value))
    scenario.definition = json.loads(path.read_text())
    planner = FakeVoyagePlanner()
    build = Builder(
        scenario_path=path,
        out=tmp_path / "run",
        project_root=tmp_path,
        scenario=scenario,
        simulator=FakeSimulator(OUTCOMES),
        route_search=route_search_for(),
        voyage_planner=planner,
        coast_guard=synthetic_coast_guard,
        coverage_check=coverage_ok,
        gameplay_builder=gameplay_for,
        expert_limit=0,
        max_revisions=0,
        max_trials=8,
        min_solutions=1,
    )
    result = build.build("local")

    assert result["status"] == "pending"
    assert any(
        reason.startswith("voyage_plan_failed")
        for reason in result["pending_reasons"]
    )
    assert planner.calls == []


def test_valid_designer_voyage_is_preserved_without_regeneration(tmp_path):
    path, scenario = make_input(tmp_path)
    voyage = synthetic_voyage()
    value = json.loads(path.read_text())
    value["voyage"] = voyage
    path.write_text(json.dumps(value))
    scenario.definition = json.loads(path.read_text())
    planner = FakeVoyagePlanner()
    build = Builder(
        scenario_path=path,
        out=tmp_path / "run",
        project_root=tmp_path,
        scenario=scenario,
        simulator=FakeSimulator(OUTCOMES),
        route_search=route_search_for(),
        voyage_planner=planner,
        coast_guard=synthetic_coast_guard,
        coverage_check=coverage_ok,
        gameplay_builder=gameplay_for,
        expert_limit=0,
        max_revisions=0,
        max_trials=8,
        min_solutions=1,
    )
    result = build.build("local")

    assert result["status"] == "review_ready"
    assert planner.calls == []
    assert build.voyage_source == "designer_input"
    assert packaged(build)["voyage"] == validate_voyage(voyage)


def test_resume_reuses_the_persisted_voyage_without_regeneration(tmp_path):
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
    first_planner = FakeVoyagePlanner()
    first = builder(
        tmp_path,
        route_search=search,
        simulator=FlakySimulator(OUTCOMES, fail_heading=270),
        voyage_planner=first_planner,
        max_trials=4,
    )
    result = first.build("local")
    assert result["status"] == "pending"
    assert len(first_planner.calls) == 1
    voyage = copy.deepcopy(first.voyage)
    navigation = copy.deepcopy(first.navigation_metadata)

    second_planner = FakeVoyagePlanner()
    resumed = builder(
        tmp_path,
        route_search=search,
        simulator=FakeSimulator(OUTCOMES),
        voyage_planner=second_planner,
        max_trials=4,
    )
    result = resumed.build("local", resume=True)

    assert result["status"] == "review_ready"
    assert second_planner.calls == []
    assert resumed.voyage == voyage
    assert resumed.navigation_metadata == navigation
    assert packaged(resumed)["voyage"] == voyage


@pytest.mark.parametrize(
    "name",
    [
        "web/game/ports.json",
        "data/navigation/manifest.json",
        "agent/voyage.py",
        "agent/solutions.py",
    ],
)
def test_changed_implementation_or_data_invalidates_completed_resume(tmp_path, name):
    first = builder(tmp_path)
    assert first.build("local")["status"] == "review_ready"

    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("changed-after-the-run\n")

    resumed = builder(tmp_path)
    with pytest.raises(ValueError, match="context|configuration"):
        resumed.build("local", resume=True)


def test_builder_cannot_lower_the_fixed_coast_margin(tmp_path):
    def lowered(bbox):
        return CoastGuard(
            bbox,
            clearance_nm=4.0,
            land=box(0.8, 0.8, 1.0, 1.0),
        )

    simulator = FakeSimulator(OUTCOMES)
    search = route_search_for()
    build = builder(
        tmp_path,
        coast_guard=lowered,
        simulator=simulator,
        route_search=search,
    )
    result = build.build("local")

    assert result["status"] == "pending"
    assert any(
        reason.startswith("navigation_unavailable")
        for reason in result["pending_reasons"]
    )
    assert build.navigation_metadata is None
    assert search.calls == []
    assert simulator.calls == []

    handler = build._ai_handlers()["plan_voyage"]
    with pytest.raises(ToolError, match="no arguments"):
        handler({"clearance_nm": 1.0})

    specs = {
        tool["name"]: tool for tool in load_tool_specs(HARNESS_DIR / "tools.json")
    }
    assert specs["plan_voyage"]["parameters"] == {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    }


def test_plan_voyage_tool_is_bounded_and_reads_the_fixed_initial(tmp_path):
    build = builder(tmp_path)
    build._ensure_navigation()
    before = copy.deepcopy(build.source_definition)
    handler = build._ai_handlers()["plan_voyage"]

    first = handler({})
    second = handler({})

    assert first == second
    assert first["status"] == "ok"
    assert first["source"] == "generated"
    assert first["fixed_initial"] == {
        "lon": 0.0,
        "lat": 0.0,
        "heading_deg": 0.0,
        "speed_mps": 5.0,
        "start": definition()["start"],
    }
    assert first["navigation"]["clearance_nm"] == 5.0
    assert first["navigation"]["override_allowed"] is False
    assert first["limits"]["initial_is_fixed"] is True
    assert build.source_definition == before
    assert len(build.voyage_planner.calls) == 1


def test_unsafe_initial_position_is_pending_without_relocation(tmp_path):
    def land_under_start(bbox):
        return CoastGuard(bbox, land=box(-0.2, -0.2, 0.2, 0.2))

    simulator = FakeSimulator(OUTCOMES)
    search = route_search_for()
    build = builder(
        tmp_path,
        coast_guard=land_under_start,
        simulator=simulator,
        route_search=search,
    )
    initial_before = copy.deepcopy(build.source_definition["initial"])
    result = build.build("local")

    assert result["status"] == "pending"
    assert "navigation_initial_state_unsafe" in result["pending_reasons"]
    assert build.source_definition["initial"] == initial_before
    assert search.calls == []
    assert simulator.calls == []
    validation = json.loads((build.package / "validation.json").read_text())
    details = validation["metadata"]["details"]
    assert details["relocated"] is False
    assert details["initial"] == {"lon": 0.0, "lat": 0.0}


@pytest.mark.skipif(
    importlib.util.find_spec("searoute") is None,
    reason="searoute is not installed",
)
def test_real_biscay_voyage_plan_smoke_runs_once():
    scenario = json.loads(
        (ROOT / "scenarios/biscay-20261022-21/scenario.json").read_text()
    )
    initial = copy.deepcopy(scenario["initial"])
    initial["time"] = scenario["start"]
    before = copy.deepcopy(initial)

    voyage = VoyagePlanner().build(initial)

    assert initial == before
    assert voyage["version"] == 1
    assert voyage["authored"] is True
    assert voyage["origin"] != voyage["destination"]
    assert voyage["track"][0] == [
        voyage["origin"]["lon"],
        voyage["origin"]["lat"],
    ]
    assert voyage["track"][-1] == [
        voyage["destination"]["lon"],
        voyage["destination"]["lat"],
    ]
    assert voyage["provenance"]["initial"]["lon"] == initial["lon"]
    assert voyage["provenance"]["initial"]["lat"] == initial["lat"]
    assert voyage["provenance"]["catalogue_sha256"]
    assert voyage["provenance"]["searoute_version"] == "1.6.0"
