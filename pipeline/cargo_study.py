"""Offline cargo sensitivity study on synthetic S175 motion, not accident replay."""

import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import itertools
import json
import math
from pathlib import Path
import platform
import statistics
import time
import tracemalloc
from types import SimpleNamespace

import numpy as np

from archive.game.cargo import CargoModel as LegacyCargoModel
from archive.pipeline.cargo_study import candidate_loss, legacy_loss
from archive.pipeline.cargo_stacks import StackCargo as ReferenceStackCargo
from game.cargo import CargoModel
from game.simulation.runtime import Command, Settings, Simulation
from game.simulation.vessel import OUTPUT, load_s175
from pipeline.cargo_stacks import EFFECTIVE_HEIGHT_M, stack_loss
from pipeline.validate_storm import ConstantSea


# Authored total seas. Reported wind-sea and swell heights must not be added.
CASES = {
    "calm": (0, 11.5, 90, 3),
    "moderate-beam": (3, 8, 90, 3),
    "6m-head": (6, 11.5, 0, 3),
    "6m-beam": (6, 11.5, 90, 3),
    "6m-beam-12kn": (6, 11.5, 90, 12 * 1852 / 3600),
    "6m-quarter": (6, 11.5, 135, 3),
    "6m-swell-quarter": (6, 15, 135, 10 * 1852 / 3600),
    "8m-head": (8.2, 11.5, 0, 3),
    "8m-beam": (8.2, 11.5, 90, 3),
    "8m-following": (8.2, 11.5, 180, 3),
    "16m-beam": (16, 19, 90, 3),
}
HEIGHTS_M = np.array([5.0, 10.0, 15.0])  # Authored cargo centres above CG.
MULTIPLIERS = np.array([1.0, 1.15, 1.3])


class TraceSimulation(Simulation):
    """Record accepted integration samples without changing the motion equations."""

    def _sample(self, state, sea, acceleration, response_derivative=None):
        sample = super()._sample(state, sea, acceleration, response_derivative)
        self.rows.append([sample.time_s, *state.x[3:6], state.x[9], state.x[10],
                          acceleration, sample.speed_through_water_mps, sample.angular_acceleration_x_rad_s2])
        return sample


def simulate(task):
    case, seed, seconds, dt, output = task
    hs, tp, heading, speed = CASES[case]
    sim = TraceSimulation(load_s175(), ConstantSea(hs, tp), seed=seed,
                          settings=Settings(dt_s=dt), wind_parameters=None)
    state = sim.initial_state(datetime(2026, 10, 29, tzinfo=UTC), -5, 48,
                              heading_deg=heading, speed_mps=speed)
    sim.rows = []
    result = sim.advance(state, Command(heading, speed), seconds)
    trace = np.asarray(sim.rows)
    if result.stopping_reason != "duration_reached":
        raise RuntimeError(f"{case}/{seed}: {result.stopping_reason} at {result.advanced_s}s")
    flags = sorted({e.detail for e in result.events if e.kind == "wave_quality"})
    metadata = {
        "case": case, "wave_seed": seed, "seconds": seconds,
        "sea": asdict(sim.environment.sea), "heading_deg": heading,
        "target_speed_mps": speed, "settings": asdict(sim.settings),
        "wave_quality_flags": flags, "stopping_reason": result.stopping_reason,
        "prepared_sha256": json.loads((OUTPUT / "provenance.json").read_text())["prepared_sha256"],
    }
    path = Path(output) / f"{case}-seed{seed}.npz"
    np.savez_compressed(path, trace=trace, metadata=json.dumps(metadata))
    print(f"simulated {case}, seed {seed}", flush=True)
    return str(path)


def demands(trace, heights_m=HEIGHTS_M):
    """CG and three centreline cargo-point lateral specific accelerations in g."""
    time, p, q, r, roll, pitch, ay, _ = trace[:, :8].T
    gravity_y = 9.81 * np.sin(roll) * np.cos(pitch)
    signed_cg = ay - gravity_y
    # a(P)=a(CG)+alpha cross r+omega cross (omega cross r), r=(0,0,-h).
    # shortcut: finite-difference body rates; use endpoint angular acceleration in a runtime model.
    alpha_x = trace[:, 8] if trace.shape[1] == 9 else np.gradient(p, time, edge_order=2)
    signed_deck = signed_cg[:, None] + np.asarray(heights_m) * (alpha_x - q * r)[:, None]
    return np.abs(signed_cg) / 9.81, np.abs(signed_deck) / 9.81


