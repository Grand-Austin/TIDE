"""Independent GNU Octave oracle for the S175 manoeuvring hull force.

The pinned MSS ``CRAFT/SHIP/models/container.m`` is executed.  Its returned
accelerations are converted back to normalized generalized force with the
known original 4-DOF coupled mass matrix.  The source actuator, Coriolis and
roll-restoring terms are removed once.  The remainder is the unique pure hull
force.  The Octave script also writes those pure-force equations explicitly
and checks that both paths agree.

This reference is deliberately separate from ``game.simulation.maneuvering``.
The pipeline prepares inputs, runs Octave, and only then compares the new
Python hull-force core.  A missing Python core produces a truthful pending
result; a mismatching core produces a failure.  It never produces a fake pass.
This force reference does not validate the full runtime coupling.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from scipy.io import loadmat, savemat

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_OUTPUT = ROOT / "data/runs/maneuvering-octave"
REFERENCE_NAME = "reference.json"

SOURCE_COMMIT = "ce0b40ed101411fc4eebd064b35c5a96916106ad"
SOURCE_LICENSE = "MIT"
SOURCE_SHA256 = {
    "CRAFT/SHIP/models/container.m": (
        "c2de8f29268ae239b9856c27ee8da6901329e449e809d81c9763047f843d3df7"
    ),
    "CRAFT/SHIP/SIMcontainer.m": (
        "c03c7d60e6b4f2ef8058a9f003d11ee0e72800b5e7211f861c3fa0ef5fbfb8be"
    ),
    "CRAFT/hydroVessel.m": (
        "d07187ddbcb98a20196c32902deb202f64afcaf2551cc44ff44da2eb337e829b"
    ),
    "LIBRARY/mssConstants.m": (
        "6855f41a56d9da49cb26a0c81fce7f07eea9e94882afc75fd7ac27b37450a4ce"
    ),
    "LICENSE": (
        "922bb433450a9e39368009c29db7b07857520b8e3aad20797f5dc082df6dcce1"
    ),
}

LENGTH_M = 175.0
DENSITY = 1025.0
SHORT_H = 0.01
SHORT_STEPS = 100

FORCE_NORMALIZATION = (
    "source: X,Y divided by 0.5*rho*L^2*U^2; K,N divided by "
    "0.5*rho*L^3*U^2; U = hypot(u,v); acceleration scales are "
    "u_dot*L/U^2, v_dot*L/U^2, p_dot*L^2/U^2 and r_dot*L^2/U^2"
)

RUDDER_NOTE = (
    "Original container.m command servo saturates at 10 degrees.  The force "
    "equations use the actual rudder state.  The 35-degree cases therefore "
    "exercise the actual-angle force algebra, not a 35-degree command servo. "
    "The 35-degree extension is a documented variant, not the original model."
)


def _deg(value: float) -> float:
    return math.radians(value)


def maneuvering_cases() -> tuple[dict, ...]:
    """Return signed coupled states at 3, 5 and 6.214 m/s plus 35-degree cases."""
    values = (
        (
            "u3_straight",
            [3.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 70.0],
            [0.0, 70.0],
            "straight-ahead reference at 3 m/s",
        ),
        (
            "u5_signed_coupled",
            [5.0, -0.35, 0.030, 1200.0, -400.0, 0.45, -0.016, 0.09,
             _deg(10.0), 85.0],
            [_deg(10.0), 85.0],
            "negative sway, positive yaw, negative p, positive roll",
        ),
        (
            "u6p214_signed_coupled",
            [6.214, 0.45, -0.040, -800.0, 650.0, -0.70, 0.020, -0.12,
             _deg(-10.0), 100.0],
            [_deg(-10.0), 100.0],
            "positive sway, negative yaw, positive p, negative roll",
        ),
        (
            "u6p214_large_roll",
            [6.214, -0.60, 0.050, 300.0, 900.0, 1.10, 0.030, -0.18,
             0.0, 90.0],
            [0.0, 90.0],
            "large negative sway and roll at 6.214 m/s",
        ),
        (
            "u5_rudder_35_right",
            [5.0, 0.22, 0.015, 0.0, 0.0, 0.20, 0.006, 0.035,
             _deg(35.0), 80.0],
            [_deg(10.0), 80.0],
            "actual +35 deg rudder; command is clamped to +10 deg",
        ),
        (
            "u5_rudder_35_left",
            [5.0, -0.22, -0.015, 0.0, 0.0, -0.20, -0.006, -0.035,
             _deg(-35.0), 80.0],
            [_deg(-10.0), 80.0],
            "actual -35 deg rudder; command is clamped to -10 deg",
        ),
        (
            "u6p214_rudder_m10",
            [6.214, 0.18, 0.020, 0.0, 0.0, 0.0, 0.008, 0.02,
             _deg(-10.0), 95.0],
            [_deg(-10.0), 95.0],
            "actual and commanded -10 deg rudder",
        ),
        (
            "u6p214_rudder_p10",
            [6.214, -0.18, -0.020, 0.0, 0.0, 0.0, -0.008, -0.02,
             _deg(10.0), 95.0],
            [_deg(10.0), 95.0],
            "actual and commanded +10 deg rudder",
        ),
        (
            "u3_high_relative",
            [3.0, 0.55, -0.060, 0.0, 0.0, -0.35, -0.025, 0.15,
             _deg(10.0), 110.0],
            [_deg(10.0), 110.0],
            "3 m/s with large relative sway and yaw",
        ),
        (
            "u5_roll_p_negative",
            [5.0, 0.50, 0.050, 0.0, 0.0, 2.0, -0.020, 0.18,
             _deg(-10.0), 105.0],
            [_deg(-10.0), 105.0],
            "negative p with positive roll at 5 m/s",
        ),
    )
    return tuple(
        {
            "name": name,
            "state": state,
            "orders": orders,
            "note": note,
        }
        for name, state, orders, note in values
    )


def short_initial_states() -> np.ndarray:
    return np.array(
        [
            [6.214, 0.45, -0.040, 0.0, 0.0, 0.0, 0.020, -0.08,
             _deg(-10.0), 100.0],
            [3.0, 0.55, -0.060, 0.0, 0.0, 0.0, -0.025, 0.15,
             _deg(10.0), 110.0],
        ],
        dtype=float,
    )


def rudder_variant_state() -> np.ndarray:
    return np.array(
        [[5.0, 0.22, 0.015, 0.0, 0.0, 0.20, 0.006, 0.035, 0.0, 85.0]],
        dtype=float,
    )


def source_mass_matrix() -> np.ndarray:
    """Original 4-DOF coupled mass matrix in the source force convention.

    Row order is [X, Y, K, N].  Column order is [a_u, a_v, a_p, a_r].
    """
    m = 0.00792
    mx = 0.000238
    my = 0.007049
    alphay = 0.05
    ly = 0.0313
    Ix = 0.0000176
    Iz = 0.000456
    Jx = 0.0000034
    Jz = 0.000419
    m11 = m + mx
    m22 = m + my
    m32 = -my * ly
    m42 = my * alphay
    m33 = Ix + Jx
    m44 = Iz + Jz
    return np.array(
        [
            [m11, 0.0, 0.0, 0.0],
            [0.0, m22, m32, m42],
            [0.0, m32, m33, 0.0],
            [0.0, m42, 0.0, m44],
        ],
        dtype=float,
    )


def verify_sources(mss_root) -> dict[str, str]:
    """Reject any change to the pinned original MSS files before Octave runs."""
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


def prepare(directory) -> dict:
    """Write only inputs for Octave.  No Python derivative is an expected value."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    cases = maneuvering_cases()
    states = np.array([case["state"] for case in cases], dtype=float)
    orders = np.array([case["orders"] for case in cases], dtype=float)
    savemat(
        directory / "inputs.mat",
        {
            "derivative_states": states,
            "derivative_orders": orders,
            "short_x0": short_initial_states(),
            "short_h": SHORT_H,
            "short_steps": SHORT_STEPS,
            "rudder_variant_state": rudder_variant_state(),
        },
    )
    return {
        "states": states,
        "orders": orders,
        "short_x0": short_initial_states(),
        "short_h": SHORT_H,
        "short_steps": SHORT_STEPS,
        "rudder_variant_state": rudder_variant_state(),
        "cases": cases,
    }


