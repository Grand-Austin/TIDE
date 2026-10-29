"""Compare the Python S175 port with pinned MSS under identical numerical inputs."""

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
from scipy.io import loadmat, savemat

from game.simulation.dynamics import derivative
from game.simulation.environment import SeaState
from game.simulation.vessel import load_s175
from game.simulation.waves import WaveModel, jonswap
from pipeline.prepare_vessel import COMMIT, FILES, ROOT, fetch_sources


def rk4(x, tau, vessel, current, h):
    k1 = derivative(x, tau, vessel, current)
    k2 = derivative(x + h * k1 / 2, tau, vessel, current)
    k3 = derivative(x + h * k2 / 2, tau, vessel, current)
    k4 = derivative(x + h * k3, tau, vessel, current)
    return x + h * (k1 + 2 * k2 + 2 * k3 + k4) / 6


def prepare(directory, *, seconds=60, h=0.05, replay=None, start_s=0):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    vessel = load_s175()
    states = np.array(
        [
            np.zeros(12),
            [2, 0.1, 0, 0.01, 0, 0.002, 0, 0, 0, 0.02, 0.01, 0.5],
            [3, 0, 0.2, 0, 0.005, 0, 0, 0, 0, 0, 0, 0],
            [2, 0.2, -0.3, 0.02, -0.03, 0.01, 0, 0, 0, 0.1, -0.05, 1.2],
        ]
    )
    currents = np.array([[0, 0], [0.3, 0.2], [0, 0], [-0.2, 0.4]])
    forces = np.array(
        [
            [0] * 6,
            [120000, 0, 0, 0, 0, 1000000],
            [0] * 6,
            [-200000, 150000, 300000, 1e6, -2e6, 3e6],
        ]
    )
    expected_derivatives = np.array(
        [
            derivative(x, tau, vessel, current)
            for x, tau, current in zip(states, forces, currents)
        ]
    )
    t = np.arange(round(seconds / h)) * h
    tau_history = np.column_stack(
        [
            np.full(len(t), 120000),
            50000 * np.sin(0.4 * t),
            2e6 * np.sin(0.55 * t),
            2e6 * np.sin(0.7 * t),
            1e8 * np.cos(0.55 * t),
            1e6 * np.sin(0.1 * t),
        ]
    )
    current_history = np.tile([0.3, 0.2], (len(t), 1))
    trajectory = [states[1].copy()]
    for tau, current in zip(tau_history, current_history):
        trajectory.append(rk4(trajectory[-1], tau, vessel, current, h))
    x0 = states[1]
    if replay:
        with np.load(replay, allow_pickle=False) as tape:
            h = float(tape["h"])
            begin, count = round(start_s / h), round(seconds / h)
            if begin < 0 or count < 1 or begin + count > len(tape["forces"]):
                raise ValueError(
                    "Requested replay interval is outside the accepted force tape"
                )
            tau_history = tape["forces"][begin : begin + count]
            current_history = tape["currents"][begin : begin + count]
            trajectory = tape["states"][begin : begin + count + 1]
            x0 = trajectory[0]
    wave = WaveModel(vessel, seed=1)
    sea = SeaState(8.2, 11.5, 180)
    wave_time, wave_speed, wave_heading = 1.25, 3.0, 0.5
    result = wave.evaluate(
        wave.initial_state(),
        wave_time,
        sea,
        wave_heading,
        wave_speed * np.array([np.cos(wave_heading), np.sin(wave_heading)]),
    )
    amplitude = sea.hs * np.sqrt(
        2
        * jonswap(wave.omega, 1, 2 * np.pi / sea.tp)[:, None]
        * wave.dw[:, None]
        * wave.direction_weights
    )
    provenance = json.loads((ROOT / "data/processed/s175/provenance.json").read_text())
    payload = {
        name: getattr(vessel, name)
        for name in ("main", "MA", "Minv", "D", "G", "nonlinear_456")
    }
    # JSON retains integer-valued parameters; MSS computes with double scalars.
    payload["main"] = {
        key: value if isinstance(value, str) else np.asarray(value, dtype=float)
        for key, value in vessel.main.items()
    }
    payload.update(
        states=states,
        currents=currents,
        forces=forces,
        x0=x0,
        tau_history=tau_history,
        current_history=current_history,
        h=h,
        T126=provenance["model_assumptions"]["T126_s"],
        omega=wave.omega,
        mu=wave.mu,
        rao=wave.rao,
        rao_angles=wave.headings,
        wave_amplitude=amplitude,
        wave_phases=wave.initial_phases,
        wave_time=wave_time,
        wave_speed=wave_speed,
        wave_heading=wave_heading,
        wave_propagation=0.0,
    )
    savemat(directory / "inputs.mat", payload)
    expected = {f"converted_{k}": getattr(vessel, k) for k in ("M", "MA", "D", "G")}
    expected.update(
        derivatives=expected_derivatives,
        trajectory=np.array(trajectory),
        wave_force=result.force[:, None],
        wave_elevation=np.array([[result.elevation]]),
        wave_mean_surge=np.array([[result.mean_surge_n]]),
    )
    savemat(directory / "python.mat", expected)
    return expected


