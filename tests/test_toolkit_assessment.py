from pathlib import Path
import math

import pytest

import pipeline.validate_toolkit as validate_toolkit
from game.simulation.environment import SeaState
from pipeline.validate_toolkit import assess


def test_assessment_uses_real_fields_and_counterfactuals():
    if not Path("data/processed/s175/vessel.npz").exists():
        pytest.skip("Prepare the pinned S175 data first")
    report = assess(1)
    assert report["coarse_functional_pass"], report["checks"]
    for trial in report["trials"].values():
        assert math.isfinite(trial["heading_error_deg"])
        assert math.isfinite(trial["speed_error_mps"])


@pytest.mark.parametrize("provenance, missing_sources", [
    ({"waves": {}, "currents": {}}, {"temperature", "wind"}),
    ({"waves": {}, "currents": {}, "wind": {}}, {"temperature"}),
    ({"waves": {}, "currents": {}, "temperature": {}}, {"wind"}),
])
def test_assessment_rejects_missing_sources_before_vessel_or_trials(
        monkeypatch, provenance, missing_sources):
    class MissingEnvironment:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def sample(self, time, lon, lat):
            return SeaState(1.0, 8.0, 90.0, sst_c=None)

    environment = MissingEnvironment()
    environment.provenance = provenance
    monkeypatch.setattr(
        validate_toolkit.CopernicusEnvironment,
        "from_directory",
        lambda path: environment,
    )

    def fail_load_s175():
        pytest.fail("vessel loading must happen after environment preflight")

    def fail_simulation(*args, **kwargs):
        pytest.fail("simulation trials must not run without required environment sources")

    monkeypatch.setattr(validate_toolkit, "load_s175", fail_load_s175)
    monkeypatch.setattr(validate_toolkit, "Simulation", fail_simulation)

    with pytest.raises(ValueError) as error:
        assess(1)

    message = str(error.value)
    for source in missing_sources:
        assert source in message