def _octave_version(octave: str) -> str:
    result = subprocess.run(
        [octave, "--version"], capture_output=True, text=True, timeout=30,
        check=False,
    )
    match = re.search(r"version\s+(\S+)", result.stdout)
    return match.group(1) if match else "unknown"


def _scalar(value) -> float:
    return float(np.asarray(value, dtype=float).ravel()[0])


def _matrix(value) -> np.ndarray:
    return np.asarray(value, dtype=float)


def _text(value) -> str:
    array = np.asarray(value)
    if array.dtype.kind in "US":
        return "".join(array.ravel().tolist())
    return str(array.ravel()[0])


def compare_arrays(actual, reference, *, rtol: float, atol: float) -> dict:
    actual = np.asarray(actual, dtype=float)
    reference = np.asarray(reference, dtype=float)
    shape_ok = actual.shape == reference.shape
    if shape_ok:
        passed = bool(np.allclose(actual, reference, rtol=rtol, atol=atol))
        difference = np.abs(actual - reference)
        max_abs = float(np.max(difference)) if difference.size else 0.0
        relative_l2 = float(
            np.linalg.norm(actual - reference)
            / max(1.0, np.linalg.norm(reference))
        )
    else:
        passed = False
        max_abs = float("nan")
        relative_l2 = float("nan")
    return {
        "passed": passed,
        "rtol": rtol,
        "atol": atol,
        "shape_actual": list(actual.shape),
        "shape_reference": list(reference.shape),
        "max_absolute_difference": max_abs,
        "relative_l2_difference": relative_l2,
    }


