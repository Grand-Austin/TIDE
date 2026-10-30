"""Measure the S175 hull with Son–Nomoto actuators; no mission or IMO pass claim."""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np

from game.simulation import actuators
from game.simulation.environment import SeaState
from game.simulation.runtime import Command, Settings, Simulation
from game.simulation.vessel import load_s175


class CalmWater:
    def sample(self, time, lon, lat):
        return SeaState(0, 8, 0)


def trial(name, *, seconds=600, dt=.1, speed=5, yaw_frequency=.08):
    sim = Simulation(load_s175(), CalmWater(), wind_parameters=None,
                     settings=Settings(actuator_model="son_nomoto", dt_s=dt,
                                       wave_period_s=.2, yaw_natural_frequency=yaw_frequency))
    state = sim.initial_state(datetime(2026, 1, 23, tzinfo=timezone.utc), -8, 47, speed_mps=speed)
    if name.startswith("rudder_"):
        order = actuators.RUDDER_LIMIT_RAD * (-1 if name == "rudder_left" else 1)
        rpm = state.control.shaft_rpm

        def open_loop(candidate, command, sea):
            candidate.control.rudder_order_rad = order
            candidate.control.shaft_order_rpm = rpm
            return actuators.loads(sim._actuator_velocity(candidate.x, sea), candidate.control.rudder_rad,
                                   candidate.control.shaft_rpm, rho=sim.vessel.main["rho"])

        sim._control = open_loop
    command = Command(90 if name == "heading90" else 10 if name == "heading10" else 0, speed)
    initial_force = actuators.loads([speed, 0, 0, 0, 0, 0], actuators.RUDDER_LIMIT_RAD,
                                    state.control.shaft_rpm, rho=sim.vessel.main["rho"])
    result = sim.advance(state, command, seconds)
    samples = [asdict(sim.observe(state)), *map(asdict, result.trajectory)]
    headings = np.unwrap(np.radians([p["heading_deg"] for p in samples]))
    turn = np.degrees(headings-headings[0])
    row = {
        "name": name, "algorithm": sim.algorithm_id, "settings": asdict(sim.settings),
        "scope": "Calm water, no wind/current; source actuator geometry with unchanged S175 hull; not IMO certification",
        "initial_speed_mps": speed, "initial_shaft_rpm": state.control.shaft_rpm,
        "initial_10deg_actuator_force_si": initial_force.tolist(),
        "advanced_s": result.advanced_s, "stop": result.stopping_reason,
        "net_heading_change_deg": float(turn[-1]),
        "final_yaw_rate_deg_min": np.degrees(result.state.x[5])*60,
        "final_speed_mps": samples[-1]["speed_through_water_mps"],
        "max_abs_roll_deg": max(abs(p["roll_deg"]) for p in samples),
        "time_to_90_s": next((p["time_s"] for p, a in zip(samples, turn) if abs(a) >= 90), None),
        "time_to_180_s": next((p["time_s"] for p, a in zip(samples, turn) if abs(a) >= 180), None),
        "final_heading_error_deg": (command.heading_deg-samples[-1]["heading_deg"]+180)%360-180,
        "matrix_sha256": {k: hashlib.sha256(getattr(sim.vessel, k).tobytes()).hexdigest()
                          for k in ("M", "MA", "D", "G")},
        "events": [asdict(e) for e in result.events],
    }
    return row, samples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/runs/actuator-validation"))
    parser.add_argument("--cases", nargs="+", choices=["rudder_left", "rudder_right", "heading90", "heading10"],
                        default=["rudder_left", "rudder_right", "heading90"])
    parser.add_argument("--seconds", type=float, default=600)
    parser.add_argument("--yaw-frequency", type=float, default=.08)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    for name in args.cases:
        row, samples = trial(name, seconds=args.seconds, yaw_frequency=args.yaw_frequency)
        (args.output / f"{name}.json").write_text(json.dumps({"result": row, "trajectory": samples}, indent=2)+"\n")
        rows.append(row)
        print(json.dumps(row), flush=True)
    (args.output / "report.json").write_text(json.dumps(rows, indent=2)+"\n")
    if any(row["stop"] != "duration_reached" for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
