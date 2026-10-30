"""Official MCP stdio server for simulator trials and the live terminal Session."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import threading

from mcp.server.fastmcp import FastMCP

from agent.simulator import RPC_LINE_LIMIT, SimulatorService


class _BoundedFastMCP(FastMCP):
    """FastMCP server that rejects arguments outside each fixed tool schema."""

    def __init__(self, name: str, arguments: dict[str, set[str]]):
        super().__init__(name)
        self._arguments = arguments

    async def call_tool(self, name: str, arguments: dict) -> object:
        allowed = self._arguments.get(name)
        extra = set(arguments or {}) - allowed if allowed is not None else set()
        if extra:
            raise ValueError(
                f"Unexpected argument(s): {', '.join(sorted(extra))}"
            )
        return await super().call_tool(name, arguments)


class UnixSocketClient:
    """Small JSON-line client for the terminal host's private socket."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._socket = None
        self._stream = None
        self._next_id = 0

    def _connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(str(self.path))
        except BaseException:
            sock.close()
            raise
        self._socket = sock
        self._stream = sock.makefile("rwb")

    def _call(self, method: str, params: dict) -> dict:
        with self._lock:
            if self._socket is None:
                self._connect()
            self._next_id += 1
            request_id = self._next_id
            payload = (
                json.dumps(
                    {"id": request_id, "method": method, "params": params},
                    allow_nan=False,
                )
                + "\n"
            ).encode()
            if len(payload) > RPC_LINE_LIMIT:
                raise ValueError("RPC request line exceeds the size limit")
            self._stream.write(payload)
            self._stream.flush()
            line = self._stream.readline(RPC_LINE_LIMIT + 1)
            if not line:
                self.close()
                raise ConnectionError("Simulator socket closed the connection")
            if len(line) > RPC_LINE_LIMIT:
                self.close()
                raise ValueError("RPC response line exceeds the size limit")
            response = json.loads(line)
        if response.get("id") != request_id:
            raise ConnectionError("Simulator socket returned a mismatched response")
        if response.get("error"):
            raise ValueError(response["error"])
        return response["result"]

    def create(self, scenario_path: str) -> dict:
        return self._call("create", {"scenario_path": scenario_path})

    def read(self, trial_id: str) -> dict:
        return self._call("read", {"trial_id": trial_id})

    def step(
        self,
        trial_id: str,
        heading_deg: float,
        speed_knots: float,
        duration_s: float,
        *,
        expected_revision: int,
        operation_id: str,
    ) -> dict:
        return self._call(
            "step",
            {
                "trial_id": trial_id,
                "heading_deg": heading_deg,
                "speed_knots": speed_knots,
                "duration_s": duration_s,
                "expected_revision": expected_revision,
                "operation_id": operation_id,
            },
        )

    def fork(self, trial_id: str) -> dict:
        return self._call("fork", {"trial_id": trial_id})

    def run_plan(self, scenario_path: str, orders: list[dict]) -> dict:
        return self._call(
            "run_plan", {"scenario_path": scenario_path, "orders": orders}
        )

    def plan_voyage(self, scenario_path: str) -> dict:
        return self._call("plan_voyage", {"scenario_path": scenario_path})

    def close_trial(self, trial_id: str) -> dict:
        return self._call("close_trial", {"trial_id": trial_id})

    def close(self) -> None:
        with self._lock:
            stream, sock = self._stream, self._socket
            self._stream = self._socket = None
        for resource in (stream, sock):
            if resource is not None:
                try:
                    resource.close()
                except OSError:
                    pass


def build_mcp(backend, *, name: str = "TIDE Simulator") -> FastMCP:
    """Expose one backend with the bounded simulator tool set."""

    server = _BoundedFastMCP(
        name,
        {
            "create": {"scenario_path"},
            "read": {"trial_id"},
            "step": {
                "trial_id",
                "heading_deg",
                "speed_knots",
                "duration_s",
                "expected_revision",
                "operation_id",
            },
            "fork": {"trial_id"},
            "run_plan": {"scenario_path", "orders"},
            "plan_voyage": {"scenario_path"},
            "close": {"trial_id"},
        },
    )

    @server.tool()
    def create(scenario_path: str) -> dict:
        """Start an independent paused trial from a project scenario file."""
        return backend.create(scenario_path)

    @server.tool()
    def read(trial_id: str) -> dict:
        """Read the authoritative Session view for one trial."""
        return backend.read(trial_id)

    @server.tool()
    def step(
        trial_id: str,
        heading_deg: float,
        speed_knots: float,
        duration_s: float,
        expected_revision: int,
        operation_id: str,
    ) -> dict:
        """Apply an order for a bounded duration, then pause the trial."""
        return backend.step(
            trial_id,
            heading_deg,
            speed_knots,
            duration_s,
            expected_revision=expected_revision,
            operation_id=operation_id,
        )

    @server.tool()
    def fork(trial_id: str) -> dict:
        """Copy one complete paused trial into an isolated trial."""
        return backend.fork(trial_id)

    @server.tool()
    def run_plan(scenario_path: str, orders: list[dict]) -> dict:
        """Run an independent Session to an outcome with timed orders."""
        return backend.run_plan(scenario_path, orders)

    @server.tool()
    def plan_voyage(scenario_path: str) -> dict:
        """Read authored voyage context from the scenario initial state.

        The call starts no trial, moves no vessel and changes no coast policy.
        """
        return backend.plan_voyage(scenario_path)

    @server.tool()
    def close(trial_id: str) -> dict:
        """Close one trial and release its environment."""
        return backend.close_trial(trial_id)

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--socket", type=Path)
    args = parser.parse_args()
    if args.socket is None and args.workspace is None:
        parser.error("--workspace or --socket is required")

    backend = (
        UnixSocketClient(args.socket)
        if args.socket is not None
        else SimulatorService(args.workspace)
    )
    server = build_mcp(backend)
    try:
        server.run(transport="stdio")
    finally:
        backend.close()


if __name__ == "__main__":
    main()
