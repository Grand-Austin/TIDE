import json
from copy import deepcopy
from types import SimpleNamespace

from game.live import Game
from game.scenario import ROOT


def scenario_definition():
    path = ROOT / "scenarios/biscay-20251218-03/scenario.json"
    return json.loads(path.read_text())


def briefing_for(definition):
    game = object.__new__(Game)  # briefing() reads the scenario definition and the rules' field extent
    game.scenario = SimpleNamespace(definition=definition)
    game.session = SimpleNamespace(rules=SimpleNamespace(report_bbox=definition["area_bbox"]))
    return game.briefing()


AUTHORED_VOYAGE = {
    "version": 1,
    "origin": {"name": "Brest", "country": "France", "lon": -4.5, "lat": 48.383},
    "destination": {"name": "A Coruña", "country": "Spain", "lon": -8.4, "lat": 43.367},
    "track": [[-4.5, 48.383], [-6.2, 47.4], [-8.4, 43.367]],
    "distance_nm": 321.4,
    "snapped_position": [-4.7, 48.1],
    "snap_distance_nm": 12.3,
    "provenance": {"source": "searoute", "version": "1.6.0"},
    "authored": True,
}


def test_briefing_carries_the_authored_planned_voyage():
    definition = scenario_definition()
    definition["voyage"] = deepcopy(AUTHORED_VOYAGE)

    brief = briefing_for(definition)

    assert brief["voyage"] == AUTHORED_VOYAGE
    assert brief["voyage"]["authored"] is True
    assert brief["voyage"]["origin"]["name"] == "Brest"
    assert brief["voyage"]["destination"]["name"] == "A Coruña"


def test_a_legacy_definition_without_a_voyage_reports_no_plan():
    definition = scenario_definition()
    definition.pop("voyage", None)

    brief = briefing_for(definition)

    assert brief.get("voyage") is None