def assess(path):
    with np.load(path, allow_pickle=False) as data:
        trace = data["trace"]
        metadata = json.loads(str(data["metadata"]))
    if len(trace) < 3:
        raise ValueError("At least three accepted motion samples required")
    dt = metadata["settings"]["dt_s"]
    if not np.allclose(np.diff(trace[:, 0]), dt, atol=1e-9, rtol=0):
        raise ValueError("Trace must use the fixed integration clock")
    cg, deck = demands(trace)
    cg_categories = np.repeat(cg[:, None], 3, axis=1)
    variants = {}
    for median, sigma, fatigue in itertools.product((0.4, 0.6, 0.8), (0.2, 0.35), (600, 1800)):
        key = f"median{median}-sigma{sigma}-fatigue{fatigue}"
        variants[key] = candidate_loss(deck, dt, median_g=median, sigma=sigma, fatigue_s=fatigue)
    baseline = candidate_loss(deck, dt)
    cargo_seeds = [candidate_loss(deck, dt, cargo_seed=s)["lost_units"] for s in (101, 102, 103)]
    stack_demand = demands(trace, [EFFECTIVE_HEIGHT_M])[1][:, 0]
    stack_sweep = {
        f"median{m}-sigma{s}-exposure{e}": stack_loss(
            stack_demand, dt, median_g=m, sigma=s, exposure_s=e
        )
        for m, s, e in itertools.product((0.4, 0.5, 0.6), (0.2, 0.35), (0.5, 1.0, 2.0))
    }
    return {
        **metadata, "angular_acceleration_source": "recorded_endpoint" if trace.shape[1] == 9 else "body_rate_difference", "trace_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "max_roll_deg": float(np.degrees(np.abs(trace[:, 4])).max()),
        "max_pitch_deg": float(np.degrees(np.abs(trace[:, 5])).max()),
        "max_speed_mps": float(trace[:, 7].max()),
        "max_cg_demand_g": float(cg.max()), "max_deck_demand_g": deck.max(axis=0).tolist(),
        "v1_014": legacy_loss(cg, dt), "v1_030": legacy_loss(cg, dt, threshold_g=0.30),
        "candidate_cg": candidate_loss(cg_categories, dt), "candidate_deck": baseline,
        "candidate_at_05": candidate_loss(deck, dt, median_g=0.5),
        "candidate_cg_multipliers": candidate_loss(cg[:, None] * MULTIPLIERS, dt),
        "candidate_peak_only": candidate_loss(deck, dt, fatigue_s=1e300),
        "candidate_weakened": candidate_loss(deck, dt, strength=0.75),
        "candidate_strengthened": candidate_loss(deck, dt, strength=1.25),
        "candidate_cargo_seed_units": cargo_seeds, "sweep": variants,
        "stack_candidate": stack_loss(stack_demand, dt),
        "stack_same_capacity_08": stack_loss(stack_demand, dt, median_g=0.8),
        "stack_peak_only": stack_loss(stack_demand, dt, exposure_s=dt),
        "stack_weakened": stack_loss(stack_demand, dt, strength=0.75),
        "stack_strengthened": stack_loss(stack_demand, dt, strength=1.25),
        "stack_cargo_seed_units": [stack_loss(stack_demand, dt, cargo_seed=s)["lost_units"]
                                   for s in (101, 102, 103)],
        "stack_sweep": stack_sweep,
        "stack_duration_units": {str(s): stack_loss(stack_demand[trace[:, 0] <= s], dt)["lost_units"]
                                 for s in (60, 300, 600, 1200) if s <= metadata["seconds"]},
        "v1_duration_units": {str(s): legacy_loss(cg[trace[:, 0] <= s], dt)["lost_units"]
                              for s in (60, 300, 600, 1200) if s <= metadata["seconds"]},
    }


