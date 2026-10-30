"""Trial isolation and deterministic control around the authoritative Session."""

from __future__ import annotations

from copy import deepcopy
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from game.navigation import CoastGuard, guard_environment
from game.scenario import ROOT, load_scenario
from game.session import Session, motion_dict

STEP_LIMIT_S = 900.0
STEP_CHUNK_S = 1.0
SEGMENT_LIMIT_S = 5.0
RPC_LINE_LIMIT = 1_000_000
CONTINUATION_REASONS = frozenset({"weather_report", "cargo_grade_increased"})


@dataclass
class SessionHandle:
    """A Session and the resources that must stay open for the Session."""

    session: Session
    release: Callable[[], None] = lambda: None


SessionFactory = Callable[[Path, Path, bool], Session | SessionHandle]


def _inside(path: str | Path, root: Path, label: str) -> Path:
    root = root.resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{label} must remain inside {root}") from error
    return candidate


def scenario_reference(path: str | Path, project_root: Path = ROOT) -> Path:
    """Resolve a scenario file, and reject references outside the project."""

    resolved = _inside(path, project_root, "Scenario path")
    if not resolved.is_file():
        raise ValueError(f"Scenario file does not exist: {resolved}")
    return resolved


def open_session(
    scenario_path: str | Path,
    directory: str | Path,
    *,
    project_root: Path = ROOT,
    restore: bool = False,
    coast_guard: CoastGuard | None = None,
) -> SessionHandle:
    """Open the guarded scenario environment and its authoritative Session.

    The scenario environment stays owned by the returned handle's ExitStack.
    The coast wrapper does not own or close its source. ``coast_guard`` is for
    tests with explicit synthetic geometry; production callers omit it.
    """

    scenario_path = scenario_reference(scenario_path, project_root)
    scenario = load_scenario(scenario_path, root=project_root)
    resources = ExitStack()
    try:
        environment = resources.enter_context(scenario.environment())
        guarded = guard_environment(
            environment,
            scenario.definition["area_bbox"],
            coast_guard=coast_guard,
        )
        session = Session(
            scenario,
            scenario.simulation(guarded),
            scenario.gameplay(),
            directory,
            restore=restore,
        )
    except BaseException:
        resources.close()
        raise
    return SessionHandle(session, resources.close)


@dataclass
class _Trial:
    trial_id: str
    scenario_path: Path | None
    handle: SessionHandle
    owned: bool = True
    live: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock)
    receipts: dict[str, tuple[tuple, dict]] = field(default_factory=dict)

    @property
    def session(self) -> Session:
        return self.handle.session


