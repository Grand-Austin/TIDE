import copy
import hashlib
import importlib.util
import itertools
import json
import math
from pathlib import Path

import pytest

from agent.voyage import VoyagePlanner, VoyageUnavailable

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = ROOT / "web/game/ports.json"


def _graph(edges):
    """Build an undirected adjacency mapping from (u, v, weight_km) edges."""
    adjacency = {}
    for first, second, weight in edges:
        adjacency.setdefault(first, {})[second] = {"weight": weight}
        adjacency.setdefault(second, {})[first] = {"weight": weight}
    return adjacency


def _port(name, point):
    return {"name": name, "country": "Test", "lon": point[0], "lat": point[1]}


def _close(first, second, tolerance=1e-9):
    return abs(first - second) <= tolerance


def _track_distance_nm(track):
    total = 0.0
    for first, second in itertools.pairwise(track):
        lon1, lat1 = map(math.radians, first)
        lon2, lat2 = map(math.radians, second)
        value = (
            math.sin((lat2 - lat1) / 2.0) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2.0) ** 2
        )
        total += 2.0 * 3440.065 * math.asin(min(1.0, math.sqrt(value)))
    return total


def test_canonical_catalogue_is_plain_json_array_from_the_game_list():
    data = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) == 223
    assert all(set(item) == {"name", "country", "lon", "lat"} for item in data)
    assert all(
        isinstance(item["name"], str)
        and item["name"]
        and isinstance(item["country"], str)
        and item["country"]
        and math.isfinite(item["lon"])
        and math.isfinite(item["lat"])
        for item in data
    )
    assert data[0] == {"name": "Luanda", "country": "Angola", "lon": 13.25, "lat": -8.8}
    assert {"Le Havre", "Halifax", "Brest", "A Coruña"} <= {
        item["name"] for item in data
    }


def test_weighted_dijkstra_beats_hop_count_and_uses_the_snapped_position():
    start = (0.0, 0.0)
    two_hops = (-0.4, 0.15)
    origin = (-2.0, 0.0)
    weighted = (-0.4, -0.15)
    middle = (-1.5, -0.1)
    destination = (2.0, 0.0)
    forward = (1.0, 0.0)
    graph = _graph(
        [
            (start, two_hops, 100.0),
            (two_hops, origin, 100.0),
            (start, weighted, 1.0),
            (weighted, middle, 1.0),
            (middle, origin, 1.0),
            (start, forward, 1.0),
            (forward, destination, 1.0),
        ]
    )
    planner = VoyagePlanner(
        graph=graph,
        ports=[_port("Origin", origin), _port("Destination", destination)],
    )
    initial = {"lon": 0.0, "lat": 0.0, "heading_deg": 90.0}
    voyage = planner.build(initial)

    assert voyage["origin"]["name"] == "Origin"
    assert voyage["destination"]["name"] == "Destination"
    assert [middle[0], middle[1]] in voyage["track"]
    assert [weighted[0], weighted[1]] in voyage["track"]
    assert [two_hops[0], two_hops[1]] not in voyage["track"]
    assert voyage["snapped_position"] == [0.0, 0.0]
    assert voyage["snap_distance_nm"] == pytest.approx(0.0, abs=1e-9)
    assert voyage["provenance"]["graph_weight_unit"] == "km"
    assert voyage["provenance"]["weight_to_nm"] == pytest.approx(1.0 / 1.852)
    assert planner.candidates(initial, limit=1) == [voyage]
    assert planner.build(copy.deepcopy(initial)) == voyage


def test_route_passes_through_an_edge_projection_not_only_a_node():
    west, centre, east = (-2.0, 0.0), (0.0, 0.0), (2.0, 0.0)
    graph = _graph(
        [
            (west, centre, 120.0),
            (centre, east, 60.0),
        ]
    )
    planner = VoyagePlanner(
        graph=graph,
        ports=[_port("West", west), _port("East", east)],
    )
    voyage = planner.build({"lon": 0.5, "lat": 0.2, "heading_deg": 90.0})

    snapped = voyage["snapped_position"]
    assert snapped[0] == pytest.approx(0.5, abs=1e-9)
    assert snapped[1] == pytest.approx(0.0, abs=1e-9)
    assert voyage["snap_distance_nm"] > 10.0
    assert voyage["provenance"]["snap_method"] == "nearest_edge_projection"
    assert any(
        _close(point[0], snapped[0]) and _close(point[1], snapped[1])
        for point in voyage["track"]
    )
    assert voyage["track"][0] == [west[0], west[1]]
    assert voyage["track"][-1] == [east[0], east[1]]


def test_ports_are_distinct_and_on_opposite_heading_branches():
    start = (0.0, 0.0)
    south = (0.0, -2.0)
    north = (0.0, 2.0)
    north_west = (-1.0, 2.0)
    north_east = (1.0, 2.0)
    graph = _graph(
        [
            (start, south, 10.0),
            (start, north, 10.0),
            (start, north_west, 12.0),
            (start, north_east, 12.0),
        ]
    )
    planner = VoyagePlanner(
        graph=graph,
        ports=[
            _port("North", north),
            _port("North West", north_west),
            _port("North East", north_east),
            _port("South", south),
        ],
    )
    voyage = planner.build({"lon": 0.0, "lat": 0.0, "heading_deg": 0.0})
    provenance = voyage["provenance"]

    assert voyage["origin"]["name"] == "South"
    assert voyage["destination"]["name"] in {"North", "North West", "North East"}
    assert voyage["origin"] != voyage["destination"]
    assert provenance["branch_separation_deg"] >= 120.0
    assert provenance["origin_port_bearing_deg"] == pytest.approx(180.0)
    assert provenance["destination_port_bearing_deg"] == pytest.approx(0.0)


