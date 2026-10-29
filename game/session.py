"""Authoritative local execution, information projection and explicit snapshots."""

from __future__ import annotations

import hashlib
import json
import math
import queue
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

import numpy as np

from game.cargo import CargoModel
from game.rules.mission import MissionRules
from game.scenario import utc
from game.simulation.environment import EnvironmentError
from game.simulation.runtime import Command, ControlState, Exposure, State
from game.simulation.waves import WaveState


class OperatingEnvironment:
    """Reject scenario-domain endpoints before a numerical step is committed."""

    def __init__(self, source, bbox):
        self.source, self.bbox = source, bbox

    def sample(self, time, lon, lat):
        west, east, south, north = self.bbox
        if not west <= lon <= east or not south <= lat <= north:
            raise EnvironmentError(
                "domain_boundary", "Outside the authored operating area"
            )
        return self.source.sample(time, lon, lat)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    )
    temp.replace(path)


def motion_dict(state):
    d = asdict(state)
    d["start_time"] = state.start_time.isoformat()
    for key in ("x", "wave_force", "wind_force"):
        d[key] = d[key].tolist()
    for key in ("phase", "previous_rate"):
        if d["wave"][key] is not None:
            d["wave"][key] = d["wave"][key].tolist()
    return d


def motion_state(d):
    d = deepcopy(d)
    d["start_time"] = utc(d["start_time"])
    for key in ("x", "wave_force", "wind_force"):
        d[key] = np.array(d[key], dtype=float)
    for key in ("phase", "previous_rate"):
        if d["wave"][key] is not None:
            d["wave"][key] = np.array(d["wave"][key], dtype=float)
    d["wave"] = WaveState(**d["wave"])
    d["control"] = ControlState(**d["control"])
    d["exposure"] = Exposure(**d["exposure"])
    d["command"] = Command(**d["command"]) if d["command"] else None
    d["active_risks"] = tuple(d["active_risks"])
    d["wave_flags"] = tuple(d["wave_flags"])
    return State(**d)


