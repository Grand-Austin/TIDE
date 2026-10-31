import json
from pathlib import Path
import pytest

from game.scenario import load_scenario
from pipeline.scenario import build, displacement

SOURCE = Path("scenarios/sole-20261022-21/scenario.json")


def test_bad_initial_state_and_budget(tmp_path):
    d = json.loads(SOURCE.read_text())
    budget = d["mission"]["budget_s"]
    d["mission"]["budget_s"] = 1
    p = tmp_path / "scenario.json"
    p.write_text(json.dumps(d))
    with pytest.raises(ValueError, match="budget"):
        load_scenario(p)
    d["mission"]["budget_s"] = budget
    d["initial"]["lat"] = 90
    p.write_text(json.dumps(d))
    with pytest.raises(ValueError, match="coordinates"):
        load_scenario(p)


def test_source_integrity_is_required(tmp_path):
    d = json.loads(SOURCE.read_text())
    d["environment"]["waves"]["sha256"] = "bad"
    p = tmp_path / "scenario.json"
    p.write_text(json.dumps(d))
    with pytest.raises(ValueError, match="checksum"):
        with load_scenario(p).environment():
            pass


def test_build_reproducible_and_complete(tmp_path):
    build(SOURCE, tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    build(SOURCE, tmp_path)
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    manifest = json.loads((tmp_path / "data-manifest.json").read_text())
    from game.cargo import CargoModel
    assert manifest["gameplay"]["cargo"] == CargoModel().config
    assert len(manifest["hourly"]) == 3
    assert manifest["hourly"][0]["initial"]["hs"] > 3
    assert manifest["hourly"][-1]["target"]["hs"] < manifest["hourly"][0]["initial"]["hs"]
    assert not manifest["sources"]["waves"]["available_to_player"]


def test_heading_and_distance():
    assert displacement((3, 40), (3, 40)) == (0, 0)
    distance, heading = displacement((3, 40), (3, 39.9))
    assert distance == pytest.approx(11119.49, rel=1e-5)
    assert heading == 180


def test_vessel_prepared_elsewhere_from_the_pinned_source_is_accepted(tmp_path):
    import shutil
    from game.scenario import check_vessel

    model = tmp_path / "s175"
    shutil.copytree("data/processed/s175", model)
    pinned = json.loads(SOURCE.read_text())["vessel"]
    check_vessel(model, dict(pinned, sha256="prepared on another machine"))
    with pytest.raises(ValueError, match="vessel checksum"):
        check_vessel(model, dict(pinned, sha256="other", source_commit="other"))
    provenance = json.loads((model / "provenance.json").read_text())
    provenance["source_sha256"] = "other"
    (model / "provenance.json").write_text(json.dumps(provenance))
    with pytest.raises(ValueError, match="vessel checksum"):
        check_vessel(model, dict(pinned, sha256="other"))


def test_scenario_split_run_preserves_runtime_state():
    import numpy as np
    from game.simulation.runtime import Command

    scenario = load_scenario(SOURCE)
    with scenario.environment() as env:
        sim = scenario.simulation(env)
        state = scenario.initial_state(sim)
        full = sim.advance(state, Command(155, 3), 1)
        a = sim.advance(state, Command(155, 3), 0.4)
        split = sim.advance(a.state, Command(155, 3), 0.6)
        np.testing.assert_array_equal(full.state.x, split.state.x)
        assert full.state.exposure == split.state.exposure


def test_reports_exclude_unrelated_dates(tmp_path):
    from pipeline.scenario import report_evidence
    from game.scenario import Scenario

    d = json.loads(SOURCE.read_text())
    archive = tmp_path / "data/raw/aemet"
    archive.mkdir(parents=True)
    report = {
        "kind": "official_forecast",
        "issued_at": "2026-10-29T10:00:00Z",
        "valid_from": "2026-10-29T12:00:00Z",
        "valid_to": "2026-10-30T12:00:00Z",
    }
    (archive / "today.json").write_text(json.dumps(report))
    scenario = Scenario(d, tmp_path)
    assert report_evidence(scenario) == []
    report.update(
        issued_at="2026-10-22T20:00:00Z",
        valid_from="2026-10-22T20:00:00Z",
        valid_to="2026-10-22T23:00:00Z",
    )
    (archive / "historical.json").write_text(json.dumps(report))
    assert report_evidence(scenario) == [report]


def test_build_refuses_report_for_another_definition(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"passed": True, "definition_sha256": "wrong"}))
    with pytest.raises(ValueError, match="Validation"):
        build(SOURCE, tmp_path / "package", report)
    assert not (tmp_path / "package").exists()
