"""Copilot L1/L2 advisory for the live game.

L1 returns one method-level insight from the forecasts the player already holds. L2 returns one validated
reference route, its remaining waypoints and the caveats that belong to it. Neither mode steers: the Copilot
reads released evidence and answers in words.

The scenario package identity rule is deliberately narrower than the Builder's full evidence fingerprint:
a package is usable when its stored solutions name the same initial state and budget, and when the scenario's
``validation.json`` still matches the current ``scenario.json`` and ``gameplay.json`` bytes. A package whose
environment or algorithm hashes moved is a Builder question, not a play-time one.
"""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from agent.harness import AgentHarness, ToolRegistry, instructions_from, load_tool_specs
from agent.models import digest_file
from agent.transport import ChatClient, load_profile

EARTH_RADIUS_NM = 3440.065
KNOTS_PER_MPS = 1.94384
MAX_MATCH_NM = 20.0
MAX_TIME_GAP_S = 900.0
MAX_INSIGHT_CHARS = 240
PROFILE_ROLE = "copilot"
HARNESS_DIR = Path(__file__).resolve().parent / "copilot_harness"
L1_AUDIT = "copilot-l1.jsonl"
L1_NOTE = "Call read_released_forecast once, then finish_insight with one method-level insight of at most two sentences."
L2_NOTE = "Call read_reference_route once, then finish_insight with one short note on the selected reference route."
L1_FORBIDDEN = ("route-", "waypoint", "solution", "hidden region", "threshold")
# L2 may describe a direction and a rough distance. It may not name the route, quote a time or a TEU loss,
# and it may not present the reference as a validation or a guarantee.
L2_FORBIDDEN = ("route-", "solution", "hidden region", "threshold", "guarantee", "validated", "teu", "escaped",
                "nm", "mile", "nautical")
COMPASS_POINTS = ("north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west")