def load_python_core():
    """Return (module, status).  A missing core is pending, never passed."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from game.simulation import maneuvering as core
    except Exception as exc:  # pragma: no cover - exercised only when absent
        return None, f"pending: cannot import game.simulation.maneuvering ({exc})"
    if not hasattr(core, "hull_force"):
        return None, "pending: game.simulation.maneuvering.hull_force is absent"
    return core, "available"


def python_hull_forces(core, states, *, length=LENGTH_M, density=DENSITY):
    forces = []
    for state in np.asarray(states, dtype=float):
        velocity = np.array(
            [state[0], state[1], 0.0, state[6], 0.0, state[2]], dtype=float
        )
        forces.append(
            core.hull_force(
                velocity,
                float(state[7]),
                {"Lpp": float(length), "rho": float(density)},
            )
        )
    return np.array(forces, dtype=float)


def hull_force_to_derivative(state, force, *, length=LENGTH_M, density=DENSITY):
    """Propagate a pure SI hull force through the original source mass matrix."""
    state = np.asarray(state, dtype=float)
    force = np.asarray(force, dtype=float)
    u, v, r = state[0], state[1], state[2]
    p_rate, phi = state[6], state[7]
    speed = math.hypot(u, v)
    force_scale = 0.5 * density * length**2 * speed**2
    moment_scale = force_scale * length
    normalized = np.array(
        [force[0] / force_scale, force[1] / force_scale,
         force[3] / moment_scale, force[5] / moment_scale],
        dtype=float,
    )
    acceleration = np.linalg.solve(source_mass_matrix(), normalized)
    derivative = np.zeros(10, dtype=float)
    derivative[0] = acceleration[0] * speed**2 / length
    derivative[1] = acceleration[1] * speed**2 / length
    derivative[2] = acceleration[3] * speed**2 / length**2
    derivative[5] = math.cos(phi) * r
    derivative[6] = acceleration[2] * speed**2 / length**2
    derivative[7] = p_rate
    return derivative


def python_hull_only_derivatives(core, states, *, length=LENGTH_M,
                                 density=DENSITY):
    forces = python_hull_forces(
        core, states, length=length, density=density
    )
    return np.array(
        [
            hull_force_to_derivative(
                state, force, length=length, density=density
            )
            for state, force in zip(np.asarray(states), forces)
        ],
        dtype=float,
    )


def _python_hull_derivative(core, state, *, length, density):
    force = python_hull_forces(
        core, np.asarray(state, dtype=float)[None, :],
        length=length, density=density,
    )[0]
    return hull_force_to_derivative(
        state, force, length=length, density=density
    )


def _rk4(core, state, h, *, length, density):
    k1 = _python_hull_derivative(core, state, length=length, density=density)
    k2 = _python_hull_derivative(
        core, state + h * k1 / 2, length=length, density=density
    )
    k3 = _python_hull_derivative(
        core, state + h * k2 / 2, length=length, density=density
    )
    k4 = _python_hull_derivative(
        core, state + h * k3, length=length, density=density
    )
    return state + h * (k1 + 2 * k2 + 2 * k3 + k4) / 6


def python_short_trajectories(core, short_x0, short_h, short_steps,
                              *, length=LENGTH_M, density=DENSITY):
    trajectories = []
    for x0 in np.asarray(short_x0, dtype=float):
        state = np.array(x0, dtype=float)
        output = np.zeros((int(short_steps) + 1, 10), dtype=float)
        output[0] = state
        for index in range(int(short_steps)):
            state = _rk4(
                core, state, float(short_h),
                length=length, density=density,
            )
            output[index + 1] = state
        trajectories.append(output)
    return np.array(trajectories, dtype=float)


def _case_metadata(case, state):
    return {
        "name": case["name"],
        "note": case["note"],
        "state": np.asarray(state, dtype=float).tolist(),
        "orders": np.asarray(case["orders"], dtype=float).tolist(),
        "speed_mps": float(np.hypot(state[0], state[1])),
        "u_mps": float(state[0]),
        "v_mps": float(state[1]),
        "yaw_rate_rad_s": float(state[2]),
        "p_rad_s": float(state[6]),
        "roll_rad": float(state[7]),
        "actual_rudder_deg": float(np.degrees(state[8])),
        "commanded_rudder_deg": float(np.degrees(case["orders"][0])),
        "shaft_rpm": float(state[9]),
    }


def build_reference(actual, prepared, actual_hashes, octave_version,
                    octave_command):
    """Build the stored fixture from arrays that Octave actually produced."""
    derivatives = _matrix(actual["derivatives"])
    hull_force_si = _matrix(actual["hull_force_si"])
    hull_force_norm = _matrix(actual["hull_force_norm"])
    explicit_hull_norm = _matrix(actual["explicit_hull_norm"])
    explicit_hull_si = _matrix(actual["explicit_hull_si"])
    residual = _matrix(actual["reconstruction_residual"])
    mutated_hull_norm = _matrix(actual["mutated_hull_norm"])
    mutated_hull_si = _matrix(actual["mutated_hull_si"])
    short = _matrix(actual["short"])
    states = np.asarray(prepared["states"], dtype=float)
    length_m = _scalar(actual["length_m"])
    density = _scalar(actual["density"])
    mass_matrix_normalized = _matrix(actual["mass_matrix_normalized"])
    short_x0 = _matrix(actual["short_x0"])
    short_h = _scalar(actual["short_h"])
    short_steps = int(_scalar(actual["short_steps"]))
    rudder_variant_state_actual = _matrix(actual["rudder_variant_state"])

    reconstruction_max_abs = _scalar(actual["reconstruction_max_abs"])
    explicit_match_max_abs = _scalar(actual["explicit_match_max_abs"])
    mutation_detection_max_abs = _scalar(actual["mutation_detection_max_abs"])
    rudder_10 = _matrix(actual["rudder_variant_10deg_norm"]).ravel()
    rudder_35 = _matrix(actual["rudder_variant_35deg_norm"]).ravel()
    rudder_variant_difference = float(np.max(np.abs(rudder_35 - rudder_10)))

    checks = {
        "source_derivative_finite": {
            "passed": bool(np.all(np.isfinite(derivatives))),
        },
        "source_force_reconstruction": {
            "passed": bool(reconstruction_max_abs <= 1e-12),
            "max_absolute_normalized_residual": reconstruction_max_abs,
            "tolerance": 1e-12,
        },
        "explicit_hull_force_match": {
            "passed": bool(explicit_match_max_abs <= 1e-12),
            "max_absolute_normalized_difference": explicit_match_max_abs,
            "tolerance": 1e-12,
        },
        "mutated_coefficient_detected": {
            "passed": bool(mutation_detection_max_abs > 1e-12),
            "max_absolute_normalized_difference": mutation_detection_max_abs,
            "threshold": 1e-12,
        },
        "rudder_35deg_force_algebra_distinct": {
            "passed": bool(rudder_variant_difference > 1e-9),
            "max_absolute_normalized_difference": rudder_variant_difference,
            "threshold": 1e-9,
        },
    }

    return {
        "source_commit": SOURCE_COMMIT,
        "source_license": SOURCE_LICENSE,
        "source_sha256": actual_hashes,
        "source_sha256_expected": SOURCE_SHA256,
        "octave_version": octave_version,
        "octave_command": octave_command,
        "scope": (
            "Direct execution of pinned MSS container.m.  Unique pure "
            "Son-Nomoto hull force recovered from the original derivative "
            "through the known 4-DOF coupled mass matrix, and independently "
            "written pure-force equations.  Actuator, Coriolis and original "
            "roll restoring are removed once.  No full runtime coupling claim."
        ),
        "force_normalization": FORCE_NORMALIZATION,
        "length_m": length_m,
        "density": density,
        "rudder_note": RUDDER_NOTE,
        "cases": [
            _case_metadata(case, state)
            for case, state in zip(prepared["cases"], states)
        ],
        "derivative_states": states.tolist(),
        "derivative_orders": np.asarray(
            prepared["orders"], dtype=float
        ).tolist(),
        "derivatives": derivatives.tolist(),
        "mass_matrix_normalized": mass_matrix_normalized.tolist(),
        "total_norm": _matrix(actual["total_norm"]).tolist(),
        "actuator_norm": _matrix(actual["actuator_norm"]).tolist(),
        "coriolis_norm": _matrix(actual["coriolis_norm"]).tolist(),
        "restoring_norm": _matrix(actual["restoring_norm"]).tolist(),
        "hull_force_norm": hull_force_norm.tolist(),
        "hull_force_si": hull_force_si.tolist(),
        "explicit_hull_norm": explicit_hull_norm.tolist(),
        "explicit_hull_si": explicit_hull_si.tolist(),
        "reconstruction_residual": residual.tolist(),
        "reconstruction_max_abs": reconstruction_max_abs,
        "explicit_match_max_abs": explicit_match_max_abs,
        "mutation_coefficient": _text(actual["mutation_coefficient"]),
        "mutation_factor": _scalar(actual["mutation_factor"]),
        "mutated_hull_norm": mutated_hull_norm.tolist(),
        "mutated_hull_si": mutated_hull_si.tolist(),
        "mutation_detection_max_abs": mutation_detection_max_abs,
        "hull_only_derivative": _matrix(actual["hull_only_derivative"]).tolist(),
        "short_x0": short_x0.tolist(),
        "short_h": short_h,
        "short_steps": short_steps,
        "short": short.tolist(),
        "rudder_variant_state": rudder_variant_state_actual.tolist(),
        "rudder_original_command_limit_deg": _scalar(
            actual["rudder_original_command_limit_deg"]
        ),
        "rudder_variant_deg": _scalar(actual["rudder_variant_deg"]),
        "rudder_variant_10deg_norm": rudder_10.tolist(),
        "rudder_variant_35deg_norm": rudder_35.tolist(),
        "rudder_variant_10deg_si": _matrix(
            actual["rudder_variant_10deg_si"]
        ).ravel().tolist(),
        "rudder_variant_35deg_si": _matrix(
            actual["rudder_variant_35deg_si"]
        ).ravel().tolist(),
        "checks": checks,
        "source_reference_status": (
            "passed" if all(check["passed"] for check in checks.values())
            else "failed"
        ),
        "full_runtime_coupled_trajectory": "pending",
        "full_runtime_note": (
            "This fixture validates the original container reference and the "
            "pure new hull-force oracle only.  Full new coupled-model "
            "trajectory comparison waits for the finalized LF/WF runtime "
            "architecture and must not be inferred from force parity."
        ),
    }


def compare_python_core(reference):
    """Compare the Octave fixture with the new Python hull-force core."""
    core, status = load_python_core()
    if core is None:
        return {
            "status": status,
            "pure_hull_force": "pending",
            "pure_hull_derivative": "pending",
            "pure_hull_short_trajectory": "pending",
        }
    try:
        states = np.asarray(reference["derivative_states"], dtype=float)
        forces = python_hull_forces(core, states)
        derivatives = python_hull_only_derivatives(core, states)
        short = python_short_trajectories(
            core,
            np.asarray(reference["short_x0"], dtype=float),
            reference["short_h"],
            reference["short_steps"],
        )
        checks = {
            "pure_hull_force": compare_arrays(
                forces, reference["hull_force_si"], rtol=1e-11, atol=1e-8
            ),
            "pure_hull_derivative": compare_arrays(
                derivatives, reference["hull_only_derivative"],
                rtol=1e-10, atol=1e-10,
            ),
            "pure_hull_short_trajectory": compare_arrays(
                short, reference["short"], rtol=1e-9, atol=1e-9
            ),
        }
    except Exception as exc:
        return {
            "status": f"failed: {type(exc).__name__}: {exc}",
            "pure_hull_force": "failed",
            "pure_hull_derivative": "failed",
            "pure_hull_short_trajectory": "failed",
        }
    passed = all(check["passed"] for check in checks.values())
    checks["status"] = "passed" if passed else "failed"
    return checks


def _octave_expression(mss_root, input_path, output_path):
    quote = lambda path: str(Path(path).resolve()).replace("'", "''")
    return (
        f"addpath('{quote(ROOT / 'tests/reference')}'); "
        f"matched_maneuvering('{quote(mss_root)}',"
        f"'{quote(input_path)}','{quote(output_path)}');"
    )


PHASE2_FIXTURE = ROOT / "tests/fixtures/mss/two-timescale-reference.json"
PHASE2_DT = 0.05
PHASE2_STEPS = 200
PHASE2_RUNTIME_NAME = "runtime_simulation"
PHASE2_SCOPE = (
    "Independent Octave coupled RK4 of the approved two-time-scale model: "
    "8 native LF states + 12 original hydroVessel WF states + 2 servo states. "
    "LF uses actual S175 rigid mass/inertia and Groll with source Son-Nomoto "
    "hull force, source convective terms and physical actuator loads. WF uses "
    "pinned hydroVessel.m with full MSS M, MA, D, G, Coriolis, crossflow, zero "
    "current and the held first-order wave-force tape only. External LF wind, "
    "mean surge and current are held inputs. Exposed composition follows the "
    "root integration contract. This is not a full-model validation claim."
)
PHASE2_LIMITATIONS = (
    "Bounded 10 s trajectories. Direct cases use held open-loop actuator "
    "orders; the runtime case records actual controller orders and held "
    "wind/wave/mean/current tapes, so it validates dynamics, clock, servo and "
    "composition but not the independently tuned PID decision logic. The "
    "two-time-scale WF slow-frame feedback approximation remains a documented "
    "limitation. No coefficient fitting was used."
)


def _phase2_code_hashes() -> dict[str, str]:
    paths = (
        "game/simulation/two_timescale.py",
        "game/simulation/runtime.py",
        "game/simulation/maneuvering.py",
        "game/simulation/actuators.py",
        "game/simulation/dynamics.py",
        "game/simulation/vessel.py",
        "game/simulation/waves.py",
        "game/simulation/wind.py",
        "pipeline/validate_maneuvering_octave.py",
        "tests/reference/matched_two_timescale.m",
    )
    return {
        str(Path(path)): hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in paths
        if (ROOT / path).is_file()
    }


def _phase2_sea(case) -> object:
    from game.simulation.environment import SeaState

    return SeaState(
        hs=float(case["hs"]),
        tp=float(case["tp"]),
        wave_from_deg=float(case["wave_from_deg"]),
        current_east_mps=float(case["current"][0]),
        current_north_mps=float(case["current"][1]),
        wind_east_mps=float(case["wind_east"]),
        wind_north_mps=float(case["wind_north"]),
    )


def _lf_wind_state(lf, sea) -> np.ndarray:
    lf = np.asarray(lf, dtype=float)
    u, v = lf[0], lf[1]
    phi, psi = lf[6], lf[7]
    current_body = np.array(
        [
            math.cos(psi) * sea.current_north_mps
            + math.sin(psi) * sea.current_east_mps,
            -math.sin(psi) * sea.current_north_mps
            + math.cos(psi) * sea.current_east_mps,
        ]
    )
    x = np.zeros(12)
    x[:3] = [u, v * math.cos(phi), 0.0] + np.r_[current_body, 0.0]
    x[11] = psi
    return x


def _held_wind(lf, sea) -> np.ndarray:
    from game.simulation.wind import WindParameters, wind_load

    return np.asarray(
        wind_load(_lf_wind_state(lf, sea), sea, WindParameters()), dtype=float
    )


def _wave_tape(vessel, sea, lf0, *, seed: int, steps: int, dt: float,
               hold_s: float = 0.2):
    from game.simulation.waves import WaveModel

    wave_sea = type(sea)(sea.hs, sea.tp, sea.wave_from_deg)
    model = WaveModel(vessel, seed=seed)
    state = model.initial_state()
    u, v = float(lf0[0]), float(lf0[1])
    phi, psi = float(lf0[6]), float(lf0[7])
    velocity_ne = np.array(
        [
            math.cos(psi) * u - math.sin(psi) * math.cos(phi) * v,
            math.sin(psi) * u + math.cos(psi) * math.cos(phi) * v,
        ]
    )
    force = np.zeros((steps, 6))
    mean = np.zeros(steps)
    nholds = int(math.ceil(steps * dt / hold_s))
    for hold in range(nholds):
        time_s = hold * hold_s
        result = model.evaluate(state, time_s, wave_sea, psi, velocity_ne)
        state = result.state
        begin = int(round(time_s / dt))
        end = min(steps, int(round((hold + 1) * hold_s / dt)))
        if end > begin:
            force[begin:end] = result.force
            mean[begin:end] = result.mean_surge_n
    return force, mean


def _direct_two_timescale_specs() -> tuple[dict, ...]:
    deg = math.radians
    return (
        {
            "name": "direct_lf_turn_right_wf_wave",
            "description": (
                "6.214 m/s positive LF turn with heel and a nonzero oscillatory "
                "WF heave/pitch/roll/translation state"
            ),
            "lf0": [6.214, 0.25, 0.012, 0.018, 0.0, 0.0, 0.02, 0.10],
            "wf0": [0.35, -0.22, 0.18, 0.02, -0.03, 0.045,
                    0.6, -0.3, 0.12, 0.05, -0.02, 0.06],
            "rudder0": 0.0,
            "rpm0": 120.0,
            "rudder_order": deg(35.0),
            "rpm_order": 120.0,
            "current": [0.10, -0.20],
            "wind_east": 8.0,
            "wind_north": 0.0,
            "wave_from_deg": 40.0,
            "hs": 2.0,
            "tp": 8.0,
            "seed": 11,
        },
        {
            "name": "direct_lf_turn_left_wf_wave",
            "description": (
                "5 m/s negative LF turn with opposite heel and a signed WF "
                "translation/rotation perturbation"
            ),
            "lf0": [5.0, -0.30, -0.018, -0.025, 0.0, 0.0, -0.12, -0.25],
            "wf0": [-0.30, 0.25, -0.15, -0.02, 0.03, -0.04,
                    -0.5, 0.25, -0.1, -0.06, 0.03, -0.08],
            "rudder0": deg(-10.0),
            "rpm0": 90.0,
            "rudder_order": deg(-35.0),
            "rpm_order": 90.0,
            "current": [-0.15, 0.10],
            "wind_east": 6.0,
            "wind_north": -3.0,
            "wave_from_deg": 220.0,
            "hs": 1.8,
            "tp": 7.0,
            "seed": 22,
        },
        {
            "name": "direct_lf_moderate_wf_translation",
            "description": (
                "3 m/s moderate turn with large WF translation, heave and pitch "
                "but a bounded actuator envelope"
            ),
            "lf0": [3.0, 0.45, 0.02, 0.015, 0.0, 0.0, 0.08, 0.50],
            "wf0": [0.2, 0.1, -0.2, 0.01, 0.02, 0.03,
                    1.0, 0.5, -0.4, 0.08, -0.05, 0.10],
            "rudder0": deg(10.0),
            "rpm0": 110.0,
            "rudder_order": deg(20.0),
            "rpm_order": 110.0,
            "current": [0.05, 0.15],
            "wind_east": 4.0,
            "wind_north": 2.0,
            "wave_from_deg": 100.0,
            "hs": 1.5,
            "tp": 6.0,
            "seed": 33,
        },
        {
            "name": "direct_lf_high_speed_heel",
            "description": (
                "6.214 m/s negative LF turn with large negative heel and "
                "nonzero WF roll/pitch/heave"
            ),
            "lf0": [6.214, -0.50, -0.03, 0.04, 0.0, 0.0, -0.18, -0.60],
            "wf0": [0.5, -0.35, 0.25, 0.03, -0.04, 0.06,
                    -0.8, 0.4, 0.2, 0.10, 0.04, -0.12],
            "rudder0": deg(-10.0),
            "rpm0": 100.0,
            "rudder_order": deg(-35.0),
            "rpm_order": 100.0,
            "current": [-0.20, -0.10],
            "wind_east": -5.0,
            "wind_north": 4.0,
            "wave_from_deg": 300.0,
            "hs": 2.2,
            "tp": 9.0,
            "seed": 44,
        },
    )


def _build_case_arrays(case, vessel) -> dict:
    sea = _phase2_sea(case)
    lf0 = np.asarray(case["lf0"], dtype=float)
    wind = _held_wind(lf0, sea)
    wave, mean = _wave_tape(
        vessel, sea, lf0, seed=int(case["seed"]),
        steps=PHASE2_STEPS, dt=PHASE2_DT,
    )
    steps = PHASE2_STEPS
    return {
        "name": case["name"],
        "kind": case.get("kind", "direct"),
        "description": case["description"],
        "lf0": lf0,
        "wf0": np.asarray(case["wf0"], dtype=float),
        "rudder0": float(case["rudder0"]),
        "rpm0": float(case["rpm0"]),
        "current": np.tile(np.asarray(case["current"], dtype=float),
                           (steps, 1)),
        "wind_force": np.tile(wind, (steps, 1)),
        "mean_surge": np.asarray(mean, dtype=float),
        "wave_force": np.asarray(wave, dtype=float),
        "rudder_order": np.full(steps, float(case["rudder_order"])),
        "rpm_order": np.full(steps, float(case["rpm_order"])),
        "runtime": None,
    }


def _direct_cases(vessel) -> list[dict]:
    return [_build_case_arrays(spec, vessel) for spec in _direct_two_timescale_specs()]


def run_runtime_two_timescale_case(vessel=None) -> dict:
    """Record the actual Simulation.advance inputs and accepted trajectory.

    The controller is wrapped only to record its external rudder/RPM orders and
    the LF/WF state at the start of each accepted step.  Wind, wave/mean drift
    and current are recorded as held input tapes.  The returned case is then
    replayed by the independent Octave equations.
    """
    from game.simulation import runtime as runtime_module
    from game.simulation.environment import SeaState
    from game.simulation.runtime import (
        MANEUVERING_MODEL, Command, Settings, Simulation,
    )
    from game.simulation.vessel import load_s175
    from game.simulation.wind import WindParameters

    if vessel is None:
        vessel = load_s175()
    sea = SeaState(
        2.0, 8.0, 40.0, current_east_mps=0.2, current_north_mps=0.1,
        wind_east_mps=8.0, wind_north_mps=0.0,
    )

    class ConstantEnvironment:
        def sample(self, time, lon, lat):
            return sea

    sim = Simulation(
        vessel, ConstantEnvironment(), seed=23,
        settings=Settings(
            actuator_model=MANEUVERING_MODEL, dt_s=PHASE2_DT,
            wave_period_s=0.1, record_period_s=1.0,
        ),
        wind_parameters=WindParameters(),
    )
    state = sim.initial_state(
        datetime(2026, 1, 23, tzinfo=timezone.utc), -8.0, 47.0,
        speed_mps=6.214, heading_deg=0.0,
    )
    command = Command(20.0, 6.214)

    control_records = []
    wind_records = []
    wave_records = []
    original_control = sim._control
    original_wind_load = runtime_module.wind_load
    original_wave_evaluate = sim.waves.evaluate

    def recording_control(candidate, candidate_command, candidate_sea):
        record = {
            "lf": np.asarray(candidate.lf, dtype=float).copy(),
            "wf": np.asarray(candidate.wf, dtype=float).copy(),
            "rudder": float(candidate.control.rudder_rad),
            "rpm": float(candidate.control.shaft_rpm),
            "current": (
                float(candidate_sea.current_north_mps),
                float(candidate_sea.current_east_mps),
            ),
        }
        control_records.append(record)
        force = original_control(candidate, candidate_command, candidate_sea)
        record["rudder_order"] = float(candidate.control.rudder_order_rad)
        record["rpm_order"] = float(candidate.control.shaft_order_rpm)
        return force

    def recording_wind(x, candidate_sea, parameters):
        force = np.asarray(
            original_wind_load(x, candidate_sea, parameters), dtype=float
        ).copy()
        wind_records.append(force)
        return force

    def recording_wave(wave_state, time_s, candidate_sea, heading, velocity):
        result = original_wave_evaluate(
            wave_state, time_s, candidate_sea, heading, velocity
        )
        wave_records.append({
            "time_s": float(time_s),
            "force": np.asarray(result.force, dtype=float).copy(),
            "mean_surge": float(result.mean_surge_n),
        })
        return result

    sim._control = recording_control
    runtime_module.wind_load = recording_wind
    sim.waves.evaluate = recording_wave
    try:
        result = sim.advance(state, command, PHASE2_STEPS * PHASE2_DT)
    finally:
        sim._control = original_control
        runtime_module.wind_load = original_wind_load
        sim.waves.evaluate = original_wave_evaluate

    if result.stopping_reason != "duration_reached":
        raise RuntimeError(
            f"runtime stopped at {result.stopping_reason} after "
            f"{result.advanced_s} s"
        )
    steps = int(result.state.tick - state.tick)
    if steps != PHASE2_STEPS:
        raise RuntimeError(f"runtime advanced {steps} steps, expected {PHASE2_STEPS}")
    if len(control_records) < steps or len(wind_records) < steps:
        raise RuntimeError("runtime recorder did not capture every accepted step")
    if not wave_records:
        raise RuntimeError("runtime wave recorder captured no wave updates")

    wave_force = np.zeros((steps, 6))
    mean_surge = np.zeros(steps)
    wave_index = 0
    for k in range(steps):
        absolute_tick = state.tick + k
        while (wave_index + 1 < len(wave_records)
               and round(wave_records[wave_index + 1]["time_s"] / PHASE2_DT)
               <= absolute_tick):
            wave_index += 1
        wave_force[k] = wave_records[wave_index]["force"]
        mean_surge[k] = wave_records[wave_index]["mean_surge"]

    lf_states = np.array(
        [record["lf"] for record in control_records[:steps]]
        + [np.asarray(result.state.lf, dtype=float)]
    )
    wf_states = np.array(
        [record["wf"] for record in control_records[:steps]]
        + [np.asarray(result.state.wf, dtype=float)]
    )
    servo_states = np.array(
        [[record["rudder"], record["rpm"]] for record in control_records[:steps]]
        + [[float(result.state.control.rudder_rad),
            float(result.state.control.shaft_rpm)]]
    )

    return {
        "name": PHASE2_RUNTIME_NAME,
        "kind": "runtime",
        "description": (
            "Actual Simulation.advance with s175_maneuvering; recorded "
            "controller orders and held wind/wave/mean/current tapes"
        ),
        "lf0": np.asarray(control_records[0]["lf"], dtype=float),
        "wf0": np.asarray(control_records[0]["wf"], dtype=float),
        "rudder0": float(control_records[0]["rudder"]),
        "rpm0": float(control_records[0]["rpm"]),
        "current": np.array(
            [record["current"] for record in control_records[:steps]]
        ),
        "wind_force": np.array(wind_records[:steps]),
        "mean_surge": mean_surge,
        "wave_force": wave_force,
        "rudder_order": np.array(
            [record["rudder_order"] for record in control_records[:steps]]
        ),
        "rpm_order": np.array(
            [record["rpm_order"] for record in control_records[:steps]]
        ),
        "runtime": {
            "lf_trajectory": lf_states,
            "wf_trajectory": wf_states,
            "servo_trajectory": servo_states,
            "samples": result.trajectory,
            "final_x": np.asarray(result.state.x, dtype=float),
            "stopping_reason": result.stopping_reason,
            "dt": PHASE2_DT,
            "steps": steps,
        },
    }


def prepare_two_timescale(directory) -> dict:
    """Prepare direct cases and, when available, the recorded runtime case."""
    from game.simulation.vessel import load_s175

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    vessel = load_s175()
    try:
        baseline = source_native_baseline()
    except Exception as exc:
        baseline = {
            "status": f"failed: {type(exc).__name__}: {exc}",
            "max_absolute_difference": float("nan"),
        }

    cases = _direct_cases(vessel)
    runtime_status = "pending: runtime comparison not requested"
    try:
        runtime_case = run_runtime_two_timescale_case(vessel)
        if runtime_case["runtime"]["steps"] != PHASE2_STEPS:
            raise RuntimeError("runtime case does not match the prepared step count")
        cases.append(runtime_case)
        runtime_status = "available"
    except ImportError as exc:
        runtime_status = f"pending: runtime unavailable ({exc})"
    except (AttributeError, TypeError) as exc:
        runtime_status = f"pending: runtime API unavailable ({type(exc).__name__}: {exc})"
    except Exception as exc:
        runtime_status = f"failed: {type(exc).__name__}: {exc}"

    steps = PHASE2_STEPS
    payload = {
        "ncase": len(cases),
        "steps": steps,
        "dt": PHASE2_DT,
        "lf0": np.array([case["lf0"] for case in cases]),
        "wf0": np.array([case["wf0"] for case in cases]),
        "rudder0": np.array([case["rudder0"] for case in cases]),
        "rpm0": np.array([case["rpm0"] for case in cases]),
        "current": np.array([case["current"] for case in cases]),
        "wind_force": np.array([case["wind_force"] for case in cases]),
        "mean_surge": np.array([case["mean_surge"] for case in cases]),
        "wave_force": np.array([case["wave_force"] for case in cases]),
        "rudder_order": np.array([case["rudder_order"] for case in cases]),
        "rpm_order": np.array([case["rpm_order"] for case in cases]),
        "vessel_main": {
            key: value if isinstance(value, str) else np.asarray(value, dtype=float)
            for key, value in vessel.main.items()
        },
        "vessel_MA": vessel.MA,
        "vessel_Minv": vessel.Minv,
        "vessel_D": vessel.D,
        "vessel_G": vessel.G,
        "vessel_kappa": vessel.nonlinear_456,
        "lf_L": float(vessel.main["Lpp"]),
        "lf_rho": float(vessel.main["rho"]),
        "lf_m": float(vessel.main["m"]),
        "lf_ixx": float(vessel.MRB[3, 3]),
        "lf_izz": float(vessel.MRB[5, 5]),
        "lf_Groll": float(vessel.G[3, 3]),
    }
    savemat(directory / "inputs.mat", payload)
    metadata = {
        "cases": cases,
        "vessel": vessel,
        "runtime_status": runtime_status,
        "source_native_baseline": baseline,
        "steps": steps,
        "dt": PHASE2_DT,
    }
    (directory / "prepared.json").write_text(
        json.dumps({
            "case_names": [case["name"] for case in cases],
            "case_kinds": [case["kind"] for case in cases],
            "runtime_status": runtime_status,
            "source_native_baseline": baseline,
            "steps": steps,
            "dt": PHASE2_DT,
        }, indent=2) + "\n"
    )
    return metadata


def _two_timescale_octave_expression(mss_root, input_path, output_path):
    quote = lambda path: str(Path(path).resolve()).replace("'", "''")
    return (
        f"addpath('{quote(ROOT / 'tests/reference')}'); "
        f"matched_two_timescale('{quote(mss_root)}',"
        f"'{quote(input_path)}','{quote(output_path)}');"
    )


def _array(value) -> np.ndarray:
    return np.asarray(value, dtype=float)


def build_two_timescale_reference(actual, prepared, actual_hashes,
                                  octave_version, octave_command,
                                  input_sha256, octave_output_sha256):
    cases = prepared["cases"]
    inputs = {
        "lf0": np.array([case["lf0"] for case in cases]).tolist(),
        "wf0": np.array([case["wf0"] for case in cases]).tolist(),
        "rudder0": [float(case["rudder0"]) for case in cases],
        "rpm0": [float(case["rpm0"]) for case in cases],
        "current": np.array([case["current"] for case in cases]).tolist(),
        "wind_force": np.array([case["wind_force"] for case in cases]).tolist(),
        "mean_surge": np.array([case["mean_surge"] for case in cases]).tolist(),
        "wave_force": np.array([case["wave_force"] for case in cases]).tolist(),
        "rudder_order": np.array(
            [case["rudder_order"] for case in cases]
        ).tolist(),
        "rpm_order": np.array([case["rpm_order"] for case in cases]).tolist(),
    }
    return {
        "schema_version": 1,
        "source_commit": SOURCE_COMMIT,
        "source_license": SOURCE_LICENSE,
        "source_sha256": actual_hashes,
        "source_sha256_expected": SOURCE_SHA256,
        "code_sha256": _phase2_code_hashes(),
        "octave_version": octave_version,
        "octave_command": octave_command,
        "input_sha256": input_sha256,
        "octave_output_sha256": octave_output_sha256,
        "scope": PHASE2_SCOPE,
        "limitations": PHASE2_LIMITATIONS,
        "dt": float(prepared["dt"]),
        "steps": int(prepared["steps"]),
        "case_names": [case["name"] for case in cases],
        "case_kinds": [case["kind"] for case in cases],
        "case_descriptions": [case["description"] for case in cases],
        "inputs": inputs,
        "octave": {
            "lf_trajectory": _array(actual["lf_trajectory"]).tolist(),
            "wf_trajectory": _array(actual["wf_trajectory"]).tolist(),
            "servo_trajectory": _array(actual["servo_trajectory"]).tolist(),
            "total_x": _array(actual["total_x"]).tolist(),
            "total_dx": _array(actual["total_dx"]).tolist(),
            "derivative_start": _array(actual["derivative_start"]).tolist(),
            "derivative_mid": _array(actual["derivative_mid"]).tolist(),
            "derivative_end": _array(actual["derivative_end"]).tolist(),
            "derivative_state_indices": _array(
                actual["derivative_state_indices"]
            ).astype(int).ravel().tolist(),
            "derivative_input_indices": _array(
                actual["derivative_input_indices"]
            ).astype(int).ravel().tolist(),
        },
        "source_native_baseline": prepared["source_native_baseline"],
        "runtime_status": prepared["runtime_status"],
        "bounded_runtime_comparison_status": (
            "available" if prepared["runtime_status"] == "available"
            else "pending"
        ),
        "full_model_acceptance": (
            "pending: separate long-run turning and reference acceptance not run"
        ),
    }


def source_native_baseline() -> dict:
    """Python-only source-loading identity used before the S175 LF comparison."""
    from game.simulation import actuators, container, two_timescale

    length, density, gravity, displacement = 175.0, 1025.0, 9.81, 21222.0
    mass_scale = 0.5 * density * length**3
    inertia_scale = 0.5 * density * length**5
    mass = 0.00792 * mass_scale
    ixx = 0.0000176 * inertia_scale
    izz = 0.000456 * inertia_scale
    main = {
        "Lpp": length, "rho": density, "m": mass,
        "k44": 1.0, "k55": 1.0, "k66": 1.0,
    }
    mrb = np.diag([mass, mass, mass, ixx, 1.0e12, izz])
    restoring = np.zeros((6, 6))
    restoring[3, 3] = 0.3 * density * gravity * displacement
    vessel = SimpleNamespace(main=main, MRB=mrb, G=restoring)
    states = (
        [5.0, 0.3, 0.01, 0.0, 0.0, 0.2, 0.02, 0.01, 0.1, 70.0],
        [6.0, -0.4, -0.02, 0.0, 0.0, -0.3, 0.03, -0.02, -0.15, 90.0],
        [4.5, 0.8, 0.03, 0.0, 0.0, 1.0, -0.01, 0.04, 0.2, 60.0],
    )
    maximum = 0.0
    for state in states:
        u, v, r, north, east, psi, p, phi, delta, rpm = state
        external = actuators.loads([u, v, 0.0, p, 0.0, r], delta, rpm)
        lf = np.array([u, v, p, r, north, east, phi, psi])
        actual = two_timescale.lf_derivative(lf, external, vessel)
        expected = container.derivative(state, [delta, rpm])
        expected_lf = np.array([
            expected[0], expected[1], expected[6], expected[2],
            expected[3], expected[4], expected[7], expected[5],
        ])
        maximum = max(maximum, float(np.max(np.abs(actual - expected_lf))))
    return {
        "status": "passed" if maximum <= 1e-10 else "failed",
        "max_absolute_difference": maximum,
        "states": len(states),
        "note": (
            "Python source-loading LF identity to the immutable container "
            "baseline; the existing kernel test covers the same identity."
        ),
    }


def _case_from_reference(reference, index):
    inputs = reference["inputs"]
    return {
        "name": reference["case_names"][index],
        "kind": reference["case_kinds"][index],
        "lf0": np.asarray(inputs["lf0"][index], dtype=float),
        "wf0": np.asarray(inputs["wf0"][index], dtype=float),
        "rudder0": float(inputs["rudder0"][index]),
        "rpm0": float(inputs["rpm0"][index]),
        "current": np.asarray(inputs["current"][index], dtype=float),
        "wind_force": np.asarray(inputs["wind_force"][index], dtype=float),
        "mean_surge": np.asarray(inputs["mean_surge"][index], dtype=float),
        "wave_force": np.asarray(inputs["wave_force"][index], dtype=float),
        "rudder_order": np.asarray(inputs["rudder_order"][index], dtype=float),
        "rpm_order": np.asarray(inputs["rpm_order"][index], dtype=float),
    }


def _python_coupled_derivative(z, case, step, vessel):
    from game.simulation import actuators, dynamics, maneuvering, two_timescale

    lf, wf = z[:8], z[8:20]
    rudder, rpm = z[20], z[21]
    velocity = np.array([lf[0], lf[1], 0.0, lf[2], 0.0, lf[3]])
    external = actuators.loads(
        velocity, rudder, rpm, rho=vessel.main["rho"]
    ) + case["wind_force"][step]
    external[0] += case["mean_surge"][step]
    lf_dot = two_timescale.lf_derivative(
        lf, external, vessel, current_ne=case["current"][step]
    )
    wf_dot = dynamics.derivative(
        wf, case["wave_force"][step], vessel, (0.0, 0.0)
    )
    servo = maneuvering.servo_rates(
        rudder, rpm, case["rudder_order"][step], case["rpm_order"][step]
    )
    return np.r_[lf_dot, wf_dot, servo]


def _python_rk4(z, case, step, vessel, h):
    k1 = _python_coupled_derivative(z, case, step, vessel)
    k2 = _python_coupled_derivative(z + h * k1 / 2, case, step, vessel)
    k3 = _python_coupled_derivative(z + h * k2 / 2, case, step, vessel)
    k4 = _python_coupled_derivative(z + h * k3, case, step, vessel)
    return z + h * (k1 + 2 * k2 + 2 * k3 + k4) / 6


def run_python_two_timescale_case(case, vessel):
    from game.simulation import two_timescale

    steps, h = PHASE2_STEPS, PHASE2_DT
    z = np.r_[case["lf0"], case["wf0"], case["rudder0"], case["rpm0"]]
    lf_trajectory = np.zeros((steps + 1, 8))
    wf_trajectory = np.zeros((steps + 1, 12))
    servo_trajectory = np.zeros((steps + 1, 2))
    total_x = np.zeros((steps + 1, 12))
    total_dx = np.zeros((steps + 1, 12))
    lf_trajectory[0], wf_trajectory[0] = z[:8], z[8:20]
    servo_trajectory[0] = z[20:22]
    d0 = _python_coupled_derivative(z, case, 0, vessel)
    total_x[0], _ = two_timescale.compose(
        z[:8], z[8:20], np.zeros(8), np.zeros(12), case["current"][0]
    )
    _, total_dx[0] = two_timescale.compose(
        z[:8], z[8:20], d0[:8], d0[8:20], case["current"][0]
    )
    for step in range(steps):
        z = _python_rk4(z, case, step, vessel, h)
        lf_trajectory[step + 1], wf_trajectory[step + 1] = z[:8], z[8:20]
        servo_trajectory[step + 1] = z[20:22]
        d_end = _python_coupled_derivative(z, case, step, vessel)
        total_x[step + 1], _ = two_timescale.compose(
            z[:8], z[8:20], np.zeros(8), np.zeros(12),
            case["current"][step],
        )
        _, total_dx[step + 1] = two_timescale.compose(
            z[:8], z[8:20], d_end[:8], d_end[8:20],
            case["current"][step],
        )
    mid = steps // 2
    derivative_start = d0
    derivative_mid = _python_coupled_derivative(
        np.r_[lf_trajectory[mid], wf_trajectory[mid], servo_trajectory[mid]],
        case, mid, vessel,
    )
    derivative_end = _python_coupled_derivative(
        np.r_[lf_trajectory[steps], wf_trajectory[steps],
              servo_trajectory[steps]],
        case, steps - 1, vessel,
    )
    return {
        "lf_trajectory": lf_trajectory,
        "wf_trajectory": wf_trajectory,
        "servo_trajectory": servo_trajectory,
        "total_x": total_x,
        "total_dx": total_dx,
        "derivative_start": derivative_start,
        "derivative_mid": derivative_mid,
        "derivative_end": derivative_end,
    }


def compare_two_timescale_python(reference) -> dict:
    from game.simulation.vessel import load_s175

    vessel = load_s175()
    checks = {}
    worst = 0.0
    for index, name in enumerate(reference["case_names"]):
        case = _case_from_reference(reference, index)
        try:
            actual = run_python_two_timescale_case(case, vessel)
        except Exception as exc:
            checks[name] = {"passed": False, "error": f"{type(exc).__name__}: {exc}"}
            continue
        case_checks = {}
        for key in (
            "lf_trajectory", "wf_trajectory", "servo_trajectory",
            "total_x", "total_dx", "derivative_start",
            "derivative_mid", "derivative_end",
        ):
            check = compare_arrays(
                actual[key], np.asarray(reference["octave"][key][index], dtype=float),
                rtol=1e-10, atol=1e-9,
            )
            case_checks[key] = check
            if math.isfinite(check["max_absolute_difference"]):
                worst = max(worst, check["max_absolute_difference"])
        case_checks["passed"] = all(
            item["passed"] for item in case_checks.values()
        )
        checks[name] = case_checks
    return {
        "status": "passed" if all(
            item.get("passed") for item in checks.values()
        ) else "failed",
        "max_absolute_difference": worst,
        "cases": checks,
    }


def _sample_fields(total_x, total_dx, servo, current, order_rudder, order_rpm):
    from game.simulation.dynamics import rotation

    x = np.asarray(total_x, dtype=float)
    dx = np.asarray(total_dx, dtype=float)
    ground = rotation(x[9:12]) @ x[:3]
    water = ground - np.array([current[0], current[1], 0.0])
    sog = float(np.linalg.norm(ground[:2]))
    return {
        "heading_deg": math.degrees(x[11]) % 360,
        "speed_through_water_mps": float(np.linalg.norm(water[:2])),
        "speed_over_ground_mps": sog,
        "course_over_ground_deg": (
            math.degrees(math.atan2(ground[1], ground[0])) % 360
            if sog > 1e-9 else float("nan")
        ),
        "roll_deg": math.degrees(x[9]),
        "pitch_deg": math.degrees(x[10]),
        "heave_m": -float(x[8]),
        "lateral_acceleration_mps2": float(dx[1] + x[5]*x[0] - x[3]*x[2]),
        "longitudinal_acceleration_mps2": float(dx[0] + x[4]*x[2] - x[5]*x[1]),
        "vertical_acceleration_mps2": float(dx[2] + x[3]*x[1] - x[4]*x[0]),
        "angular_velocity_x_rad_s": float(x[3]),
        "angular_velocity_y_rad_s": float(x[4]),
        "angular_velocity_z_rad_s": float(x[5]),
        "angular_acceleration_x_rad_s2": float(dx[3]),
        "angular_acceleration_y_rad_s2": float(dx[4]),
        "rudder_angle_deg": math.degrees(servo[0]),
        "shaft_rpm": float(servo[1]),
        "ordered_rudder_deg": math.degrees(order_rudder),
        "ordered_shaft_rpm": float(order_rpm),
    }


def compare_two_timescale_runtime(reference, runtime_case) -> dict:
    if runtime_case is None:
        return {"status": reference["runtime_status"]}
    if PHASE2_RUNTIME_NAME not in reference["case_names"]:
        return {"status": "pending: runtime case absent from Octave fixture"}
    index = reference["case_names"].index(PHASE2_RUNTIME_NAME)
    runtime = runtime_case["runtime"]
    octave = reference["octave"]
    checks = {}
    for key, actual_key in (
        ("lf_trajectory", "lf_trajectory"),
        ("wf_trajectory", "wf_trajectory"),
        ("servo_trajectory", "servo_trajectory"),
    ):
        checks[key] = compare_arrays(
            runtime[actual_key], np.asarray(octave[key][index], dtype=float),
            rtol=1e-10, atol=1e-9,
        )
    fields = [
        "heading_deg", "speed_through_water_mps", "speed_over_ground_mps",
        "course_over_ground_deg", "roll_deg", "pitch_deg", "heave_m",
        "lateral_acceleration_mps2", "longitudinal_acceleration_mps2",
        "vertical_acceleration_mps2", "angular_velocity_x_rad_s",
        "angular_velocity_y_rad_s", "angular_velocity_z_rad_s",
        "angular_acceleration_x_rad_s2", "angular_acceleration_y_rad_s2",
        "rudder_angle_deg", "shaft_rpm", "ordered_rudder_deg",
        "ordered_shaft_rpm",
    ]
    expected = {name: [] for name in fields}
    actual = {name: [] for name in fields}
    total_x = np.asarray(octave["total_x"][index], dtype=float)
    total_dx = np.asarray(octave["total_dx"][index], dtype=float)
    servo = np.asarray(octave["servo_trajectory"][index], dtype=float)
    current = np.asarray(reference["inputs"]["current"][index], dtype=float)
    orders_r = np.asarray(reference["inputs"]["rudder_order"][index], dtype=float)
    orders_n = np.asarray(reference["inputs"]["rpm_order"][index], dtype=float)
    for sample in runtime["samples"]:
        step = int(round(sample.time_s / reference["dt"]))
        if step < 0 or step > reference["steps"]:
            continue
        expected_fields = _sample_fields(
            total_x[step], total_dx[step], servo[step],
            current[min(step, len(current) - 1)],
            orders_r[max(0, step - 1)], orders_n[max(0, step - 1)],
        )
        for name in fields:
            expected[name].append(expected_fields[name])
            actual[name].append(float(getattr(sample, name)))
    sample_checks = {}
    for name in fields:
        sample_checks[name] = compare_arrays(
            np.asarray(expected[name]), np.asarray(actual[name]),
            rtol=1e-8, atol=1e-8,
        )
    checks["samples"] = {
        "passed": all(check["passed"] for check in sample_checks.values()),
        "fields": sample_checks,
    }
    checks["final_x"] = compare_arrays(
        runtime["final_x"], np.asarray(octave["total_x"][index][-1], dtype=float),
        rtol=1e-10, atol=1e-9,
    )
    return {
        "status": "passed" if all(
            check["passed"] for check in checks.values()
        ) else "failed",
        "checks": checks,
        "runtime_stopping_reason": runtime["stopping_reason"],
        "runtime_samples": len(runtime["samples"]),
    }


def two_timescale_main(args):
    output = args.output
    if output == DEFAULT_OUTPUT:
        output = DEFAULT_OUTPUT / "two-timescale"
    actual_hashes = verify_sources(args.mss_root)
    prepared = prepare_two_timescale(output)
    print(
        f"Prepared two-time-scale Octave inputs: {output / 'inputs.mat'} "
        f"({len(prepared['cases'])} cases; runtime {prepared['runtime_status']})"
    )
    if args.prepare_only:
        print("Prepare-only stage complete; no Octave reference was executed.")
        return
    if not args.octave:
        raise SystemExit(
            "GNU Octave is required for the two-time-scale reference run; "
            "install it or use --prepare-only"
        )
    expression = _two_timescale_octave_expression(
        args.mss_root, output / "inputs.mat", output / "octave.mat"
    )
    result = subprocess.run(
        [args.octave, "--quiet", "--no-init-all", "--eval", expression],
        capture_output=True, text=True, timeout=600, check=False,
    )
    (output / "octave.log").write_text(result.stdout + result.stderr)
    result.check_returncode()
    actual = loadmat(output / "octave.mat")
    reference = build_two_timescale_reference(
        actual, prepared, actual_hashes, _octave_version(args.octave),
        expression,
        hashlib.sha256((output / "inputs.mat").read_bytes()).hexdigest(),
        hashlib.sha256((output / "octave.mat").read_bytes()).hexdigest(),
    )
    python_checks = compare_two_timescale_python(reference)
    runtime_case = None
    for case in prepared["cases"]:
        if case["kind"] == "runtime":
            runtime_case = case
            break
    runtime_checks = compare_two_timescale_runtime(reference, runtime_case)
    report = {
        "source_commit": SOURCE_COMMIT,
        "source_license": SOURCE_LICENSE,
        "source_sha256": actual_hashes,
        "code_sha256": reference["code_sha256"],
        "octave_version": reference["octave_version"],
        "scope": reference["scope"],
        "limitations": reference["limitations"],
        "source_native_baseline": prepared["source_native_baseline"],
        "source_native_baseline_status": prepared["source_native_baseline"].get("status"),
        "direct_kernel_octave_vs_python": python_checks["status"],
        "python_kernel_status": python_checks["status"],
        "python_kernel_checks": python_checks,
        "runtime_status": prepared["runtime_status"],
        "runtime_checks": runtime_checks,
        "bounded_runtime_comparison_status": (
            "passed" if runtime_checks.get("status") == "passed"
            else "pending" if prepared["runtime_status"].startswith("pending")
            else "failed"
        ),
        "bounded_runtime_octave_vs_simulation": runtime_checks.get("status"),
        "full_model_acceptance": (
            "pending: separate long-run turning and reference acceptance not run"
        ),
        "fixture": str(PHASE2_FIXTURE.relative_to(ROOT)),
        "input_sha256": reference["input_sha256"],
        "octave_output_sha256": reference["octave_output_sha256"],
    }
    PHASE2_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    PHASE2_FIXTURE.write_text(json.dumps(reference, indent=2) + "\n")
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if prepared["source_native_baseline"].get("status") != "passed":
        raise SystemExit(1)
    if python_checks["status"] != "passed":
        raise SystemExit(1)
    if runtime_checks.get("status") == "failed":
        raise SystemExit(1)



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--mss-root", type=Path, default=ROOT / "data/raw/mss")
    parser.add_argument(
        "--octave", default=shutil.which("octave-cli") or shutil.which("octave")
    )
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument(
        "--two-timescale", action="store_true",
        help="Run the phase-2 full LF/WF/servo coupled reference instead of phase 1",
    )
    args = parser.parse_args()

    if args.two_timescale:
        two_timescale_main(args)
        return

    actual_hashes = verify_sources(args.mss_root)
    prepared = prepare(args.output)
    print(f"Prepared Octave inputs: {args.output / 'inputs.mat'}")
    if args.prepare_only:
        print("Prepare-only stage complete; no Octave reference was executed.")
        return
    if not args.octave:
        parser.error(
            "GNU Octave is required for the reference run; "
            "install it or use --prepare-only"
        )

    expression = _octave_expression(
        args.mss_root, args.output / "inputs.mat", args.output / "octave.mat"
    )
    result = subprocess.run(
        [args.octave, "--quiet", "--no-init-all", "--eval", expression],
        capture_output=True, text=True, timeout=300, check=False,
    )
    (args.output / "octave.log").write_text(result.stdout + result.stderr)
    result.check_returncode()

    actual = loadmat(args.output / "octave.mat")
    version = _octave_version(args.octave)
    reference = build_reference(
        actual, prepared, actual_hashes, version, expression
    )
    python_checks = compare_python_core(reference)

    core_path = ROOT / "game/simulation/maneuvering.py"
    report = {
        "source_commit": SOURCE_COMMIT,
        "source_license": SOURCE_LICENSE,
        "source_sha256": actual_hashes,
        "octave_version": version,
        "python_core_path": (
            str(core_path.relative_to(ROOT)) if core_path.is_file() else None
        ),
        "python_core_sha256": (
            hashlib.sha256(core_path.read_bytes()).hexdigest()
            if core_path.is_file() else None
        ),
        "original_container_reference_status": reference["source_reference_status"],
        "pure_hull_force_oracle_status": reference["source_reference_status"],
        "source_reference_status": reference["source_reference_status"],
        "source_checks": reference["checks"],
        "python_core_status": python_checks["status"],
        "python_hull_force_parity_status": python_checks["status"],
        "python_core_checks": python_checks,
        "full_runtime_coupled_trajectory": "pending",
        "full_runtime_note": reference["full_runtime_note"],
        "input_sha256": hashlib.sha256(
            (args.output / "inputs.mat").read_bytes()
        ).hexdigest(),
        "octave_output_sha256": hashlib.sha256(
            (args.output / "octave.mat").read_bytes()
        ).hexdigest(),
    }
    (args.output / REFERENCE_NAME).write_text(
        json.dumps(reference, indent=2) + "\n"
    )
    (args.output / "report.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    print(json.dumps(report, indent=2))
    if reference["source_reference_status"] != "passed":
        raise SystemExit(1)
    if python_checks["status"].startswith("failed"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
