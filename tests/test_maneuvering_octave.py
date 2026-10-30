"""Tests for the independent Octave manoeuvring hull-force oracle.

The reference JSON is produced only by executing pinned MSS container.m under
GNU Octave.  These tests check provenance, dimensions, source-force
reconstruction, coefficient sensitivity, and parity with the new Python
hull-force core.  They do not claim full runtime coupling validation.
"""

import json
import math
import re
from pathlib import Path

import numpy as np
import pytest

from pipeline import validate_maneuvering_octave as oracle

REFERENCE = Path("data/runs/maneuvering-octave/reference.json")
REPORT = Path("data/runs/maneuvering-octave/report.json")


@pytest.fixture(scope="module")
def reference():
    if not REFERENCE.is_file():
        pytest.skip(
            "Octave manoeuvring reference is absent; run "
            ".venv/bin/python pipeline/validate_maneuvering_octave.py"
        )
    return json.loads(REFERENCE.read_text())


def test_source_metadata_hash_guard_and_executed_octave(reference):
    assert reference["source_commit"] == oracle.SOURCE_COMMIT
    assert reference["source_license"] == "MIT"
    assert reference["source_sha256"] == oracle.SOURCE_SHA256
    assert reference["source_sha256_expected"] == oracle.SOURCE_SHA256
    assert re.fullmatch(r"\d+\.\d+\.\d+", reference["octave_version"])
    assert "matched_maneuvering" in reference["octave_command"]
    assert "container.m" in reference["scope"]
    assert "pure" in reference["scope"].lower()
    assert reference["full_runtime_coupled_trajectory"] == "pending"
    assert reference["length_m"] == pytest.approx(175.0)
    assert reference["density"] == pytest.approx(1025.0)
    assert reference["source_reference_status"] == "passed"
    assert all(check["passed"] for check in reference["checks"].values())


def test_cases_cover_signed_coupled_states_speeds_and_rudder_variants(reference):
    cases = reference["cases"]
    assert len(cases) >= 9
    speeds = [case["speed_mps"] for case in cases]
    for expected in (3.0, 5.0, 6.214):
        assert any(math.isclose(value, expected, rel_tol=1e-3) for value in speeds)
    for key in ("v_mps", "yaw_rate_rad_s", "p_rad_s", "roll_rad"):
        values = [case[key] for case in cases]
        assert min(values) < 0.0 < max(values), key
    actual_rudder = [case["actual_rudder_deg"] for case in cases]
    for expected in (-35.0, -10.0, 10.0, 35.0):
        assert any(math.isclose(value, expected, abs_tol=1e-9)
                   for value in actual_rudder)
    assert all(case["u_mps"] > 0.0 and case["shaft_rpm"] > 0.0 for case in cases)

    variant_cases = [
        case for case in cases
        if math.isclose(abs(case["actual_rudder_deg"]), 35.0, abs_tol=1e-9)
    ]
    assert len(variant_cases) == 2
    for case in variant_cases:
        assert abs(case["commanded_rudder_deg"]) == pytest.approx(10.0)
    assert "35" in reference["rudder_note"]
    assert "10 degrees" in reference["rudder_note"]
    np.testing.assert_allclose(
        np.asarray(reference["derivative_states"], dtype=float),
        np.asarray([case["state"] for case in cases], dtype=float),
        rtol=0.0,
        atol=0.0,
    )
    np.testing.assert_allclose(
        np.asarray(reference["derivative_orders"], dtype=float),
        np.asarray([case["orders"] for case in cases], dtype=float),
        rtol=0.0,
        atol=0.0,
    )


def test_source_oracle_shapes_dimensions_and_reconstruction(reference):
    ncases = len(reference["cases"])
    derivatives = np.asarray(reference["derivatives"], dtype=float)
    hull_si = np.asarray(reference["hull_force_si"], dtype=float)
    hull_norm = np.asarray(reference["hull_force_norm"], dtype=float)
    explicit_norm = np.asarray(reference["explicit_hull_norm"], dtype=float)
    short = np.asarray(reference["short"], dtype=float)

    assert derivatives.shape == (ncases, 10)
    assert hull_si.shape == (ncases, 6)
    assert hull_norm.shape == (ncases, 4)
    assert explicit_norm.shape == (ncases, 4)
    assert short.shape == (2, reference["short_steps"] + 1, 10)
    assert np.all(np.isfinite(derivatives))
    assert np.all(np.isfinite(hull_si))
    assert np.all(np.isfinite(short))

    np.testing.assert_allclose(
        np.asarray(reference["mass_matrix_normalized"], dtype=float),
        oracle.source_mass_matrix(),
        rtol=0.0,
        atol=0.0,
    )
    np.testing.assert_array_equal(hull_si[:, 2], np.zeros(ncases))
    np.testing.assert_array_equal(hull_si[:, 4], np.zeros(ncases))
    np.testing.assert_allclose(hull_norm, explicit_norm, rtol=0.0, atol=1e-12)
    assert reference["reconstruction_max_abs"] <= 1e-12
    assert reference["explicit_match_max_abs"] <= 1e-12
    assert np.max(np.abs(np.asarray(reference["reconstruction_residual"]))) <= 1e-12


