"""Compare the independent container-ship port with pinned MSS under GNU Octave.

The Python derivative and the original ``container.m`` receive identical states,
orders and RK4 steps.  The Octave run also records a sustained 600 s rudder trial.
The stored JSON fixture is produced only from an executed Octave result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
from scipy.io import loadmat, savemat

from game.simulation.container import SOURCE_COMMIT, SOURCE_SHA256, derivative
from game.simulation.actuators import loads

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/mss/container-reference.json"
REFERENCE_KEYS = ("derivatives", "short", "sustained", "actuator_differences")

DERIVATIVE_CASES = (
    (
        "nominal",
        [7.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 70.0],
        [0.0, 70.0],
    ),
    (
        "negative_rudder",
        [7.2, 0.25, 0.02, 1200.0, 350.0, 0.15, 0.01, 0.03, -0.05, 85.0],
        [-0.12, 95.0],
    ),
    (
        "positive_rudder",
        [6.8, -0.30, -0.015, -500.0, 800.0, -0.25, -0.008, -0.04, 0.06, 75.0],
        [0.16, 80.0],
    ),
    (
        "coupled_v_r_roll_p",
        [6.2, 0.50, 0.045, 1500.0, -400.0, 0.60, -0.02, 0.10, 0.03, 90.0],
        [-0.07, 110.0],
    ),
    (
        "rudder_saturation",
        [7.0, -0.10, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 70.0],
        [0.50, 70.0],
    ),
    (
        "rpm_saturation",
        [7.0, 0.10, -0.01, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 70.0],
        [-0.18, 250.0],
    ),
    (
        "combined_saturation",
        [6.5, 0.20, 0.02, 0.0, 0.0, 0.40, 0.005, 0.02, -0.02, 120.0],
        [-0.60, 250.0],
    ),
)

SHORT_X0 = np.array(
    [
        [7.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 70.0],
        [6.8, 0.20, 0.01, 0.0, 0.0, 0.20, -0.005, 0.02, 0.01, 80.0],
    ]
)
SHORT_H = 0.05
SHORT_STEPS = 400

SUSTAINED_X0 = np.array([7.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 70.0])
SUSTAINED_COMMAND = np.array([0.10, 70.0])
SUSTAINED_H = 0.1
SUSTAINED_STEPS = 6000


def rk4(x, orders, h):
    """Advance one RK4 step with the same clock and controls as MSS."""
    k1 = derivative(x, orders)
    k2 = derivative(x + h * k1 / 2, orders)
    k3 = derivative(x + h * k2 / 2, orders)
    k4 = derivative(x + h * k3, orders)
    return x + h * (k1 + 2 * k2 + 2 * k3 + k4) / 6


def _trajectory(x0, order_history, h):
    trajectory = [np.asarray(x0, dtype=float).copy()]
    for orders in order_history:
        trajectory.append(rk4(trajectory[-1], orders, h))
    return np.array(trajectory)


def prepare(directory):
    """Build identical inputs and the Python reference for the Octave replay."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    derivative_states = np.array([case[1] for case in DERIVATIVE_CASES])
    derivative_orders = np.array([case[2] for case in DERIVATIVE_CASES])

    short_time = np.arange(SHORT_STEPS) * SHORT_H
    short_orders = np.empty((len(SHORT_X0), SHORT_STEPS, 2))
    short_orders[0, :, 0] = -0.10 + 0.02 * np.sin(0.2 * short_time)
    short_orders[0, :, 1] = 85.0
    short_orders[1, :, 0] = 0.12 * np.sin(0.3 * short_time)
    short_orders[1, :, 1] = 90.0 + 10.0 * np.sin(0.15 * short_time)

    sustained_orders = np.tile(SUSTAINED_COMMAND, (SUSTAINED_STEPS, 1))

    savemat(
        directory / "inputs.mat",
        {
            "derivative_states": derivative_states,
            "derivative_orders": derivative_orders,
            "short_x0": SHORT_X0,
            "short_orders": short_orders,
            "short_h": SHORT_H,
            "sustained_x0": SUSTAINED_X0,
            "sustained_orders": sustained_orders,
            "sustained_h": SUSTAINED_H,
        },
    )

    expected = {
        "derivative_states": derivative_states,
        "derivative_orders": derivative_orders,
        "short_x0": SHORT_X0,
        "short_orders": short_orders,
        "short_h": SHORT_H,
        "sustained_x0": SUSTAINED_X0,
        "sustained_orders": sustained_orders,
        "sustained_h": SUSTAINED_H,
        "derivatives": np.array(
            [
                derivative(state, orders)
                for state, orders in zip(derivative_states, derivative_orders)
            ]
        ),
        "short": np.array(
            [
                _trajectory(x0, orders, SHORT_H)
                for x0, orders in zip(SHORT_X0, short_orders)
            ]
        ),
        "sustained": _trajectory(SUSTAINED_X0, sustained_orders, SUSTAINED_H),
        "actuator_differences": np.array([
            loads([s[0],s[1],0,s[6],0,s[2]], s[8], s[9]) -
            loads([s[0],s[1],0,s[6],0,s[2]], 0, 70) for s in derivative_states
        ]),
    }
    savemat(
        directory / "python.mat",
        {key: expected[key] for key in REFERENCE_KEYS},
    )
    return expected


