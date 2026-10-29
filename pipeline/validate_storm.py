"""Synthetic S175 limit tests and exact applied-force tapes for MSS replay."""

import argparse
import json
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from game.simulation import runtime
from game.simulation.dynamics import added_coriolis, rigid_coriolis
from game.simulation.environment import SeaState
from game.simulation.runtime import Command, Settings, Simulation
from game.simulation.vessel import load_s175
from game.simulation.waves import WaveModel

CASES = {
    "calm": (0, 11.5, 0, 3),
    "head-slow": (8.2, 11.5, 0, 1.5),
    "head": (8.2, 11.5, 0, 3),
    "head-fast": (8.2, 11.5, 0, 5),
    "bow": (8.2, 11.5, 45, 3),
    "beam": (8.2, 11.5, 90, 3),
    "stern-quarter": (8.2, 11.5, 135, 3),
    "following": (8.2, 11.5, 180, 3),
    "head-12m": (12, 15, 0, 3),
    "head-16m": (16, 19, 0, 3),
    "beam-16m": (16, 19, 90, 3),
}


class ConstantSea:
    def __init__(self, hs, tp):
        self.sea = SeaState(hs, tp, 0)

    def sample(self, *args):
        return self.sea


def trial(
    case,
    output,
    *,
    seconds=1200,
    seed=1,
    frequencies=128,
    settings=None,
    speed_knots=None,
):
    hs, tp, heading, speed = CASES[case]
    if speed_knots is not None:
        speed = speed_knots * 1852 / 3600
    vessel = load_s175()
    sim = Simulation(
        vessel,
        ConstantSea(hs, tp),
        seed=seed,
        settings=settings or Settings(),
        wind_parameters=None,
    )
    sim.waves = WaveModel(vessel, seed=seed, n_frequencies=frequencies)
    state = sim.initial_state(
        datetime(2026, 10, 22, 21, tzinfo=UTC),
        -5,
        48,
        heading_deg=heading,
        speed_mps=speed,
    )
    states, forces, currents, controls = [], [], [], []
    original_derivative, original_control = runtime.derivative, sim._control
    calls = 0

    def trace(x, tau, model, current):
        nonlocal calls
        # RK4 evaluates four times, then the accepted endpoint is sampled once.
        if calls % 5 == 0:
            states.append(x.copy())
            forces.append(tau.copy())
            currents.append(current)
        calls += 1
        return original_derivative(x, tau, model, current)

    def control(candidate, command, sea):
        value = original_control(candidate, command, sea)
        controls.append(value.copy())
        return value

    started = time.monotonic()
    # This standalone diagnostic runs one simulation at a time, never a live Session.
    runtime.derivative, sim._control = trace, control
    try:
        result = sim.advance(state, Command(heading, speed), seconds)
    finally:
        runtime.derivative = original_derivative
    n = result.state.tick
    x = np.array(states[:n] + [result.state.x.copy()])
    tau = np.array(forces[:n]).reshape(n, 6)
    controls = np.array(controls[:n]).reshape(n, 6)
    current = np.array(currents[:n]).reshape(n, 2)
    # Current is zero in these tests; isolate the nonlinear surge force budget.
    coupling = np.array(
        [
            -(
                (
                    rigid_coriolis(vessel.main, row[3:6])
                    + added_coriolis(vessel.MA, row[:6])
                )
                @ row[:6]
            )[0]
            for row in x[:-1]
        ]
    )
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    stem = f"{case}-seed{seed}-n{frequencies}"
    if speed_knots is not None:
        stem += f"-{speed_knots:g}kn"
    np.savez_compressed(
        output / (stem + ".npz"),
        states=x,
        forces=tau,
        controls=controls,
        currents=current,
        h=sim.settings.dt_s,
    )
    samples = [sim.observe(state), *result.trajectory]
    report = {
        "case": case,
        "scope": "Synthetic constant sea; not historical storm reconstruction",
        "sea": asdict(sim.environment.sea),
        "heading_deg": heading,
        "speed_mps": speed,
        "speed_knots": speed * 3600 / 1852,
        "seed": seed,
        "frequencies": frequencies,
        "settings": asdict(sim.settings),
        "advanced_s": result.advanced_s,
        "stopping_reason": result.stopping_reason,
        "max_water_speed_mps": max(s.speed_through_water_mps for s in samples),
        "max_water_speed_knots": max(s.speed_through_water_mps for s in samples)
        * 3600
        / 1852,
        "max_roll_deg": max(abs(s.roll_deg) for s in samples),
        "max_pitch_deg": max(abs(s.pitch_deg) for s in samples),
        "mean_coriolis_surge_n": float(np.mean(coupling)) if n else 0,
        "mean_control_surge_n": float(np.mean(controls[:, 0])) if n else 0,
        "mean_sampled_wave_surge_n": float(
            np.mean([s.wave_mean_surge_n for s in samples])
        ),
        "braking_saturation_fraction": float(
            np.mean(controls[:, 0] <= -0.999 * sim.settings.max_thrust_n)
        )
        if n
        else 0,
        "events": [
            asdict(e)
            for e in result.events
            if e.kind in ("model_limit", "numerical_failure", "wave_quality")
        ],
        "replay": stem + ".npz",
        "wall_s": time.monotonic() - started,
    }
    (output / (stem + ".json")).write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("data/runs/storm-validation")
    )
    parser.add_argument("--cases", choices=CASES, nargs="+", default=list(CASES))
    parser.add_argument("--seconds", type=float, default=1200)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--frequencies", type=int, default=128)
    parser.add_argument("--max-thrust", type=float, default=Settings().max_thrust_n)
    parser.add_argument(
        "--speed-knots",
        type=float,
        nargs="+",
        help="Override initial and target speed for each selected case, in knots",
    )
    args = parser.parse_args()
    rows = []
    for case, speed in (
        (case, speed) for speed in (args.speed_knots or [None]) for case in args.cases
    ):
        row = trial(
            case,
            args.output,
            seconds=args.seconds,
            seed=args.seed,
            frequencies=args.frequencies,
            settings=Settings(max_thrust_n=args.max_thrust),
            speed_knots=speed,
        )
        rows.append(row)
        print(
            json.dumps(
                {
                    key: row[key]
                    for key in (
                        "case",
                        "speed_knots",
                        "advanced_s",
                        "stopping_reason",
                        "max_water_speed_mps",
                        "max_water_speed_knots",
                        "max_roll_deg",
                        "mean_sampled_wave_surge_n",
                    )
                }
            ),
            flush=True,
        )
        (args.output / "report.json").write_text(json.dumps(rows, indent=2) + "\n")


if __name__ == "__main__":
    main()