def compare(expected, actual):
    checks = {}
    for key, value in expected.items():
        reference = np.asarray(actual[key])
        checks[key] = {
            "passed": bool(
                value.shape == reference.shape
                and np.allclose(value, reference, rtol=1e-8, atol=1e-9)
            ),
            "max_absolute_difference": float(np.max(np.abs(value - reference))),
            "relative_l2_difference": float(
                np.linalg.norm(value - reference) / max(1, np.linalg.norm(reference))
            ),
        }
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data/runs/mss-comparison"
    )
    parser.add_argument("--mss-root", type=Path, default=ROOT / "data/raw/mss")
    parser.add_argument(
        "--octave", default=shutil.which("octave-cli") or shutil.which("octave")
    )
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument(
        "--replay", type=Path, help="Applied-force tape from pipeline.validate_storm"
    )
    parser.add_argument("--start-s", type=float, default=0)
    parser.add_argument("--seconds", type=float, default=60)
    args = parser.parse_args()
    expected = prepare(
        args.output, seconds=args.seconds, replay=args.replay, start_s=args.start_s
    )
    manifest = json.loads(
        (ROOT / "data/vessels/s175/reference-manifest.json").read_text()
    )
    assert manifest["commit"] == COMMIT
    fetch_sources(args.mss_root, dict(FILES, **manifest["files"]))
    if args.prepare_only:
        print(f"Prepared identical reference inputs: {args.output}")
        return
    if not args.octave:
        parser.error(
            "GNU Octave is required for the reference run; install it or use --prepare-only"
        )
    quote = lambda p: str(Path(p).resolve()).replace("'", "''")
    expression = (
        f"addpath('{quote(ROOT / 'tests/reference')}'); "
        f"matched_dynamics('{quote(args.mss_root)}','{quote(args.output / 'inputs.mat')}',"
        f"'{quote(args.output / 'octave.mat')}');"
    )
    result = subprocess.run(
        [args.octave, "--quiet", "--no-init-all", "--eval", expression],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    (args.output / "octave.log").write_text(result.stdout + result.stderr)
    result.check_returncode()
    checks = compare(expected, loadmat(args.output / "octave.mat"))
    report = {
        "source_commit": COMMIT,
        "python_sources_sha256": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (
                ROOT / "game/simulation/dynamics.py",
                ROOT / "game/simulation/waves.py",
            )
        },
        "scope": "Identical prepared matrices, open-loop applied forces, currents, states and RK4; separate source-matrix conversion and shared-component wave checks",
        "checks": checks,
        "passed": all(c["passed"] for c in checks.values()),
        "replay": str(args.replay) if args.replay else None,
        "start_s": args.start_s,
        "seconds": args.seconds,
        "input_sha256": hashlib.sha256(
            (args.output / "inputs.mat").read_bytes()
        ).hexdigest(),
    }
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
