"""Run a short S175 integration exercise, not a scored mission."""

import argparse
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import time

from .environment import CopernicusEnvironment
from .runtime import Command, Settings, Simulation
from .vessel import load_s175


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--time", default="2026-09-09T08:00:00+00:00")
    parser.add_argument("--lon", type=float, default=3.5)
    parser.add_argument("--lat", type=float, default=40.0)
    parser.add_argument("--heading", type=float, default=90)
    parser.add_argument("--speed", type=float, default=3, help="Target speed through water, m/s")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--actuator-model", choices=["teaching", "son_nomoto", "s175_maneuvering"], default="teaching")
    parser.add_argument("--output", type=Path, default=Path("data/runs/toolkit-demo.json"))
    args = parser.parse_args()
    started = time.perf_counter()
    with CopernicusEnvironment.from_directory(Path(__file__).resolve().parents[2] / "data/copernicus") as env:
        sim = Simulation(load_s175(), env, seed=args.seed, settings=Settings(actuator_model=args.actuator_model))
        state = sim.initial_state(datetime.fromisoformat(args.time), args.lon, args.lat,
                                  heading_deg=args.heading, speed_mps=min(2, args.speed))
        result = sim.advance(state, Command(args.heading, args.speed), args.seconds)
    payload = {
        "model": sim.algorithm_id,
        "seed": args.seed,
        "start_time": state.start_time.isoformat(),
        "settings": asdict(sim.settings),
        "wind_parameters": asdict(sim.wind_parameters) if sim.wind_parameters is not None else None,
        "command": asdict(result.state.command),
        "stopping_reason": result.stopping_reason,
        "advanced_s": result.advanced_s,
        "exposure": asdict(result.state.exposure),
        "wave_flags": result.state.wave_flags,
        "retained_wave_energy": result.state.retained_wave_energy,
        "final_x": result.state.x.tolist(),
        "trajectory": [asdict(sample) for sample in result.trajectory],
        "events": [asdict(event) for event in result.events],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")
    print(f"{result.stopping_reason}: {result.advanced_s:g} simulated seconds; "
          f"{time.perf_counter()-started:.2f} wall seconds; {args.output}")


if __name__ == "__main__":
    main()