def _finite(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int(value, default: int = 0) -> int:
    number = _finite(value)
    return default if number is None else int(number)


def _same_number(left, right) -> bool:
    a, b = _finite(left), _finite(right)
    return a is not None and b is not None and abs(a - b) <= 1e-6


def _utc(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def load_solutions(scenario_dir: Path, *, definition: dict, root: Path) -> tuple[dict | None, str | None]:
    """Read a scenario's solutions package, or the reason it cannot be used (the check order is the reason order)."""
    scenario_dir, root = Path(scenario_dir), Path(root)
    path = scenario_dir / "solutions.json"
    if not path.is_file():
        return None, "solutions_missing"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None, "solutions_not_ready"
    if not isinstance(data, dict) or data.get("kind") != "solution_routes" or data.get("status") != "ready":
        return None, "solutions_not_ready"
    if not isinstance(data.get("solutions"), list) or not data["solutions"]:
        return None, "solutions_not_ready"
    state = data.get("initial_state")
    if not isinstance(state, dict):
        return None, "solutions_initial_state_mismatch"
    initial = definition.get("initial") or {}
    if not all(_same_number(state.get(key), initial.get(key))
               for key in ("lon", "lat", "heading_deg", "speed_mps")):
        return None, "solutions_initial_state_mismatch"
    if state.get("seed", data.get("seed")) != definition.get("seed"):
        return None, "solutions_initial_state_mismatch"
    if _utc(state.get("start", data.get("start"))) != _utc(definition.get("start")):
        return None, "solutions_initial_state_mismatch"
    budget, mission = _finite(data.get("budget_s")), _finite((definition.get("mission") or {}).get("budget_s"))
    if budget is None or mission is None or int(budget) != int(mission):
        return None, "solutions_budget_mismatch"
    fingerprint = data.get("source_fingerprint")
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        return None, "solutions_source_fingerprint_missing"
    try:
        report = json.loads((scenario_dir / "validation.json").read_text())
    except (OSError, ValueError):
        return None, "scenario_validation_missing"
    if not isinstance(report, dict):
        return None, "scenario_validation_missing"
    gameplay = (definition.get("gameplay") or {}).get("path")
    if (report.get("passed") is not True
            or report.get("definition_sha256") != digest_file(scenario_dir / "scenario.json")
            or not isinstance(gameplay, str)
            or report.get("gameplay_sha256") != digest_file(root / gameplay)):
        return None, "scenario_validation_stale"
    return data, None


def _distance_nm(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    first, second = math.radians(lat1), math.radians(lat2)
    delta_lon = math.radians(lon2 - lon1)
    haversine = (math.sin((second - first) / 2) ** 2
                 + math.cos(first) * math.cos(second) * math.sin(delta_lon / 2) ** 2)
    return 2 * EARTH_RADIUS_NM * math.asin(math.sqrt(min(1.0, haversine)))


def _heading_gap(first: float, second: float) -> float:
    return abs((first - second + 540.0) % 360.0 - 180.0)


def _bearing_deg(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    first, second = math.radians(lat1), math.radians(lat2)
    delta_lon = math.radians(lon2 - lon1)
    east = math.sin(delta_lon) * math.cos(second)
    north = math.cos(first) * math.sin(second) - math.sin(first) * math.cos(second) * math.cos(delta_lon)
    return (math.degrees(math.atan2(east, north)) + 360.0) % 360.0


def _compass(bearing: float) -> str:
    return COMPASS_POINTS[int(((bearing % 360.0) + 22.5) // 45.0) % 8]


def _side(relative_deg: float) -> str:
    """Where a bearing sits relative to the bow: ahead, a bow, a beam, a quarter, astern."""
    hand = "starboard" if relative_deg >= 0 else "port"
    for limit, label in ((22.5, "dead ahead"), (67.5, f"on your {hand} bow"),
                         (112.5, f"on your {hand} beam"), (157.5, f"on your {hand} quarter")):
        if abs(relative_deg) <= limit:
            return label
    return "dead astern"


def _position(lon: float, lat: float) -> str:
    """A chart-style position to two decimals: about 1 km, which is finer than the sampled track."""
    return f"{abs(lat):.2f}° {'N' if lat >= 0 else 'S'} {abs(lon):.2f}° {'E' if lon >= 0 else 'W'}"


def _route_view(route: dict, match: dict, *, elapsed_s: float, ship_lon: float,
                ship_lat: float, ship_heading: float) -> dict | None:
    """What the Copilot may show: the remaining geometry, plus where it lies and which way it runs.

    The route's name, solver, escape time and cargo loss stay out: the player gets a bearing, not a result.
    """
    waypoints = []
    for point in route["track"][match["track_index"]:]:
        t, lon, lat = _finite(point.get("t")), _finite(point.get("lon")), _finite(point.get("lat"))
        if t is None or lon is None or lat is None or t < elapsed_s - 60.0:
            continue
        waypoints.append([lon, lat])
    if not waypoints:
        return None
    first, last = waypoints[0], waypoints[-1]
    bearing = _bearing_deg(ship_lon, ship_lat, first[0], first[1])
    offset = _distance_nm(ship_lon, ship_lat, first[0], first[1])
    runs = _bearing_deg(first[0], first[1], last[0], last[1]) if len(waypoints) > 1 else bearing
    relative = (bearing - ship_heading + 540.0) % 360.0 - 180.0
    return {
        "id": str(route.get("id", "")),
        "waypoints": waypoints,
        "start": {"lon": round(first[0], 4), "lat": round(first[1], 4)},
        "end": {"lon": round(last[0], 4), "lat": round(last[1], 4)},
        "bearing_deg": round(bearing, 1),
        "direction": _compass(bearing),
        "relative_deg": round(relative, 1),
        "side": _side(relative),
        "offset_nm": round(offset, 2),
        "runs": _compass(runs),
    }


def select_route(solutions: dict, context: dict) -> dict | None:
    """The nearest reference route the ship could still follow, or None when nothing passes the gates."""
    time_ctx, vessel = context.get("time") or {}, context.get("vessel") or {}
    elapsed_s, remaining_s = _finite(time_ctx.get("elapsed_s")), _finite(time_ctx.get("remaining_s"))
    lon, lat = _finite(vessel.get("lon")), _finite(vessel.get("lat"))
    heading = _finite(vessel.get("heading_deg"))
    if elapsed_s is None or remaining_s is None or lon is None or lat is None:
        return None
    best = None
    for route in solutions.get("solutions") or []:
        if not isinstance(route, dict) or route.get("ship_lost") is not False:
            continue
        track, escape_elapsed = route.get("track"), _finite(route.get("elapsed_s"))
        if not isinstance(track, list) or escape_elapsed is None:
            continue
        chosen = None
        for index, point in enumerate(track):
            if not isinstance(point, dict):
                continue
            t, point_lon, point_lat = _finite(point.get("t")), _finite(point.get("lon")), _finite(point.get("lat"))
            if t is None or point_lon is None or point_lat is None:
                continue
            distance = _distance_nm(lon, lat, point_lon, point_lat)
            gap = abs(elapsed_s - t)
            if distance > MAX_MATCH_NM or gap > MAX_TIME_GAP_S:
                continue
            if escape_elapsed - t > remaining_s + 1.0:
                continue
            point_heading = _finite(point.get("heading_deg"))
            heading_gap = 0.0 if point_heading is None else _heading_gap(heading, point_heading)
            cost = distance + gap / 60.0 + heading_gap / 30.0
            candidate = (cost, _int(route.get("lost_teu")), str(route.get("id", "")))
            if chosen is None or candidate < chosen[0]:
                chosen = (candidate, index, t, distance, gap, heading_gap)
        if chosen is None:
            continue
        cost, index, t, distance, gap, heading_gap = chosen
        match = {"track_index": index, "t": t, "distance_nm": round(distance, 3),
                 "time_gap_s": round(gap, 1), "heading_gap_deg": round(heading_gap, 1)}
        view = _route_view(route, match, elapsed_s=elapsed_s, ship_lon=lon, ship_lat=lat,
                           ship_heading=heading)
        if view is None:
            continue
        if best is None or cost < best[0]:
            best = (cost, view)
    return None if best is None else best[1]


class SolutionsGrant:
    """A request-scoped grant of exactly one selected route; the harness never sees the solutions file."""

    def __init__(self, route: dict):
        self._route = deepcopy(route)
        self._revoked = False

    def read(self) -> dict:
        if self._revoked:
            raise PermissionError("The reference route grant has been revoked")
        return deepcopy(self._route)

    def revoke(self) -> None:
        self._route = {}
        self._revoked = True


def _record_plan(args: dict) -> dict:
    return {"plan": args["steps"], "status": "planned"}


def _prompt(context: dict, note: str) -> dict:
    """The JSON-safe state the Copilot may read: no delivered-report list, no solutions, no log."""
    visible = {key: context.get(key) for key in ("scenario", "time", "vessel", "target", "cargo", "sector")}
    visible["note"] = note
    return visible


def _clean(text, forbidden: tuple[str, ...]) -> str | None:
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not text or len(text) > MAX_INSIGHT_CHARS or "\n" in text or "\r" in text:
        return None
    lowered = text.lower()
    if any(word in lowered for word in forbidden):
        return None
    if text[-1] not in ".!?" or len(re.findall(r"[.!?](?:\s|$)", text)) > 2:
        return None
    return text


def _valid_insight(text) -> str | None:
    return _clean(text, L1_FORBIDDEN)


def _valid_l2_insight(text) -> str | None:
    return _clean(text, L2_FORBIDDEN)


def fallback_l1(context: dict) -> str:
    """One method-level insight built only from the sample the player can see."""
    vessel = context.get("vessel") or {}
    hs, roll = _finite(vessel.get("hs_m")) or 0.0, _finite(vessel.get("roll_deg")) or 0.0
    heading = _finite(vessel.get("heading_deg")) or 0.0
    wave_from = _finite(vessel.get("wave_from_deg")) or 0.0
    speed_kn = (_finite(vessel.get("speed_through_water_mps")) or 0.0) * KNOTS_PER_MPS
    sea = context.get("encounter")
    if sea == "Beam seas":
        return (f"The ship is rolling {abs(roll):.0f}° with {hs:.1f} m seas on the beam; changing heading so the "
                f"waves meet the bow or stern quarter reduces roll.")
    if sea == "Following seas":
        return (f"With {hs:.1f} m following seas at {speed_kn:.1f} kn, the ship can surf and slew; easing speed "
                f"or taking them at an angle reduces that risk.")
    return (f"Seas at the ship are {hs:.1f} m from {wave_from:.0f}° while you steer {heading:.0f}°; compare the "
            f"neighbouring released forecast areas before the next order.")


def fallback_l2(route: dict, context: dict | None = None) -> str:
    """One sentence of positions and bearings: no route name, no time, no cargo loss."""
    runs = route.get("runs") or "ahead"
    side = route.get("side") or "ahead of you"
    offset = _finite(route.get("offset_nm")) or 0.0
    start, end = route.get("start") or {}, route.get("end") or {}
    if _finite(start.get("lon")) is None or _finite(end.get("lon")) is None:
        return f"A reference track runs {runs}; compare it with the released forecast before you order."
    near = "from your position" if offset < 0.1 else f"about {offset:.1f} nm {side}"
    return (f"A reference track runs from {_position(_finite(start['lon']), _finite(start['lat']))} to "
            f"{_position(_finite(end['lon']), _finite(end['lat']))}, {near}, heading {runs}; "
            f"compare it with the released forecast before you order.")


def _result(mode: str, status: str, source: str, text: str, *, route: dict | None = None,
            reason: str | None = None, at_s: float | None = None) -> dict:
    return {"status": status, "mode": mode, "source": source, "text": text, "route": route,
            "reason": reason, "at_s": at_s}


def _close(transport) -> None:
    close = getattr(transport, "close", None)
    if close:
        try:
            close()
        except Exception:
            pass


class Copilot:
    """The Copilot role: one bounded harness run per request, no checkpoint file, no owned game state."""

    def __init__(self, *, models_path: Path, keys_path: Path, audit_dir: Path | None = None,
                 profile: dict | None = None, provider: dict | None = None,
                 transport_factory: Callable[[], Callable] | None = None):
        self.audit_dir = Path(audit_dir) if audit_dir is not None else None
        self._factory = transport_factory
        self._profile = profile
        self._provider = provider
        self._keys_path = keys_path
        if transport_factory is None:
            self._profile, self._provider = load_profile(models_path, PROFILE_ROLE)
            _close(ChatClient(self._profile, self._provider, keys_path))  # fail now, not on the first hint
        elif not isinstance(profile, dict):
            raise ValueError("A scripted Copilot transport needs an explicit profile")

    # ------------------------------ requests ------------------------------
    def advise(self, mode: str, context: dict, solutions: dict | None) -> dict:
        if mode == "l1":
            return self._l1(context)
        if mode == "l2":
            return self._l2(context, solutions)
        raise ValueError("unknown copilot mode")

    def close(self) -> None:
        """Release what this instance still owns: every client goes to a harness, which closes it."""

    def _transport(self):
        return self._factory() if self._factory is not None else ChatClient(
            self._profile, self._provider, self._keys_path)

    def _run(self, *, context: dict, specs_path: Path, handlers: dict, audit_path: Path | None) -> dict | None:
        transport = self._transport()
        harness = None
        try:
            harness = AgentHarness(
                role=PROFILE_ROLE,
                instructions=instructions_from(HARNESS_DIR),
                registry=ToolRegistry.from_specs(load_tool_specs(specs_path), handlers),
                transport=transport,
                profile=self._profile,
                checkpoint_path=":memory:",
                max_rounds=4,
                max_tool_calls=4,
                max_failures=2,
                audit_path=audit_path,
            )
            return harness.run(context)
        except Exception:
            return None
        finally:
            if harness is not None:
                harness.close()
            else:
                _close(transport)

    def _l1(self, context: dict) -> dict:
        reports = context.get("reports") or []
        at_s = _finite((context.get("time") or {}).get("elapsed_s"))
        if not reports:
            return _result("l1", "refused", "local", "No forecast has been released yet.",
                           reason="no_released_forecast", at_s=at_s)
        used = {"forecast": False}

        def forecast(args: dict) -> dict:
            used["forecast"] = True
            return {"status": "ok", "label": "released_game_forecast",
                    "issued_at": max(r["issued_at"] for r in reports),
                    "valid_to": min(r["valid_to"] for r in reports),
                    "note": "Game forecasts generated from the historical reconstruction, not observations.",
                    "reports": reports}

        def finish(args: dict) -> dict:
            return {"status": "ok", "text": args["text"].strip()}

        outcome = self._run(
            context=_prompt(context, L1_NOTE),
            specs_path=HARNESS_DIR / "tools_l1.json",
            handlers={"record_plan": _record_plan, "read_released_forecast": forecast, "finish_insight": finish},
            audit_path=self.audit_dir / L1_AUDIT if self.audit_dir is not None else None,
        )
        if outcome is None:
            return _result("l1", "ready", "fallback", fallback_l1(context), reason="model_failed", at_s=at_s)
        if not used["forecast"]:
            return _result("l1", "ready", "fallback", fallback_l1(context),
                           reason="evidence_not_read", at_s=at_s)
        text = _valid_insight((outcome.get("result") or {}).get("text"))
        if text is None:
            return _result("l1", "ready", "fallback", fallback_l1(context),
                           reason="invalid_model_output", at_s=at_s)
        return _result("l1", "ready", "agent", text, at_s=at_s)

    def _l2(self, context: dict, solutions: dict | None) -> dict:
        at_s = _finite((context.get("time") or {}).get("elapsed_s"))
        if solutions is None:
            return _result("l2", "refused", "local",
                           "No validated reference route is available for this scenario.",
                           reason="solutions_unavailable", at_s=at_s)
        route = select_route(solutions, context)
        if route is None:
            return _result("l2", "refused", "local",
                           "No reference route passes near the current position and time.",
                           reason="no_nearby_reference_route", at_s=at_s)
        grant, used = SolutionsGrant(route), {"route": False}

        def reference(args: dict) -> dict:
            used["route"] = True
            return {"status": "ok", "route": grant.read()}

        def finish(args: dict) -> dict:
            return {"status": "ok", "text": args["text"].strip()}

        try:
            outcome = self._run(
                context=_prompt(context, L2_NOTE),
                specs_path=HARNESS_DIR / "tools_l2.json",
                handlers={"record_plan": _record_plan, "read_reference_route": reference,
                          "finish_insight": finish},
                audit_path=None,  # no solution payload reaches disk
            )
        finally:
            grant.revoke()
        if outcome is None:
            return _result("l2", "ready", "fallback", fallback_l2(route, context), route=route,
                           reason="model_failed", at_s=at_s)
        if not used["route"]:
            return _result("l2", "ready", "fallback", fallback_l2(route, context), route=route,
                           reason="evidence_not_read", at_s=at_s)
        text = _valid_l2_insight((outcome.get("result") or {}).get("text"))
        if text is None:
            return _result("l2", "ready", "fallback", fallback_l2(route, context), route=route,
                           reason="invalid_model_output", at_s=at_s)
        return _result("l2", "ready", "agent", text, route=route, at_s=at_s)
