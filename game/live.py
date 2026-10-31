"""Local live-play server for the 3D frontend: serves web/ and a small JSON API around game.session.Session.

Physics, cargo, the hidden escape region and the released forecasts are the session's own rules. This
module adds pacing, judgement checkpoints and a score. Checkpoint answers are graded only against
information the player could see at that moment (heading, waves, released forecasts), never against the
hidden region.

    uv run python -m game.live            # then open http://localhost:8000/game/ (pick a "Play:" scenario)
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
import json
import math
import threading
import time
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agent.copilot import Copilot, load_solutions
from agent.tutor import Tutor, fallback_evaluation
from game.cargo import STACKS, stack_unit_ids
from game.scenario import ROOT, load_scenario
from game.session import Session
from game.simulation.runtime import Command, Simulation
from game.simulation.turn_rate import sample_turn_rate_deg_s

WEB = ROOT / "web"
SCENARIOS = ROOT / "scenarios"
SEGMENT_S = 1.0
RATES = (1.0, 5.0, 20.0, 0.0)  # 0 = as fast as the simulation runs
ROLL_CHECK_DEG = 10.0
MAX_FORECAST_CHECKS = 2
CHECKPOINT_GAP_S = 300.0  # game seconds after one decision before the next opens; the briefing does not count
PREVIEW_S = 480.0  # longest helm preview
PREVIEW_CHUNK_S = 60.0  # the preview stops a chunk after the ship settles on the ordered heading
PREVIEW_TRACK_S = 10.0  # one preview track point per this much game time
FIELDS = ["t", "lon", "lat", "heading", "roll", "pitch", "heave", "stw", "hs", "tp", "wave_from",
          "wind_e", "wind_n", "current_e", "current_n", "lost_teu", "grade", "cloud", "precip", "turn_rate"]
ENCOUNTERS = ["Head seas", "Bow-quartering seas", "Beam seas", "Stern-quartering seas", "Following seas"]
COMPASS = {(0, 1): "North", (1, 1): "North-east", (1, 0): "East", (1, -1): "South-east",
           (0, -1): "South", (-1, -1): "South-west", (-1, 0): "West", (-1, 1): "North-west"}
STAY = "No neighbouring area is forecast lower"
KNOTS_PER_MPS = 1.94384


@dataclass(frozen=True)
class AgentSettings:
    """Where the Copilot and Tutor read their model profile and key (--agent only)."""

    models_path: Path
    keys_path: Path


AGENT_SETTINGS: AgentSettings | None = None


def encounter(heading: float, wave_from: float) -> str:
    rel = (wave_from - heading) % 360
    a = 360 - rel if rel > 180 else rel
    return ENCOUNTERS[0 if a < 30 else 1 if a < 60 else 2 if a <= 120 else 3 if a <= 150 else 4]


def lower_seas_direction(reports: list[dict], sector: tuple[int, int]) -> str:
    """The neighbouring forecast area with the lowest upper Hs, if it beats the ship's own area."""
    by_sector = {tuple(r["sector"]): r for r in reports}
    own = by_sector.get(tuple(sector))
    best, best_hs = STAY, own["hs_range_m"][1] if own else math.inf
    for (dx, dy), name in COMPASS.items():
        r = by_sector.get((sector[0] + dx, sector[1] + dy))
        if r and r["hs_range_m"][1] < best_hs:
            best, best_hs = name, r["hs_range_m"][1]
    return best


