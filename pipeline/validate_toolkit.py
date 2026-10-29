"""Assess coarse motion with real fields and controlled input ablations."""

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import numpy as np

from game.simulation.environment import CopernicusEnvironment
from game.simulation.runtime import Command, Simulation
from game.simulation.vessel import load_s175

ROOT = Path(__file__).resolve().parents[1]


class TrialEnvironment:
    def __init__(self, source, changes):
        self.source, self.changes = source, changes

    def sample(self, time, lon, lat):
        sea = self.source.sample(time, lon, lat)
        return replace(sea, **self.changes(sea))


def assess(seconds=120):
    """Ablations are counterfactual tests, not alternative historical reconstructions."""
    start = datetime(2026, 9, 9, 8, tzinfo=timezone.utc)
    variants = {
        "historical": lambda s: {},
        "still_air": lambda s: dict(wind_east_mps=0, wind_north_mps=0),
        "double_wind": lambda s: dict(wind_east_mps=2*s.wind_east_mps, wind_north_mps=2*s.wind_north_mps),
        "no_waves": lambda s: dict(hs=0),
        "higher_waves": lambda s: dict(hs=1.5*s.hs),
        "rotated_waves": lambda s: dict(wave_from_deg=(s.wave_from_deg+90)%360),
        "longer_period": lambda s: dict(tp=1.2*s.tp),
        "no_current": lambda s: dict(current_east_mps=0, current_north_mps=0),
        "warmer_water": lambda s: dict(sst_c=s.sst_c+5),
        "new_heading": lambda s: {},
        "new_speed": lambda s: {},
    }
    vessel = load_s175()
    results = {}
    with CopernicusEnvironment.from_directory(ROOT / "data/copernicus") as source:
        inputs = asdict(source.sample(start, 3.5, 40))
        available_sources = tuple(source.provenance)
        for name, changes in variants.items():
            sim = Simulation(vessel, TrialEnvironment(source, changes), seed=1)
            state = sim.initial_state(start, 3.5, 40, heading_deg=90, speed_mps=2)
            command = Command(0 if name == "new_heading" else 90, 1 if name == "new_speed" else 3)
            result = sim.advance(state, command, seconds)
            last = result.trajectory[-1] if result.trajectory else None
            results[name] = {
                "stopping_reason": result.stopping_reason,
                "advanced_s": result.advanced_s,
                "command": asdict(command),
                "final_x": result.state.x.tolist(),
                "final_sample": asdict(last) if last else None,
                "heading_error_deg": ((command.heading_deg-last.heading_deg+180)%360-180) if last else None,
                "speed_error_mps": command.speed_mps-last.speed_through_water_mps if last else None,
                "exposure": asdict(result.state.exposure),
                "wave_flags": result.state.wave_flags,
            }
    baseline = np.asarray(results["historical"]["final_x"])
    differences = {name: float(np.linalg.norm(np.asarray(r["final_x"])[6:8]-baseline[6:8]))
                   for name, r in results.items()}
    changed = lambda name: not np.allclose(results[name]["final_x"], baseline, rtol=0, atol=1e-10)
    checks = {
        "all_trials_completed": all(r["stopping_reason"] == "duration_reached" for r in results.values()),
        "all_states_finite": all(np.all(np.isfinite(r["final_x"])) for r in results.values()),
        "all_six_environment_groups_available": ("wind" in available_sources and "temperature" in available_sources
                                                   and all(v is not None and np.isfinite(v) for v in inputs.values())),
        "wind_affects_motion": changed("still_air") and changed("double_wind"),
        "wave_height_affects_response": changed("higher_waves") and changed("no_waves"),
        "wave_direction_affects_response": changed("rotated_waves"),
        "wave_period_affects_response": changed("longer_period"),
        "current_affects_motion": changed("no_current"),
        "heading_command_affects_motion": changed("new_heading"),
        "speed_command_affects_motion": changed("new_speed"),
        "sst_is_evidence_only": np.array_equal(results["warmer_water"]["final_x"], baseline),
    }
    return {"scope": "Coarse functional response at one historical start; not control tracking or physical validation",
            "historical_start_utc": start.isoformat(), "initial_lon_lat": [3.5, 40],
            "available_sources": available_sources,
            "source_inputs": inputs, "checks": checks, "coarse_functional_pass": all(checks.values()),
            "horizontal_endpoint_difference_m": differences, "trials": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--output", type=Path, default=ROOT / "data/runs/toolkit-assessment.json")
    args = parser.parse_args()
    started = time.perf_counter()
    report = assess(args.seconds)
    report["wall_seconds"] = time.perf_counter()-started
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
    print(json.dumps({"checks": report["checks"], "wall_seconds": report["wall_seconds"]}, indent=2))
    if not report["coarse_functional_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
