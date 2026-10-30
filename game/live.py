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
import json
import math
import threading
import time
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

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
CHECKPOINT_GAP_S = 600.0  # game seconds after one decision before the next opens; the briefing does not count
PREVIEW_S = 480.0  # longest helm preview
PREVIEW_CHUNK_S = 60.0  # the preview stops a chunk after the ship settles on the ordered heading
PREVIEW_TRACK_S = 10.0  # one preview track point per this much game time
FIELDS = ["t", "lon", "lat", "heading", "roll", "pitch", "heave", "stw", "hs", "tp", "wave_from",
          "wind_e", "wind_n", "current_e", "current_n", "lost_teu", "grade", "cloud", "precip", "turn_rate"]
ENCOUNTERS = ["Head seas", "Bow-quartering seas", "Beam seas", "Stern-quartering seas", "Following seas"]
COMPASS = {(0, 1): "North", (1, 1): "North-east", (1, 0): "East", (1, -1): "South-east",
           (0, -1): "South", (-1, -1): "South-west", (-1, 0): "West", (-1, 1): "North-west"}
STAY = "No neighbouring area is forecast lower"


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
    def __init__(self, scenario_id: str):
        self.scenario = load_scenario(SCENARIOS / scenario_id / "scenario.json")
        d = self.scenario.definition
        gameplay = json.loads((ROOT / d["gameplay"]["path"]).read_text())
        self._environment = self.scenario.environment()
        env = self._environment.__enter__()
        out = ROOT / "data" / "runs" / f"live-{scenario_id}-{datetime.now():%Y%m%d-%H%M%S}"
        self.session = Session(self.scenario, self.scenario.simulation(env), gameplay, out)
        weather = SCENARIOS / scenario_id / "weather.json"
        self.weather = json.loads(weather.read_text())["hourly"] if weather.exists() else []
        self.lock = threading.RLock()
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
                cp["order"] = order
            self.checkpoints.append(cp)
            self.session.record("checkpoint", cp)
            self.pending = None
            if not self.session.outcome:
                self.session.authorize_resume()
            return cp["results"]

    # ---------- player controls ----------
    def order(self, heading_deg: float, speed_knots: float):
        with self.lock:
            paused = self.session.pause_signal.is_set()
            self.session.set_order(float(heading_deg) % 360, float(speed_knots))
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
                    "log": [e for e in self.log if e["t"] is not None and e["t"] > since]}

    def briefing(self) -> dict:
        d = self.scenario.definition
        storm = d["collection"]["storm"]
        return {"id": d["id"], "title": d["title"], "start": d["start"], "budget_s": d["mission"]["budget_s"],
                "initial": d["initial"], "area_bbox": d["area_bbox"], "storm": {k: storm[k] for k in ("peak", "area", "hs_m", "max_wind_kn")},
                "fields": FIELDS, "cargo": {"deck_units": 320, "total_teu": 1200,
                "units": [{"id": unit, "teu": 1 + stack % 2, "stack": stack, "tier": tier}
                          for stack in range(STACKS) for tier, unit in enumerate(stack_unit_ids(stack))]},
                "objective": "Leave the storm's adverse-weather area before time runs out, and lose as little cargo as you can.",
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
                GAME = Game(body["scenario"])
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
    args = parser.parse_args()
    global UNVALIDATED
    UNVALIDATED = args.unvalidated
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"TIDE live play: http://localhost:{args.port}/game/ (pick a 'Play:' scenario)")
    server.serve_forever()


if __name__ == "__main__":
    main()