class Game:
    def __init__(self, scenario_id: str, *, agent_settings: AgentSettings | None = None,
                 copilot=None, tutor_agent=None):
        self.scenario = load_scenario(SCENARIOS / scenario_id / "scenario.json")
        d = self.scenario.definition
        gameplay = json.loads((ROOT / d["gameplay"]["path"]).read_text())
        self._environment = self.scenario.environment()
        env = self._environment.__enter__()
        out = ROOT / "data" / "runs" / f"live-{scenario_id}-{datetime.now():%Y%m%d-%H%M%S-%f}"  # unique per start
        self.session = Session(self.scenario, self.scenario.simulation(env), gameplay, out)
        weather = SCENARIOS / scenario_id / "weather.json"
        self.weather = json.loads(weather.read_text())["hourly"] if weather.exists() else []
        self.lock = threading.RLock()
        self.scenario_dir = SCENARIOS / scenario_id
        self.solutions, self.solutions_reason = (
            load_solutions(self.scenario_dir, definition=d, root=ROOT)
            if (agent_settings is not None or copilot is not None) else (None, "agent_disabled"))
        self.copilot = copilot or (
            Copilot(models_path=agent_settings.models_path, keys_path=agent_settings.keys_path, audit_dir=out)
            if agent_settings is not None else None)
        self.tutor_agent = tutor_agent or (
            Tutor(models_path=agent_settings.models_path, keys_path=agent_settings.keys_path, audit_dir=out)
            if agent_settings is not None else None)
        self.tutor_result = None
        self.tutor_evidence = None
        self.orders = [{"t": 0.0, "heading_deg": float(d["initial"]["heading_deg"]),
                        "speed_knots": float(d["initial"]["speed_mps"]) * KNOTS_PER_MPS}]
        self.samples, self.log, self.checkpoints = [], [], []
        self.pending = None
        self.rate = 5.0
        self.roll_checked = False
        self.forecast_checks = 0
        self.seen_reports = set()
        self.score = None
        self.stopped = False
        self.preview_busy = threading.Lock()
        sim = self.session.sim
        # Keep the preview on the session's integration clock. Copying a state onto a coarser dt would
        # multiply its tick-derived time (and wave/controller/exposure clock) by that step ratio.
        self.preview_sim = Simulation(sim.vessel, sim.environment, seed=d["seed"], wind_parameters=sim.wind_parameters,
                                      settings=sim.settings)
        self._append_sample()
        self._open_checkpoint("briefing", "Before you sail: read the sea and the forecasts.")
        threading.Thread(target=self._loop, daemon=True).start()

    # ---------- simulation pacing ----------
    def _loop(self):
        while not self.stopped:
            s = self.session
            if s.outcome or self.pending or s.pause_signal.is_set():
                time.sleep(0.05)
                continue
            began = time.time()
            with self.lock:
                view = s.advance_segment(SEGMENT_S)
                self._append_sample()
                self.log.extend({"t": e.get("time_s"), "kind": e["kind"], "detail": e.get("detail"),
                                 "units": e.get("unit_ids", []), "teu": e.get("teu"), "demand_g": e.get("demand_g")}
                                for e in view["events"])
                self._triggers(view)
            if self.rate > 0:
                wait = SEGMENT_S / self.rate - (time.time() - began)
                if wait > 0:
                    time.sleep(wait)
            s.achieved_rate = round(SEGMENT_S / max(time.time() - began, 1e-6), 1)

    def _weather_at(self, t: float):
        if not self.weather:
            return None, None
        hour = min(len(self.weather) - 1, max(0, int(t // 3600)))
        return self.weather[hour]["cloud"], self.weather[hour]["precip"]

    def _append_sample(self):
        s, v = self.session, self.session.last_sample
        cargo = s.cargo_model.summary(s.cargo)
        cloud, precip = self._weather_at(v["time_s"])
        self.samples.append([round(x, 6) if isinstance(x, float) else x for x in (
            v["time_s"], v["lon"], v["lat"], v["heading_deg"], v["roll_deg"], v["pitch_deg"], v["heave_m"],
            v["speed_through_water_mps"], v["hs_m"], v["tp_s"], v["wave_from_deg"], v["wind_east_mps"],
            v["wind_north_mps"], v["current_east_mps"], v["current_north_mps"], cargo["lost_teu"], cargo["grade"],
            cloud, precip, sample_turn_rate_deg_s(v))])

    # ---------- judgement checkpoints ----------
    def _triggers(self, view):
        s = self.session
        if s.outcome or s.reason in ("domain_boundary", "model_limit", "numerical_failure"):
            self._finish()
            return
        new = [r for r in view["reports"] if r["id"] not in self.seen_reports]
        self.seen_reports |= {r["id"] for r in view["reports"]}
        if self._recently_decided(view):
            return  # a burst of losses or a forecast right after a decision does not stack another card
        if s.reason == "cargo_grade_increased":
            self._open_checkpoint("cargo", f"Cargo grade rose to {view['cargo']['grade']}: deck containers are going overboard.")
            return
        if not self.roll_checked and abs(view["vessel"]["roll_deg"]) > ROLL_CHECK_DEG:
            self.roll_checked = True
            self._open_checkpoint("roll", f"The ship rolled {abs(view['vessel']['roll_deg']):.0f}°.")
            return
        if any(r["important"] for r in new) and self.forecast_checks < MAX_FORECAST_CHECKS:
            self.forecast_checks += 1
            self._open_checkpoint("forecast", "A new forecast has been released.")

    def _recently_decided(self, view) -> bool:
        last = [cp["t"] for cp in self.checkpoints if cp["trigger"] != "briefing"]
        return bool(last) and view["vessel"]["time_s"] - last[-1] < CHECKPOINT_GAP_S

    def _open_checkpoint(self, trigger: str, message: str):
        s = self.session
        s.pause()
        v = s.visible()
        vessel = v["vessel"]
        questions = [{
            "id": "encounter", "prompt": "Where do the waves come from, relative to the bow?",
            "options": ENCOUNTERS, "answer": encounter(vessel["heading_deg"], vessel["wave_from_deg"]),
        }]
        if v["reports"]:
            questions.append({
                "id": "lower_seas", "prompt": "From the released forecasts, which way are the seas lower?",
                "options": [*COMPASS.values(), STAY],
                "answer": lower_seas_direction(v["reports"], s.rules.sector(vessel["lon"], vessel["lat"])),
            })
        self.pending = {"id": len(self.checkpoints), "trigger": trigger, "message": message,
                        "t": vessel["time_s"], "questions": questions}

    def answer(self, body: dict):
        with self.lock:
            if not self.pending or body.get("id") != self.pending["id"]:
                raise ValueError("No open checkpoint with that id")
            cp, answers = self.pending, body.get("answers", {})
            cp["results"] = {q["id"]: {"given": answers.get(q["id"]), "answer": q["answer"],
                                       "correct": answers.get(q["id"]) == q["answer"]} for q in cp["questions"]}
            cp["reason"] = str(body.get("reason", ""))[:500]
            order = body.get("order") or {}
            if order:
                self.session.set_order(float(order["heading_deg"]) % 360, float(order["speed_knots"]))
                self._note_order(float(order["heading_deg"]) % 360, float(order["speed_knots"]))
                cp["order"] = order
            self.checkpoints.append(cp)
            self.session.record("checkpoint", cp)
            self.pending = None
            if not self.session.outcome:
                self.session.authorize_resume()
            return cp["results"]

    # ---------- player controls ----------
    def _note_order(self, heading_deg: float, speed_knots: float):
        """Keep the order log the Tutor reads: one entry per real change, timed on the session clock."""
        entry = {"t": float(self.session.last_sample["time_s"]), "heading_deg": float(heading_deg),
                 "speed_knots": float(speed_knots)}
        last = self.orders[-1]
        if all(abs(entry[key] - last[key]) <= 1e-6 for key in ("heading_deg", "speed_knots")):
            return
        self.orders.append(entry)

    def order(self, heading_deg: float, speed_knots: float):
        with self.lock:
            paused = self.session.pause_signal.is_set()
            self.session.set_order(float(heading_deg) % 360, float(speed_knots))
            self._note_order(float(heading_deg) % 360, float(speed_knots))
            if not paused and not self.pending:
                self.session.authorize_resume()

    def pause(self, paused: bool):
        with self.lock:
            if paused:
                self.session.pause(manual=True)
            elif not self.pending and not self.session.outcome:
                self.session.authorize_resume()

    def preview(self, heading_deg: float, speed_knots: float, horizon_s: float = PREVIEW_S) -> dict:
        """Where this order takes the ship: the session's own ship model, on the session's integration clock,
        run ahead on a copy of its state until a minute after the ship settles on the order. Track only: no cargo,
        escape region or forecast is evaluated, so nothing hidden leaks into it."""
        if not self.preview_busy.acquire(blocking=False):
            return {"busy": True}  # one preview at a time; the page asks again
        try:
            with self.lock:
                state = deepcopy(self.session.state)
                start = float(self.session.last_sample["time_s"])
            settings = self.preview_sim.settings
            command = Command(float(heading_deg) % 360, float(speed_knots) * 1852 / 3600)
            try:
                horizon = float(horizon_s)
            except (TypeError, ValueError):
                horizon = PREVIEW_S
            if not math.isfinite(horizon) or horizon <= 0:
                horizon = 0.0
            horizon = min(horizon, PREVIEW_S)
            # Whole integration steps keep every chunk accepted on the same fixed clock.
            remaining_steps = math.floor(horizon / settings.dt_s + 1e-9)
            chunk_steps = max(1, round(PREVIEW_CHUNK_S / settings.dt_s))
            path, settled, stop = [], None, "duration_reached"
            while remaining_steps > 0 and stop == "duration_reached":
                steps = min(chunk_steps, remaining_steps)
                result = self.preview_sim.advance(state, command, steps * settings.dt_s)
                state, stop = result.state, result.stopping_reason
                path += result.trajectory
                remaining_steps -= steps
                if settled is None:
                    settled = next((p.time_s - start for p in path
                                    if abs((p.heading_deg - command.heading_deg + 540) % 360 - 180) < 3), None)
                if settled is not None and path and path[-1].time_s - start >= settled + PREVIEW_CHUNK_S:
                    break
            stride = max(1, round(PREVIEW_TRACK_S / settings.record_period_s))
            return {"from_s": start, "heading_deg": command.heading_deg, "speed_knots": float(speed_knots),
                    "track": [[round(p.lon, 5), round(p.lat, 5), round(p.time_s, 1)]
                              for p in path[stride - 1::stride]],
                    "settled_s": settled, "stop": stop}
        finally:
            self.preview_busy.release()

    # ---------- score ----------
    def _finish(self):
        if self.score is not None:
            return
        s, v = self.session, self.session.visible()
        budget = self.scenario.definition["mission"]["budget_s"]
        escaped = s.outcome == "escaped"
        lost = v["cargo"]["lost_teu"] / v["cargo"]["total_teu"]
        graded = [r for cp in self.checkpoints for r in cp.get("results", {}).values()]
        correct = sum(r["correct"] for r in graded)
        judgement = correct / len(graded) if graded else 0.0
        parts = {
            "escape": round((30 + 10 * v["remaining_s"] / budget) if escaped else 0, 1),
            "cargo": round(40 * max(0.0, 1 - lost / 0.10), 1),  # 10 % TEU lost (grade 5) scores nothing
            "judgement": round(20 * judgement, 1),
        }
        stars = int(escaped) + int(escaped and lost <= 0.01) + int(escaped and lost <= 0.01 and judgement >= 0.7)
        self.score = {"outcome": s.outcome or s.reason, "escaped": escaped, "elapsed_s": v["elapsed_s"],
                      "ship_lost": s.outcome == "ship_lost", "ship": v.get("ship"),
                      "lost_teu": v["cargo"]["lost_teu"], "grade": v["cargo"]["grade"], "answers_correct": correct,
                      "answers_total": len(graded), "parts": parts, "total": round(sum(parts.values()), 1),
                      "stars": stars, "checkpoints": self.checkpoints}
        s.record("score", self.score)
        self._prepare_tutor()

    # ---------- tutor ----------
    def _prepare_tutor(self):
        """Freeze the log into evidence, then answer locally or ask the Tutor off the game-loop thread."""
        try:
            self.tutor_evidence = self._tutor_evidence()
            if self.tutor_agent is None:
                self.tutor_result = fallback_evaluation(self.tutor_evidence)
                return
            self.tutor_result = {"status": "pending", "source": "fallback", "decision": None,
                                 "improvement": None, "sentences": [], "reason": None}
            threading.Thread(target=self._evaluate_tutor, daemon=True).start()
        except Exception:
            self.tutor_result = self._tutor_error()

    def _evaluate_tutor(self, evidence: dict | None = None):
        evidence = evidence if evidence is not None else self.tutor_evidence
        try:
            result = (self.tutor_agent.evaluate(evidence) if self.tutor_agent is not None
                      else fallback_evaluation(evidence))
        except Exception:
            result = self._tutor_fallback(evidence)
        if not isinstance(result, dict) or result.get("status") not in ("ready", "unavailable"):
            result = self._tutor_fallback(evidence)
        with self.lock:
            self.tutor_result = result

    def _tutor_fallback(self, evidence):
        try:
            return fallback_evaluation(evidence)
        except Exception:
            return self._tutor_error()

    @staticmethod
    def _tutor_error() -> dict:
        return {"status": "unavailable", "source": "fallback", "reason": "tutor_error",
                "decision": None, "improvement": None, "sentences": []}

    def _tutor_evidence(self) -> dict:
        """Everything the Tutor may cite: the frozen score, the answered decisions, the orders and the sea."""
        d = self.scenario.definition
        with self.lock:
            view = self.session.visible()
            score = self.score or {}
            samples, log, orders = list(self.samples), list(self.log), list(self.orders)
            checkpoints = [cp for cp in self.checkpoints if cp.get("results")]
        column = {name: FIELDS.index(name) for name in ("t", "roll", "hs", "heading", "wave_from")}

        def sea_point(row):
            return {"t": row[column["t"]], "roll_deg": row[column["roll"]], "hs_m": row[column["hs"]],
                    "heading_deg": row[column["heading"]], "wave_from_deg": row[column["wave_from"]],
                    "encounter": encounter(row[column["heading"]], row[column["wave_from"]])}

        peak = max(samples, key=lambda row: abs(row[column["roll"]]), default=None)
        highest = max(samples, key=lambda row: row[column["hs"]], default=None)
        loss_row = next((entry for entry in log if entry.get("kind") == "cargo_lost"), None)
        first_loss = None
        if loss_row is not None:
            lost_at = loss_row.get("t") or 0.0
            nearest = min(samples, key=lambda row: abs(row[column["t"]] - lost_at), default=None)
            sea = sea_point(nearest) if nearest is not None else {}
            first_loss = {"t": loss_row.get("t"), "teu": loss_row.get("teu"), "demand_g": loss_row.get("demand_g"),
                          "hs_m": sea.get("hs_m"), "encounter": sea.get("encounter"),
                          "heading_deg": sea.get("heading_deg")}
        ship = score.get("ship") or {}
        return {
            "scenario": {"id": d["id"], "title": d["title"], "start": d["start"],
                         "budget_s": d["mission"]["budget_s"]},
            "outcome": {"outcome": score.get("outcome"), "escaped": bool(score.get("escaped")),
                        "elapsed_s": score.get("elapsed_s"), "remaining_s": view["remaining_s"],
                        "ship_lost": bool(score.get("ship_lost")), "lost_teu": score.get("lost_teu"),
                        "total_teu": view["cargo"]["total_teu"], "grade": score.get("grade"),
                        "stars": score.get("stars"), "total": score.get("total")},
            "ship": {"lost": bool(ship.get("lost")), "cause": ship.get("cause"), "time_s": ship.get("time_s")},
            "checkpoints": [{"trigger": cp.get("trigger"), "t": cp.get("t"),
                             "results": dict(cp.get("results") or {}), "reason": cp.get("reason", "")}
                            for cp in checkpoints],
            "orders": [{"t": entry["t"], "heading_deg": entry["heading_deg"],
                        "speed_knots": entry["speed_knots"]} for entry in orders],
            "sea": {"peak_roll": sea_point(peak) if peak is not None else None,
                    "max_hs": sea_point(highest) if highest is not None else None,
                    "first_loss": first_loss},
        }

    # ---------- copilot ----------
    def _copilot_context(self) -> dict:
        """The visible state the Copilot may read, and nothing hidden."""
        v = self.session.visible()
        row = v["vessel"]
        return {
            "scenario": {"id": self.scenario.definition["id"], "title": self.scenario.definition["title"],
                         "start": self.scenario.definition["start"],
                         "budget_s": self.scenario.definition["mission"]["budget_s"]},
            "time": {"elapsed_s": v["elapsed_s"], "remaining_s": v["remaining_s"], "utc": v["utc"]},
            "vessel": {key: row[key] for key in (
                "lon", "lat", "heading_deg", "speed_through_water_mps", "speed_over_ground_mps",
                "course_over_ground_deg", "roll_deg", "pitch_deg", "heave_m", "hs_m", "tp_s", "wave_from_deg",
                "current_east_mps", "current_north_mps")},
            "target": {"heading_deg": v["target_heading_deg"], "speed_knots": v["target_speed_knots"]},
            "cargo": {"lost_teu": v["cargo"]["lost_teu"], "total_teu": v["cargo"]["total_teu"],
                      "grade": v["cargo"]["grade"]},
            "sector": list(self.session.rules.sector(row["lon"], row["lat"])),
            "reports": v["reports"],
            "encounter": encounter(row["heading_deg"], row["wave_from_deg"]),
            "orders": self.orders[-5:],
        }

    def copilot_advice(self, mode: str) -> dict:
        """One advisory answer; the model call runs with the game lock released."""
        if mode not in ("l1", "l2"):
            raise ValueError("unknown copilot mode")

        def unavailable(reason: str) -> dict:
            text = "Copilot is not enabled on this server." if reason == "agent_disabled" else "Copilot is unavailable."
            return {"status": "unavailable", "mode": mode, "source": "local", "text": text,
                    "route": None, "reason": reason, "at_s": None}

        if self.copilot is None:
            return unavailable("agent_disabled")
        with self.lock:
            if self.score is not None:
                return unavailable("mission_ended")
            context = self._copilot_context()
        try:
            return self.copilot.advise(mode, context, self.solutions)
        except Exception:  # the model path must never break the page: say so and carry on
            return unavailable("agent_error")

    def state(self, since: float) -> dict:
        with self.lock:
            s, v = self.session, self.session.visible()
            pending = None
            if self.pending:
                pending = {k: self.pending[k] for k in ("id", "trigger", "message", "t")}
                pending["questions"] = [{k: q[k] for k in ("id", "prompt", "options")} for q in self.pending["questions"]]
            vessel = v["vessel"]
            v["ship_sector"] = list(s.rules.sector(vessel["lon"], vessel["lat"]))  # which forecast area the ship is in
            return {"samples": [row for row in self.samples if row[0] > since], "view": v, "rate": self.rate,
                    "paused": s.pause_signal.is_set(), "checkpoint": pending, "score": self.score,
                    "tutor": self.tutor_result,
                    "log": [e for e in self.log if e["t"] is not None and e["t"] > since]}

    def briefing(self) -> dict:
        d = self.scenario.definition
        storm = d["collection"]["storm"]
        return {"id": d["id"], "title": d["title"], "start": d["start"], "budget_s": d["mission"]["budget_s"],
                "initial": d["initial"], "area_bbox": d["area_bbox"],
                "field_bbox": list(self.session.rules.report_bbox), "voyage": d.get("voyage"),
                "storm": {k: storm[k] for k in ("peak", "area", "hs_m", "max_wind_kn")},
                "fields": FIELDS, "cargo": {"deck_units": 320, "total_teu": 1200,
                "units": [{"id": unit, "teu": 1 + stack % 2, "stack": stack, "tier": tier}
                          for stack in range(STACKS) for tier, unit in enumerate(stack_unit_ids(stack))]},
                "objective": "Leave the storm's adverse-weather area before time runs out, and lose as little cargo as you can.",
                "copilot": {"l1": bool(self.copilot), "l2": bool(self.copilot and self.solutions is not None)},
                "notes": "Forecast cards are generated from the historical reconstruction and labelled as game forecasts."}


def validated(folder: Path) -> bool:
    """Playable only with a passing validation report for exactly these scenario files (docs/STORM_SCENARIOS.md)."""
    from game.scenario import digest
    report = folder / "validation.json"
    if not report.exists():
        return False
    v = json.loads(report.read_text())
    return (v.get("passed") is True and v.get("definition_sha256") == digest(folder / "scenario.json")
            and v.get("gameplay_sha256") == digest(folder / "gameplay.json"))


GAME: Game | None = None
UNVALIDATED = False  # --unvalidated: also list scenarios without a passing report, marked as such (playtests only)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def log_message(self, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")  # pages and modules change often; never play a stale script
        super().end_headers()

    def _json(self, value, status=200):
        body = json.dumps(value, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/scenarios":
            items = []
            for path in sorted(SCENARIOS.glob("*/scenario.json")):
                d = json.loads(path.read_text())
                ok = validated(path.parent)
                if "storm" in d.get("collection", {}) and (ok or UNVALIDATED):
                    title = d["title"] if ok else f"[unverified] {d['title']}"
                    items.append({"id": d["id"], "title": title, "start": d["start"], "validated": ok})
            return self._json(items)
        if url.path == "/api/state":
            if GAME is None:
                return self._json({"error": "no game"}, 404)
            return self._json(GAME.state(float(parse_qs(url.query).get("since", ["-1"])[0])))
        return super().do_GET()

    def do_POST(self):
        global GAME
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
        try:
            if self.path == "/api/start":
                if GAME is not None:
                    GAME.stopped = True
                GAME = Game(body["scenario"], agent_settings=AGENT_SETTINGS)
                return self._json(GAME.briefing())
            if GAME is None:
                return self._json({"error": "no game"}, 404)
            if self.path == "/api/order":
                GAME.order(body["heading_deg"], body["speed_knots"])
            elif self.path == "/api/pause":
                GAME.pause(bool(body.get("paused", True)))
            elif self.path == "/api/rate":
                GAME.rate = float(body["rate"]) if float(body["rate"]) in RATES else GAME.rate
            elif self.path == "/api/answer":
                return self._json(GAME.answer(body))
            elif self.path == "/api/preview":
                return self._json(GAME.preview(body["heading_deg"], body["speed_knots"]))
            elif self.path == "/api/copilot":
                return self._json(GAME.copilot_advice(str(body.get("mode", ""))))
            else:
                return self._json({"error": "unknown"}, 404)
            return self._json({"ok": True})
        except (ValueError, KeyError) as error:
            return self._json({"error": str(error)}, 400)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--unvalidated", action="store_true",
                        help="also offer scenarios whose validation is missing, failed or out of date (playtests only)")
    parser.add_argument("--agent", action="store_true", help="enable Copilot and Tutor model calls")
    parser.add_argument("--models", default="models.json")
    parser.add_argument("--keys", default="config/api_keys.local.json")
    args = parser.parse_args()
    global UNVALIDATED, AGENT_SETTINGS
    UNVALIDATED = args.unvalidated
    if args.agent:
        settings = AgentSettings(Path(args.models), Path(args.keys))
        for probe in (Copilot(models_path=settings.models_path, keys_path=settings.keys_path),
                      Tutor(models_path=settings.models_path, keys_path=settings.keys_path)):
            probe.close()  # a missing key or profile fails here, before the server accepts a click
        AGENT_SETTINGS = settings
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"TIDE live play: http://localhost:{args.port}/game/ (pick a 'Play:' scenario)")
    server.serve_forever()


if __name__ == "__main__":
    main()
