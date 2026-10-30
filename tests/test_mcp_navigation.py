"""Integration tests for the shared coast guard and read-only voyage tool."""

import asyncio
import hashlib
import json
import math
from pathlib import Path
import sys
import uuid

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from shapely.geometry import box

from agent.simulator import SimulatorService, open_session
from agent.tui import UnixToolServer
from game.navigation import DEFAULT_CLEARANCE_NM, CoastGuard
from game.simulation.environment import EnvironmentError

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = "scenarios/menorca-20260909/scenario.json"
MENORCA = ROOT / SCENARIO
BBOX = (3.5, 3.95, 39.7, 40.15)


class _StopAfterRules:
    budget_s = 1.0

    def evaluate(self, sample, consumed):
        if sample.time_s >= 0.5:
            return "timeout", [
                {"time_s": sample.time_s, "kind": "timeout", "detail": "test stop"}
            ]
        return None, []

    def visible_reports(self, elapsed, lon, lat):
        return []


def short_socket():
    # AF_UNIX paths must stay short. /tmp exists on Linux runners and on macOS; /private/tmp is macOS only.
    return Path("/tmp") / f"tide-nav-{uuid.uuid4().hex[:12]}.sock"


def payload(result):
    if result.isError:
        raise AssertionError(result.content)
    data = result.structuredContent
    if data is None:
        data = json.loads(result.content[0].text)
    if isinstance(data, dict) and set(data) == {"result"}:
        data = data["result"]
    return data


def mcp_command(*arguments):
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "agent.mcp", *arguments],
        cwd=str(ROOT),
    )


def coast_guard():
    # Start at 3.65 E is just outside 5 nm from this east coast fixture.
    return CoastGuard(BBOX, land=box(3.76, 39.90, 3.95, 40.15))


def guarded_factory(guard, rules=None):
    def build(scenario_path, directory, restore):
        handle = open_session(
            scenario_path,
            directory,
            project_root=ROOT,
            restore=restore,
            coast_guard=guard,
        )
        if rules is not None:
            handle.session.rules = rules
        return handle

    return build


def guarded_service(tmp_path, guard, rules=None):
    return SimulatorService(
        tmp_path / "workspace",
        session_factory=guarded_factory(guard, rules),
    )


def assert_authored_voyage(voyage):
    assert voyage["version"] == 1
    assert voyage["authored"] is True
    assert voyage["origin"] != voyage["destination"]
    assert voyage["origin"]["name"] != voyage["destination"]["name"]
    assert len(voyage["track"]) >= 2
    assert voyage["track"][0] == [
        voyage["origin"]["lon"],
        voyage["origin"]["lat"],
    ]
    assert voyage["track"][-1] == [
        voyage["destination"]["lon"],
        voyage["destination"]["lat"],
    ]
    assert len(voyage["snapped_position"]) == 2
    assert all(math.isfinite(value) for value in voyage["snapped_position"])
    assert any(
        point[0] == pytest.approx(voyage["snapped_position"][0])
        and point[1] == pytest.approx(voyage["snapped_position"][1])
        for point in voyage["track"]
    )
    assert voyage["snap_distance_nm"] >= 0
    assert voyage["distance_nm"] > 0
    assert voyage["provenance"]["catalogue_ports"] == 223
    assert voyage["provenance"]["branch_separation_deg"] >= 120.0


def test_real_headless_create_and_fork_share_the_fixed_default_guard(tmp_path):
    service = SimulatorService(tmp_path / "workspace")
    try:
        created = service.create(SCENARIO)
        metadata = created["view"]["coast_guard"]
        assert metadata["clearance_nm"] == DEFAULT_CLEARANCE_NM == 5.0
        assert metadata["clearance_m"] == 9260.0
        assert metadata["version"] == "5.1.1"
        assert metadata["fixture"] is False

        forked = service.fork(created["trial_id"])
        assert forked["view"]["coast_guard"] == metadata
        assert forked["view"]["coast_guard"]["clearance_nm"] == 5.0
    finally:
        service.close()


def test_run_plan_keeps_the_injected_synthetic_guard_policy(tmp_path):
    guard = coast_guard()
    service = SimulatorService(
        tmp_path / "workspace",
        session_factory=guarded_factory(guard, _StopAfterRules()),
    )
    try:
        result = service.run_plan(
            SCENARIO, [{"t": 0, "heading_deg": 180, "speed_knots": 2}]
        )
        assert result["outcome"] == "timeout"
        assert result["view"]["coast_guard"]["fixture"] is True
        assert result["view"]["coast_guard"]["clearance_nm"] == 5.0
    finally:
        service.close()


def test_guard_stops_at_the_last_safe_state_and_rejects_the_next_step(tmp_path):
    guard = coast_guard()
    service = guarded_service(tmp_path, guard)
    try:
        created = service.create(SCENARIO)
        assert created["view"]["coast_guard"]["fixture"] is True
        stepped = service.step(
            created["trial_id"],
            90,
            15,
            900,
            expected_revision=created["view"]["revision"],
            operation_id="toward-coast",
        )
        view = stepped["view"]
        assert view["stopping_reason"] == "coast_clearance"
        assert view["elapsed_s"] < 900
        assert view["paused"] is True
        assert guard.allowed(view["vessel"]["lon"], view["vessel"]["lat"])

        before = service.read(created["trial_id"])["view"]
        blocked = service.step(
            created["trial_id"],
            90,
            15,
            900,
            expected_revision=before["revision"],
            operation_id="still-unsafe",
        )
        assert blocked["view"]["stopping_reason"] == "coast_clearance"
        assert blocked["view"]["elapsed_s"] == before["elapsed_s"]
        assert blocked["view"]["state_fingerprint"] == before["state_fingerprint"]
        assert guard.allowed(
            blocked["view"]["vessel"]["lon"], blocked["view"]["vessel"]["lat"]
        )
    finally:
        service.close()


