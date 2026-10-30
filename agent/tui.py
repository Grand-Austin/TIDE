"""Terminal host with the same in-process Session exposed on a private socket."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socketserver
import stat
import threading

from game.console import Controls
from game.engine import GameEngine
from game.scenario import ROOT
from agent.simulator import (
    RPC_LINE_LIMIT,
    SimulatorService,
    open_session,
    scenario_reference,
)

_RPC_METHODS = (
    "create",
    "read",
    "step",
    "fork",
    "run_plan",
    "plan_voyage",
    "close_trial",
)


class _UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, path: str, backend):
        self.backend = backend
        self.bound_inode = None
        super().__init__(path, _Handler)

    def server_bind(self):
        old_umask = os.umask(0o177)
        try:
            super().server_bind()
        finally:
            os.umask(old_umask)
        os.chmod(self.server_address, 0o600)
        self.bound_inode = os.stat(self.server_address).st_ino

    def dispatch(self, method, params):
        if method not in _RPC_METHODS:
            raise ValueError("Unknown RPC method")
        if not isinstance(params, dict):
            raise ValueError("RPC params must be an object")
        return getattr(self.backend, method)(**params)


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        while True:
            line = self.rfile.readline(RPC_LINE_LIMIT + 1)
            if not line:
                return
            request_id = None
            if len(line) > RPC_LINE_LIMIT:
                response = {
                    "id": None,
                    "error": "ValueError: RPC request line exceeds the size limit",
                }
                self.wfile.write(
                    (json.dumps(response, allow_nan=False) + "\n").encode()
                )
                self.wfile.flush()
                return
            try:
                request = json.loads(line)
                request_id = request.get("id")
                if not isinstance(request_id, int):
                    raise ValueError("RPC id must be an integer")
                result = self.server.dispatch(request.get("method"), request.get("params"))
                response = {"id": request_id, "result": result}
            except Exception as error:  # noqa: BLE001 - Return a bounded RPC error.
                response = {
                    "id": request_id,
                    "error": f"{type(error).__name__}: {error}",
                }
            self.wfile.write(
                (json.dumps(response, allow_nan=False) + "\n").encode()
            )
            self.wfile.flush()


class UnixToolServer:
    """Private local RPC transport for one backend, normally a SimulatorService."""

    def __init__(self, path: str | Path, backend):
        self.path = Path(path)
        if os.path.lexists(self.path):
            raise FileExistsError(f"Socket path already exists: {self.path}")
        if not self.path.parent.is_dir():
            raise ValueError(f"Socket directory does not exist: {self.path.parent}")
        self.backend = backend
        self._server = _UnixServer(str(self.path), backend)
        self._thread = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="tide-live-rpc", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join(timeout=5)
            self._thread = None
        self._server.server_close()
        try:
            current = os.lstat(self.path)
        except FileNotFoundError:
            return
        if (
            stat.S_ISSOCK(current.st_mode)
            and self._server.bound_inode is not None
            and current.st_ino == self._server.bound_inode
        ):
            self.path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--workspace", type=Path)
    args = parser.parse_args()

    scenario_path = scenario_reference(args.scenario, ROOT)
    session_directory = args.session.resolve()
    restore = (session_directory / "state.json").exists()
    handle = open_session(
        scenario_path, session_directory, project_root=ROOT, restore=restore
    )
    session = handle.session
    workspace = args.workspace or session_directory.parent
    service = SimulatorService(
        workspace,
        project_root=ROOT,
        live_session=session,
        live_scenario_path=scenario_path,
    )
    server = None
    controls = None
    try:
        server = UnixToolServer(args.socket, service)
        server.start()
        controls = Controls(session, GameEngine(session))
        try:
            from game.terminal import run_screen

            run_screen(controls)
        except KeyboardInterrupt:
            pass
        finally:
            controls.close()
    finally:
        if server is not None:
            server.close()
        service.close()
        handle.release()
    print(f"Session saved: {session_directory}")


if __name__ == "__main__":
    main()
