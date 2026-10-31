"""Typed, JSON-ready records shared by the TIDE agents.

The Builder keeps every external fact in one of these records.  Records never
contain API keys or transport headers.  Fingerprints cover the scenario,
environmental sources, vessel, gameplay and the algorithm implementation that
produced a result, so a changed model or physics file invalidates old evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ALGORITHM_FILES = (
    "game/simulation/runtime.py",
    "game/simulation/two_timescale.py",
    "game/simulation/environment.py",
    "game/simulation/dynamics.py",
    "game/simulation/actuators.py",
    "game/simulation/maneuvering.py",
    "game/simulation/turn_rate.py",
    "game/simulation/waves.py",
    "game/simulation/wind.py",
    "game/simulation/vessel.py",
    "game/simulation/container.py",
    "game/session.py",
    "game/cargo.py",
    "game/ship.py",
    "game/rules/mission.py",
    "pipeline/gameplay.py",
    "agent/routing.py",
    "agent/simulator.py",
    "agent/builder.py",
    "agent/harness.py",
    "agent/solutions.py",
    "agent/models.py",
    "agent/voyage.py",
    "agent/builder_harness/tools.json",
    "agent/builder_harness/INSTRUCTIONS.md",
    "game/navigation.py",
    "web/game/ports.json",
    "data/navigation/manifest.json",
)

ESCAPE_OUTCOMES = frozenset({"escaped"})
MAX_ORDERS = 32
MAX_SPEED_KNOTS = 15.0
LOSS_LIMIT = 0.01


class AgentError(RuntimeError):
    """Base error for agent-side failures."""


class DependencyUnavailable(AgentError):
    """A peer-owned service is not installed in this checkout."""


class TrialLimit(AgentError):
    """The finite full-session trial budget is exhausted."""


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_file(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def stable_digest(value: Any) -> str:
    return digest_bytes(canonical(value).encode())


def relative_path(path: Path, root: Path) -> str:
    path, root = Path(path), Path(root)
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


@dataclass(frozen=True)
class Candidate:
    """One route candidate proposed by search or by a bounded revision."""

    index: int
    name: str
    solver: str
    orders: tuple[dict, ...]
    estimated_s: float | None = None
    provenance: str = "route_search"
    revision_of: str | None = None

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "name": self.name,
            "solver": self.solver,
            "orders": [dict(order) for order in self.orders],
            "estimated_s": self.estimated_s,
            "provenance": self.provenance,
            "revision_of": self.revision_of,
            "key": self.key,
        }

    @property
    def key(self) -> str:
        return stable_digest(
            {"orders": [dict(order) for order in self.orders], "name": self.name}
        )

    @classmethod
    def from_dict(cls, value: dict) -> "Candidate":
        return cls(
            index=int(value.get("index", 0)),
            name=str(value["name"]),
            solver=str(value.get("solver", "unknown")),
            orders=tuple(dict(order) for order in value["orders"]),
            estimated_s=value.get("estimated_s"),
            provenance=str(value.get("provenance", "route_search")),
            revision_of=value.get("revision_of"),
        )


@dataclass(frozen=True)
class BudgetPlan:
    source_budget_s: int
    budget_s: int
    start: str
    end: str
    formula: str = "900 * ceil(1.2 * T / 900), minimum 900"
    coverage: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "source_budget_s": self.source_budget_s,
            "budget_s": self.budget_s,
            "start": self.start,
            "end": self.end,
            "formula": self.formula,
            "coverage": self.coverage,
        }


@dataclass
class TrialResult:
    """A real, full SimulatorService session, or a cached copy of one."""

    candidate: Candidate
    trial_id: str | None
    outcome: str | None
    elapsed_s: float
    lost_teu: int
    total_teu: int
    ship_lost: bool
    session: str | None
    view: dict
    qualified: bool
    reason: str
    fingerprint: str
    orders: tuple[dict, ...]
    grade: int = 0
    final_validation: bool = False
    metrics_valid: bool = True
    cached: bool = False
    index: int = 0

    @property
    def loss_fraction(self) -> float:
        if self.total_teu <= 0:
            return 1.0 if self.lost_teu > 0 else 0.0
        return self.lost_teu / self.total_teu

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "candidate": self.candidate.as_dict(),
            "trial_id": self.trial_id,
            "outcome": self.outcome,
            "elapsed_s": self.elapsed_s,
            "lost_teu": self.lost_teu,
            "total_teu": self.total_teu,
            "loss_fraction": self.loss_fraction,
            "grade": self.grade,
            "ship_lost": self.ship_lost,
            "qualified": self.qualified,
            "metrics_valid": self.metrics_valid,
            "reason": self.reason,
            "fingerprint": self.fingerprint,
            "orders": [dict(order) for order in self.orders],
            "session": self.session,
            "view": self.view,
            "final_validation": self.final_validation,
            "cached": self.cached,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "TrialResult":
        candidate = value["candidate"]
        return cls(
            candidate=Candidate(
                index=int(candidate.get("index", 0)),
                name=str(candidate["name"]),
                solver=str(candidate.get("solver", "unknown")),
                orders=tuple(dict(o) for o in candidate["orders"]),
                estimated_s=candidate.get("estimated_s"),
                provenance=str(candidate.get("provenance", "route_search")),
                revision_of=candidate.get("revision_of"),
            ),
            trial_id=value.get("trial_id"),
            outcome=value.get("outcome"),
            elapsed_s=float(value.get("elapsed_s", 0.0)),
            lost_teu=int(value.get("lost_teu", 0)),
            total_teu=int(value.get("total_teu", 0)),
            ship_lost=bool(value.get("ship_lost", False)),
            session=value.get("session"),
            view=value.get("view") or {},
            qualified=bool(value.get("qualified", False)),
            reason=str(value.get("reason", "")),
            fingerprint=str(value.get("fingerprint", "")),
            orders=tuple(dict(o) for o in value.get("orders", [])),
            grade=int(value.get("grade", 0)),
            final_validation=bool(value.get("final_validation", False)),
            metrics_valid=bool(value.get("metrics_valid", True)),
            cached=bool(value.get("cached", False)),
            index=int(value.get("index", 0)),
        )


def normalize_orders(
    orders: list[dict], budget_s: float
) -> tuple[dict, ...]:
    """Clamp a model or solver proposal to the finite command envelope."""
    if not isinstance(orders, list) or not orders:
        raise ValueError("A route needs at least one order")
    if len(orders) > MAX_ORDERS:
        raise ValueError(f"A route may have at most {MAX_ORDERS} orders")
    result: list[dict] = []
    previous = -math.inf
    for raw in orders:
        if not isinstance(raw, dict):
            raise ValueError("Each order must be an object")
        extra = set(raw) - {"t", "heading_deg", "speed_knots"}
        if extra:
            raise ValueError("Orders only accept t, heading_deg and speed_knots")
        try:
            t = float(raw["t"])
            heading = float(raw["heading_deg"])
            speed = float(raw["speed_knots"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("Orders need numeric t, heading_deg and speed_knots") from None
        if not all(math.isfinite(v) for v in (t, heading, speed)):
            raise ValueError("Orders must be finite")
        if t < 0 or t > budget_s:
            raise ValueError("Order time is outside the mission budget")
        if t < previous - 1e-9:
            raise ValueError("Order times must not decrease")
        if not 0 <= heading < 360:
            raise ValueError("Heading must be in [0, 360)")
        if not 0 <= speed <= MAX_SPEED_KNOTS:
            raise ValueError(f"Speed must be in [0, {MAX_SPEED_KNOTS:g}] knots")
        result.append(
            {
                "t": round(t, 3),
                "heading_deg": round(heading, 3),
                "speed_knots": round(speed, 3),
            }
        )
        previous = t
    return tuple(result)


def normalize_outcome(outcome: str | None) -> str | None:
    return None if outcome is None else str(outcome)


def is_qualified(
    outcome: str | None,
    *,
    ship_lost: bool,
    lost_teu: int,
    total_teu: int,
    metrics_valid: bool = True,
) -> bool:
    """The published qualification rule: escape, survive, retain >= 99 % TEU.

    Missing, non-finite, negative or inconsistent result metrics never qualify.
    """
    if not metrics_valid:
        return False
    if normalize_outcome(outcome) != "escaped":
        return False
    if not isinstance(ship_lost, bool) or ship_lost:
        return False
    if (
        not isinstance(lost_teu, int)
        or isinstance(lost_teu, bool)
        or not isinstance(total_teu, int)
        or isinstance(total_teu, bool)
    ):
        return False
    if total_teu <= 0 or lost_teu < 0 or lost_teu > total_teu:
        return False
    return lost_teu / total_teu <= LOSS_LIMIT + 1e-12


def _algorithm_hashes(root: Path) -> dict:
    result = {}
    for name in ALGORITHM_FILES:
        path = Path(root) / name
        result[name] = digest_file(path) if path.is_file() else "unavailable"
    return result


def algorithm_hashes(root: Path) -> dict:
    """Hash the implementation files that can change a trial result."""
    return _algorithm_hashes(root)


def evidence_fingerprint(
    definition: dict,
    root: Path,
    *,
    gameplay: dict | None = None,
    gameplay_path: Path | None = None,
    navigation: dict | None = None,
) -> dict:
    """Hash the actual scenario, algorithms, vessel, environment and gameplay.

    ``navigation`` is the fixed coast-policy metadata for this run. A report
    that carries a different fingerprint is stale and must not be reused, even
    when its prose still looks plausible.
    """
    root = Path(root)
    environment = {}
    for layer, source in sorted(definition.get("environment", {}).items()):
        path = root / source["path"]
        actual = digest_file(path) if path.is_file() else "unavailable"
        environment[layer] = {
            "path": str(source["path"]),
            "declared_sha256": source.get("sha256"),
            "actual_sha256": actual,
            "matches_declared": actual == source.get("sha256"),
        }
    vessel = definition.get("vessel", {})
    vessel_model = root / vessel.get("path", "")
    vessel_files = {}
    for name in ("vessel.npz", "provenance.json"):
        path = vessel_model / name
        vessel_files[name] = digest_file(path) if path.is_file() else "unavailable"
    voyage = definition.get("voyage")
    voyage_provenance = voyage.get("provenance") if isinstance(voyage, dict) else None
    if not isinstance(voyage_provenance, dict):
        voyage_provenance = {}
    payload = {
        "version": 1,
        "scenario": definition,
        "gameplay_sha256": (
            digest_file(gameplay_path)
            if gameplay_path is not None and Path(gameplay_path).is_file()
            else stable_digest(gameplay)
            if gameplay is not None
            else None
        ),
        "algorithms": _algorithm_hashes(root),
        "vessel": {
            "declared": {
                "sha256": vessel.get("sha256"),
                "source_commit": vessel.get("source_commit"),
                "loading_condition": vessel.get("loading_condition"),
            },
            "files": vessel_files,
        },
        "environment": environment,
        "voyage": {
            "present": isinstance(voyage, dict),
            "sha256": stable_digest(voyage) if isinstance(voyage, dict) else None,
            "authored": voyage.get("authored") if isinstance(voyage, dict) else None,
            "catalogue_sha256": voyage_provenance.get("catalogue_sha256"),
            "searoute_version": voyage_provenance.get("searoute_version"),
            "snap_distance_nm": (
                voyage.get("snap_distance_nm") if isinstance(voyage, dict) else None
            ),
        },
        "navigation": navigation,
    }
    return dict(payload, fingerprint=stable_digest(payload), algorithm_root=str(root))