def compare(expected, actual):
    """Compare only arrays that originate from the original model."""
    checks = {}
    for key in REFERENCE_KEYS:
        value = np.asarray(expected[key], dtype=float)
        reference = np.asarray(actual[key], dtype=float)
        rtol, atol = (1e-10, 1e-12) if key == "derivatives" else (1e-9, 1e-10)
        checks[key] = {
            "passed": bool(
                value.shape == reference.shape
                and np.allclose(value, reference, rtol=rtol, atol=atol)
            ),
            "rtol": rtol,
            "atol": atol,
            "max_absolute_difference": float(
                np.max(np.abs(value - reference))
                if value.shape == reference.shape
                else np.nan
            ),
            "relative_l2_difference": float(
                np.linalg.norm(value - reference) / max(1.0, np.linalg.norm(reference))
                if value.shape == reference.shape
                else np.nan
            ),
        }
    return checks


def verify_sources(mss_root):
    """Reject any change to the pinned original files."""
    root = Path(mss_root)
    actual = {}
    for name, expected in SOURCE_SHA256.items():
        path = root / name
        if not path.is_file():
            raise ValueError(f"Missing pinned MSS source: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected:
            raise ValueError(f"Source checksum mismatch: {name}; no replay was run")
        actual[name] = digest
    return actual


def _turn_response(trajectory):
    final = trajectory[-1]
    return {
        "final_state": final.tolist(),
        "final_speed_mps": float(np.hypot(final[0], final[1])),
        "final_yaw_rate_deg_s": float(np.degrees(final[2])),
        "max_abs_roll_deg": float(np.degrees(np.max(np.abs(trajectory[:, 7])))),
        "net_heading_change_deg": float(
            np.degrees(final[5] - trajectory[0, 5])
        ),
    }


def build_fixture(expected, actual, octave_version):
    """Build the stored fixture from the executed Octave arrays."""
    sustained = np.asarray(actual["sustained"], dtype=float)
    indices = np.arange(0, sustained.shape[0], 60)
    short_indices = np.arange(0, SHORT_STEPS+1, 20)
    if indices[-1] != sustained.shape[0] - 1:
        indices = np.append(indices, sustained.shape[0] - 1)
    return {
        "source_commit": SOURCE_COMMIT,
        "source_license": "MIT",
        "source_sha256": SOURCE_SHA256,
        "octave_version": octave_version,
        "scope": (
            "Direct execution of pinned MSS container.m; identical states, "
            "orders and RK4 clock; 600 s sustained rudder trial"
        ),
        "derivative_cases": [case[0] for case in DERIVATIVE_CASES],
        "derivative_states": np.asarray(expected["derivative_states"]).tolist(),
        "derivative_orders": np.asarray(expected["derivative_orders"]).tolist(),
        "derivatives": np.asarray(actual["derivatives"], dtype=float).tolist(),
        "actuator_differences": np.asarray(actual["actuator_differences"], dtype=float).tolist(),
        "short_x0": np.asarray(expected["short_x0"]).tolist(),
        "short_orders": np.asarray(expected["short_orders"]).tolist(),
        "short_h": SHORT_H,
        "short_indices": short_indices.tolist(),
        "short": np.asarray(actual["short"], dtype=float)[:, short_indices].tolist(),
        "sustained_x0": np.asarray(expected["sustained_x0"]).tolist(),
        "sustained_command": SUSTAINED_COMMAND.tolist(),
        "sustained_h": SUSTAINED_H,
        "sustained_steps": SUSTAINED_STEPS,
        "sustained_indices": indices.tolist(),
        "sustained": sustained[indices].tolist(),
        "turn_response": _turn_response(sustained),
    }


def _octave_version(octave):
    result = subprocess.run(
        [octave, "--version"], capture_output=True, text=True, timeout=30, check=False
    )
    match = re.search(r"version\s+(\S+)", result.stdout)
    return match.group(1) if match else "unknown"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data/runs/container-comparison"
    )
    parser.add_argument("--mss-root", type=Path, default=ROOT / "data/raw/mss")
    parser.add_argument(
        "--octave", default=shutil.which("octave-cli") or shutil.which("octave")
    )
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument(
        "--write-fixture",
        type=Path,
        help=(
            "Write the executed Octave result to this JSON fixture path; for "
            f"example, {FIXTURE.relative_to(ROOT)}"
        ),
    )
    args = parser.parse_args()

    actual_hashes = verify_sources(args.mss_root)
    expected = prepare(args.output)
    print(f"Prepared identical reference inputs: {args.output}")
    if args.prepare_only:
        return
    if not args.octave:
        parser.error(
            "GNU Octave is required for the reference run; "
            "install it or use --prepare-only"
        )

    quote = lambda path: str(Path(path).resolve()).replace("'", "''")
    expression = (
        f"addpath('{quote(ROOT / 'tests/reference')}'); "
        f"matched_container('{quote(args.mss_root)}',"
        f"'{quote(args.output / 'inputs.mat')}',"
        f"'{quote(args.output / 'octave.mat')}');"
    )
    result = subprocess.run(
        [args.octave, "--quiet", "--no-init-all", "--eval", expression],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    (args.output / "octave.log").write_text(result.stdout + result.stderr)
    result.check_returncode()

    actual = loadmat(args.output / "octave.mat")
    checks = compare(expected, actual)
    report = {
        "source_commit": SOURCE_COMMIT,
        "source_license": "MIT",
        "source_sha256": actual_hashes,
        "python_sources_sha256": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                ROOT / "game/simulation/container.py",
                ROOT / "game/simulation/actuators.py",
                ROOT / "pipeline/validate_container.py",
            )
        },
        "scope": (
            "Original MSS container.m derivative and identical RK4 trajectories; "
            "signed rudder, coupled sway/yaw/roll, saturation and sustained turn"
        ),
        "checks": checks,
        "passed": all(check["passed"] for check in checks.values()),
        "turn_response": _turn_response(np.asarray(actual["sustained"], dtype=float)),
        "short_steps": SHORT_STEPS,
        "short_h": SHORT_H,
        "sustained_steps": SUSTAINED_STEPS,
        "sustained_h": SUSTAINED_H,
        "input_sha256": hashlib.sha256(
            (args.output / "inputs.mat").read_bytes()
        ).hexdigest(),
    }
    if args.write_fixture and report["passed"]:
        fixture = build_fixture(expected, actual, _octave_version(args.octave))
        args.write_fixture.parent.mkdir(parents=True, exist_ok=True)
        args.write_fixture.write_text(json.dumps(fixture, indent=2) + "\n")
        report["fixture"] = str(args.write_fixture)

    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
