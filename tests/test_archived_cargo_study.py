import hashlib
import json
from pathlib import Path
import sys

import numpy as np

from archive.pipeline import cargo_study


def test_archived_simulation_and_report_use_the_archived_sources(tmp_path, monkeypatch):
    trace_path = cargo_study.simulate(("calm", 1, 0.15, 0.05, str(tmp_path)))
    with np.load(trace_path, allow_pickle=False) as data:
        assert data["trace"].shape == (3, 8)
        np.testing.assert_allclose(data["trace"][:, 0], [0.05, 0.10, 0.15])

    output = tmp_path / "report"
    monkeypatch.setattr(sys, "argv", ["cargo_study", "--replay", str(tmp_path),
                                    "--output", str(output)])
    # The regression checks generation and provenance, not the separate sensitivity sweep.
    monkeypatch.setattr(cargo_study, "prescribed_rolls", lambda: [])
    cargo_study.main()
    report = json.loads((output / "results.json").read_text())
    hashes = report["source_sha256"]
    for path in ("archive/game/cargo.py", "archive/pipeline/cargo_study.py",
                 "archive/pipeline/cargo_stacks.py"):
        assert hashes[path] == hashlib.sha256(Path(path).read_bytes()).hexdigest()
        assert path.removeprefix("archive/") not in hashes