def prescribed_rolls():
    """Response-controlled bursts to separate cargo behaviour from wave response."""
    reports = []
    dt = 0.05
    for angle, period, seconds in itertools.product((8, 15, 25, 36), (10, 15, 20), (60, 300)):
        time = np.arange(1, round(seconds / dt) + 1) * dt
        omega, amplitude = 2 * np.pi / period, np.radians(angle)
        trace = np.zeros((len(time), 9))
        trace[:, 0] = time
        trace[:, 1] = amplitude * omega * np.cos(omega * time)
        trace[:, 4] = amplitude * np.sin(omega * time)
        trace[:, 8] = -amplitude * omega**2 * np.sin(omega * time)
        cg, deck = demands(trace)
        stack_demand = demands(trace, [EFFECTIVE_HEIGHT_M])[1][:, 0]
        reports.append({
            "roll_amplitude_deg": angle, "roll_period_s": period, "seconds": seconds,
            "scope": "Prescribed sinusoidal roll about fixed CG; no wind, waves, sway or pitch",
            "max_deck_demand_g": deck.max(axis=0).tolist(),
            "v1_014": legacy_loss(cg, dt),
            "candidates": {str(m): candidate_loss(deck, dt, median_g=m) for m in (0.4, 0.6, 0.8)},
            "stack_candidates": {str(m): stack_loss(stack_demand, dt, median_g=m)
                                 for m in (0.4, 0.5, 0.6)},
        })
    return reports