class Session:
    def __init__(
        self,
        scenario,
        simulation,
        gameplay,
        directory,
        *,
        seed=None,
        restore=False,
        cargo_model=None,
        rules=None,
    ):
        self.scenario, self.sim = scenario, simulation
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.snapshot_path = self.directory / "state.json"
        self.log_path = self.directory / "events.jsonl"
        self.lock = threading.RLock()
        self.control_lock = threading.Lock()
        self.pause_signal = threading.Event()
        self.pause_signal.set()
        self.seed = scenario.definition["seed"] if seed is None else seed
        self.cargo_model = cargo_model or CargoModel(
            gameplay.get("cargo"), seed=self.seed
        )
        self.rules = rules or MissionRules(
            gameplay,
            scenario.definition["mission"]["budget_s"],
            scenario.definition["area_bbox"],
        )
        self.sim.environment = OperatingEnvironment(
            self.sim.environment, scenario.definition["area_bbox"]
        )
        self.fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "scenario": scenario.definition,
                    "gameplay": gameplay,
                    "motion": self.sim.algorithm_id,
                    "cargo": self.cargo_model.algorithm_id,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        self.session_id = uuid.uuid4().hex
        self.state = scenario.initial_state(simulation)
        self.cargo = self.cargo_model.initial_state()
        self.consumed = set()
        self.order = Command(
            scenario.definition["initial"]["heading_deg"],
            scenario.definition["initial"]["speed_mps"],
        )
        self.revision, self.rate = 0, 20.0
        self.achieved_rate = 0.0
        self.outcome, self.reason = None, "initial"
        self.last_events = []
        self.report_mode = "fixed"
        self.review_callback = None
        self.delivered_reports = set()
        self.attention_receipts = set()
        self.attention_notice = None
        self.notifications = queue.SimpleQueue()
        self.receipts = {}
        self.resume_authorized = False
        self.player_epoch = 0
        self.last_sample = asdict(self.sim.observe(self.state))
        if restore:
            self.restore()
        else:
            if self.snapshot_path.exists():
                raise ValueError(
                    "Session already exists; use --resume or a new directory"
                )
            self.record("session_started", {"seed": self.seed})
            self.save()

    def record(self, kind, data):
        with self.lock, self.log_path.open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "revision": self.revision,
                        "time_s": self.state.tick * self.sim.settings.dt_s,
                        "kind": kind,
                        "data": data,
                    },
                    ensure_ascii=False,
                    allow_nan=False,
                )
                + "\n"
            )

    def save(self):
        with self.lock:
            atomic_json(
                self.snapshot_path,
                {
                    "version": 1,
                    "fingerprint": self.fingerprint,
                    "session_id": self.session_id,
                    "seed": self.seed,
                    "revision": self.revision,
                    "state": motion_dict(self.state),
                    "cargo": self.cargo,
                    "consumed": sorted(self.consumed),
                    "order": asdict(self.order),
                    "rate": self.rate,
                    "outcome": self.outcome,
                    "reason": self.reason,
                    "receipts": self.receipts,
                    "last_sample": self.last_sample,
                    "last_events": self.last_events,
                    "delivered_reports": sorted(self.delivered_reports),
                    "attention_receipts": sorted(self.attention_receipts),
                    "attention_notice": self.attention_notice,
                    "last_attention_s": getattr(self, "last_attention_s", -600),
                    "log_size": self.log_path.stat().st_size
                    if self.log_path.exists()
                    else 0,
                },
            )

    def restore(self):
        d = json.loads(self.snapshot_path.read_text())
        if (
            d["version"] != 1
            or d["fingerprint"] != self.fingerprint
            or d["seed"] != self.seed
        ):
            raise ValueError(
                "Saved scenario, algorithm configuration or seed does not match"
            )
        if self.log_path.stat().st_size < d["log_size"]:
            raise ValueError("Session log is incomplete")
        with self.log_path.open("r+b") as stream:
            stream.truncate(d["log_size"])
        self.state, self.cargo = motion_state(d["state"]), d["cargo"]
        self.consumed, self.order = set(d["consumed"]), Command(**d["order"])
        for key in (
            "session_id",
            "revision",
            "rate",
            "outcome",
            "reason",
            "receipts",
            "last_sample",
        ):
            setattr(self, key, d[key])
        self.last_events = d.get("last_events", [])
        self.delivered_reports = set(d.get("delivered_reports", []))
        self.attention_receipts = set(d.get("attention_receipts", []))
        self.attention_notice = d.get("attention_notice")
        self.last_attention_s = d.get("last_attention_s", -600)
        for key, receipt in self.receipts.items():
            if receipt.get("pending"):
                self.receipts[key] = {
                    "interrupted": True,
                    "revision": self.revision,
                    "paused": True,
                }
        self.save()

    def pause(self, *, manual=False):
        with self.control_lock:
            if manual:
                self.player_epoch += 1
            self.pause_signal.set()
            self.resume_authorized = False

    def set_order(self, heading=None, speed_knots=None):
        self.pause(manual=True)
        with self.lock:
            if self.outcome:
                raise ValueError("Mission has ended")
            command = Command(
                self.order.heading_deg if heading is None else heading,
                self.order.speed_mps
                if speed_knots is None
                else speed_knots * 1852 / 3600,
            )
            if (
                not math.isfinite(command.heading_deg)
                or not 0 <= command.heading_deg < 360
            ):
                raise ValueError("Heading must be in [0,360) degrees")
            self.sim.validate_command(command)
            self.order = command
            self.revision += 1
            self.record("player_order", asdict(command))
            self.save()

    def authorize_resume(self, *, expected_epoch=None):
        with self.lock, self.control_lock:
            if expected_epoch is not None and self.player_epoch != expected_epoch:
                return False
            if self.outcome:
                raise ValueError("Mission has ended")
            if expected_epoch is None:
                self.player_epoch += 1
            self.resume_authorized = True
            self.pause_signal.clear()
            return True

    def visible(self):
        with self.lock:
            elapsed = self.state.tick * self.sim.settings.dt_s
            return {
                "session_id": self.session_id,
                "revision": self.revision,
                "utc": (self.state.start_time + timedelta(seconds=elapsed)).isoformat(),
                "elapsed_s": elapsed,
                "remaining_s": max(0, self.rules.budget_s - elapsed),
                "paused": self.pause_signal.is_set(),
                "rate": "max" if self.rate == 0 else self.rate,
                "achieved_rate": self.achieved_rate,
                "target_heading_deg": self.order.heading_deg,
                "target_speed_knots": self.order.speed_mps * 3600 / 1852,
                "vessel": dict(self.last_sample),
                "cargo": self.cargo_model.summary(self.cargo),
                "reports": [
                    r
                    for r in self.rules.visible_reports(
                        elapsed, *self.sim.position(self.state)
                    )
                    if self.report_mode == "fixed" or r["id"] in self.delivered_reports
                ],
                "attention_notice": self.attention_notice,
                "events": list(self.last_events),
                "outcome": self.outcome,
                "stopping_reason": self.reason,
            }

    def director_context(self):
        with self.lock:
            view = self.visible()
            available = self.rules.visible_reports(
                view["elapsed_s"], *self.sim.position(self.state)
            )
            view["pending_reports"] = [
                r for r in available if r["id"] not in self.delivered_reports
            ]
            return view

    def apply_attention(self, decision, *, review_id, expected_epoch, observed_s):
        with self.lock, self.control_lock:
            if review_id in self.attention_receipts:
                return {"duplicate": True}
            now = self.state.exposure.elapsed_s
            if (
                self.report_mode != "director"
                or self.player_epoch != expected_epoch
                or self.outcome
                or self.pause_signal.is_set()
                or now - observed_s > 600
            ):
                raise ValueError(
                    "Attention decision is stale or player control superseded it"
                )
            if set(decision) != {"report_ids", "pause", "reason", "evidence_ids"}:
                raise ValueError("Invalid attention decision fields")
            ids, evidence = decision["report_ids"], decision["evidence_ids"]
            if not isinstance(ids, list) or not all(isinstance(v, str) for v in ids):
                raise ValueError("Report IDs must be strings")
            if not isinstance(evidence, list) or not all(
                isinstance(v, str) for v in evidence
            ):
                raise ValueError("Evidence IDs must be strings")
            available_reports = {
                r["id"]: r
                for r in self.rules.visible_reports(now, *self.sim.position(self.state))
            }
            available = set(available_reports)
            if not set(ids) <= available or not set(evidence) <= available | {
                "current_observation"
            }:
                raise ValueError(
                    "Only available reports/current observation may support an attention decision"
                )
            if len(ids) > 9 or len(evidence) > 10:
                raise ValueError("Attention decision exceeds evidence budget")
            if decision["pause"] and not (
                set(evidence) - {"current_observation"}
            ) <= self.delivered_reports | set(ids):
                raise ValueError("Publish the reports supporting the attention request")
            if (
                not isinstance(decision["pause"], bool)
                or not isinstance(decision["reason"], str)
                or len(decision["reason"]) > 500
            ):
                raise ValueError("Invalid pause or reason")
            if decision["pause"] and not evidence:
                raise ValueError("Attention pause requires evidence")
            if (
                decision["pause"]
                and now - getattr(self, "last_attention_s", -600) < 300
            ):
                raise ValueError("Attention pause cooldown has not elapsed")
            newly_delivered = [v for v in ids if v not in self.delivered_reports]
            self.delivered_reports.update(ids)
            self.attention_receipts.add(review_id)
            if decision["pause"]:
                self.pause_signal.set()
                self.resume_authorized = False
                self.last_attention_s = now
                self.reason = "director_attention"
                self.attention_notice = dict(decision, time_s=now)
            self.record(
                "director_attention", dict(decision, review_id=review_id, time_s=now)
            )
            self.save()
            if newly_delivered or decision["pause"]:
                self.notifications.put(
                    {
                        "kind": "director_attention",
                        "report_ids": newly_delivered,
                        "reports": [available_reports[v] for v in newly_delivered],
                        "paused": decision["pause"],
                        "reason": decision["reason"],
                    }
                )
            return {"published": ids, "paused": decision["pause"]}

    def publish_fixed_reports(self, expected_epoch=None):
        with self.lock:
            if expected_epoch is not None and self.player_epoch != expected_epoch:
                return
            reports = self.rules.visible_reports(
                self.state.exposure.elapsed_s, *self.sim.position(self.state)
            )
            fresh = [r["id"] for r in reports if r["id"] not in self.delivered_reports]
            self.delivered_reports.update(fresh)
            if fresh:
                self.record("fixed_report_delivery", {"report_ids": fresh})
                self.save()
                self.notifications.put(
                    {
                        "kind": "weather_reports",
                        "report_ids": fresh,
                        "reports": [r for r in reports if r["id"] in fresh],
                        "paused": False,
                    }
                )

    def advance_segment(self, duration_s=1.0):
        with self.lock:
            if self.outcome or self.pause_signal.is_set():
                return self.visible()
            cargo, consumed = deepcopy(self.cargo), set(self.consumed)
            events = []

            def step(sample, dt):
                before = self.cargo_model.summary(cargo)["grade"]
                events.extend(self.cargo_model.advance(cargo, sample, dt))
                reason, new = self.rules.evaluate(sample, consumed)
                events.extend(new)
                if reason and reason != "weather_report":
                    return reason
                if self.cargo_model.summary(cargo)["grade"] > before:
                    return "cargo_grade_increased"
                return None

            result = self.sim.advance(
                self.state,
                self.order,
                duration_s,
                should_pause=self.pause_signal.is_set,
                on_step=step,
            )
            self.state, self.cargo, self.consumed = result.state, cargo, consumed
            if result.trajectory:
                self.last_sample = asdict(result.trajectory[-1])
            self.reason = result.stopping_reason
            self.revision += 1
            events.extend(
                asdict(event)
                for event in result.events
                if not (
                    event.kind == self.reason
                    and any(
                        e["kind"] == event.kind and e["time_s"] == event.time_s
                        for e in events
                    )
                )
            )
            self.last_events = events
            if self.reason != "duration_reached":
                self.pause()
                if self.reason in (
                    "escaped",
                    "escape",
                    "mission_complete",
                    "timeout",
                    "budget_reached",
                ):
                    self.outcome = self.reason
            self.record(
                "advance",
                {
                    "advanced_s": result.advanced_s,
                    "events": events,
                    "stopping_reason": self.reason,
                    "vessel": self.last_sample,
                    "cargo": self.cargo_model.summary(cargo),
                },
            )
            self.save()
            if self.report_mode == "fixed":
                self.publish_fixed_reports()
            return self.visible()

    def tool(self, name, args, operation_id, expected_revision):
        if args:
            raise ValueError("This tool accepts no model-selected arguments")
        with self.lock:
            if name in (
                "read_status",
                "read_reports",
                "request_decision",
                "finish_mission",
            ):
                if name == "finish_mission" and not self.outcome:
                    raise ValueError("Mission has no terminal outcome")
                if name == "request_decision":
                    self.pause()
                value = self.visible()
                return (
                    {"reports": value["reports"]} if name == "read_reports" else value
                )
            if name != "advance":
                raise ValueError("Unknown tool")
            if operation_id in self.receipts:
                return self.receipts[operation_id]
            if (
                expected_revision != self.revision
                or not self.resume_authorized
                or self.pause_signal.is_set()
            ):
                raise ValueError(
                    "Stale revision or no current player resume authorization"
                )
            self.receipts[operation_id] = {"pending": True, "revision": self.revision}
            self.save()
        try:
            while not self.pause_signal.is_set() and not self.outcome:
                start = time.monotonic()
                before = self.state.exposure.elapsed_s
                self.advance_segment()
                if self.review_callback and not self.pause_signal.is_set():
                    self.review_callback()
                if self.rate:
                    self.pause_signal.wait(
                        max(0, 1 / self.rate - (time.monotonic() - start))
                    )
                self.achieved_rate = (self.state.exposure.elapsed_s - before) / max(
                    1e-9, time.monotonic() - start
                )
            value = self.visible()
        except Exception:
            self.pause()
            self.save()
            raise
        with self.lock:
            self.receipts[operation_id] = value
            self.save()
        return value