def test_projected_edge_falls_back_to_its_nearest_node_when_split_cannot_orient():
    hub, dead_end = (0.0, 0.0), (1.0, 0.0)
    north, south = (0.0, 1.0), (0.0, -1.0)
    graph = _graph(
        [
            (hub, dead_end, 100.0),
            (hub, north, 100.0),
            (hub, south, 100.0),
        ]
    )
    planner = VoyagePlanner(
        graph=graph,
        ports=[_port("North", north), _port("South", south)],
    )
    voyage = planner.build({"lon": 0.05, "lat": 0.0, "heading_deg": 0.0})
    provenance = voyage["provenance"]

    assert voyage["origin"]["name"] == "South"
    assert voyage["destination"]["name"] == "North"
    assert provenance["snap_method"] == "nearest_network_node"
    assert provenance["snap_fallback_from"] == "nearest_edge_projection"
    assert voyage["snap_distance_nm"] > 0.0
    assert voyage["snapped_position"] == [0.0, 0.0]


def test_disconnected_ports_raise_a_pending_diagnostic_not_a_fallback():
    start, neighbour = (0.0, 0.0), (1.0, 0.0)
    island_a, island_b = (10.0, 10.0), (11.0, 10.0)
    graph = _graph([(start, neighbour, 10.0), (island_a, island_b, 10.0)])
    planner = VoyagePlanner(
        graph=graph,
        ports=[_port("Island A", island_a), _port("Island B", island_b)],
    )
    with pytest.raises(VoyageUnavailable) as raised:
        planner.build({"lon": 0.0, "lat": 0.0, "heading_deg": 90.0})

    assert raised.value.reason == "no_plausible_port_pair"
    assert raised.value.status["status"] == "pending"
    assert raised.value.status["reachable_ports"] == 0


def test_dateline_route_uses_wrapped_distance_and_does_not_mutate_input():
    west, dateline, east, far_east = (
        (179.0, 0.0),
        (180.0, 0.0),
        (-179.0, 0.0),
        (-178.0, 0.0),
    )
    graph = _graph(
        [
            (west, dateline, 111.0),
            (dateline, east, 111.0),
            (east, far_east, 111.0),
        ]
    )
    ports = [_port("West", west), _port("Far East", far_east)]
    planner = VoyagePlanner(graph=graph, ports=ports)
    initial = {
        "lon": 179.5,
        "lat": 0.0,
        "heading_deg": 90.0,
        "time": "2026-10-30T00:00:00Z",
    }
    initial_before = copy.deepcopy(initial)
    ports_before = copy.deepcopy(ports)

    voyage = planner.build(initial)

    assert initial == initial_before
    assert ports == ports_before
    assert voyage["origin"]["name"] == "West"
    assert voyage["destination"]["name"] == "Far East"
    assert all(-180.0 <= point[0] <= 180.0 for point in voyage["track"])
    assert 150.0 < voyage["distance_nm"] < 250.0
    assert voyage["distance_nm"] == pytest.approx(_track_distance_nm(voyage["track"]))
    assert voyage["provenance"]["dateline_crossings"] >= 1
    assert voyage["provenance"]["dateline_crossing_indices"]


@pytest.mark.skipif(
    importlib.util.find_spec("searoute") is None, reason="searoute is not installed"
)
def test_real_biscay_smoke_uses_marnet_and_canonical_catalogue():
    planner = VoyagePlanner()
    initial = {
        "lon": -10.4929,
        "lat": 48.6908,
        "heading_deg": 246.5,
        "speed_mps": 5.0,
        "time": "2026-10-22T21:00:00Z",
    }
    initial_before = copy.deepcopy(initial)
    voyage = planner.build(initial)

    assert initial == initial_before
    assert voyage["version"] == 1
    assert voyage["authored"] is True
    assert voyage["origin"] != voyage["destination"]
    assert voyage["track"][0] == [voyage["origin"]["lon"], voyage["origin"]["lat"]]
    assert voyage["track"][-1] == [
        voyage["destination"]["lon"],
        voyage["destination"]["lat"],
    ]
    assert voyage["distance_nm"] > 100.0
    assert voyage["distance_nm"] == pytest.approx(_track_distance_nm(voyage["track"]))
    assert voyage["snap_distance_nm"] >= 0.0
    assert any(
        _close(point[0], voyage["snapped_position"][0], tolerance=1e-9)
        and _close(point[1], voyage["snapped_position"][1], tolerance=1e-9)
        for point in voyage["track"]
    )
    provenance = voyage["provenance"]
    assert provenance["searoute_version"] == "1.6.0"
    assert provenance["graph_weight_unit"] == "km"
    assert provenance["catalogue_ports"] == 223
    assert (
        provenance["catalogue_sha256"]
        == hashlib.sha256(CATALOGUE.read_bytes()).hexdigest()
    )
    assert provenance["snap_method"] in {
        "nearest_network_node",
        "nearest_edge_projection",
    }
    assert provenance["branch_separation_deg"] >= 120.0