def benchmark(paths):
    """Compare live runtime calls with the archived category and array kernels."""
    reports = []
    for path in paths:
        with np.load(path, allow_pickle=False) as data:
            trace, metadata = data["trace"], json.loads(str(data["metadata"]))
        if metadata["case"] not in ("calm", "8m-beam", "16m-beam") or metadata["wave_seed"] != 1:
            continue
        dt = metadata["settings"]["dt_s"]
        demand = demands(trace, [EFFECTIVE_HEIGHT_M])[1][:, 0]
        deck = demands(trace)[1]
        alpha = trace[:, 8] if trace.shape[1] == 9 else np.gradient(trace[:, 1], trace[:, 0], edge_order=2)
        samples = [SimpleNamespace(time_s=float(row[0]), roll_deg=math.degrees(row[4]),
                                   pitch_deg=math.degrees(row[5]), lateral_acceleration_mps2=float(row[6]),
                                   angular_acceleration_x_rad_s2=float(acceleration),
                                   angular_velocity_y_rad_s=float(row[2]),
                                   angular_velocity_z_rad_s=float(row[3]))
                   for row, acceleration in zip(trace, alpha)]
        timings = {"legacy_stream_s": [], "reference_stack_stream_s": [], "stack_stream_s": []}
        for _ in range(3):
            old, reference, new = LegacyCargoModel(seed=1), ReferenceStackCargo(dt), CargoModel(seed=101)
            old_state, new_state = old.initial_state(), new.initial_state()
            start = time.perf_counter()
            for sample in samples:
                old.advance(old_state, sample, dt)
            timings["legacy_stream_s"].append(time.perf_counter() - start)
            start = time.perf_counter()
            for value in demand:
                reference.advance(value)
            timings["reference_stack_stream_s"].append(time.perf_counter() - start)
            start = time.perf_counter()
            for sample in samples:
                new.advance(new_state, sample, dt)
            timings["stack_stream_s"].append(time.perf_counter() - start)
        expected = stack_loss(demand, dt)
        assert reference.summary() == expected
        assert new_state["lost_units"] == expected["lost_units"]
        assert new_state["lost_teu"] == expected["lost_teu"]
        assert sorted(new_state["stack_order"][:new_state["failed_count"]]) == expected["failed_stack_ids"]
        allocation = {}
        for name, run in (("independent_batch", lambda: candidate_loss(deck, dt)),
                          ("stack_batch", lambda: stack_loss(demand, dt))):
            tracemalloc.start()
            start = time.perf_counter()
            run()
            elapsed = time.perf_counter() - start
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            allocation[name] = {"wall_s": elapsed, "traced_peak_bytes": peak}
        reports.append({
            "case": metadata["case"], "wave_seed": 1, "steps": len(trace), "dt_s": dt,
            "timings": timings,
            "legacy_median_us_per_step": statistics.median(timings["legacy_stream_s"]) * 1e6 / len(trace),
            "reference_stack_median_us_per_step": statistics.median(timings["reference_stack_stream_s"]) * 1e6 / len(trace),
            "stack_median_us_per_step": statistics.median(timings["stack_stream_s"]) * 1e6 / len(trace),
            "legacy_state_json_bytes": len(json.dumps(old_state)),
            "stack_state_json_bytes": len(json.dumps(new_state)), "batch": allocation,
        })
    return {"scope": "Local runtime cargo calls include point-demand calculation and event creation; excludes motion integration, I/O and sample construction. Archived array kernel accepts precomputed demand. Allocation is tracemalloc peak, not RSS.",
            "environment": {"system": platform.system(), "machine": platform.machine(),
                            "python": platform.python_version(), "numpy": np.__version__},
            "repetitions": 3, "cases": reports}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/runs/cargo-study"))
    parser.add_argument("--replay", type=Path, help="Explicitly reuse this directory's motion traces")
    parser.add_argument("--cases", choices=CASES, nargs="+", default=list(CASES))
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    parser.add_argument("--seconds", type=float, default=1200)
    parser.add_argument("--dt", type=float, default=0.05)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--benchmark", action="store_true", help="Measure streaming kernels and batch allocations")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.replay:
        paths = sorted(args.replay.glob("*.npz"))
        if not paths:
            parser.error("Replay directory contains no motion traces")
    else:
        tasks = [(c, s, args.seconds, args.dt, str(args.output))
                 for c, s in itertools.product(args.cases, args.seeds)]
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            paths = list(pool.map(simulate, tasks))
    reports = [assess(path) for path in paths]
    result = {
        "schema_version": 2,
        "scope": "Authored synthetic S175 seas and experimental cargo proxy; not accident reconstruction or calibrated failure probabilities",
        "source_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in (Path("pipeline/cargo_study.py"), Path("game/cargo.py"),
                                       Path("pipeline/cargo_stacks.py"),
                                       Path("archive/game/cargo.py"),
                                       Path("archive/pipeline/cargo_study.py"),
                                       Path("archive/pipeline/cargo_stacks.py"),
                                       Path("game/simulation/runtime.py"),
                                       Path("game/simulation/dynamics.py"),
                                       Path("game/simulation/waves.py"))},
        "trace_columns": ["time_s", "p_rad_s", "q_rad_s", "r_rad_s", "roll_rad",
                          "pitch_rad", "cg_lateral_inertial_mps2", "water_speed_mps", "optional_angular_acceleration_x_rad_s2"],
        "candidate": {"median_g": 0.8, "log_sigma": 0.35, "fatigue_s": 1800,
                      "fatigue_onset_capacity_fraction": 0.65, "heights_above_cg_m": HEIGHTS_M.tolist(),
                      "cargo_seed": 101, "deck_units": 320, "protected_hold_units": 480},
        "stack_candidate": {"algorithm_id": CargoModel.algorithm_id, "median_g": 0.5,
                            "log_sigma": 0.35, "exposure_s": 1.0, "stacks": 64,
                            "boxes_per_stack": 5, "tier_heights_above_cg_m": [5, 7.5, 10, 12.5, 15],
                            "effective_height_m": EFFECTIVE_HEIGHT_M, "cargo_seed": 101,
                            "protected_hold_units": 480,
                            "selection": "Authored higher-loss gameplay range C50=0.4-0.6g requested by user; not an accident fit"},
        "cases": reports, "prescribed_rolls": prescribed_rolls(),
    }
    if args.benchmark:
        result["performance"] = benchmark(paths)
    destination = args.output / "results.json"
    destination.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(destination, flush=True)


if __name__ == "__main__":
    main()
