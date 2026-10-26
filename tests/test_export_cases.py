import json

import pytest

from pipeline.export_cases import CASES, OUT
from pipeline.routes import ROUTES

FILES = [OUT / f"{case_id}.json" for case_id in CASES]


def best(entry):
    safe = [(r["arrival"], k) for k, r in entry.items() if r["safe"]]
    return min(safe)[1] if safe else None


@pytest.mark.skipif(not all(f.exists() for f in FILES), reason="run python -m pipeline.export_cases first")
@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_case_file_is_consistent(path):
    case = json.loads(path.read_text())
    assert {k: v["points"] for k, v in case["routes"].items()} == {k: [list(p) for p in v] for k, v in ROUTES.items()}
    grid = case["grid"]
    assert len(grid["times"]) == len(grid["layers"]["hs"]["values"])
    assert all(len(t) == len(grid["lat"]) and len(t[0]) == len(grid["lon"]) for t in grid["layers"]["hs"]["values"])
    sequence = [(dep, best(entry)) for dep, entry in sorted(case["sims"].items())]
    assert all(set(entry) == {"A", "B", "C"} for entry in case["sims"].values())
    flips = [(dep, prev, cur) for (_, prev), (dep, cur) in zip(sequence, sequence[1:]) if prev != cur]
    assert flips == [(f["at"], f["from"], f["to"]) for f in case["flips"]]
