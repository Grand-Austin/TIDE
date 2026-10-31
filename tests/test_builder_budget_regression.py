"""Acceptance checks for the agreed Builder budget and solution policy."""

import inspect
import json
import math

import pytest

from test_agent_builder import builder
from test_agent_simulator import SCENARIO, service_for
from test_session import vessel as vessel


def test_final_budget_uses_verified_route_time_not_input_budget(tmp_path):
    build = builder(tmp_path, budget_s=7200, max_revisions=0)
    result = build.build("local")
    assert result["status"] == "review_ready"
    expected = 900 * math.ceil(1.2 * result["elapsed_s"] / 900)
    assert result["budget_s"] == expected
    packaged = json.loads((build.package / "scenario.json").read_text())
    assert packaged["mission"]["budget_s"] == expected
    original = json.loads(build.scenario_path.read_text())
    assert original["mission"]["budget_s"] == 7200


def test_solutions_package_is_concrete_and_hint_free(tmp_path):
    build = builder(tmp_path)
    result = build.build("local")
    assert result["status"] == "review_ready"
    assert result["solution_count"] == 1

    package = build.package
    assert (package / "solutions.json").is_file()
    assert not (package / "hints.json").exists()
    assert not (package / "hints.md").exists()

    solutions = json.loads((package / "solutions.json").read_text())
    assert solutions["kind"] == "solution_routes"
    assert solutions["status"] == "ready"
    assert solutions["required_minimum"] == 1
    assert solutions["source_fingerprint"]
    assert solutions["solution_count"] == len(solutions["solutions"]) == 1
    assert "levels" not in solutions
    for solution in solutions["solutions"]:
        assert solution["provenance"]["final_validation"] is True
        assert solution["provenance"]["fingerprint"] == solutions["source_fingerprint"]
        assert solution["track_status"] in {"sampled", "endpoints_only"}
        assert solution["elapsed_s"] <= solutions["budget_s"]
        assert solution["orders"]
        for order in solution["orders"]:
            assert {"t", "heading_deg", "speed_knots"} <= set(order)
            assert all(
                isinstance(order[key], (int, float))
                for key in ("t", "heading_deg", "speed_knots")
            )


def test_trial_state_identity_includes_controller_history(vessel, tmp_path):
    service = service_for(tmp_path, vessel)
    try:
        created = service.create(str(SCENARIO))
        original = created["view"]["state_fingerprint"]
        session = service._trials[created["trial_id"]].session
        session.state.control.speed_integral += 1.0
        changed = service.read(created["trial_id"])["view"]["state_fingerprint"]
        assert changed != original
    finally:
        service.close()


def test_completed_resume_rejects_changed_simulation_implementation(tmp_path):
    first = builder(tmp_path)
    assert first.build("local")["status"] == "review_ready"
    implementation = tmp_path / "game" / "cargo.py"
    implementation.parent.mkdir()
    implementation.write_text("ALGORITHM_VERSION = 'changed'\n")
    resumed = builder(tmp_path)
    with pytest.raises(ValueError, match="algorithm|implementation|evidence"):
        resumed.build("local", resume=True)


def test_cli_and_builder_defaults_match_solution_scope():
    from agent.__main__ import parser
    from agent.builder import Builder

    args = parser().parse_args(
        ["build", "--scenario", "scenario.json", "--out", "run"]
    )
    assert args.min_solutions == 3
    assert args.max_trials == 64
    assert args.max_candidates == 12
    assert args.expert_limit == 3
    assert args.max_revisions == 2

    build_parser = parser()._subparsers._group_actions[0].choices["build"]
    assert "--hint-branch-limit" not in build_parser._option_string_actions

    parameters = inspect.signature(Builder).parameters
    assert "hint_builder" not in parameters
    assert "hint_branch_limit" not in parameters
    assert parameters["min_solutions"].default == 3
    assert parameters["max_trials"].default == 64
    assert parameters["max_candidates"].default == 12
    assert parameters["expert_limit"].default == 3
    assert parameters["max_revisions"].default == 2