def test_unsafe_initial_state_fails_clearly_without_creating_a_trial(tmp_path):
    guard = CoastGuard(BBOX, land=box(3.60, 39.95, 3.95, 40.15))
    service = guarded_service(tmp_path, guard)
    before = hashlib.sha256(MENORCA.read_bytes()).hexdigest()
    try:
        with pytest.raises(EnvironmentError) as error:
            service.create(SCENARIO)
        assert error.value.reason == "coast_clearance"
        assert service._trials == {}
        assert list((tmp_path / "workspace").iterdir()) == []
        assert hashlib.sha256(MENORCA.read_bytes()).hexdigest() == before
    finally:
        service.close()


def test_plan_voyage_is_read_only_and_uses_the_fixed_initial_state(tmp_path):
    service = SimulatorService(tmp_path / "workspace")
    before = hashlib.sha256(MENORCA.read_bytes()).hexdigest()
    try:
        voyage = service.plan_voyage(SCENARIO)
        assert_authored_voyage(voyage)
        assert voyage["provenance"]["initial"]["lon"] == pytest.approx(3.65)
        assert voyage["provenance"]["initial"]["lat"] == pytest.approx(40.05)
        assert voyage["provenance"]["initial"]["heading_deg"] == pytest.approx(155.0)
        assert service._trials == {}
        assert hashlib.sha256(MENORCA.read_bytes()).hexdigest() == before
        assert service.plan_voyage(SCENARIO) == voyage
    finally:
        service.close()


def test_mcp_stdio_lists_the_voyage_tool_and_rejects_unknown_calls(tmp_path):
    async def run():
        async with stdio_client(
            mcp_command("--workspace", str(tmp_path / "workspace"))
        ) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as mcp:
                await mcp.initialize()
                tools = await mcp.list_tools()
                assert {tool.name for tool in tools.tools} == {
                    "create",
                    "read",
                    "step",
                    "fork",
                    "run_plan",
                    "plan_voyage",
                    "close",
                }

                voyage = payload(
                    await mcp.call_tool("plan_voyage", {"scenario_path": SCENARIO})
                )
                assert_authored_voyage(voyage)

                unknown = await mcp.call_tool("unknown_tool", {})
                assert unknown.isError
                assert "Unknown tool" in str(unknown.content)
                bad_arguments = await mcp.call_tool(
                    "plan_voyage",
                    {"scenario_path": SCENARIO, "lon": 3.65},
                )
                assert bad_arguments.isError

    asyncio.run(run())


def test_mcp_stdio_over_unix_attaches_the_guarded_live_session(tmp_path):
    guard = coast_guard()
    handle = open_session(
        MENORCA,
        tmp_path / "live",
        project_root=ROOT,
        coast_guard=guard,
    )
    session = handle.session
    service = SimulatorService(
        tmp_path / "workspace",
        live_session=session,
        live_scenario_path=MENORCA,
    )
    path = short_socket()
    server = UnixToolServer(path, service)
    server.start()
    try:

        async def run():
            async with stdio_client(
                mcp_command("--socket", str(path))
            ) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as mcp:
                    await mcp.initialize()
                    tools = await mcp.list_tools()
                    assert "plan_voyage" in {tool.name for tool in tools.tools}

                    live = payload(await mcp.call_tool("read", {"trial_id": "live"}))
                    assert live["view"]["session_id"] == session.session_id
                    assert live["view"]["coast_guard"]["fixture"] is True
                    before = (
                        live["view"]["revision"],
                        live["view"]["elapsed_s"],
                        live["view"]["state_fingerprint"],
                    )

                    voyage = payload(
                        await mcp.call_tool(
                            "plan_voyage", {"scenario_path": SCENARIO}
                        )
                    )
                    assert_authored_voyage(voyage)
                    unchanged = payload(
                        await mcp.call_tool("read", {"trial_id": "live"})
                    )
                    assert unchanged["view"]["revision"] == before[0]
                    assert unchanged["view"]["elapsed_s"] == before[1]
                    assert unchanged["view"]["state_fingerprint"] == before[2]

                    stepped = payload(
                        await mcp.call_tool(
                            "step",
                            {
                                "trial_id": "live",
                                "heading_deg": 90,
                                "speed_knots": 15,
                                "duration_s": 900,
                                "expected_revision": before[0],
                                "operation_id": "mcp-coast",
                            },
                        )
                    )
                    view = stepped["view"]
                    assert view["session_id"] == session.session_id
                    assert view["stopping_reason"] == "coast_clearance"
                    assert view["elapsed_s"] < 900
                    assert view["paused"] is True
                    assert guard.allowed(view["vessel"]["lon"], view["vessel"]["lat"])

        asyncio.run(run())
        assert session.pause_signal.is_set()
        assert session.reason == "coast_clearance"
    finally:
        server.close()
        service.close()
        handle.release()