def test_source_hash_guard_rejects_a_changed_file(tmp_path, monkeypatch):
    changed = tmp_path / "container.m"
    changed.write_text("% tampered source\n")
    monkeypatch.setattr(oracle, "SOURCE_SHA256", {"container.m": "0" * 64})
    with pytest.raises(ValueError, match="checksum mismatch"):
        oracle.verify_sources(tmp_path)


def test_mutated_coefficient_is_rejected(reference):
    recovered = np.asarray(reference["hull_force_si"], dtype=float)
    mutated = np.asarray(reference["mutated_hull_si"], dtype=float)
    assert reference["mutation_coefficient"] == "Xvv"
    assert reference["mutation_factor"] == pytest.approx(1.01)
    difference = float(np.max(np.abs(mutated - recovered)))
    assert difference > 1.0
    check = oracle.compare_arrays(mutated, recovered, rtol=1e-11, atol=1e-8)
    assert not check["passed"]
    assert check["max_absolute_difference"] > 1.0


def test_rudder_35_degree_force_algebra_is_distinct(reference):
    ten = np.asarray(reference["rudder_variant_10deg_norm"], dtype=float)
    thirty_five = np.asarray(reference["rudder_variant_35deg_norm"], dtype=float)
    assert not np.allclose(ten, thirty_five, rtol=1e-12, atol=1e-12)
    assert np.max(np.abs(thirty_five - ten)) > 1e-9
    assert reference["rudder_original_command_limit_deg"] == pytest.approx(10.0)
    assert reference["rudder_variant_deg"] == pytest.approx(35.0)
    assert np.any(np.asarray(reference["rudder_variant_35deg_si"]) != 0.0)


def test_python_hull_force_core_matches_octave_reference(reference):
    core, status = oracle.load_python_core()
    if core is None:
        pytest.skip(status)
    states = np.asarray(reference["derivative_states"], dtype=float)
    forces = oracle.python_hull_forces(core, states)
    check = oracle.compare_arrays(
        forces, reference["hull_force_si"], rtol=1e-11, atol=1e-8
    )
    assert check["passed"], check


def test_python_hull_only_derivative_and_short_trajectory_match_octave(reference):
    core, status = oracle.load_python_core()
    if core is None:
        pytest.skip(status)
    states = np.asarray(reference["derivative_states"], dtype=float)
    derivatives = oracle.python_hull_only_derivatives(core, states)
    derivative_check = oracle.compare_arrays(
        derivatives, reference["hull_only_derivative"],
        rtol=1e-10, atol=1e-10,
    )
    assert derivative_check["passed"], derivative_check

    short = oracle.python_short_trajectories(
        core,
        np.asarray(reference["short_x0"], dtype=float),
        reference["short_h"],
        reference["short_steps"],
    )
    short_check = oracle.compare_arrays(
        short, reference["short"], rtol=1e-9, atol=1e-9
    )
    assert short_check["passed"], short_check


def test_report_separates_reference_oracle_and_runtime_status():
    if not REPORT.is_file():
        pytest.skip("Run the Octave manoeuvring pipeline to create report.json")
    report = json.loads(REPORT.read_text())
    assert report["original_container_reference_status"] == "passed"
    assert report["pure_hull_force_oracle_status"] == "passed"
    assert report["python_core_status"] in {"passed", "failed"} or (
        report["python_core_status"].startswith("pending")
    )
    assert report["python_hull_force_parity_status"] == report["python_core_status"]
    assert report["full_runtime_coupled_trajectory"] == "pending"
    assert len(report["octave_output_sha256"]) == 64


TWO_TIMESCALE_REFERENCE = Path("tests/fixtures/mss/two-timescale-reference.json")


@pytest.fixture(scope="module")
def two_timescale_reference():
    if not TWO_TIMESCALE_REFERENCE.is_file():
        pytest.skip(
            "Two-time-scale Octave reference is absent; run "
            ".venv/bin/python pipeline/validate_maneuvering_octave.py "
            "--two-timescale"
        )
    return json.loads(TWO_TIMESCALE_REFERENCE.read_text())


