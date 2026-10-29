from pathlib import Path
import math

import pytest

from pipeline.validate_toolkit import assess


def test_assessment_uses_real_fields_and_counterfactuals():
    if not Path("data/processed/s175/vessel.npz").exists():
        pytest.skip("Prepare the pinned S175 data first")
    report = assess(1)
    assert report["coarse_functional_pass"], report["checks"]
    for trial in report["trials"].values():
        assert math.isfinite(trial["heading_error_deg"])
        assert math.isfinite(trial["speed_error_mps"])