class SimulatorService:
    """Own independent Session trials below one fixed workspace.

    Step receipts are kept in memory for the life of this service process. They
    are not in the Session snapshot, so a restarted service cannot recognise an
    operation ID that a previous process already committed.
    """

    def __init__(
        self,
        workspace: str | Path,
        *,
        project_root: Path = ROOT,
        session_factory: SessionFactory | None = None,
        live_session: Session | None = None,
        live_scenario_path: str | Path | None = None,
    ):
        self.workspace = Path(workspace).resolve()
        self.project_root = Path(project_root).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        if not self.workspace.is_dir():
            raise ValueError("Workspace must be a directory")
        self._factory = session_factory
        self._lock = threading.RLock()
        self._trials: dict[str, _Trial] = {}
        if live_session is not None:
            scenario_path = (
                scenario_reference(live_scenario_path, self.project_root)
                if live_scenario_path is not None
                else None
            )
            self._trials["live"] = _Trial(
                "live", scenario_path, SessionHandle(live_session), owned=False, live=True
            )

    # ---------- lifecycle ----------
    def _open(self, scenario_path: Path, directory: Path, restore: bool) -> SessionHandle:
        value = (
            self._factory(scenario_path, directory, restore)
            if self._factory is not None
            else open_session(
                scenario_path, directory, project_root=self.project_root, restore=restore
            )
        )
        return value if isinstance(value, SessionHandle) else SessionHandle(value)

    def _directory(self, trial_id: str) -> Path:
        path = (self.workspace / trial_id).resolve()
        if path.parent != self.workspace:
            raise ValueError("Trial directory must remain inside the workspace")
        return path

    def _start_trial(self, scenario_path: str | Path) -> _Trial:
        scenario_path = scenario_reference(scenario_path, self.project_root)
        trial_id = uuid.uuid4().hex
        directory = self._directory(trial_id)
        directory.mkdir()
        try:
            handle = self._open(scenario_path, directory, False)
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        trial = _Trial(trial_id, scenario_path, handle)
        with self._lock:
            self._trials[trial_id] = trial
        return trial

    def _get(self, trial_id: str) -> _Trial:
        with self._lock:
            try:
                return self._trials[trial_id]
            except KeyError as error:
                raise ValueError(f"Unknown trial_id: {trial_id}") from error

    @staticmethod
    def _coast_guard_metadata(session: Session) -> dict | None:
        """Return the fixed coast policy attached to a Session environment."""
        environment = getattr(session.sim, "environment", None)
        seen = set()
        while environment is not None and id(environment) not in seen:
            seen.add(id(environment))
            guard = getattr(environment, "guard", None)
            metadata = getattr(guard, "metadata", None)
            if callable(metadata):
                return metadata()
            environment = getattr(environment, "source", None)
        return None

    @staticmethod
    def _state_fingerprint(session: Session) -> str:
        state = {
            "motion": motion_dict(session.state),
            "cargo": session.cargo,
            "ship": session.ship,
            "consumed": sorted(session.consumed),
            "seed": session.seed,
            "order": asdict(session.order),
        }
        payload = json.dumps(
            state, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        return hashlib.sha256(payload).hexdigest()

    @classmethod
    def _result(cls, trial: _Trial) -> dict:
        with trial.session.lock:
            view = trial.session.visible()
            # Complete physical identity for Builder and hint applicability.
            view["state_fingerprint"] = cls._state_fingerprint(trial.session)
            view["coast_guard"] = cls._coast_guard_metadata(trial.session)
            return {"trial_id": trial.trial_id, "view": view}

    def create(self, scenario_path: str) -> dict:
        return self._result(self._start_trial(scenario_path))

    def read(self, trial_id: str) -> dict:
        trial = self._get(trial_id)
        return self._result(trial)

    def fork(self, trial_id: str) -> dict:
        source = self._get(trial_id)
        if source.scenario_path is None:
            raise ValueError("This trial has no scenario reference to fork")
        with source.lock:
            with source.session.lock:
                source.session.save()
                new_id = uuid.uuid4().hex
                directory = self._directory(new_id)
                directory.mkdir()
                try:
                    shutil.copyfile(
                        source.session.snapshot_path, directory / "state.json"
                    )
                    shutil.copyfile(source.session.log_path, directory / "events.jsonl")
                    handle = self._open(source.scenario_path, directory, True)
                except BaseException:
                    shutil.rmtree(directory, ignore_errors=True)
                    raise
            trial = _Trial(new_id, source.scenario_path, handle)
            with self._lock:
                self._trials[new_id] = trial
        return self._result(trial)

    def close_trial(self, trial_id: str) -> dict:
        with self._lock:
            trial = self._trials.get(trial_id)
            if trial is None:
                raise ValueError(f"Unknown trial_id: {trial_id}")
            if trial.live:
                raise ValueError("The live trial closes with its terminal host")
            del self._trials[trial_id]
        with trial.lock:
            trial.handle.release()
        return {"trial_id": trial_id, "closed": True}

    def close(self) -> None:
        with self._lock:
            trials = [trial for trial in self._trials.values() if trial.owned]
            self._trials.clear()
        error = None
        for trial in trials:
            try:
                with trial.lock:
                    trial.handle.release()
            except Exception as failure:  # noqa: BLE001 - release every remaining trial.
                error = error or failure
        if error is not None:
            raise error

    # ---------- bounded mutation ----------
    @staticmethod
    def _number(value, label: str) -> float:
        if isinstance(value, bool):
            raise ValueError(f"{label} must be a finite number")
        try:
            number = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{label} must be a finite number") from error
        if not math.isfinite(number):
            raise ValueError(f"{label} must be a finite number")
        return number

    def _step_values(
        self,
        session: Session,
        heading_deg,
        speed_knots,
        duration_s,
        expected_revision,
        operation_id,
    ) -> tuple[float, float, float, int, str]:
        heading = self._number(heading_deg, "heading_deg")
        if not 0 <= heading < 360:
            raise ValueError("heading_deg must be in [0, 360)")
        knots = self._number(speed_knots, "speed_knots")
        if knots < 0:
            raise ValueError("speed_knots must be nonnegative")
        duration = self._number(duration_s, "duration_s")
        if not 0 < duration <= STEP_LIMIT_S:
            raise ValueError(f"duration_s must be in (0, {STEP_LIMIT_S}]")
        dt_s = float(session.sim.settings.dt_s)
        steps = round(duration / dt_s)
        if steps < 1 or not math.isclose(
            steps * dt_s, duration, rel_tol=0, abs_tol=1e-9
        ):
            raise ValueError("duration_s must be a positive multiple of the simulation step")
        if (
            not isinstance(expected_revision, int)
            or isinstance(expected_revision, bool)
            or expected_revision < 0
        ):
            raise ValueError("expected_revision must be a nonnegative integer")
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("operation_id must be a nonempty string")
        return heading, knots, duration, expected_revision, operation_id

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
        trial = self._get(trial_id)
        session = trial.session
        heading, knots, duration, expected_revision, operation_id = self._step_values(
            session,
            heading_deg,
            speed_knots,
            duration_s,
            expected_revision,
            operation_id,
        )
        fingerprint = (heading, knots, duration, expected_revision)
        with trial.lock:
            previous = trial.receipts.get(operation_id)
            if previous is not None:
                if previous[0] != fingerprint:
                    raise ValueError(
                        "operation_id was already used with different arguments"
                    )
                return deepcopy(previous[1])
            with session.lock:
                if session.outcome:
                    raise ValueError("Mission has ended")
                if expected_revision != session.revision:
                    raise ValueError(
                        f"Stale revision: expected {expected_revision}, "
                        f"current {session.revision}"
                    )
                if trial.live and not session.pause_signal.is_set():
                    raise ValueError("The live Session is sailing; pause it first")
                epoch = session.player_epoch
                session.set_order(heading, knots)
                # set_order records one manual epoch. A further increment means
                # the player paused between our command and resume authorization.
                expected_epoch = epoch + 1
                authorized = session.authorize_resume(expected_epoch=expected_epoch)
            if authorized:
                dt_s = float(session.sim.settings.dt_s)
                remaining_steps = round(duration / dt_s)
                chunk_steps = min(
                    max(1, round(STEP_CHUNK_S / dt_s)), remaining_steps
                )
                while remaining_steps > 0:
                    steps = min(chunk_steps, remaining_steps)
                    with session.lock:
                        # A player pause, order or resume increments the epoch.
                        # Stop before the next chunk instead of taking control back.
                        if (
                            session.player_epoch != expected_epoch
                            or session.pause_signal.is_set()
                            or session.outcome
                        ):
                            break
                        before = session.state.exposure.elapsed_s
                        session.advance_segment(steps * dt_s)
                        advanced = session.state.exposure.elapsed_s - before
                        reason = session.reason
                    remaining_steps -= max(0, round(advanced / dt_s))
                    if (
                        session.outcome
                        or reason != "duration_reached"
                        or advanced <= 0
                    ):
                        break
                    # Give readers, including the curses loop, the lock between
                    # short chunks instead of immediately taking it again.
                    time.sleep(0.001)
            with session.lock:
                # Preserve a newer player resume or order; otherwise the agent
                # step always leaves the Session paused.
                if session.player_epoch == expected_epoch:
                    session.pause()
                result = self._result(trial)
            trial.receipts[operation_id] = (fingerprint, deepcopy(result))
            return result

    # ---------- deterministic plans ----------
    def _orders(self, orders) -> list[dict]:
        if not isinstance(orders, (list, tuple)):
            raise ValueError("orders must be a list")
        result, previous = [], -math.inf
        for raw in orders:
            if not isinstance(raw, dict) or set(raw) != {
                "t",
                "heading_deg",
                "speed_knots",
            }:
                raise ValueError(
                    "Each order requires exactly t, heading_deg and speed_knots"
                )
            t = self._number(raw["t"], "order t")
            heading = self._number(raw["heading_deg"], "order heading_deg")
            knots = self._number(raw["speed_knots"], "order speed_knots")
            if t < 0 or t < previous:
                raise ValueError("Order times must be nonnegative and nondecreasing")
            if not 0 <= heading < 360:
                raise ValueError("Order heading_deg must be in [0, 360)")
            if knots < 0:
                raise ValueError("Order speed_knots must be nonnegative")
            result.append({"t": t, "heading_deg": heading, "speed_knots": knots})
            previous = t
        return result

    @staticmethod
    def _apply_order(session: Session, order: dict) -> None:
        session.set_order(order["heading_deg"], order["speed_knots"])

    def _run(self, session: Session, orders: list[dict]) -> dict:
        index = 0
        dt_s = float(session.sim.settings.dt_s)
        budget_s = float(session.rules.budget_s)
        while session.outcome is None:
            elapsed = session.state.exposure.elapsed_s
            if elapsed >= budget_s - 1e-9:
                break
            while index < len(orders) and orders[index]["t"] <= elapsed + 1e-9:
                self._apply_order(session, orders[index])
                index += 1
            next_t = orders[index]["t"] if index < len(orders) else budget_s
            until = min(next_t, budget_s, elapsed + SEGMENT_LIMIT_S)
            steps = math.floor((until - elapsed) / dt_s + 1e-9)
            if steps < 1:
                # Stop on the first integration state at or after an order or
                # the budget, not before it. This keeps fractional order times
                # from acting early on the fixed simulation clock.
                if elapsed >= budget_s - 1e-9:
                    break
                steps = 1
            session.authorize_resume()
            session.advance_segment(steps * dt_s)
            if session.outcome:
                break
            if session.reason == "duration_reached":
                continue
            if session.reason in CONTINUATION_REASONS:
                session.record(
                    "plan_continuation",
                    {
                        "reason": session.reason,
                        "time_s": session.state.exposure.elapsed_s,
                        "order": asdict(session.order),
                    },
                )
                session.authorize_resume()
                continue
            break
        session.pause()
        with session.lock:
            return session.visible()

    # ---------- read-only voyage context ----------
    def plan_voyage(self, scenario_path: str) -> dict:
        """Build authored voyage context from the fixed scenario initial state.

        This method starts no trial and does not change the scenario, the live
        Session or the coast policy. It never accepts caller-selected geometry.
        """
        from agent.voyage import VoyagePlanner

        scenario_path = scenario_reference(scenario_path, self.project_root)
        scenario = load_scenario(scenario_path, root=self.project_root)
        return VoyagePlanner(project_root=self.project_root).build(
            deepcopy(scenario.definition["initial"])
        )

    def run_plan(self, scenario_path: str, orders: list[dict]) -> dict:
        orders = self._orders(orders)
        trial = self._start_trial(scenario_path)
        with trial.lock:
            view = self._run(trial.session, orders)
            view["state_fingerprint"] = self._state_fingerprint(trial.session)
            view["coast_guard"] = self._coast_guard_metadata(trial.session)
        return {
            "trial_id": trial.trial_id,
            "outcome": trial.session.outcome or trial.session.reason,
            "elapsed_s": view["elapsed_s"],
            "lost_teu": view["cargo"]["lost_teu"],
            "total_teu": view["cargo"]["total_teu"],
            "ship_lost": bool(
                trial.session.ship["lost"] or trial.session.outcome == "ship_lost"
            ),
            "orders": deepcopy(orders),
            "session": str(trial.session.directory),
            "view": view,
        }