def test_two_timescale_fixture_metadata_shapes_and_scope(two_timescale_reference):
    reference = two_timescale_reference
    assert reference["source_commit"] == oracle.SOURCE_COMMIT
    assert reference["source_license"] == "MIT"
    assert reference["source_sha256"] == oracle.SOURCE_SHA256
    assert reference["source_sha256_expected"] == oracle.SOURCE_SHA256
    assert re.fullmatch(r"\d+\.\d+\.\d+", reference["octave_version"])
    assert "matched_two_timescale" in reference["octave_command"]
    assert reference["dt"] == pytest.approx(0.05)
    assert reference["steps"] == 200
    assert len(reference["case_names"]) >= 5
    assert "direct" in reference["case_kinds"]
    assert "runtime" in reference["case_kinds"]
    assert reference["source_native_baseline"]["status"] == "passed"
    assert reference["full_model_acceptance"].startswith("pending")
    assert "two-time-scale" in reference["scope"]
    assert "hydroVessel" in reference["scope"]
    assert reference["limitations"]

    for path in (
        "game/simulation/two_timescale.py",
        "game/simulation/runtime.py",
        "tests/reference/matched_two_timescale.m",
    ):
        assert len(reference["code_sha256"][path]) == 64

    ncases = len(reference["case_names"])
    steps = reference["steps"]
    inputs = reference["inputs"]
    octave = reference["octave"]
    assert np.asarray(inputs["lf0"]).shape == (ncases, 8)
    assert np.asarray(inputs["wf0"]).shape == (ncases, 12)
    assert np.asarray(inputs["current"]).shape == (ncases, steps, 2)
    assert np.asarray(inputs["wind_force"]).shape == (ncases, steps, 6)
    assert np.asarray(inputs["mean_surge"]).shape == (ncases, steps)
    assert np.asarray(inputs["wave_force"]).shape == (ncases, steps, 6)
    assert np.asarray(inputs["rudder_order"]).shape == (ncases, steps)
    assert np.asarray(inputs["rpm_order"]).shape == (ncases, steps)
    assert np.asarray(octave["lf_trajectory"]).shape == (ncases, steps + 1, 8)
    assert np.asarray(octave["wf_trajectory"]).shape == (ncases, steps + 1, 12)
    assert np.asarray(octave["servo_trajectory"]).shape == (ncases, steps + 1, 2)
    assert np.asarray(octave["total_x"]).shape == (ncases, steps + 1, 12)
    assert np.asarray(octave["total_dx"]).shape == (ncases, steps + 1, 12)
    assert np.asarray(octave["derivative_start"]).shape == (ncases, 22)
    assert np.asarray(octave["derivative_mid"]).shape == (ncases, 22)
    assert np.asarray(octave["derivative_end"]).shape == (ncases, 22)
    for key in ("lf_trajectory", "wf_trajectory", "servo_trajectory",
                "total_x", "total_dx"):
        assert np.all(np.isfinite(np.asarray(octave[key], dtype=float)))


def test_two_timescale_direct_cases_exercise_signed_lf_and_nonzero_wf(
        two_timescale_reference):
    reference = two_timescale_reference
    direct = [
        index for index, kind in enumerate(reference["case_kinds"])
        if kind == "direct"
    ]
    assert len(direct) >= 4
    total_x = np.asarray(reference["octave"]["total_x"], dtype=float)
    wf = np.asarray(reference["octave"]["wf_trajectory"], dtype=float)
    lf = np.asarray(reference["octave"]["lf_trajectory"], dtype=float)
    assert np.min(total_x[direct, :, 9]) < 0.0 < np.max(total_x[direct, :, 9])
    assert np.min(lf[direct, :, 7]) < 0.0 < np.max(lf[direct, :, 7])
    assert np.max(np.abs(wf[direct, :, 2])) > 0.01
    assert np.max(np.abs(wf[direct, :, 6:9])) > 0.1
    assert np.max(np.abs(wf[direct, :, 9:12])) > 0.01


def test_two_timescale_octave_matches_python_kernel_and_derivatives(
        two_timescale_reference):
    checks = oracle.compare_two_timescale_python(two_timescale_reference)
    assert checks["status"] == "passed", checks
    assert checks["max_absolute_difference"] < 1e-9
    for name, case in checks["cases"].items():
        assert case["passed"], (name, case)
        for key in ("derivative_start", "derivative_mid", "derivative_end"):
            assert case[key]["passed"], (name, key, case[key])


def test_two_timescale_bounded_runtime_comparison(two_timescale_reference):
    reference = two_timescale_reference
    if reference["runtime_status"].startswith("pending"):
        pytest.skip(reference["runtime_status"])
    if reference["runtime_status"].startswith("failed"):
        pytest.fail(reference["runtime_status"])
    try:
        runtime_case = oracle.run_runtime_two_timescale_case()
    except (ImportError, AttributeError, TypeError) as exc:
        pytest.skip(f"Runtime API unavailable: {exc}")
    checks = oracle.compare_two_timescale_runtime(reference, runtime_case)
    assert checks["status"] == "passed", checks
    assert checks["checks"]["samples"]["passed"]
    assert checks["runtime_stopping_reason"] == "duration_reached"
