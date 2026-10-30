import asyncio
import json
import threading
from pathlib import Path
import socket
import stat
import sys
import time
import uuid

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_session import make_session
from test_session import vessel as vessel  # noqa: PLC0414 - Shared pytest fixture.

from agent.mcp import UnixSocketClient
from agent.simulator import RPC_LINE_LIMIT, SimulatorService
from agent.tui import UnixToolServer

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = "scenarios/menorca-20260909/scenario.json"
MENORCA = ROOT / SCENARIO


def short_socket():
    return Path("/private/tmp") / f"tide-test-{uuid.uuid4().hex[:12]}.sock"


def live_service(tmp_path, vessel):
    session = make_session(vessel, tmp_path / "live")
    service = SimulatorService(
        tmp_path / "workspace",
        live_session=session,
        live_scenario_path=MENORCA,
    )
    return session, service


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


def test_unix_socket_is_private_and_controls_the_same_live_session(vessel, tmp_path):
    session, service = live_service(tmp_path, vessel)
    path = short_socket()
    server = UnixToolServer(path, service)
    server.start()
    client = UnixSocketClient(path)
    try:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        live = client.read("live")
        assert live["view"]["session_id"] == session.session_id
        assert live["view"]["elapsed_s"] == 0

        session.authorize_resume()
        with pytest.raises(ValueError, match="sailing"):
            client.step(
                "live",
                30,
                2,
                0.1,
                expected_revision=session.revision,
                operation_id="active",
            )
        assert session.state.tick == 0
        session.pause()

        result = client.step(
            "live",
            30,
            2,
            0.1,
            expected_revision=session.revision,
            operation_id="live-step",
        )
        assert result["view"]["session_id"] == session.session_id
        assert result["view"]["elapsed_s"] == pytest.approx(0.1)
        assert session.state.tick == 2
        assert session.pause_signal.is_set()

        with pytest.raises(ValueError, match="Stale revision"):
            client.step(
                "live",
                40,
                2,
                0.1,
                expected_revision=result["view"]["revision"] - 1,
                operation_id="stale-live",
            )
        assert client.read("live")["view"]["elapsed_s"] == pytest.approx(0.1)
    finally:
        client.close()
        server.close()
        service.close()


def test_socket_client_eof_does_not_deadlock(tmp_path):
    path = short_socket()
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(1)

    def close_client():
        connection, _ = listener.accept()
        connection.close()

    server = threading.Thread(target=close_client, daemon=True)
    server.start()
    client = UnixSocketClient(path)
    outcome = {}

    def call():
        try:
            client.read("live")
        except BaseException as error:  # noqa: BLE001 - Checked below.
            outcome["error"] = error

    worker = threading.Thread(target=call)
    worker.start()
    worker.join(2)
    try:
        assert not worker.is_alive()
        assert isinstance(outcome.get("error"), ConnectionError)
    finally:
        client.close()
        listener.close()
        if path.exists():
            path.unlink()


def test_socket_client_bounds_request_lines(vessel, tmp_path):
    _session, service = live_service(tmp_path, vessel)
    path = short_socket()
    server = UnixToolServer(path, service)
    server.start()
    client = UnixSocketClient(path)
    try:
        with pytest.raises(ValueError, match="size limit"):
            client._call("read", {"trial_id": "x" * RPC_LINE_LIMIT})
    finally:
        client.close()
        server.close()
        service.close()


def test_existing_socket_is_rejected_and_preserved(tmp_path):
    path = short_socket()
    existing = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        existing.bind(str(path))
        service = SimulatorService(tmp_path / "workspace")
        try:
            with pytest.raises(FileExistsError):
                UnixToolServer(path, service)
            assert path.exists()
            assert stat.S_ISSOCK(path.stat().st_mode)
        finally:
            service.close()
    finally:
        existing.close()
        if path.exists():
            path.unlink()


def test_mcp_stdio_direct_handshake_and_real_trial(tmp_path):
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
                created = payload(
                    await mcp.call_tool("create", {"scenario_path": SCENARIO})
                )
                trial_id = created["trial_id"]
                stepped = payload(
                    await mcp.call_tool(
                        "step",
                        {
                            "trial_id": trial_id,
                            "heading_deg": 20,
                            "speed_knots": 2,
                            "duration_s": 1,
                            "expected_revision": created["view"]["revision"],
                            "operation_id": "mcp-direct",
                        },
                    )
                )
                assert stepped["view"]["elapsed_s"] == pytest.approx(1)
                read_back = payload(
                    await mcp.call_tool("read", {"trial_id": trial_id})
                )
                assert read_back["view"]["elapsed_s"] == pytest.approx(1)
                closed = payload(
                    await mcp.call_tool("close", {"trial_id": trial_id})
                )
                assert closed["closed"]

    asyncio.run(run())


def test_mcp_stdio_cancellation_stops_the_live_step(vessel, tmp_path, monkeypatch):
    session, service = live_service(tmp_path, vessel)
    original_advance = session.sim.advance

    def slow_advance(*args, **kwargs):
        time.sleep(0.02)
        return original_advance(*args, **kwargs)

    monkeypatch.setattr(session.sim, "advance", slow_advance)
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
                    live = payload(await mcp.call_tool("read", {"trial_id": "live"}))
                    pending = asyncio.create_task(
                        mcp.call_tool(
                            "step",
                            {
                                "trial_id": "live",
                                "heading_deg": 30,
                                "speed_knots": 2,
                                "duration_s": 900,
                                "expected_revision": live["view"]["revision"],
                                "operation_id": "cancel-live",
                            },
                        )
                    )
                    deadline = asyncio.get_running_loop().time() + 5
                    while session.revision == live["view"]["revision"]:
                        if asyncio.get_running_loop().time() >= deadline:
                            pending.cancel()
                            raise AssertionError("live step did not start")
                        await asyncio.sleep(0.01)
                    session.pause(manual=True)
                    stepped = payload(await pending)

                    assert stepped["view"]["elapsed_s"] < 900
                    assert stepped["view"]["paused"]
                    assert stepped["view"]["session_id"] == session.session_id

        asyncio.run(run())
        assert session.pause_signal.is_set()
    finally:
        server.close()
        service.close()


def test_mcp_stdio_socket_handshake_calls_same_live_session(vessel, tmp_path):
    session, service = live_service(tmp_path, vessel)
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
                    assert "step" in {tool.name for tool in tools.tools}
                    assert "plan_voyage" in {tool.name for tool in tools.tools}
                    live = payload(await mcp.call_tool("read", {"trial_id": "live"}))
                    assert live["view"]["session_id"] == session.session_id
                    stepped = payload(
                        await mcp.call_tool(
                            "step",
                            {
                                "trial_id": "live",
                                "heading_deg": 45,
                                "speed_knots": 2,
                                "duration_s": 0.1,
                                "expected_revision": live["view"]["revision"],
                                "operation_id": "mcp-live",
                            },
                        )
                    )
                    assert stepped["view"]["session_id"] == session.session_id
                    assert stepped["view"]["elapsed_s"] == pytest.approx(0.1)

        asyncio.run(run())
        assert session.state.tick == 2
        assert session.pause_signal.is_set()
    finally:
        server.close()
        service.close()
