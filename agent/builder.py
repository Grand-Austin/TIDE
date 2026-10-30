"""Autonomous, bounded scenario construction for TIDE.

The designer owns the frozen inputs.  The Builder owns only the mission
budget/end and the gameplay/report schedule derived from them.  It searches
with both route solvers, adds bounded expert proposals, runs real full
sessions, inspects failures, revises candidates, and packages evidence for a
human reviewer.  It never publishes to ``scenarios/`` and never treats model
text as approval.
"""

from __future__ import annotations

import copy
import json
import math
from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Callable

import numpy as np
import xarray as xr

from agent.harness import (
    AgentHarness,
    HarnessPending,
    ToolError,
    ToolRegistry,
    instruction_digest,
    instructions_from,
    load_builder_profile,
    load_tool_specs,
)
from agent.models import (
    Candidate,
    DependencyUnavailable,
    LOSS_LIMIT,
    MAX_ORDERS,
    TrialLimit,
    TrialResult,
    algorithm_hashes,
    evidence_fingerprint,
    is_qualified,
    normalize_orders,
    relative_path,
    stable_digest,
)
from agent.solutions import build_solutions
from agent.models import BudgetPlan
from agent.routing import RouteSearch
from agent.simulator import SimulatorService
from agent.voyage import VoyagePlanner, VoyageUnavailable
from game.navigation import DEFAULT_CLEARANCE_NM, CoastGuard
from game.scenario import ROOT, Scenario, digest, load_scenario, utc
from pipeline.gameplay import build_gameplay
from pipeline.scenario import write_json

HARNESS_DIR = Path(__file__).with_name("builder_harness")
SEARCH_METHODS = ("simroute", "visir2")
QUALIFICATION_RULE = (
    "escape in a full session, keep the ship afloat, and lose at most 1 % of the initial TEU"
)


def iso_z(value) -> str:
    return value.astimezone(utc("2000-01-01T00:00:00Z").tzinfo).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def planned_budget(elapsed_s: float) -> int:
    """900 * ceil(1.2 * T / 900), with a 900 s floor.

    ``T`` is a verified full-session escape time, never the input budget.
    """
    value = float(elapsed_s)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Elapsed time must be finite and positive")
    return max(900, 900 * math.ceil(1.2 * value / 900))


def select_budget(
    definition: dict, root: Path, *, budget_s: int | None = None
) -> BudgetPlan:
    """Build a plan for either the search horizon or the final calibrated budget."""
    start = utc(definition["start"])
    source = int(definition["mission"]["budget_s"])
    budget = source if budget_s is None else int(budget_s)
    end = start + timedelta(seconds=budget)
    return BudgetPlan(
        source_budget_s=source,
        budget_s=budget,
        start=iso_z(start),
        end=iso_z(end),
    )


def check_environment_coverage(definition: dict, root: Path) -> dict:
    """Check the extended window and the authored area in every source layer."""
    root = Path(root)
    start = utc(definition["start"])
    end = start + timedelta(seconds=int(definition["mission"]["budget_s"]))
    west, east, south, north = definition["area_bbox"]
    layers, errors = {}, []
    for name, source in sorted(definition.get("environment", {}).items()):
        path = root / source["path"]
        item = {"path": str(source["path"]), "sha256": source.get("sha256")}
        try:
            with xr.open_dataset(path) as dataset:
                times = dataset["time"].values
                lons = dataset["longitude"].values
                lats = dataset["latitude"].values
                first = np.datetime64(start.replace(tzinfo=None))
                last = np.datetime64(end.replace(tzinfo=None))
                item.update(
                    {
                        "time": [str(times.min()), str(times.max())],
                        "longitude": [float(np.nanmin(lons)), float(np.nanmax(lons))],
                        "latitude": [float(np.nanmin(lats)), float(np.nanmax(lats))],
                    }
                )
                if first < times.min() or last > times.max():
                    errors.append(f"{name}: mission window is outside source time coverage")
                if west < np.nanmin(lons) or east > np.nanmax(lons):
                    errors.append(f"{name}: mission longitude is outside source coverage")
                if south < np.nanmin(lats) or north > np.nanmax(lats):
                    errors.append(f"{name}: mission latitude is outside source coverage")
        except Exception as error:
            errors.append(f"{name}: source coverage cannot be read ({error})")
        layers[name] = item
    return {
        "ok": not errors,
        "errors": errors,
        "layers": layers,
        "start": iso_z(start),
        "end": iso_z(end),
        "budget_s": int(definition["mission"]["budget_s"]),
        "intersection": "all historical environment layers",
    }


def coverage_time_limit_s(coverage: dict, start_value: str) -> float | None:
    """Smallest source time limit implied by a coverage report, if present."""
    layers = coverage.get("layers")
    if not isinstance(layers, dict):
        return None
    try:
        origin = np.datetime64(utc(start_value).replace(tzinfo=None))
    except (TypeError, ValueError):
        return None
    limits = []
    for layer in layers.values():
        if not isinstance(layer, dict):
            continue
        times = layer.get("time")
        if not isinstance(times, (list, tuple)) or len(times) != 2:
            continue
        try:
            maximum = np.datetime64(str(times[1]).replace("Z", ""))
            limit = (maximum - origin) / np.timedelta64(1, "s")
        except (TypeError, ValueError):
            continue
        if math.isfinite(float(limit)):
            limits.append(float(limit))
    return min(limits) if limits else None


