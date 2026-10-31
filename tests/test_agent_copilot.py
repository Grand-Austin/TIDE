import json
from pathlib import Path

from agent.copilot import (
    MAX_MATCH_NM,
    MAX_TIME_GAP_S,
    Copilot,
    SolutionsGrant,
    load_solutions,
    select_route,
)
from agent.harness import load_tool_specs
from agent.transport import ModelError
from game.scenario import ROOT, load_scenario

HARNESS = ROOT / "agent" / "copilot_harness"
FINGERPRINT = "a" * 64
ANSWER = "Compare the neighbouring released forecast areas before you commit the next order."


def call(name, args=None):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": name, "arguments": json.dumps(args or {})}}]}


class Scripted:
    """A transport that replays scripted messages, keeps every payload, and can raise on cue."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.payloads = []

    def __call__(self, payload):
        self.payloads.append(payload)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def profile():
    return {"model": "fake-tool-model", "temperature": 0.0, "top_p": 1.0, "max_tokens": 200}


def copilot(script, *, audit_dir=None):
    return Copilot(models_path=None, keys_path=None, audit_dir=audit_dir, profile=profile(),
                   transport_factory=lambda: script)


def solution(track, *, elapsed_s=3000.0, lost_teu=10, route_id="route-a", name="a"):
    return {"id": route_id, "name": name, "solver": "solver", "elapsed_s": elapsed_s, "lost_teu": lost_teu,
            "total_teu": 1200, "ship_lost": False, "automatic_qualified": True, "final_validation": True,
            "orders": [{"t": 0.0, "heading_deg": 10.0, "speed_knots": 9.0}], "track": track}


def solutions_of(*routes):
    return {"kind": "solution_routes", "status": "ready", "source_fingerprint": FINGERPRINT,
            "solutions": list(routes)}


def state(lon, lat, elapsed_s, *, remaining_s=2400.0, heading=90.0, reports=None):
    return {"scenario": {"id": "s"}, "time": {"elapsed_s": elapsed_s, "remaining_s": remaining_s, "utc": "u"},
            "vessel": {"lon": lon, "lat": lat, "heading_deg": heading, "hs_m": 6.2, "roll_deg": -9.0,
                       "wave_from_deg": 300.0, "speed_through_water_mps": 5.0},
            "target": {"heading_deg": heading, "speed_knots": 9.0},
            "cargo": {"lost_teu": 0, "total_teu": 1200, "grade": 0}, "sector": [1, 1],
            "reports": [{"id": "forecast-0-0-0", "issued_at": "2026-10-22T21:00:00+00:00",
                         "valid_to": "2026-10-22T22:00:00+00:00"}] if reports is None else reports,
            "encounter": "Beam seas", "orders": []}


def three_point_track():
    return [{"t": 0.0, "lon": 0.0, "lat": 0.0, "heading_deg": 0.0},
            {"t": 600.0, "lon": 0.01, "lat": 0.01, "heading_deg": 90.0},
            {"t": 1200.0, "lon": 0.02, "lat": 0.02, "heading_deg": 90.0}]


def test_the_current_package_is_usable_and_carries_eight_routes():
    definition = load_scenario(ROOT / "scenarios/sole-20261022-21/scenario.json").definition
    data, reason = load_solutions(ROOT / "scenarios/sole-20261022-21", definition=definition, root=ROOT)
    assert reason is None
    assert len(data["solutions"]) == 8


def test_a_missing_package_names_the_first_failing_check(tmp_path):
    definition = load_scenario(ROOT / "scenarios/sole-20261022-21/scenario.json").definition
    data, reason = load_solutions(tmp_path, definition=definition, root=ROOT)
    assert data is None
    assert reason == "solutions_missing"

    (tmp_path / "solutions.json").write_text("{not json")
    assert load_solutions(tmp_path, definition=definition, root=ROOT)[1] == "solutions_not_ready"

    stale = json.loads((ROOT / "scenarios/sole-20261022-21/solutions.json").read_text())
    stale["initial_state"]["seed"] = 99
    (tmp_path / "solutions.json").write_text(json.dumps(stale))
    assert load_solutions(tmp_path, definition=definition, root=ROOT)[1] == "solutions_initial_state_mismatch"

    stale = json.loads((ROOT / "scenarios/sole-20261022-21/solutions.json").read_text())
    stale["budget_s"] = 60
    (tmp_path / "solutions.json").write_text(json.dumps(stale))
    assert load_solutions(tmp_path, definition=definition, root=ROOT)[1] == "solutions_budget_mismatch"

    stale["budget_s"] = definition["mission"]["budget_s"]
    stale["source_fingerprint"] = "not-a-hash"
    (tmp_path / "solutions.json").write_text(json.dumps(stale))
    assert load_solutions(tmp_path, definition=definition, root=ROOT)[1] == "solutions_source_fingerprint_missing"

    stale["source_fingerprint"] = FINGERPRINT
    (tmp_path / "solutions.json").write_text(json.dumps(stale))
    assert load_solutions(tmp_path, definition=definition, root=ROOT)[1] == "scenario_validation_missing"

    (tmp_path / "scenario.json").write_bytes((ROOT / "scenarios/sole-20261022-21/scenario.json").read_bytes())
    (tmp_path / "validation.json").write_text(json.dumps({"passed": True, "definition_sha256": "x",
                                                         "gameplay_sha256": "y"}))
    assert load_solutions(tmp_path, definition=definition, root=ROOT)[1] == "scenario_validation_stale"


def test_the_nearest_matching_reference_route_wins_and_starts_at_the_match():
    data = solutions_of(solution(three_point_track()))
    route = select_route(data, state(0.01, 0.01, 600.0))

    assert route["id"] == "route-a"
    assert route["waypoints"][0] == [0.01, 0.01]
    assert set(route) == {"id", "waypoints", "start", "end", "bearing_deg", "direction", "relative_deg",
                          "side", "offset_nm", "runs"}
    assert route["start"] == {"lon": 0.01, "lat": 0.01}
    assert route["end"] == {"lon": 0.02, "lat": 0.02}
    assert route["offset_nm"] == 0.0
    assert route["direction"] == "north"
    assert route["runs"] == "north-east"
    assert route["relative_deg"] == -90.0  # dead north while steering east is to port
    assert route["side"] == "on your port beam"


def test_a_route_too_far_or_too_late_is_not_offered():
    data = solutions_of(solution(three_point_track()))
    far_lat = 0.01 + 0.4  # about 24 nm north of the matched point
    assert select_route(data, state(0.01, far_lat, 600.0)) is None

    later = solutions_of(solution([{"t": 600.0, "lon": 0.01, "lat": 0.01, "heading_deg": 90.0},
                                   {"t": 2000.0, "lon": 0.5, "lat": 0.5, "heading_deg": 90.0}]))
    assert select_route(later, state(0.01, 0.01, 600.0 + MAX_TIME_GAP_S)) is not None
    assert select_route(later, state(0.01, 0.01, 600.0 + MAX_TIME_GAP_S + 1.0)) is None


def test_a_lost_ship_or_a_track_without_points_is_skipped():
    lost = solution(three_point_track(), route_id="route-lost")
    lost["ship_lost"] = True
    empty = solution([], route_id="route-empty")
    assert select_route(solutions_of(lost, empty), state(0.01, 0.01, 600.0)) is None


def test_the_grant_returns_one_route_and_stops_after_revoke():
    route = select_route(solutions_of(solution(three_point_track())), state(0.01, 0.01, 600.0))
    grant = SolutionsGrant(route)

    assert grant.read()["id"] == "route-a"
    grant.revoke()
    try:
        grant.read()
    except PermissionError:
        pass
    else:
        raise AssertionError("a revoked grant must refuse to read")


def test_each_mode_authorizes_only_its_own_tool():
    assert [spec["name"] for spec in load_tool_specs(HARNESS / "tools_l1.json")] == [
        "record_plan", "read_released_forecast", "finish_insight"]
    assert [spec["name"] for spec in load_tool_specs(HARNESS / "tools_l2.json")] == [
        "record_plan", "read_reference_route", "finish_insight"]
    assert all(spec["roles"] == ["copilot"] for spec in load_tool_specs(HARNESS / "tools_l1.json"))


def test_l1_reads_the_forecast_and_returns_the_model_sentence():
    script = Scripted(call("record_plan", {"steps": ["read", "answer"]}), call("read_released_forecast"),
                      call("finish_insight", {"text": ANSWER}))
    agent = copilot(script)

    result = agent.advise("l1", state(-10.6, 48.7, 120.0), None)

    assert result["status"] == "ready"
    assert result["source"] == "agent"
    assert result["text"] == ANSWER
    assert result["at_s"] == 120.0
    prompt = script.payloads[0]["messages"][1]["content"]
    assert '"reports"' not in prompt and '"solutions"' not in prompt
    assert '"vessel"' in prompt
    assert '"note"' in prompt


def test_l1_refuses_before_the_first_released_forecast():
    result = copilot(Scripted()).advise("l1", state(-10.6, 48.7, 0.0, reports=[]), None)

    assert result == {"status": "refused", "mode": "l1", "source": "local",
                      "text": "No forecast has been released yet.", "route": None,
                      "reason": "no_released_forecast", "at_s": 0.0}


def test_l1_without_the_forecast_read_uses_the_local_method_note():
    script = Scripted(call("record_plan", {"steps": ["answer"]}), call("finish_insight", {"text": ANSWER}))
    result = copilot(script).advise("l1", state(-10.6, 48.7, 60.0), None)

    assert result["status"] == "ready"
    assert result["source"] == "fallback"
    assert result["reason"] == "evidence_not_read"
    assert result["text"].startswith("The ship is rolling 9° with 6.2 m seas on the beam")


def test_l1_rejects_a_route_shaped_or_three_sentence_insight():
    for text in ("Take route-a to the north.", "Turn to 300°. Hold it. Watch the sea."):
        script = Scripted(call("record_plan", {"steps": ["read"]}), call("read_released_forecast"),
                          call("finish_insight", {"text": text}))
        result = copilot(script).advise("l1", state(-10.6, 48.7, 60.0), None)
        assert result["source"] == "fallback"
        assert result["reason"] == "invalid_model_output"


GUIDANCE = "The reference track lies to your west and runs west-north-west; compare it with the released forecast."


def test_l2_shows_one_sanitized_reference_route():
    data = json.loads((ROOT / "scenarios/sole-20261022-21/solutions.json").read_text())
    route = select_route(data, state(-10.6147, 48.7611, 0.0, remaining_s=4500.0))
    script = Scripted(call("record_plan", {"steps": ["read"]}), call("read_reference_route"),
                      call("finish_insight", {"text": GUIDANCE}))
    result = copilot(script).advise("l2", state(-10.6147, 48.7611, 0.0, remaining_s=4500.0), data)

    assert result["status"] == "ready"
    assert result["source"] == "agent"
    assert result["text"] == GUIDANCE
    assert result["route"]["id"] == route["id"]
    assert len(result["route"]["waypoints"]) >= 2
    tool_reply = json.loads(script.payloads[2]["messages"][-1]["content"])
    assert tool_reply["route"]["id"] == route["id"]
    assert '"solutions"' not in json.dumps(tool_reply)
    assert "provenance" not in json.dumps(tool_reply)


def test_l2_never_reveals_the_route_name_time_or_loss():
    named = solution(three_point_track(), lost_teu=999, name="secret-route-name")
    named["elapsed_s"] = 3000.0
    script = Scripted(call("record_plan", {"steps": ["read"]}), call("read_reference_route"),
                      call("finish_insight", {"text": GUIDANCE}))
    result = copilot(script).advise("l2", state(0.01, 0.01, 600.0), solutions_of(named))

    tool_reply = json.dumps(json.loads(script.payloads[2]["messages"][-1]["content"]))
    for secret in ("secret-route-name", "999", "3000", "lost_teu", "elapsed_s", "escape", "validated"):
        assert secret not in tool_reply, secret
        assert secret not in json.dumps(result["route"]), secret


def test_l2_writes_no_route_audit_file(tmp_path):
    data = solutions_of(solution(three_point_track()))
    script = Scripted(call("record_plan", {"steps": ["read"]}), call("read_reference_route"),
                      call("finish_insight", {"text": GUIDANCE}))
    result = copilot(script, audit_dir=tmp_path).advise("l2", state(0.01, 0.01, 600.0), data)

    assert result["source"] == "agent"
    assert not (tmp_path / "copilot-l2.jsonl").exists()
    assert (tmp_path / "copilot-l1.jsonl").exists() is False


def test_l2_refuses_without_a_package_or_a_nearby_route():
    missing = copilot(Scripted()).advise("l2", state(0.01, 0.01, 600.0), None)
    assert (missing["status"], missing["reason"]) == ("refused", "solutions_unavailable")
    away = copilot(Scripted()).advise("l2", state(0.01, 0.01 + 0.4, 600.0),
                                      solutions_of(solution(three_point_track())))
    assert (away["status"], away["reason"]) == ("refused", "no_nearby_reference_route")


def test_a_failing_transport_still_answers_from_the_local_summary():
    for mode, solutions in (("l1", None), ("l2", solutions_of(solution(three_point_track())))):
        result = copilot(Scripted(ModelError("provider down"))).advise(
            mode, state(0.01, 0.01, 600.0, remaining_s=2400.0), solutions)
        assert result["status"] == "ready"
        assert result["source"] == "fallback"
        assert result["reason"] == "model_failed"
        assert result["text"].strip()


def test_unknown_modes_are_rejected():
    try:
        copilot(Scripted()).advise("l3", state(0.01, 0.01, 600.0), None)
    except ValueError as error:
        assert str(error) == "unknown copilot mode"
    else:
        raise AssertionError("an unknown mode must raise")
