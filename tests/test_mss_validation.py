import json
from pathlib import Path

import numpy as np
import pytest
from scipy.io import loadmat

from game.simulation.vessel import load_s175
from pipeline.validate_mss import compare, prepare, rk4
from pipeline.validate_storm import trial


def test_port_matches_executed_octave_reference(tmp_path):
    reference = json.loads(Path("tests/fixtures/mss/s175-reference.json").read_text())
    expected = prepare(tmp_path)
    for key in (
        "converted_M",
        "converted_MA",
        "converted_D",
        "converted_G",
        "derivatives",
        "wave_force",
        "wave_elevation",
        "wave_mean_surge",
    ):
        np.testing.assert_allclose(expected[key], reference[key], rtol=1e-8, atol=1e-9)
    np.testing.assert_allclose(
        expected["trajectory"][reference["trajectory_indices"]],
        reference["trajectory"],
        rtol=1e-8,
        atol=1e-9,
    )
    assert (
        np.asarray(
            loadmat(tmp_path / "inputs.mat", simplify_cells=True)["main"]["m"]
        ).dtype
        == np.float64
    )


def test_force_tape_replays_the_accepted_physical_steps(tmp_path):
    row = trial("head", tmp_path, seconds=0.2)
    with np.load(tmp_path / row["replay"]) as tape:
        x = tape["states"][0]
        vessel = load_s175()
        for force, current in zip(tape["forces"], tape["currents"]):
            x = rk4(x, force, vessel, current, float(tape["h"]))
        np.testing.assert_allclose(x, tape["states"][-1], atol=1e-12, rtol=0)
    prepared = prepare(
        tmp_path / "replay", seconds=0.1, replay=tmp_path / row["replay"]
    )
    assert prepared["trajectory"].shape == (3, 12)
    with pytest.raises(ValueError, match="outside"):
        prepare(tmp_path / "invalid", seconds=1, replay=tmp_path / row["replay"])


def test_comparison_rejects_a_changed_reference():
    expected = {"derivatives": np.zeros((4, 12))}
    actual = {"derivatives": np.zeros((4, 12))}
    assert compare(expected, actual)["derivatives"]["passed"]
    actual["derivatives"][2, 0] = 0.01
    assert not compare(expected, actual)["derivatives"]["passed"]


@pytest.mark.parametrize("knots", [12, 14, 16, 24])
def test_high_speed_trials_convert_knots_and_keep_distinct_replays(tmp_path, knots):
    row = trial("calm", tmp_path, seconds=0.1, speed_knots=knots)
    assert row["speed_mps"] == pytest.approx(knots * 1852 / 3600)
    assert row["speed_knots"] == pytest.approx(knots)
    assert f"-{knots}kn.npz" in row["replay"]
    with np.load(tmp_path / row["replay"]) as tape:
        assert tape["states"][0, 0] == pytest.approx(knots * 1852 / 3600)
    assert row["stopping_reason"] == "duration_reached"