def _number(value, default=0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(default)
    return result if math.isfinite(result) else float(default)


def _integer(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _strict_count(value) -> int | None:
    """Return an integral nonnegative count, or None for missing/inconsistent data."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        return None
    return int(number)


def _view_metric_errors(
    view: dict,
    *,
    outcome: str | None,
    elapsed_s: float | None,
    lost_teu: int | None,
    total_teu: int | None,
    ship_lost: bool | None,
) -> list[str]:
    """Reject a result whose visible metrics contradict its top-level metrics."""
    if not isinstance(view, dict) or not view:
        return []
    errors: list[str] = []
    view_outcome = view.get("outcome")
    # Session.visible() leaves outcome empty for a non-terminal reason such as
    # domain_boundary; the top-level result uses the stopping reason there.
    if view_outcome is not None and view_outcome != outcome:
        errors.append("view_outcome")
    cargo = view.get("cargo")
    if isinstance(cargo, dict):
        for key, expected in (("lost_teu", lost_teu), ("total_teu", total_teu)):
            if key in cargo:
                actual = _strict_count(cargo.get(key))
                if actual is None or expected is None or actual != expected:
                    errors.append(f"view_{key}")
    elif cargo is not None:
        errors.append("view_cargo")
    ship = view.get("ship")
    if isinstance(ship, dict):
        if "lost" in ship:
            actual = ship.get("lost")
            if not isinstance(actual, bool) or actual != ship_lost:
                errors.append("view_ship_lost")
    elif ship is not None:
        errors.append("view_ship")
    if "elapsed_s" in view:
        raw = view.get("elapsed_s")
        actual = (
            float(raw)
            if isinstance(raw, (int, float))
            and not isinstance(raw, bool)
            and math.isfinite(float(raw))
            else None
        )
        if (
            actual is None
            or elapsed_s is None
            or abs(actual - elapsed_s) > 1e-6
        ):
            errors.append("view_elapsed_s")
    return errors


def _voyage_point(value, label: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"Voyage {label} must be a lon/lat pair")
    try:
        lon, lat = float(value[0]), float(value[1])
    except (TypeError, ValueError) as error:
        raise ValueError(f"Voyage {label} must be numeric") from error
    if not (math.isfinite(lon) and math.isfinite(lat)):
        raise ValueError(f"Voyage {label} must be finite")
    if not -180.0 <= lon <= 180.0 or not -90.0 <= lat <= 90.0:
        raise ValueError(f"Voyage {label} is outside WGS84 bounds")
    return lon, lat


def _voyage_port(value, label: str) -> dict:
    if not isinstance(value, Mapping):
        raise ValueError(f"Voyage {label} must be an object")
    name, country = value.get("name"), value.get("country")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"Voyage {label} needs a non-empty name")
    if not isinstance(country, str) or not country.strip():
        raise ValueError(f"Voyage {label} needs a non-empty country")
    lon, lat = _voyage_point([value.get("lon"), value.get("lat")], f"{label} port")
    return dict(value, name=name, country=country, lon=lon, lat=lat)


def validate_voyage(value) -> dict:
    """Return a JSON-ready authored voyage, or raise ``ValueError``.

    The Builder accepts a voyage only as fixed display context. It keeps the
    designer's actual start in the voyage provenance and never uses a port
    approach as an escape condition.
    """
    if not isinstance(value, Mapping):
        raise ValueError("Voyage must be an object")
    result = copy.deepcopy(dict(value))
    if result.get("version") != 1:
        raise ValueError("Voyage has an unsupported version")
    if result.get("authored") is not True:
        raise ValueError("Voyage must be explicitly authored")
    result["origin"] = _voyage_port(result.get("origin"), "origin")
    result["destination"] = _voyage_port(result.get("destination"), "destination")
    track = result.get("track")
    if not isinstance(track, (list, tuple)) or len(track) < 2:
        raise ValueError("Voyage needs at least two track points")
    result["track"] = [list(_voyage_point(point, "track point")) for point in track]
    try:
        distance = float(result.get("distance_nm"))
    except (TypeError, ValueError) as error:
        raise ValueError("Voyage distance must be numeric") from error
    if not math.isfinite(distance) or distance <= 0:
        raise ValueError("Voyage distance must be finite and positive")
    result["distance_nm"] = distance
    snapped = _voyage_point(result.get("snapped_position"), "snapped position")
    result["snapped_position"] = list(snapped)
    try:
        snap_distance = float(result.get("snap_distance_nm"))
    except (TypeError, ValueError) as error:
        raise ValueError("Voyage snap distance must be numeric") from error
    if not math.isfinite(snap_distance) or snap_distance < 0:
        raise ValueError("Voyage snap distance must be finite and non-negative")
    result["snap_distance_nm"] = snap_distance
    if not isinstance(result.get("provenance"), Mapping):
        raise ValueError("Voyage needs provenance")
    return result


class FileTrialCache:
    """A stale-safe, restart-safe store of full-session trial results."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.entries = {}
        if self.path.is_file():
            value = json.loads(self.path.read_text())
            if value.get("version") != 1:
                raise ValueError("Unsupported trial cache")
            self.entries = value.get("entries", {})

    def get(self, key: str) -> dict | None:
        return self.entries.get(key)

    def put(self, key: str, value: dict) -> None:
        self.entries[key] = value
        write_json(self.path, {"version": 1, "entries": self.entries})


class Builder:
    def __init__(
        self,
        scenario_path: str | Path,
        out: str | Path,
        *,
        project_root: str | Path = ROOT,
        scenario=None,
        simulator=None,
        route_search=None,
        voyage_planner=None,
        coast_guard=None,
        coverage_check: Callable[[dict, Path], dict] | None = None,
        gameplay_builder: Callable[[Scenario], dict] | None = None,
        max_trials: int = 64,
        max_candidates: int = 12,
        expert_limit: int = 3,
        max_revisions: int = 2,
        min_solutions: int = 3,
        models_path: str | Path = "models.json",
        keys_path: str | Path = "config/api_keys.local.json",
        transport=None,
        thread_id: str | None = None,
    ):
        self.scenario_path = Path(scenario_path)
        self.out = Path(out)
        self.project_root = Path(project_root)
        self.package = self.out / "package"
        self.work = self.out / "work"
        self.max_trials = int(max_trials)
        self.max_candidates = int(max_candidates)
        self.expert_limit = int(expert_limit)
        self.max_revisions = int(max_revisions)
        self.min_solutions = int(min_solutions)
        self.models_path = Path(models_path)
        self.keys_path = Path(keys_path)
        self.transport = transport
        self.thread_id = thread_id
        if self.max_trials < 1:
            raise ValueError("max_trials must be positive")
        if self.min_solutions < 1:
            raise ValueError("min_solutions must be positive")
        self.source_scenario = scenario or load_scenario(
            self.scenario_path, root=self.project_root
        )
        self.source_root = Path(getattr(self.source_scenario, "root", self.project_root))
        self.source_definition = copy.deepcopy(self.source_scenario.definition)
        self._check_output_safety()
        self.coverage_check = coverage_check or check_environment_coverage
        self.gameplay_builder = gameplay_builder or build_gameplay
        self.simulator = simulator
        self.route_search = route_search
        self.voyage_planner = voyage_planner
        self.coast_guard = coast_guard
        self._guard = None
        self.navigation_metadata: dict | None = None
        self.voyage: dict | None = None
        self.voyage_source: str | None = None
        self.cache = FileTrialCache(self.out / "trial-cache.json")
        self.candidates: list[Candidate] = []
        self._candidate_keys: set[str] = set()
        self._revision_depth: dict[str, int] = {}
        self.trial_records: list[TrialResult] = []
        self._trial_by_key: dict[str, TrialResult] = {}
        self._trial_runs = 0
        self.search_horizon_s = int(self.source_definition["mission"]["budget_s"])
        self.search_budget: BudgetPlan | None = None
        self.budget: BudgetPlan | None = None
        self.coverage: dict = {}
        self.final_coverage: dict = {}
        self.work_definition: dict | None = None
        self.work_gameplay: dict | None = None
        self.work_scenario_path: Path | None = None
        self.work_evidence: dict = {}
        self.search_metadata: dict = {"methods": {}, "candidates": 0, "errors": []}
        self.mode = "local"
        self.model_name = None
        self.pending_reasons: list[str] = []
        self._input_hashes = self._hash_inputs()
        self._builder_state: dict = {}
        self._final_record: TrialResult | None = None
        self._solution_records: dict[str, TrialResult] = {}
        self._solution_attempts: dict[str, dict] = {}
        self._result: dict | None = None
        self._package_weather_status: dict = {"status": "not_checked"}

    # --------------------------- output safety ---------------------------
    @staticmethod
    def _overlaps(left: Path, right: Path) -> bool:
        left, right = left.resolve(), right.resolve()
        return left == right or left in right.parents or right in left.parents

    def _protected_paths(self) -> list[Path]:
        paths = [self.scenario_path, self.scenario_path.parent]
        gameplay = self.source_definition.get("gameplay")
        if gameplay:
            paths.append(self.source_root / gameplay["path"])
        for source in self.source_definition.get("environment", {}).values():
            paths.append(self.source_root / source["path"])
        scenarios = self.project_root / "scenarios"
        if scenarios.exists():
            paths.append(scenarios)
        return paths

    def _check_output_safety(self) -> None:
        outputs = [self.out, self.package, self.work]
        for output in outputs:
            for protected in self._protected_paths():
                if self._overlaps(output, protected):
                    raise ValueError(
                        "Builder output must not overlap an input or published scenario: "
                        f"{output} overlaps {protected}"
                    )

    # --------------------------- input immutability ---------------------------
    def _hash_inputs(self) -> dict:
        paths = [self.scenario_path]
        gameplay = self.source_definition.get("gameplay")
        if gameplay:
            paths.append(self.source_root / gameplay["path"])
        for source in self.source_definition.get("environment", {}).values():
            paths.append(self.source_root / source["path"])
        return {
            str(path): digest(path) if Path(path).is_file() else None
            for path in paths
        }

    def _inputs_unchanged(self) -> tuple[bool, list[str]]:
        changed = []
        for path, expected in self._input_hashes.items():
            actual = digest(path) if Path(path).is_file() else None
            if actual != expected:
                changed.append(path)
        return not changed, changed

    # ----------------------- navigation and voyage -----------------------
    def _ensure_navigation(self) -> dict:
        """Load the fixed 5 nm coast policy once and record its provenance."""
        if self._guard is not None:
            return self.navigation_metadata
        candidate = self.coast_guard
        if candidate is None:
            guard = CoastGuard(self.source_definition["area_bbox"])
        elif callable(candidate) and (
            isinstance(candidate, type) or not hasattr(candidate, "metadata")
        ):
            guard = candidate(self.source_definition["area_bbox"])
        else:
            guard = candidate
        metadata = dict(guard.metadata())
        clearance = metadata.get("clearance_nm")
        if clearance != DEFAULT_CLEARANCE_NM:
            raise ValueError(
                "The Builder requires the fixed "
                f"{DEFAULT_CLEARANCE_NM:g} NM coast clearance"
            )
        metadata["fixed_policy"] = {
            "clearance_nm": DEFAULT_CLEARANCE_NM,
            "override_allowed": False,
            "source": "game.navigation.CoastGuard",
        }
        metadata["override_allowed"] = False
        if self.navigation_metadata is not None and self.navigation_metadata != metadata:
            raise ValueError(
                "Navigation policy metadata changed since the saved run"
            )
        self._guard = guard
        self.navigation_metadata = metadata
        return metadata

    def _resolve_voyage(self) -> dict:
        """Preserve a designer voyage, or generate one from the fixed initial."""
        if self.voyage is not None:
            return self.voyage
        explicit = self.source_definition.get("voyage")
        if explicit is not None:
            self.voyage = validate_voyage(explicit)
            self.voyage_source = "designer_input"
            return self.voyage
        planner = self.voyage_planner or VoyagePlanner(
            project_root=self.project_root
        )
        initial = copy.deepcopy(self.source_definition["initial"])
        initial["time"] = self.source_definition["start"]
        generated = planner.build(initial)
        self.voyage = validate_voyage(generated)
        self.voyage_source = "generated"
        return self.voyage

    def _voyage_summary(self) -> dict | None:
        if self.voyage is None:
            return None
        result = copy.deepcopy(self.voyage)
        result.update(
            {
                "source": self.voyage_source,
                "authored_context": True,
                "historical_track": False,
                "harbour_safety_claimed": False,
                "snap_offset_nm": self.voyage["snap_distance_nm"],
            }
        )
        return result

    def _write_briefing(self) -> str | None:
        if self.voyage is None or not self.package.is_dir():
            return None
        origin = self.voyage["origin"]
        destination = self.voyage["destination"]
        snap = self.voyage["snap_distance_nm"]
        snapped = self.voyage["snapped_position"]
        text = (
            "# Planned voyage context\n\n"
            "This is an authored game plan. It is not a historical ship log and "
            "not a harbour-safety or navigation product. Reaching a port is not "
            "an escape condition.\n\n"
            f"Planned ports: **{origin['name']} ({origin['country']})** to "
            f"**{destination['name']} ({destination['country']})**.\n\n"
            f"Network distance: {self.voyage['distance_nm']:.1f} NM.\n\n"
            f"The designer start is preserved. The nearest network point is "
            f"{snap:.2f} NM away at {snapped[0]:.5f}, {snapped[1]:.5f}.\n\n"
            "The fixed coast guard remains 5 NM from land. The voyage does not "
            "weaken or override that guard.\n"
        )
        path = self.package / "briefing.md"
        path.write_text(text)
        return "briefing.md"

    # --------------------------- checkpoint/resume ---------------------------
    @property
    def state_path(self) -> Path:
        return self.out / "builder-state.json"

    def _configured_context(self) -> dict:
        return {
            "source_definition_sha": stable_digest(self.source_definition),
            "input_hashes": self._input_hashes,
            "algorithm_hashes": algorithm_hashes(self.project_root),
            "search_horizon_s": int(self.source_definition["mission"]["budget_s"]),
            "max_trials": self.max_trials,
            "max_candidates": self.max_candidates,
            "expert_limit": self.expert_limit,
            "max_revisions": self.max_revisions,
            "min_solutions": self.min_solutions,
        }

    def _check_run_directory(self, resume: bool) -> dict | None:
        if not self.out.exists():
            return None
        if not self.out.is_dir():
            raise ValueError("Builder output path exists and is not a directory")
        entries = list(self.out.iterdir())
        if not entries:
            return None
        if not resume:
            raise ValueError(
                "Output directory is not empty; use resume=True or a new run directory"
            )
        if not self.state_path.is_file():
            raise ValueError("Cannot resume: builder-state.json is missing")
        state = json.loads(self.state_path.read_text())
        if state.get("version") != 1:
            raise ValueError("Unsupported builder state")
        if state.get("context") != self._configured_context():
            raise ValueError(
                "Resume rejected: input, horizon, algorithm implementation or "
                "Builder configuration context changed"
            )
        return state

    def _verify_terminal_evidence(self, state: dict) -> None:
        """Reject a terminal resume when the staged final package changed."""
        saved = state.get("final_validation")
        if not saved:
            return
        scenario_file = self.package / "scenario.json"
        gameplay_file = self.package / "gameplay.json"
        if not scenario_file.is_file() or not gameplay_file.is_file():
            raise ValueError("Resume rejected: final package is incomplete")
        try:
            definition = json.loads(scenario_file.read_text())
            gameplay = json.loads(gameplay_file.read_text())
            self._ensure_navigation()
            evidence = evidence_fingerprint(
                definition,
                self.project_root,
                gameplay=gameplay,
                gameplay_path=gameplay_file,
                navigation=self.navigation_metadata,
            )
        except Exception as error:
            raise ValueError(
                f"Resume rejected: final package cannot be verified ({error})"
            ) from None
        if evidence["fingerprint"] != saved.get("fingerprint"):
            raise ValueError("Resume rejected: final package evidence changed")

    def _restore_state(self, state: dict) -> None:
        self._builder_state = state
        self.model_name = state.get("model_name")
        self.thread_id = state.get("thread_id") or self.thread_id
        self.pending_reasons = list(state.get("pending_reasons", []))
        self.candidates = [
            Candidate.from_dict(value) for value in state.get("candidates", [])
        ]
        self._candidate_keys = {candidate.key for candidate in self.candidates}
        self._revision_depth = {
            key: int(value)
            for key, value in state.get("revision_depth", {}).items()
        }
        self.trial_records = [
            TrialResult.from_dict(value) for value in state.get("trial_records", [])
        ]
        self._trial_by_key = {
            record.candidate.key: record for record in self.trial_records
        }
        self._trial_runs = int(state.get("trial_runs", 0))
        self.search_metadata = state.get("search_metadata") or {
            "methods": {},
            "candidates": 0,
            "errors": [],
        }
        self.voyage = state.get("voyage")
        self.voyage_source = state.get("voyage_source")
        self.navigation_metadata = state.get("navigation")
        self.final_coverage = state.get("final_coverage") or {}
        self._package_weather_status = state.get("weather") or {"status": "not_checked"}
        saved_final = state.get("final_validation")
        self._final_record = (
            TrialResult.from_dict(saved_final) if saved_final else None
        )
        self._solution_records = {
            key: TrialResult.from_dict(value)
            for key, value in state.get("solution_records", {}).items()
        }
        self._solution_attempts = dict(state.get("solution_attempts", {}))
        self._result = state.get("result")

    def _state_snapshot(
        self,
        *,
        status: str,
        terminal: bool = False,
        result: dict | None = None,
    ) -> dict:
        return {
            "version": 1,
            "mode": self.mode,
            "status": status,
            "terminal": bool(terminal),
            "result": result,
            "context": self._configured_context(),
            "work_evidence_fingerprint": self.work_evidence.get("fingerprint"),
            "search_horizon_s": self.search_horizon_s,
            "search_budget": self.search_budget.as_dict() if self.search_budget else None,
            "coverage": self.coverage,
            "final_coverage": self.final_coverage,
            "budget": (self.budget or self.search_budget).as_dict() if (self.budget or self.search_budget) else None,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            "revision_depth": self._revision_depth,
            "trial_records": [record.as_dict() for record in self.trial_records],
            "trial_runs": self._trial_runs,
            "search_metadata": self.search_metadata,
            "final_validation": self._final_record.as_dict()
            if self._final_record
            else None,
            "solution_records": {
                key: value.as_dict() for key, value in self._solution_records.items()
            },
            "solution_attempts": self._solution_attempts,
            "pending_reasons": self.pending_reasons,
            "model_name": self.model_name,
            "thread_id": self.thread_id,
            "voyage": self.voyage,
            "voyage_source": self.voyage_source,
            "navigation": self.navigation_metadata,
            "weather": self._package_weather_status,
        }

    def _persist_state(
        self,
        *,
        status: str,
        terminal: bool = False,
        result: dict | None = None,
    ) -> None:
        write_json(
            self.state_path,
            self._state_snapshot(status=status, terminal=terminal, result=result),
        )
        self._builder_state = self._state_snapshot(
            status=status, terminal=terminal, result=result
        )

    # ------------------------------- public API -------------------------------
    def build(self, mode: str = "local", *, resume: bool = False) -> dict:
        if mode not in {"local", "ai"}:
            raise ValueError("Mode must be local or ai")
        self.mode = mode
        state = self._check_run_directory(resume)
        if state is not None and state.get("mode") not in (None, mode):
            raise ValueError("Resume mode does not match the saved run")
        if state is not None and state.get("terminal") and state.get("result"):
            self._verify_terminal_evidence(state)
            self._result = state["result"]
            return copy.deepcopy(self._result)
        if state is not None:
            self._restore_state(state)
        self.out.mkdir(parents=True, exist_ok=True)
        self.package.mkdir(parents=True, exist_ok=True)
        self._write_run({"status": "running", "mode": mode})
        base_horizon_s = int(self.source_definition["mission"]["budget_s"])
        self.search_horizon_s = base_horizon_s
        planned_search = self._with_budget(
            self.source_definition, self.search_horizon_s
        )
        self.coverage = self.coverage_check(planned_search, self.project_root)
        if not self.coverage.get("ok"):
            limit = coverage_time_limit_s(
                self.coverage, self.source_definition["start"]
            )
            shorter = (
                max(60, 60 * math.floor(limit / 60))
                if limit is not None
                else None
            )
            if shorter is not None and shorter < base_horizon_s:
                retry = self._with_budget(self.source_definition, shorter)
                retry_coverage = self.coverage_check(retry, self.project_root)
                if retry_coverage.get("ok"):
                    self.search_horizon_s = shorter
                    planned_search = retry
                    self.coverage = dict(
                        retry_coverage,
                        adjusted_from_s=base_horizon_s,
                        adjust_reason="source coverage is shorter than the input budget",
                    )
        self.search_budget = select_budget(
            self.source_definition,
            self.project_root,
            budget_s=self.search_horizon_s,
        )
        self.search_budget = replace(self.search_budget, coverage=self.coverage)
        if not self.coverage.get("ok"):
            reason = "inadequate_environmental_time_coverage_for_search_horizon"
            self.pending_reasons.append(reason)
            return self._pending(
                reason,
                materialize=False,
                details={
                    "input_budget_s": base_horizon_s,
                    "search_horizon_s": self.search_horizon_s,
                    "coverage": self.coverage,
                },
            )
        try:
            self._ensure_navigation()
        except Exception as error:
            reason = f"navigation_unavailable: {error}"
            self.pending_reasons.append(reason)
            return self._pending(reason, materialize=False)
        initial = self.source_definition["initial"]
        if not self._guard.allowed(initial["lon"], initial["lat"]):
            reason = "navigation_initial_state_unsafe"
            self.pending_reasons.append(reason)
            return self._pending(
                reason,
                materialize=False,
                details={
                    "initial": {
                        "lon": initial["lon"],
                        "lat": initial["lat"],
                    },
                    "area_bbox": self.source_definition["area_bbox"],
                    "clearance_nm": self._guard.clearance_nm,
                    "relocated": False,
                },
            )
        try:
            self._resolve_voyage()
        except VoyageUnavailable as error:
            reason = f"voyage_unavailable: {error.reason}: {error}"
            self.pending_reasons.append(reason)
            return self._pending(
                reason,
                materialize=False,
                details=dict(error.status),
            )
        except Exception as error:
            reason = f"voyage_plan_failed: {error}"
            self.pending_reasons.append(reason)
            return self._pending(reason, materialize=False)
        try:
            self.work_definition, self.work_gameplay, self.work_scenario_path = (
                self._materialize(planned_search, self.work, self.work / "scenario.json")
            )
        except Exception as error:
            self.pending_reasons.append(f"gameplay_build_failed: {error}")
            return self._pending(f"gameplay_build_failed: {error}", materialize=False)
        self.work_evidence = evidence_fingerprint(
            self.work_definition,
            self.project_root,
            gameplay=self.work_gameplay,
            gameplay_path=self.work / "gameplay.json",
            navigation=self.navigation_metadata,
        )
        saved_evidence = (state or {}).get("work_evidence_fingerprint")
        if saved_evidence and saved_evidence != self.work_evidence["fingerprint"]:
            raise ValueError(
                "Resume rejected: algorithm, vessel, environment or configuration changed"
            )
        try:
            self._ensure_simulator()
        except Exception as error:
            reason = f"simulator_unavailable: {error}"
            self.pending_reasons.append(reason)
            return self._pending(reason, materialize=True)
        if state is None or not self.candidates:
            try:
                self._search_candidates()
            except DependencyUnavailable as error:
                self.pending_reasons.append(str(error))
                return self._pending(str(error), materialize=True)
            except Exception as error:
                self.pending_reasons.append(f"search_failed: {error}")
                return self._pending(f"search_failed: {error}", materialize=True)
            if not self.candidates:
                self.pending_reasons.append("search_exhausted_no_candidates")
                return self._pending("search_exhausted_no_candidates", materialize=True)
        self._persist_state(status="running")
        if mode == "ai":
            return self._run_ai(resume=resume)
        return self._run_local(resume=resume)

    def execute_trial(self, candidate: Candidate) -> TrialResult:
        """Run one real full session, or return a restart-safe cached result."""
        if candidate.key in self._trial_by_key:
            return self._trial_by_key[candidate.key]
        key = stable_digest(
            {
                "evidence": self.work_evidence.get("fingerprint"),
                "orders": [dict(order) for order in candidate.orders],
            }
        )
        cached = self.cache.get(key)
        if cached is not None:
            record = TrialResult.from_dict(cached)
            record.cached = True
            return self._register(record)
        if self._trial_runs >= self.max_trials:
            raise TrialLimit("full-session trial budget is exhausted")
        if self.simulator is None:
            raise DependencyUnavailable(
                "agent.simulator.SimulatorService is unavailable"
            )
        try:
            result = self._run_plan(
                str(self.work_scenario_path),
                [dict(order) for order in candidate.orders],
            )
        except Exception as error:
            raise ToolError(f"simulator trial failed: {error}") from None
        self._trial_runs += 1
        record = self._trial_from_result(
            result,
            candidate,
            fingerprint=self.work_evidence["fingerprint"],
            budget_s=self.search_horizon_s,
        )
        self.cache.put(key, record.as_dict())
        record = self._register(record)
        self._persist_state(status="running")
        return record

    def submit_revision(
        self, base_index: int, orders: list[dict], rationale: str
    ) -> Candidate:
        if not 0 <= base_index < len(self.candidates):
            raise ToolError("Unknown base candidate")
        base = self.candidates[base_index]
        depth = self._revision_depth.get(base.key, 0) + 1
        if depth > self.max_revisions:
            raise ToolError("Revision depth budget is exhausted")
        normalized = normalize_orders(orders, self.search_horizon_s)
        name = f"ai-revision-{len(self.candidates)}"
        candidate = self._add_candidate(
            Candidate(
                index=-1,
                name=name,
                solver="builder.ai_revision",
                orders=normalized,
                provenance=f"model_revision:{rationale[:160]}",
                revision_of=base.name,
            ),
            depth=depth,
        )
        # A model revision must survive a process restart even before its trial
        # starts. The SQLite graph checkpoint alone does not hold Builder state.
        if self.out.is_dir():
            self._persist_state(status="running")
        return candidate

    def _ensure_simulator(self) -> None:
        if self.simulator is not None:
            return
        self.simulator = SimulatorService(
            self.out / "trials", project_root=self.project_root
        )

    # ------------------------------- planning -------------------------------
    def _with_budget(self, definition: dict, budget_s: int) -> dict:
        result = copy.deepcopy(definition)
        start = utc(result["start"])
        result["mission"] = dict(result["mission"], budget_s=int(budget_s))
        result["end"] = iso_z(start + timedelta(seconds=int(budget_s)))
        return result

    def _materialize(
        self, definition: dict, directory: Path, scenario_path: Path
    ) -> tuple[dict, dict, Path]:
        directory.mkdir(parents=True, exist_ok=True)
        result = copy.deepcopy(definition)
        result.pop("gameplay", None)
        if self.voyage is not None:
            result["voyage"] = copy.deepcopy(self.voyage)
        built = self.gameplay_builder(Scenario(result, self.project_root))
        self._check_gameplay(built, result)
        gameplay_path = directory / "gameplay.json"
        write_json(gameplay_path, built)
        result["gameplay"] = {
            "path": relative_path(gameplay_path, self.project_root),
            "sha256": digest(gameplay_path),
        }
        write_json(scenario_path, result)
        return result, built, scenario_path

    def _check_gameplay(self, gameplay: dict, definition: dict) -> None:
        if gameplay.get("version") != 1:
            raise ValueError("Gameplay artifact has an unsupported version")
        budget = int(definition["mission"]["budget_s"])
        regions = gameplay.get("regions") or []
        if not regions or regions[0].get("time_s") != 0:
            raise ValueError("Gameplay region schedule must start at zero")
        last_region = int(regions[-1].get("time_s", -1))
        if last_region > budget or budget - last_region > 60:
            raise ValueError("Gameplay region schedule does not cover the mission budget")
        reports = gameplay.get("reports") or []
        if reports and max(int(r["valid_to_s"]) for r in reports) != budget:
            raise ValueError("Gameplay report schedule does not cover the mission budget")
        source = definition["environment"]["waves"]["sha256"]
        if gameplay.get("source_sha256") not in (None, source):
            raise ValueError("Gameplay source checksum does not match the scenario")

    # -------------------------------- search --------------------------------
    def _search_candidates(self) -> None:
        scenario = Scenario(self.work_definition, self.project_root)
        service = self.route_search
        if service is None:
            service = RouteSearch(
                scenario, self.work_gameplay, coast_guard=self._guard
            )
        elif callable(service) and not hasattr(service, "search"):
            service = service(scenario, self.work_gameplay)
        for method in SEARCH_METHODS:
            entry = {"method": method, "returned": 0, "accepted": 0, "error": None}
            try:
                raw = service.search(method, max_candidates=self.max_candidates)
                items = raw.get("candidates", []) if isinstance(raw, dict) else raw or []
                if not isinstance(items, list):
                    raise ValueError("Route search did not return a candidate list")
                entry["returned"] = len(items)
                for index, item in enumerate(items):
                    if not isinstance(item, dict):
                        continue
                    try:
                        orders = normalize_orders(
                            item.get("orders", []), self.search_horizon_s
                        )
                    except ValueError as error:
                        entry.setdefault("rejected", []).append(str(error))
                        continue
                    candidate = Candidate(
                        index=-1,
                        name=str(item.get("name") or f"{method}-{index}"),
                        solver=str(item.get("solver") or method),
                        orders=orders,
                        estimated_s=(
                            _number(item["estimated_s"])
                            if item.get("estimated_s") is not None
                            else None
                        ),
                        provenance=f"route_search:{method}",
                    )
                    self._add_candidate(candidate, depth=0)
                    entry["accepted"] += 1
            except Exception as error:
                entry["error"] = str(error)
                self.search_metadata["errors"].append(f"{method}: {error}")
            self.search_metadata["methods"][method] = entry
        if self.expert_limit:
            for candidate in self._expert_candidates():
                self._add_candidate(candidate, depth=0)
        self.search_metadata["candidates"] = len(self.candidates)

    def _expert_candidates(self) -> list[Candidate]:
        result = []
        initial = self.source_definition["initial"]
        target = self.source_definition["mission"]["target_lon_lat"]
        bearing = self._bearing(initial["lon"], initial["lat"], target[0], target[1])
        direct = (
            {
                "t": 0.0,
                "heading_deg": round(bearing, 3),
                "speed_knots": 9.0,
            },
        )
        result.append(
            Candidate(
                index=-1,
                name="expert-direct",
                solver="builder.expert:direct_bearing",
                orders=direct,
                provenance="expert_proposal",
            )
        )
        source = min(
            (c for c in self.candidates if c.orders),
            key=lambda c: c.estimated_s if c.estimated_s is not None else math.inf,
            default=None,
        )
        if source is not None:
            for suffix, delta, speed_delta in (
                ("port", 25.0, 0.0),
                ("starboard", -25.0, 0.0),
                ("faster", 0.0, 1.5),
            ):
                if len(result) >= self.expert_limit:
                    break
                orders = [
                    {
                        "t": order["t"],
                        "heading_deg": (order["heading_deg"] + delta) % 360,
                        "speed_knots": max(
                            0.0, min(15.0, order["speed_knots"] + speed_delta)
                        ),
                    }
                    for order in source.orders
                ]
                result.append(
                    Candidate(
                        index=-1,
                        name=f"expert-{suffix}",
                        solver=f"builder.expert:{suffix}",
                        orders=normalize_orders(orders, self.search_horizon_s),
                        provenance="expert_proposal",
                    )
                )
        return result[: self.expert_limit]

    @staticmethod
    def _bearing(lon: float, lat: float, target_lon: float, target_lat: float) -> float:
        east = math.radians(target_lon - lon) * math.cos(math.radians(lat))
        north = math.radians(target_lat - lat)
        return math.degrees(math.atan2(east, north)) % 360

    def _add_candidate(self, candidate: Candidate, *, depth: int) -> Candidate:
        if candidate.key in self._candidate_keys:
            return next(c for c in self.candidates if c.key == candidate.key)
        candidate = Candidate(
            index=len(self.candidates),
            name=candidate.name,
            solver=candidate.solver,
            orders=candidate.orders,
            estimated_s=candidate.estimated_s,
            provenance=candidate.provenance,
            revision_of=candidate.revision_of,
        )
        self.candidates.append(candidate)
        self._candidate_keys.add(candidate.key)
        self._revision_depth[candidate.key] = depth
        return candidate

    # ------------------------------- local loop -------------------------------
    def _run_local(self, *, resume: bool = False) -> dict:
        queue = list(self.candidates)
        scheduled = {candidate.key for candidate in queue}
        while queue and self._trial_runs < self.max_trials:
            candidate = queue.pop(0)
            try:
                record = self.execute_trial(candidate)
            except TrialLimit:
                self.pending_reasons.append("trial_budget_exhausted")
                break
            except ToolError as error:
                self.pending_reasons.append(str(error))
                return self._pending(
                    str(error), materialize=True, terminal=False
                )
            if not record.qualified and self.max_revisions:
                added = False
                for revision in self._revision_candidates(record):
                    if revision.key not in scheduled:
                        scheduled.add(revision.key)
                        queue.append(revision)
                        added = True
                if added and self.out.is_dir():
                    # Persist the revised candidate before its costly trial so a
                    # restart cannot lose the bounded revision.
                    self._persist_state(status="running")
        qualified = [record for record in self.trial_records if record.qualified]
        if not qualified:
            if self._trial_runs >= self.max_trials and queue:
                reason = "trial_budget_exhausted_no_qualified_route"
            else:
                reason = "search_exhausted_no_qualified_route"
            self.pending_reasons.append(reason)
            return self._pending(reason, materialize=True)
        if self.search_metadata["errors"]:
            reason = "required_route_search_failed"
            self.pending_reasons.append(reason)
            return self._pending(reason, materialize=True)
        winner = min(
            qualified,
            key=lambda r: (r.elapsed_s, r.lost_teu, r.candidate.name),
        )
        return self._finalize(winner)

    def _revision_candidates(self, record: TrialResult) -> list[Candidate]:
        depth = self._revision_depth.get(record.candidate.key, 0)
        if depth >= self.max_revisions:
            return []
        orders = [dict(order) for order in record.candidate.orders]
        target = self.source_definition["mission"]["target_lon_lat"]
        initial = self.source_definition["initial"]
        bearing = self._bearing(initial["lon"], initial["lat"], target[0], target[1])
        result = []
        if (
            record.outcome == "timeout"
            or "timeout" in record.reason
            or record.reason.startswith("escape_outside")
        ):
            revised = [
                {
                    "t": order["t"],
                    "heading_deg": bearing,
                    "speed_knots": min(15.0, max(order["speed_knots"], 9.0) + 1.5),
                }
                for order in orders
            ]
        elif "cargo" in record.reason or "loss" in record.reason:
            revised = [
                {
                    "t": order["t"],
                    "heading_deg": order["heading_deg"],
                    "speed_knots": max(2.0, order["speed_knots"] - 2.0),
                }
                for order in orders
            ]
        else:
            revised = [
                {
                    "t": order["t"],
                    "heading_deg": (order["heading_deg"] + 25.0) % 360,
                    "speed_knots": max(2.0, order["speed_knots"] - 1.0),
                }
                for order in orders
            ]
        try:
            result.append(
                self._add_candidate(
                    Candidate(
                        index=-1,
                        name=f"{record.candidate.name}-rev{depth + 1}",
                        solver="builder.local_revision",
                        orders=normalize_orders(revised, self.search_horizon_s),
                        provenance=f"local_revision:{record.reason}",
                        revision_of=record.candidate.name,
                    ),
                    depth=depth + 1,
                )
            )
        except ValueError:
            pass
        return result

    # --------------------------------- AI loop ---------------------------------
    def _run_ai(self, *, resume: bool = False) -> dict:
        try:
            profile, provider = load_builder_profile(self.models_path, "builder")
            self.model_name = profile.get("model")
        except Exception as error:
            self.pending_reasons.append(f"builder_model_config_failed: {error}")
            return self._pending(f"builder_model_config_failed: {error}", materialize=True)
        transport = self.transport
        owned_transport = False
        try:
            if transport is None:
                from game.director.models import ChatClient

                transport = ChatClient(profile, provider, self.keys_path)
                owned_transport = True
            registry = ToolRegistry.from_specs(
                load_tool_specs(HARNESS_DIR / "tools.json"), self._ai_handlers()
            )
            thread_id = self.thread_id or (
                self._read_run().get("thread_id") if resume else None
            )
            harness = AgentHarness(
                role="builder",
                instructions=instructions_from(HARNESS_DIR),
                registry=registry,
                transport=transport,
                profile=profile,
                checkpoint_path=self.out / "builder.sqlite",
                thread_id=thread_id,
                max_rounds=min(64, max(self.max_trials + 6, 8)),
                max_tool_calls=min(64, max(self.max_trials + 6, 8)),
                max_failures=3,
                audit_path=self.out / "audit.jsonl",
            )
        except Exception as error:
            if owned_transport:
                close = getattr(transport, "close", None)
                if close:
                    try:
                        close()
                    except Exception:
                        pass
            reason = f"builder_harness_setup_failed: {error}"
            self.pending_reasons.append(reason)
            return self._pending(reason, materialize=True)
        self.thread_id = harness.thread_id
        self._write_run(
            {
                "status": "running",
                "mode": "ai",
                "thread_id": harness.thread_id,
                "instruction_digest": instruction_digest(HARNESS_DIR),
            }
        )
        self._persist_state(status="running")
        try:
            result = harness.run(self._ai_context(), resume=resume)
        except HarnessPending as error:
            self.pending_reasons.append(f"agent_pending: {error.reason}")
            return self._pending(
                f"agent_pending: {error.reason}",
                materialize=True,
                terminal=False,
            )
        except Exception as error:
            self.pending_reasons.append(f"agent_failure: {error}")
            return self._pending(
                f"agent_failure: {error}",
                materialize=True,
                terminal=False,
            )
        finally:
            harness.close()
        value = result.get("result") or {}
        if value.get("status") not in {"review_ready", "pending"}:
            return self._pending(
                "agent_did_not_finalize", materialize=True, terminal=False
            )
        return value

    def _ai_handlers(self) -> dict:
        def record_plan(args):
            return {"plan": args["steps"], "status": "recorded"}

        def plan_voyage(args):
            if args:
                raise ToolError(
                    "plan_voyage takes no arguments; the fixed initial and "
                    "voyage inputs are not editable"
                )
            try:
                self._resolve_voyage()
            except VoyageUnavailable as error:
                return {
                    "status": "pending",
                    "reason": error.reason,
                    "details": dict(error.status),
                }
            except Exception as error:
                return {"status": "pending", "reason": str(error)}
            initial = self.source_definition["initial"]
            return {
                "status": "ok",
                "source": self.voyage_source,
                "fixed_initial": {
                    "lon": initial["lon"],
                    "lat": initial["lat"],
                    "heading_deg": initial["heading_deg"],
                    "speed_mps": initial.get("speed_mps"),
                    "start": self.source_definition["start"],
                },
                "voyage": self._voyage_summary(),
                "navigation": {
                    "dataset": (self.navigation_metadata or {}).get("dataset"),
                    "version": (self.navigation_metadata or {}).get("version"),
                    "checksum": (self.navigation_metadata or {}).get("checksum"),
                    "clearance_nm": (self.navigation_metadata or {}).get(
                        "clearance_nm"
                    ),
                    "override_allowed": False,
                },
                "limits": {
                    "initial_is_fixed": True,
                    "route_arguments_accepted": False,
                },
            }

        def list_candidates(args):
            return {
                "candidates": [
                    {
                        "index": c.index,
                        "name": c.name,
                        "solver": c.solver,
                        "orders": [dict(o) for o in c.orders],
                        "estimated_s": c.estimated_s,
                        "provenance": c.provenance,
                    }
                    for c in self.candidates
                ],
                "trial_records": len(self.trial_records),
                "trial_budget": self.max_trials,
                "qualified_routes": sum(record.qualified for record in self.trial_records),
                "minimum_solutions": self.min_solutions,
            }

        def run_trial(args):
            index = int(args["candidate_index"])
            if not 0 <= index < len(self.candidates):
                raise ToolError("Unknown candidate index")
            try:
                record = self.execute_trial(self.candidates[index])
            except TrialLimit as error:
                return {"status": "trial_limit", "error": str(error)}
            except (DependencyUnavailable, ToolError) as error:
                raise ToolError(str(error)) from None
            return self._trial_observation(record)

        def submit_revision(args):
            candidate = self.submit_revision(
                int(args["base_index"]), args["orders"], str(args["rationale"])
            )
            return {"status": "added", "candidate": candidate.as_dict()}

        def inspect_trial(args):
            index = int(args["trial_index"])
            if not 0 <= index < len(self.trial_records):
                raise ToolError("Unknown trial index")
            return self._trial_observation(self.trial_records[index])

        def finalize_build(args):
            return self._finalize_best()

        return {
            "record_plan": record_plan,
            "plan_voyage": plan_voyage,
            "list_candidates": list_candidates,
            "run_trial": run_trial,
            "submit_revision": submit_revision,
            "inspect_trial": inspect_trial,
            "finalize_build": finalize_build,
        }

    def _ai_context(self) -> dict:
        return {
            "task": "find multiple distinct qualified routes, then finalize all verified solutions",
            "scenario": {
                "id": self.source_definition.get("id"),
                "start": self.source_definition["start"],
                "source_budget_s": self.source_definition["mission"]["budget_s"],
                "search_horizon_s": self.search_horizon_s,
                "search_end": self.search_budget.end if self.search_budget else None,
                "seed": self.source_definition.get("seed"),
                "initial": self.source_definition["initial"],
                "target_lon_lat": self.source_definition["mission"]["target_lon_lat"],
            },
            "qualification": {
                "escape_outcomes": ["escaped"],
                "ship_lost": False,
                "max_loss_fraction": LOSS_LIMIT,
            },
            "budget": {
                "search_horizon_s": self.search_horizon_s,
                "search_budget": self.search_budget.as_dict()
                if self.search_budget
                else None,
                "final_budget_s": self.budget.budget_s if self.budget else None,
            },
            "coverage": self.coverage,
            "voyage": self._voyage_summary(),
            "navigation": {
                "dataset": (self.navigation_metadata or {}).get("dataset"),
                "version": (self.navigation_metadata or {}).get("version"),
                "checksum": (self.navigation_metadata or {}).get("checksum"),
                "clearance_nm": (self.navigation_metadata or {}).get("clearance_nm"),
                "override_allowed": False,
            },
            "search": self.search_metadata,
            "candidates": [
                {
                    "index": c.index,
                    "name": c.name,
                    "solver": c.solver,
                    "orders": [dict(o) for o in c.orders],
                    "estimated_s": c.estimated_s,
                }
                for c in self.candidates
            ],
            "limits": {
                "max_trials": self.max_trials,
                "min_solutions": self.min_solutions,
                "max_orders": MAX_ORDERS,
                "max_speed_knots": 15,
                "max_revisions": self.max_revisions,
            },
            "note": "Only tool observations are facts. Final local checks decide status.",
        }

    def _trial_observation(self, record: TrialResult) -> dict:
        view = record.view or {}
        vessel = view.get("vessel") or {}
        return {
            "trial_index": record.index,
            "trial_id": record.trial_id,
            "candidate": record.candidate.as_dict(),
            "outcome": record.outcome,
            "elapsed_s": record.elapsed_s,
            "lost_teu": record.lost_teu,
            "total_teu": record.total_teu,
            "loss_fraction": record.loss_fraction,
            "ship_lost": record.ship_lost,
            "qualified": record.qualified,
            "reason": record.reason,
            "cached": record.cached,
            "final_validation": record.final_validation,
            "observed": {
                "position": [vessel.get("lon"), vessel.get("lat")],
                "remaining_s": view.get("remaining_s"),
                "stopping_reason": view.get("stopping_reason"),
                "events": (view.get("events") or [])[-5:],
            },
        }

    def _finalize_best(self) -> dict:
        qualified = [r for r in self.trial_records if r.qualified]
        if self.search_metadata["errors"]:
            reason = "required_route_search_failed"
            self.pending_reasons.append(reason)
            return self._pending(reason, materialize=True)
        if not qualified:
            reason = "search_exhausted_no_qualified_route"
            self.pending_reasons.append(reason)
            return self._pending(reason, materialize=True)
        winner = min(
            qualified, key=lambda r: (r.elapsed_s, r.lost_teu, r.candidate.name)
        )
        return self._finalize(winner)

    # -------------------------------- records --------------------------------
    def _run_plan(self, path: str, orders: list[dict]) -> dict:
        result = self.simulator.run_plan(path, orders)
        trial_id = result.get("trial_id") if isinstance(result, dict) else None
        if trial_id and hasattr(self.simulator, "close_trial"):
            try:
                self.simulator.close_trial(trial_id)
            except Exception:
                pass
        return result

    def _trial_from_result(
        self,
        result: dict,
        candidate: Candidate,
        *,
        fingerprint: str,
        budget_s: int | None = None,
        final_validation: bool = False,
    ) -> TrialResult:
        budget_s = self.search_horizon_s if budget_s is None else int(budget_s)
        result = result if isinstance(result, dict) else {}
        view = result.get("view") if isinstance(result.get("view"), dict) else {}
        cargo = view.get("cargo") if isinstance(view.get("cargo"), dict) else {}
        errors: list[str] = []

        outcome = result.get("outcome") if "outcome" in result else None
        if not isinstance(outcome, str) or not outcome:
            errors.append("outcome")

        lost = _strict_count(result.get("lost_teu")) if "lost_teu" in result else None
        if lost is None:
            errors.append("lost_teu")

        total = _strict_count(result.get("total_teu")) if "total_teu" in result else None
        if total is None or total <= 0:
            errors.append("total_teu")
        if lost is not None and total is not None and total > 0 and lost > total:
            errors.append("lost_teu_above_total")

        ship_lost = result.get("ship_lost") if "ship_lost" in result else None
        if not isinstance(ship_lost, bool):
            errors.append("ship_lost")

        elapsed_raw = result.get("elapsed_s") if "elapsed_s" in result else None
        elapsed = (
            float(elapsed_raw)
            if isinstance(elapsed_raw, (int, float))
            and not isinstance(elapsed_raw, bool)
            and math.isfinite(float(elapsed_raw))
            and float(elapsed_raw) >= 0
            else None
        )
        if elapsed is None:
            errors.append("elapsed_s")

        normalized = None
        raw_orders = result.get("orders")
        if not isinstance(raw_orders, list) or not raw_orders:
            errors.append("orders")
        else:
            try:
                normalized = normalize_orders(
                    [dict(order) for order in raw_orders], budget_s
                )
            except (TypeError, ValueError):
                errors.append("orders")
            else:
                if normalized != candidate.orders:
                    errors.append("orders_do_not_match_candidate")

        errors.extend(
            _view_metric_errors(
                view,
                outcome=outcome if isinstance(outcome, str) else None,
                elapsed_s=elapsed,
                lost_teu=lost,
                total_teu=total,
                ship_lost=ship_lost if isinstance(ship_lost, bool) else None,
            )
        )
        metrics_valid = not errors
        lost_value = lost if lost is not None else 0
        total_value = total if total is not None else 0
        elapsed_value = elapsed if elapsed is not None else 0.0
        ship_value = ship_lost if isinstance(ship_lost, bool) else True
        within_budget = (
            metrics_valid and elapsed_value <= float(budget_s) + 1e-6
        )
        qualified = is_qualified(
            outcome,
            ship_lost=ship_value,
            lost_teu=lost_value,
            total_teu=total_value,
            metrics_valid=metrics_valid,
        ) and within_budget
        if qualified:
            reason = "qualified"
        elif not metrics_valid:
            reason = "invalid_result_metrics: " + ", ".join(errors)
        elif not within_budget:
            reason = "escape_outside_final_budget"
        elif outcome != "escaped":
            reason = f"outcome:{outcome}"
        elif ship_value:
            reason = "ship_lost"
        elif total_value <= 0 or lost_value > total_value:
            reason = "invalid_result_metrics: cargo"
        else:
            reason = "cargo_loss_above_1_percent"
        return TrialResult(
            candidate=candidate,
            trial_id=result.get("trial_id"),
            outcome=outcome if isinstance(outcome, str) else None,
            elapsed_s=elapsed_value,
            lost_teu=lost_value,
            total_teu=total_value,
            ship_lost=ship_value,
            session=str(result["session"]) if result.get("session") is not None else None,
            view=view,
            qualified=qualified,
            reason=reason,
            fingerprint=fingerprint,
            orders=normalized if normalized is not None else candidate.orders,
            grade=_integer(cargo.get("grade")),
            final_validation=final_validation,
            metrics_valid=metrics_valid,
        )

    def _register(self, record: TrialResult) -> TrialResult:
        if record.candidate.key in self._trial_by_key:
            return self._trial_by_key[record.candidate.key]
        record.index = len(self.trial_records)
        self.trial_records.append(record)
        self._trial_by_key[record.candidate.key] = record
        return record

    # ------------------------------- packaging -------------------------------
    def _finalize(self, winner: TrialResult) -> dict:
        if not winner.qualified:
            return self._pending("winner_is_not_qualified", materialize=True)
        try:
            final_budget = planned_budget(winner.elapsed_s)
        except ValueError as error:
            return self._pending(f"invalid_winner_elapsed: {error}", materialize=True)
        final_plan = select_budget(
            self.source_definition, self.project_root, budget_s=final_budget
        )
        planned_final = self._with_budget(self.source_definition, final_budget)
        self.final_coverage = self.coverage_check(planned_final, self.project_root)
        self.budget = replace(final_plan, coverage=self.final_coverage)
        if not self.final_coverage.get("ok"):
            reason = "inadequate_environmental_time_coverage_for_final_budget"
            self.pending_reasons.append(reason)
            return self._pending(
                reason,
                materialize=True,
                details={
                    "final_budget_s": final_budget,
                    "winner_elapsed_s": winner.elapsed_s,
                    "final_coverage": self.final_coverage,
                },
            )
        trimmed = [
            dict(order)
            for order in winner.orders
            if float(order["t"]) <= final_budget + 1e-9
        ]
        if not trimmed:
            return self._pending(
                "winner_orders_outside_final_budget", materialize=True
            )
        final_candidate = Candidate(
            index=winner.candidate.index,
            name=winner.candidate.name,
            solver=winner.candidate.solver,
            orders=tuple(trimmed),
            estimated_s=winner.candidate.estimated_s,
            provenance=winner.candidate.provenance,
            revision_of=winner.candidate.revision_of,
        )
        final_definition, final_gameplay, final_path = self._materialize(
            planned_final, self.package, self.package / "scenario.json"
        )
        final_evidence = evidence_fingerprint(
            final_definition,
            self.project_root,
            gameplay=final_gameplay,
            gameplay_path=self.package / "gameplay.json",
            navigation=self.navigation_metadata,
        )
        final_record = self._final_record
        if final_record is not None and (
            final_record.fingerprint != final_evidence["fingerprint"]
            or final_record.orders != tuple(trimmed)
        ):
            # Evidence from another algorithm, package, route or source revision
            # is not valid for this package. Re-run the final Session instead.
            final_record = None
        final_error = None
        if final_record is None:
            try:
                result = self._run_plan(
                    str(final_path), [dict(order) for order in trimmed]
                )
                final_record = self._trial_from_result(
                    result,
                    final_candidate,
                    fingerprint=final_evidence["fingerprint"],
                    budget_s=final_budget,
                    final_validation=True,
                )
            except Exception as error:
                final_error = f"final_revalidation_failed: {error}"
                self.pending_reasons.append(final_error)
            self._final_record = final_record
            self._persist_state(status="validating_final")
        unchanged, changed = self._inputs_unchanged()
        if not unchanged:
            self.pending_reasons.append(
                "frozen_input_modified: " + ", ".join(changed)
            )
        passed = bool(
            final_record is not None
            and final_record.qualified
            and unchanged
            and not self.search_metadata["errors"]
        )
        if final_record is not None and not final_record.qualified:
            self.pending_reasons.append(
                f"final_revalidation_not_qualified: {final_record.reason}"
            )
        records = (
            self._revalidate_solutions(final_record, final_path, final_evidence)
            if passed else []
        )
        solutions = build_solutions(
            records,
            definition=final_definition,
            evidence=final_evidence,
            min_solutions=self.min_solutions,
            project_root=self.project_root,
            reason=final_error if not passed else None,
        )
        if passed and solutions["status"] != "ready":
            self.pending_reasons.append("insufficient_qualified_solutions")
            passed = False
        status = "review_ready" if passed else "pending"
        validation = self._validation(
            final_definition,
            final_path,
            final_gameplay,
            final_evidence,
            winner if passed else None,
            final_record,
            status,
        )
        write_json(self.package / "validation.json", validation)
        write_json(self.package / "solutions.json", solutions)
        self._write_briefing()
        self._package_weather_status = self._package_weather()
        report = self._build_report(
            status,
            final_definition,
            final_evidence,
            winner,
            final_record,
            solutions,
            validation,
        )
        write_json(self.package / "build-report.json", report)
        result = {
            "status": status,
            "package": str(self.package),
            "scenario": final_definition.get("id"),
            "reference": winner.candidate.name if passed else None,
            "budget_s": final_budget,
            "elapsed_s": final_record.elapsed_s if final_record else None,
            "lost_teu": final_record.lost_teu if final_record else None,
            "final_revalidated": final_record is not None,
            "solution_count": solutions["solution_count"],
            "min_solutions": self.min_solutions,
            "voyage": self._voyage_summary(),
            "navigation_clearance_nm": (self.navigation_metadata or {}).get(
                "clearance_nm"
            ),
            "pending_reasons": list(self.pending_reasons),
        }
        self._result = result
        self._persist_state(status=status, terminal=True, result=result)
        self._write_run(
            {
                "status": status,
                "mode": self.mode,
                "thread_id": self.thread_id,
                "package": str(self.package),
            }
        )
        return result

    def _revalidate_solutions(
        self, reference: TrialResult, scenario_path: Path, evidence: dict
    ) -> list[TrialResult]:
        """Retain every distinct qualifying route against the final package."""
        budget_s = self.budget.budget_s
        signature = lambda orders: stable_digest([dict(order) for order in orders])
        applied = lambda record: tuple(
            dict(order) for order in record.orders
            if float(order["t"]) < min(record.elapsed_s, budget_s) - 1e-9
        )
        reference_key = signature(applied(reference))
        self._solution_records[reference_key] = reference
        seen = {reference_key}
        for source in sorted(
            (record for record in self.trial_records if record.qualified),
            key=lambda record: (record.elapsed_s, record.lost_teu, record.candidate.name),
        ):
            orders = applied(source)
            key = signature(orders)
            if not orders or key in seen:
                continue
            seen.add(key)
            cached = self._solution_records.get(key)
            if cached is not None and cached.fingerprint == evidence["fingerprint"]:
                continue
            if key in self._solution_attempts:
                continue
            candidate = replace(source.candidate, orders=orders)
            try:
                result = self._run_plan(str(scenario_path), list(orders))
                record = self._trial_from_result(
                    result, candidate, fingerprint=evidence["fingerprint"],
                    budget_s=budget_s, final_validation=True,
                )
                self._solution_records[key] = record
                self._solution_attempts[key] = {
                    "candidate": candidate.name, "qualified": record.qualified,
                    "reason": record.reason,
                }
            except Exception as error:
                self._solution_attempts[key] = {
                    "candidate": candidate.name, "qualified": False,
                    "reason": str(error),
                }
            self._persist_state(status="validating_solutions")
        return list(self._solution_records.values())

    def _pending(
        self,
        reason: str,
        *,
        materialize: bool,
        details: dict | None = None,
        terminal: bool = True,
    ) -> dict:
        if reason not in self.pending_reasons:
            self.pending_reasons.append(reason)
        definition = self.work_definition
        gameplay = self.work_gameplay
        scenario_path = self.work_scenario_path
        evidence = self.work_evidence
        if materialize and definition is not None:
            try:
                definition, gameplay, scenario_path = self._materialize(
                    definition, self.package, self.package / "scenario.json"
                )
                evidence = evidence_fingerprint(
                    definition,
                    self.project_root,
                    gameplay=gameplay,
                    gameplay_path=self.package / "gameplay.json",
                    navigation=self.navigation_metadata,
                )
            except Exception as error:
                self.pending_reasons.append(f"package_materialization_failed: {error}")
        status = "pending"
        validation = self._validation(
            definition or self.source_definition,
            scenario_path or self.scenario_path,
            gameplay,
            evidence,
            None,
            None,
            status,
            extra={"pending_reasons": list(self.pending_reasons), "details": details or {}},
        )
        write_json(self.package / "validation.json", validation)
        solutions = build_solutions(
            [], definition=definition or self.source_definition,
            evidence=evidence, min_solutions=self.min_solutions,
            project_root=self.project_root, reason=reason,
        )
        write_json(self.package / "solutions.json", solutions)
        self._write_briefing()
        self._package_weather_status = self._package_weather()
        report = self._build_report(
            status, definition, evidence, None, None, solutions, validation
        )
        write_json(self.package / "build-report.json", report)
        budget = self.budget or self.search_budget
        result = {
            "status": status,
            "package": str(self.package),
            "scenario": (definition or {}).get("id"),
            "reference": None,
            "solution_count": solutions["solution_count"],
            "min_solutions": self.min_solutions,
            "budget_s": budget.budget_s if budget else None,
            "voyage": self._voyage_summary(),
            "navigation_clearance_nm": (self.navigation_metadata or {}).get(
                "clearance_nm"
            ),
            "pending_reasons": list(self.pending_reasons),
        }
        self._result = result if terminal else None
        self._persist_state(
            status=status, terminal=terminal, result=result if terminal else None
        )
        self._write_run(
            {
                "status": status,
                "mode": self.mode,
                "thread_id": self.thread_id,
                "package": str(self.package),
            }
        )
        return result

    def _validation(
        self,
        definition: dict,
        scenario_path: Path,
        gameplay: dict | None,
        evidence: dict,
        winner: TrialResult | None,
        final_record: TrialResult | None,
        status: str,
        *,
        extra: dict | None = None,
    ) -> dict:
        strategies = []
        for record in self.trial_records:
            strategies.append(self._strategy(record))
        finals = list(self._solution_records.values())
        if final_record is not None and final_record not in finals:
            finals.append(final_record)
        strategies.extend(self._strategy(record) for record in finals)
        unchanged, changed = self._inputs_unchanged()
        metadata = {
            "builder": {
                "version": 1,
                "mode": self.mode,
                "status": status,
                "approval": "none",
                "human_review_required": True,
                "published": False,
                "input_frozen": unchanged,
                "changed_inputs": changed,
                "pending_reasons": list(self.pending_reasons),
                "qualification_rule": QUALIFICATION_RULE,
                "min_solutions": self.min_solutions,
                "search": self.search_metadata,
                "search_horizon_s": self.search_horizon_s,
                "budget": self.budget.as_dict() if self.budget else None,
                "search_budget": self.search_budget.as_dict() if self.search_budget else None,
                "coverage": self.coverage,
                "final_coverage": self.final_coverage,
                "voyage": self._voyage_summary(),
                "navigation": copy.deepcopy(self.navigation_metadata),
                "evidence_fingerprint": evidence,
                "model": self.model_name if self.mode == "ai" else None,
                "warnings": [
                    "The Builder never grants approval.",
                    "review_ready is a review label, not publication.",
                    "No impossible-mission claim is made.",
                ],
            }
        }
        if extra:
            metadata.update(extra)
        definition_sha = (
            digest(scenario_path) if Path(scenario_path).is_file() else ""
        )
        gameplay_sha = (
            digest(self.package / "gameplay.json")
            if (self.package / "gameplay.json").is_file()
            else (definition.get("gameplay") or {}).get("sha256", "")
        )
        return {
            "version": 1,
            "scenario": definition.get("id"),
            "definition_sha256": definition_sha,
            "gameplay_sha256": gameplay_sha,
            "rule": (
                "passed when a full session escapes, the ship survives, and at most "
                "1 % of the initial TEU is lost; review_ready is not publication"
            ),
            "passed": bool(status == "review_ready"),
            "reference": winner.candidate.name if winner is not None else None,
            "strategies": strategies,
            "status": status,
            "metadata": metadata,
        }

    def _strategy(self, record: TrialResult) -> dict:
        return {
            "name": record.candidate.name,
            "solver": record.candidate.solver,
            "orders": [dict(order) for order in record.orders],
            "outcome": record.outcome,
            "elapsed_s": round(record.elapsed_s, 1),
            "lost_teu": record.lost_teu,
            "total_teu": record.total_teu,
            "grade": record.grade,
            "ship_lost": record.ship_lost,
            "qualified": record.qualified,
            "metrics_valid": record.metrics_valid,
            "reason": record.reason,
            "session": record.session,
            "trial_id": record.trial_id,
            "evidence_fingerprint": record.fingerprint,
            "final_validation": record.final_validation,
        }

    def _build_report(
        self,
        status: str,
        definition: dict | None,
        evidence: dict,
        winner: TrialResult | None,
        final_record: TrialResult | None,
        solutions: dict,
        validation: dict,
    ) -> dict:
        unchanged, changed = self._inputs_unchanged()
        return {
            "version": 1,
            "status": status,
            "mode": self.mode,
            "model": self.model_name,
            "mode_label": (
                "local deterministic mode: no model or API credentials"
                if self.mode == "local"
                else "ai mode: builder model role; local tools remain authoritative"
            ),
            "input": {
                "path": str(self.scenario_path),
                "sha256": digest(self.scenario_path)
                if self.scenario_path.is_file()
                else None,
                "frozen": unchanged,
                "changed": changed,
            },
            "scenario": definition.get("id") if definition else None,
            "budget": self.budget.as_dict() if self.budget else None,
            "search_horizon_s": self.search_horizon_s,
            "search_budget": self.search_budget.as_dict() if self.search_budget else None,
            "coverage": self.coverage,
            "final_coverage": self.final_coverage,
            "voyage": self._voyage_summary(),
            "navigation": copy.deepcopy(self.navigation_metadata),
            "briefing": {
                "path": "briefing.md"
                if self.voyage is not None and (self.package / "briefing.md").is_file()
                else None,
                "labels": {
                    "authored_context": True,
                    "historical_track": False,
                    "harbour_safety_claimed": False,
                    "port_arrival_is_escape": False,
                },
            },
            "weather": self._package_weather_status,
            "search": self.search_metadata,
            "trials": [self._strategy(record) for record in self.trial_records],
            "winner": winner.candidate.as_dict() if winner is not None else None,
            "final_revalidation": final_record.as_dict() if final_record else None,
            "solutions": {
                "path": "solutions.json",
                "status": solutions["status"],
                "count": solutions["solution_count"],
                "required_minimum": self.min_solutions,
                "final_attempts": self._solution_attempts,
            },
            "validation": {
                "status": validation.get("status"),
                "passed": validation.get("passed"),
                "definition_sha256": validation.get("definition_sha256"),
                "gameplay_sha256": validation.get("gameplay_sha256"),
            },
            "evidence_fingerprint": evidence,
            "checks": {
                "input_frozen": unchanged,
                "both_search_methods_called": set(self.search_metadata["methods"])
                == set(SEARCH_METHODS),
                "search_errors": self.search_metadata["errors"],
                "coverage_ok": bool(self.coverage.get("ok")),
                "final_coverage_ok": bool(self.final_coverage.get("ok")),
                "final_budget_from_winner_elapsed": bool(
                    winner is not None
                    and self.budget is not None
                    and self.budget.budget_s == planned_budget(winner.elapsed_s)
                ),
                "search_horizon_s": self.search_horizon_s,
                "full_session_trials": len(self.trial_records),
                "qualified_trials": sum(r.qualified for r in self.trial_records),
                "final_revalidation": final_record is not None,
                "final_qualified": bool(final_record and final_record.qualified),
                "minimum_solutions_met": solutions["solution_count"] >= self.min_solutions,
                "checksums_refreshed": bool(
                    validation.get("definition_sha256")
                    and validation.get("gameplay_sha256")
                ),
            },
            "pending_reasons": list(self.pending_reasons),
            "limitations": [
                "Search is bounded. A qualified route is not a global optimum.",
                "The mission budget formula is 900*ceil(1.2*T/900), minimum 900 s.",
                "A pending result does not prove the mission impossible.",
                "The model does not approve or publish the package.",
                "Solutions are verified from the scenario initial state, not arbitrary current states.",
                "All discovered final-qualified solutions are stored; the search does not enumerate every possible route.",
                "Weather cloud/precipitation values are copied only when the source covers the final budget; otherwise the file is omitted.",
                "The search horizon is the designer input budget; the final budget uses the verified winner time.",
            ],
            "approval": "none; a human reviewer must accept or reject this package",
        }

    def _package_weather(self) -> dict:
        source = self.scenario_path.parent / "weather.json"
        target = self.package / "weather.json"
        budget = (self.budget or self.search_budget).budget_s if (
            self.budget or self.search_budget
        ) else None
        if not source.is_file():
            if target.exists():
                target.unlink()
            return {"status": "omitted_no_source"}
        value = json.loads(source.read_text())
        hourly = value.get("hourly")
        times = []
        if isinstance(hourly, list):
            for row in hourly:
                if not isinstance(row, dict):
                    continue
                number = _strict_count(row.get("t_s"))
                if number is not None:
                    times.append(number)
        if not times or budget is None:
            if target.exists():
                target.unlink()
            return {"status": "omitted_no_coverage", "source": str(source)}
        coverage_end_s = max(times)
        if float(budget) > coverage_end_s + 1e-9:
            if target.exists():
                target.unlink()
            return {
                "status": "omitted_beyond_coverage",
                "source": str(source),
                "source_last_valid_s": coverage_end_s,
                "builder_budget_s": budget,
                "reason": "Display weather does not cover the final budget; no value was invented.",
            }
        value["coverage_note"] = (
            "Copied from the frozen input package. This file is display weather, "
            "not the escape-region schedule. Gameplay reports are rebuilt from the "
            "environment for the final budget."
        )
        value["source_package"] = str(source)
        value["builder_budget_s"] = budget
        value["source_last_valid_s"] = coverage_end_s
        write_json(target, value)
        return {
            "status": "copied",
            "source": str(source),
            "source_last_valid_s": coverage_end_s,
            "builder_budget_s": budget,
        }

    def _read_run(self) -> dict:
        path = self.out / "run.json"
        return json.loads(path.read_text()) if path.is_file() else {}

    def _write_run(self, value: dict) -> None:
        path = self.out / "run.json"
        current = self._read_run()
        current.update(value)
        current.setdefault("version", 1)
        write_json(path, current)


__all__ = [
    "Builder",
    "FileTrialCache",
    "SEARCH_METHODS",
    "check_environment_coverage",
    "coverage_time_limit_s",
    "planned_budget",
    "select_budget",
    "validate_voyage",
]
