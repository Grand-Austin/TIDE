"""Export verified escape routes for the Builder package.

The exporter keeps only final-session evidence. It stores small, factual route
records and preserves the session provenance. Bulky simulator logs stay in the
trial directories.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

from agent.models import LOSS_LIMIT, normalize_orders, stable_digest
from game.scenario import ROOT

SCHEMA_VERSION = 1
KIND = "solution_routes"
GOVERNING_RULE = (
    "escape in a full session, keep the ship afloat, and lose at most 1 % of "
    "the initial TEU"
)
TRACK_INTERVAL_S = 300.0
MAX_TRACK_POINTS = 120


def build_solutions(
    records,
    *,
    definition: dict,
    evidence: dict,
    min_solutions: int = 3,
    project_root: Path = ROOT,
    reason: str | None = None,
    manual_approval: dict | None = None,
) -> dict:
    """Return one readable package of distinct, revalidated solution routes."""
    project_root = Path(project_root)
    required = max(0, int(min_solutions))
    budget_s = int(definition["mission"]["budget_s"])
    source_fingerprint = (
        evidence.get("fingerprint") if isinstance(evidence, dict) else None
    )
    approval = manual_approval or {}
    approved_names = (
        set(approval.get("candidate_names", []))
        if approval.get("approved_by") == "user"
        and approval.get("source_fingerprint") == source_fingerprint
        and source_fingerprint is not None
        else set()
    )

    prepared = []
    for record in records or ():
        approved = getattr(getattr(record, "candidate", None), "name", None) in approved_names
        entry = _prepare(record, source_fingerprint, budget_s, user_approved=approved)
        if entry is not None:
            prepared.append(entry)
    prepared.sort(
        key=lambda item: (
            item[1].elapsed_s,
            item[2]["lost_teu"],
            item[2]["name"],
        )
    )

    retained = []
    seen = set()
    for key, record, solution in prepared:
        if key in seen:
            continue
        seen.add(key)
        solution["track"], solution["track_status"] = _track(
            record,
            definition,
            project_root,
            solution["orders"],
        )
        retained.append(solution)

    count = len(retained)
    if reason is not None:
        status = "pending"
        reason_text = str(reason)
    elif count >= required:
        status = "ready"
        reason_text = None
    elif count:
        status = "insufficient"
        reason_text = "insufficient_qualified_solutions"
    else:
        status = "pending"
        reason_text = "no_qualified_final_solution"

    return {
        "version": SCHEMA_VERSION,
        "kind": KIND,
        "status": status,
        "summary": _summary(retained, required, status, reason_text),
        "reading_guide": (
            "Read each route from its timed orders. Order times are seconds "
            "from the scenario start; headings are degrees and speeds are "
            "knots. The track contains actual sampled positions when "
            "available. An endpoints_only track contains only the initial and "
            "final positions. Use a route only for the stated initial state "
            "and budget."
        ),
        "source_fingerprint": source_fingerprint,
        "governing_rule": (
            "Final-session escape and ship survival within budget; explicit user approval may retain routes above the automatic 1% TEU-loss limit."
            if approved_names else GOVERNING_RULE
        ),
        "automatic_qualification_rule": GOVERNING_RULE,
        "manual_approval": copy.deepcopy(approval) if approved_names else None,
        "initial_state": _initial_state(definition),
        "budget_s": budget_s,
        "required_minimum": required,
        "solution_count": count,
        "solutions": retained,
        "limitations": [
            "A solution applies only to the stated initial state and mission budget.",
            "A sampled track is not a full trajectory; an endpoints_only track has no intermediate positions.",
            "The stored routes are distinct final-verified trials, not a complete enumeration of all possible routes.",
            "User approval does not change actual losses or the automatic_qualified label.",
        ],
        "reason": reason_text,
    }


def _prepare(record, source_fingerprint, budget_s: int, *, user_approved=False):
    if source_fingerprint is None:
        return None
    if not (
        (bool(getattr(record, "qualified", False)) or user_approved)
        and bool(getattr(record, "final_validation", False))
        and bool(getattr(record, "metrics_valid", False))
    ):
        return None
    if getattr(record, "fingerprint", None) != source_fingerprint:
        return None
    if getattr(record, "outcome", None) != "escaped":
        return None
    if getattr(record, "ship_lost", True) is not False:
        return None

    elapsed = _number(getattr(record, "elapsed_s", None))
    if elapsed is None or elapsed < 0 or elapsed > float(budget_s) + 1e-6:
        return None

    lost = _integer(getattr(record, "lost_teu", None))
    total = _integer(getattr(record, "total_teu", None))
    if lost is None or total is None or total <= 0 or lost < 0 or lost > total:
        return None
    if lost / total > LOSS_LIMIT + 1e-12 and not user_approved:
        return None

    orders = _applied_orders(record, elapsed, budget_s)
    if orders is None:
        return None
    key = stable_digest([dict(order) for order in orders])
    candidate = getattr(record, "candidate", None)
    name = str(getattr(candidate, "name", "unnamed route"))
    solver = str(getattr(candidate, "solver", "unknown"))
    solution = {
        "id": f"route-{key[:12]}",
        "name": name,
        "solver": solver,
        "summary": _route_summary(name, solver, elapsed, lost, total, orders),
        "orders": [dict(order) for order in orders],
        "elapsed_s": round(elapsed, 3),
        "lost_teu": lost,
        "total_teu": total,
        "loss_fraction": round(lost / total, 6),
        "ship_lost": False,
        "automatic_qualified": bool(record.qualified and lost / total <= LOSS_LIMIT + 1e-12),
        "selection_basis": "user_approved" if user_approved else "automatic",
        "track": [],
        "track_status": "endpoints_only",
        "provenance": {
            "trial_id": getattr(record, "trial_id", None),
            "session": getattr(record, "session", None),
            "fingerprint": getattr(record, "fingerprint", None),
            "final_validation": True,
        },
    }
    return key, record, solution


def _applied_orders(record, elapsed_s: float, budget_s: int):
    raw_orders = getattr(record, "orders", None)
    if not isinstance(raw_orders, (list, tuple)):
        return None
    limit = min(float(elapsed_s), float(budget_s))
    applied = []
    for raw in raw_orders:
        if not isinstance(raw, dict):
            return None
        t = _number(raw.get("t"))
        if t is None:
            return None
        if t < limit - 1e-9:
            applied.append(dict(raw))
    if not applied:
        return ()
    try:
        return normalize_orders(applied, budget_s)
    except (TypeError, ValueError):
        return None


def _track(record, definition: dict, project_root: Path, orders: list[dict]):
    elapsed = float(record.elapsed_s)
    initial = _initial_point(definition)
    points = [initial] if initial is not None else []
    actual = _session_samples(record, project_root, elapsed)
    selected = _regular_samples(actual)
    selected.extend(_boundary_samples(actual, [float(order["t"]) for order in orders]))
    sampled = any(0.0 < point["t"] < elapsed - 1e-9 for point in selected)

    view = record.view if isinstance(record.view, dict) else {}
    final = _vessel_point(
        view.get("vessel"),
        fallback_time=elapsed,
    )

    merged: dict[float, dict] = {}
    for point in selected:
        if point["t"] <= 0.0:
            continue
        merged[round(point["t"], 6)] = point
    if final is not None:
        merged[round(final["t"], 6)] = final
    ordered = sorted(merged.values(), key=lambda point: point["t"])
    protected = {0.0}
    protected.update(round(float(order["t"]), 6) for order in orders)
    if final is not None:
        protected.add(round(final["t"], 6))
    limit = max(1, MAX_TRACK_POINTS - len(points))
    points.extend(_limit_track(ordered, protected, limit))
    points.sort(key=lambda point: point["t"])
    return points, "sampled" if sampled else "endpoints_only"


def _initial_point(definition: dict):
    initial = definition.get("initial") or {}
    return _point(
        0.0,
        initial.get("lon"),
        initial.get("lat"),
        heading_deg=initial.get("heading_deg"),
        speed_mps=initial.get("speed_mps"),
    )


def _initial_state(definition: dict) -> dict:
    state = copy.deepcopy(definition.get("initial") or {})
    state["start"] = definition.get("start")
    state["seed"] = definition.get("seed")
    return state


def _session_samples(record, project_root: Path, elapsed_s: float) -> list[dict]:
    path = _events_path(getattr(record, "session", None), project_root)
    if path is None:
        return []
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    samples = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(value, dict) or value.get("kind") != "advance":
            continue
        data = value.get("data")
        vessel = data.get("vessel") if isinstance(data, dict) else None
        point = _vessel_point(vessel, fallback_time=value.get("time_s"))
        if point is not None and point["t"] <= elapsed_s + 1e-6:
            samples.append(point)
    return samples


def _events_path(session, project_root: Path) -> Path | None:
    if not isinstance(session, str) or not session:
        return None
    path = Path(session)
    if not path.is_absolute():
        path = Path(project_root) / path
    if path.is_file() or path.name == "events.jsonl":
        return path
    return path / "events.jsonl"


def _regular_samples(samples: list[dict]) -> list[dict]:
    selected = []
    previous = None
    for point in sorted(samples, key=lambda item: item["t"]):
        if point["t"] <= 1e-9:
            continue
        if previous is None or point["t"] - previous >= TRACK_INTERVAL_S - 1e-9:
            selected.append(point)
            previous = point["t"]
    return selected


def _boundary_samples(samples: list[dict], order_times: list[float]) -> list[dict]:
    if not samples:
        return []
    selected = []
    for order_t in sorted(set(round(value, 6) for value in order_times)):
        selected.append(
            min(samples, key=lambda point: abs(point["t"] - order_t))
        )
    return selected


def _limit_track(
    points: list[dict], protected: set[float], maximum: int
) -> list[dict]:
    if len(points) <= maximum:
        return points
    protected_indices = {
        index
        for index, point in enumerate(points)
        if round(point["t"], 6) in protected
    }
    regular = [
        index for index in range(len(points)) if index not in protected_indices
    ]
    capacity = max(0, maximum - len(protected_indices))
    if capacity == 0:
        keep = protected_indices
    elif len(regular) <= capacity:
        keep = protected_indices | set(regular)
    else:
        step = len(regular) / capacity
        keep = protected_indices | {
            regular[min(len(regular) - 1, int(index * step))]
            for index in range(capacity)
        }
    return [point for index, point in enumerate(points) if index in keep]


def _vessel_point(vessel, *, fallback_time=None):
    if not isinstance(vessel, dict):
        return None
    speed_mps = vessel.get("speed_through_water_mps")
    if speed_mps is None:
        speed_mps = vessel.get("speed_over_ground_mps")
    return _point(
        vessel.get("time_s", fallback_time),
        vessel.get("lon"),
        vessel.get("lat"),
        heading_deg=vessel.get("heading_deg"),
        speed_mps=speed_mps,
    )


def _point(time_s, lon, lat, *, heading_deg=None, speed_mps=None):
    t = _number(time_s)
    x = _number(lon)
    y = _number(lat)
    if t is None or x is None or y is None:
        return None
    point = {"t": round(t, 3), "lon": round(x, 6), "lat": round(y, 6)}
    heading = _number(heading_deg)
    if heading is not None:
        point["heading_deg"] = round(heading % 360.0, 3)
    speed = _number(speed_mps)
    if speed is not None:
        point["speed_knots"] = round(max(0.0, speed) * 3600 / 1852, 3)
    return point


def _summary(
    solutions: list[dict],
    required: int,
    status: str,
    reason: str | None,
) -> str:
    if not solutions:
        text = "No verified escape route is available."
        return f"{text} Reason: {reason}." if reason else text
    fastest = solutions[0]
    text = (
        f"{len(solutions)} verified escape route"
        f"{'' if len(solutions) == 1 else 's'} stored. "
        f"The fastest is {fastest['name']} in "
        f"{_seconds(fastest['elapsed_s'])} s with "
        f"{fastest['lost_teu']} of {fastest['total_teu']} TEU lost."
    )
    if status != "ready":
        text += f" Required minimum: {required}."
    if reason and status == "pending":
        text += f" Reason: {reason}."
    return text


def _route_summary(
    name: str,
    solver: str,
    elapsed_s: float,
    lost_teu: int,
    total_teu: int,
    orders: tuple,
) -> str:
    loss = 100.0 * lost_teu / total_teu
    return (
        f"{name} by {solver} escapes in {_seconds(elapsed_s)} s with "
        f"{lost_teu} of {total_teu} TEU lost ({loss:.2f}%). "
        f"It uses {len(orders)} timed order"
        f"{'' if len(orders) == 1 else 's'}."
    )


def _seconds(value: float) -> str:
    return f"{float(value):.1f}".rstrip("0").rstrip(".")


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _integer(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
