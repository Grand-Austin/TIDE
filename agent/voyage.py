"""Plan a scene-specific authored voyage on the searoute MARNET sea graph.

The canonical port catalogue is ``web/game/ports.json``. It is a plain JSON
array extracted once, with Node, from ``web/game/deepwater-ports.js`` on
2026-10-30. The 223 entries keep the JavaScript list's name, country, longitude
and latitude without additions or corrections. The game UI imports the JSON so
the project maintains one port list.

The sea graph comes from ``searoute==1.6.0`` and its embedded Eurostat SeaRoute
MARNET data (``searoute/data/marnet_searoute.geojson``, Apache-2.0). Searoute
writes MARNET edge weights in kilometres; the embedded weights are rounded to
0.1 km. This module converts those weights to nautical miles with
``1 km = 1 / 1.852 nm``. It reads only package data and never downloads a graph
at runtime.

This is an authored game voyage, not a reconstructed ship log and not a
navigation product. The route is network-derived. It makes no claim about
weather safety, harbour entry, charted depth or the vessel's exact position on
a lane. The catalogue records container terminals; it is not a depth survey.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from game.scenario import ROOT

CATALOGUE_PATH = Path("web/game/ports.json")
CATALOGUE_PROVENANCE = (
    "Extracted with Node from web/game/deepwater-ports.js on 2026-10-30; "
    "NGA World Port Index (Pub. 150) coordinates where available, otherwise "
    "hand-placed; container ports with ocean-going calls, not a depth survey."
)
KM_PER_NM = 1.852
KM_TO_NM = 1.0 / KM_PER_NM
EARTH_RADIUS_NM = 3440.065
MIN_BRANCH_SEPARATION_DEG = 120.0
REAR_HALF_DEG = 90.0
DISTANCE_PENALTY_PER_NM = 0.1
SNAP = object()


class VoyageError(RuntimeError):
    """The voyage cannot be planned from the supplied inputs."""


class VoyageUnavailable(VoyageError):
    """A dependency, graph or plausible port pair is unavailable.

    ``status`` is a JSON-ready record for the Builder. It always has
    ``status: "pending"`` and a stable ``reason`` string.
    """

    def __init__(self, reason: str, message: str, *, status: dict | None = None):
        super().__init__(message)
        self.reason = reason
        self.status = dict(status or {})
        self.status.setdefault("status", "pending")
        self.status.setdefault("reason", reason)


@dataclass(frozen=True)
class _Port:
    name: str
    country: str
    lon: float
    lat: float

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "country": self.country,
            "lon": self.lon,
            "lat": self.lat,
        }


@dataclass(frozen=True)
class _Snap:
    point: tuple[float, float]
    distance_nm: float
    method: str
    node: Any
    edge: tuple[tuple[float, float], tuple[float, float]] | None = None
    edge_nodes: tuple[Any, Any] | None = None


@dataclass(frozen=True)
class _Graph:
    coords: dict[Any, tuple[float, float]]
    adjacency: dict[Any, dict[Any, float]]
    edges: tuple[tuple[Any, Any, float], ...]
    node_order: tuple[Any, ...]

    @classmethod
    def from_value(cls, graph) -> _Graph:
        if isinstance(graph, Mapping):
            adjacency, coords = _mapping_adjacency(graph), {}
        else:
            adjacency, coords = _object_adjacency(graph)
        if not adjacency:
            raise VoyageError("The maritime graph has no edges")

        directed = bool(
            not isinstance(graph, Mapping)
            and callable(getattr(graph, "is_directed", None))
            and graph.is_directed()
        )
        if not directed:
            _symmetrize(adjacency)

        for node, neighbours in adjacency.items():
            coords.setdefault(node, _node_coords(node, {}))
            for neighbour in neighbours:
                coords.setdefault(neighbour, _node_coords(neighbour, {}))

        missing = [node for node, point in coords.items() if point is None]
        if missing:
            raise VoyageError("Every graph node must carry (lon, lat) coordinates")

        order = tuple(
            sorted(coords, key=lambda node: _node_sort_key(node, coords[node]))
        )
        index = {node: position for position, node in enumerate(order)}
        seen, edges = set(), []
        for node in order:
            for neighbour, weight in sorted(
                adjacency[node].items(), key=lambda item: index[item[0]]
            ):
                key = (
                    min(index[node], index[neighbour]),
                    max(index[node], index[neighbour]),
                )
                if key in seen:
                    continue
                seen.add(key)
                edges.append((node, neighbour, weight))
        return cls(
            coords={node: coords[node] for node in order},
            adjacency={
                node: dict(
                    sorted(adjacency[node].items(), key=lambda item: index[item[0]])
                )
                for node in order
            },
            edges=tuple(edges),
            node_order=order,
        )

    def nearest_node(self, lon: float, lat: float):
        cosine = math.cos(math.radians(lat))
        best_node, best_distance = None, math.inf
        for node in self.node_order:
            node_lon, node_lat = self.coords[node]
            east = _wrap_lon(node_lon - lon) * cosine
            north = node_lat - lat
            distance = east * east + north * north
            if distance < best_distance:
                best_node, best_distance = node, distance
        return best_node


@dataclass(frozen=True)
class _PortRoute:
    port: _Port
    node: Any
    path: tuple[Any, ...]
    path_nodes: frozenset
    weight_km: float
    connector_nm: float
    branch_bearing_deg: float
    port_bearing_deg: float
    port_heading_delta_deg: float
    first_hop: Any | None

    @property
    def network_distance_nm(self) -> float:
        return self.weight_km * KM_TO_NM + self.connector_nm


@dataclass(frozen=True)
class _Pair:
    origin: _PortRoute
    destination: _PortRoute
    score: float
    branch_separation_deg: float


@dataclass(frozen=True)
class _Plan:
    snap: _Snap
    pairs: tuple[_Pair, ...]
    reachable_ports: int
    graph: _Graph
    base_graph: _Graph
    initial_lon: float
    initial_lat: float
    initial_heading_deg: float
    heading_deg: float
    preserved_initial: dict
    snap_fallback_from: str | None = None


def _mapping_adjacency(graph: Mapping):
    adjacency = {}
    for node, neighbours in graph.items():
        if not isinstance(neighbours, Mapping):
            raise VoyageError(
                "Graph adjacency values must map neighbours to attributes"
            )
        adjacency[node] = {
            neighbour: _edge_weight(value, node, neighbour, None)
            for neighbour, value in neighbours.items()
        }
    return adjacency


def _object_adjacency(graph):
    nodes = getattr(graph, "nodes", None)
    if nodes is None:
        raise VoyageError("The graph must expose nodes and adjacency")
    try:
        node_rows = list(nodes(data=True))
    except TypeError:
        node_rows = [(node, {}) for node in nodes]

    coords = {}
    for node, attrs in node_rows:
        coords[node] = _node_coords(node, attrs)

    adjacency = {}
    graph_adjacency = getattr(graph, "adj", None)
    if graph_adjacency is not None:
        for node, neighbours in graph_adjacency.items():
            adjacency[node] = {
                neighbour: _edge_weight(attrs, node, neighbour, coords)
                for neighbour, attrs in neighbours.items()
            }
    else:
        for node, neighbour, attrs in graph.edges(data=True):
            adjacency.setdefault(node, {})[neighbour] = _edge_weight(
                attrs, node, neighbour, coords
            )
    return adjacency, coords


def _edge_weight(value, node, neighbour, coords):
    if isinstance(value, Mapping):
        weight = value.get("weight")
    else:
        weight = value
    if weight is None:
        if coords is None or node not in coords or neighbour not in coords:
            raise VoyageError("A graph edge has no weight and no endpoint coordinates")
        weight = _haversine_km(coords[node], coords[neighbour])
    try:
        result = float(weight)
    except (TypeError, ValueError) as error:
        raise VoyageError("A graph edge weight is not numeric") from error
    if not math.isfinite(result) or result < 0.0:
        raise VoyageError("Graph edge weights must be finite and non-negative")
    return result


def _node_coords(node, attrs):
    if isinstance(node, (tuple, list)) and len(node) >= 2:
        lon, lat = node[0], node[1]
        if isinstance(lon, (int, float)) and isinstance(lat, (int, float)):
            return float(lon), float(lat)
    if isinstance(attrs, Mapping):
        lon, lat = attrs.get("x"), attrs.get("y")
        if isinstance(lon, (int, float)) and isinstance(lat, (int, float)):
            return float(lon), float(lat)
    return None


def _node_sort_key(node, point):
    return (point[0], point[1], str(node))


def _symmetrize(adjacency):
    for node, neighbours in list(adjacency.items()):
        for neighbour, weight in list(neighbours.items()):
            reverse = adjacency.setdefault(neighbour, {})
            previous = reverse.get(node)
            reverse[node] = weight if previous is None else min(previous, weight)


def _wrap_lon(lon: float) -> float:
    return (float(lon) + 180.0) % 360.0 - 180.0


def _clean_lon(lon: float) -> float:
    value = float(lon)
    return value if -180.0 <= value <= 180.0 else _wrap_lon(value)


def _wrap_180(angle: float) -> float:
    return (float(angle) + 180.0) % 360.0 - 180.0


def _haversine_nm(first, second) -> float:
    lon1, lat1 = map(math.radians, first)
    lon2, lat2 = map(math.radians, second)
    dlon, dlat = lon2 - lon1, lat2 - lat1
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_NM * math.asin(min(1.0, math.sqrt(a)))


def _haversine_km(first, second) -> float:
    return _haversine_nm(first, second) / KM_TO_NM


def _bearing_deg(first, second) -> float:
    lon1, lat1 = map(math.radians, first)
    lon2, lat2 = map(math.radians, second)
    dlon = _wrap_lon(math.degrees(lon2 - lon1))
    dlon = math.radians(dlon)
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(
        dlon
    )
    return math.degrees(math.atan2(y, x)) % 360.0


def _unit_vector(point):
    lon, lat = map(math.radians, point)
    return (
        math.cos(lat) * math.cos(lon),
        math.cos(lat) * math.sin(lon),
        math.sin(lat),
    )


def _angle(first, second):
    value = sum(a * b for a, b in zip(first, second))
    return math.acos(max(-1.0, min(1.0, value)))


def _lonlat(vector):
    x, y, z = vector
    return (
        math.degrees(math.atan2(y, x)),
        math.degrees(math.asin(max(-1.0, min(1.0, z)))),
    )


def _project_to_edge(point, first, second):
    """Project a point onto a great-circle edge.

    Returns ``(parameter, distance_nm, projected_lonlat)``. The parameter is
    measured from ``first`` to ``second`` on the minor arc. A projection outside
    the minor arc clamps to the nearer endpoint.
    """
    here = _unit_vector(point)
    start, end = _unit_vector(first), _unit_vector(second)
    arc = _angle(start, end)
    if arc <= 1e-12:
        return 0.0, _haversine_nm(point, first), first

    normal = (
        start[1] * end[2] - start[2] * end[1],
        start[2] * end[0] - start[0] * end[2],
        start[0] * end[1] - start[1] * end[0],
    )
    length = math.sqrt(sum(value * value for value in normal))
    if length <= 1e-12:
        distance_start = _angle(here, start) * EARTH_RADIUS_NM
        distance_end = _angle(here, end) * EARTH_RADIUS_NM
        if distance_start <= distance_end:
            return 0.0, distance_start, first
        return 1.0, distance_end, second
    normal = tuple(value / length for value in normal)
    dot = sum(a * b for a, b in zip(here, normal))
    projected = tuple(here[index] - dot * normal[index] for index in range(3))
    projected_length = math.sqrt(sum(value * value for value in projected))
    if projected_length <= 1e-12:
        distance_start = _angle(here, start) * EARTH_RADIUS_NM
        distance_end = _angle(here, end) * EARTH_RADIUS_NM
        if distance_start <= distance_end:
            return 0.0, distance_start, first
        return 1.0, distance_end, second
    projected = tuple(value / projected_length for value in projected)

    from_start = _angle(start, projected)
    from_end = _angle(projected, end)
    if from_start + from_end <= arc + 1e-9:
        parameter = max(0.0, min(1.0, from_start / arc))
        distance = _angle(here, projected) * EARTH_RADIUS_NM
        return parameter, distance, _lonlat(projected)

    distance_start = _angle(here, start) * EARTH_RADIUS_NM
    distance_end = _angle(here, end) * EARTH_RADIUS_NM
    if distance_start <= distance_end:
        return 0.0, distance_start, first
    return 1.0, distance_end, second


def _dijkstra(graph: _Graph, start):
    distances = {start: 0.0}
    previous = {}
    queue = [(0.0, 0, start)]
    sequence = 1
    while queue:
        distance, _, node = heapq.heappop(queue)
        if distance > distances.get(node, math.inf):
            continue
        for neighbour, weight in graph.adjacency[node].items():
            candidate = distance + weight
            if candidate < distances.get(neighbour, math.inf) - 1e-12:
                distances[neighbour] = candidate
                previous[neighbour] = node
                heapq.heappush(queue, (candidate, sequence, neighbour))
                sequence += 1
    return distances, previous


class VoyagePlanner:
    """Build one deterministic nautical voyage artifact for a scenario start.

    Parameters
    ----------
    project_root:
        Repository root that contains ``web/game/ports.json``.
    graph:
        Optional injected maritime graph. A mapping of node to neighbours, or a
        networkx/searoute-like object with ``nodes`` and ``adj``, is accepted.
        Edge weights are kilometres, as in searoute MARNET.
    ports:
        Optional injected port list. Each item has ``name``, ``country``,
        ``lon`` and ``lat``. The default is the canonical JSON catalogue.
    """

    def __init__(
        self,
        project_root: Path = ROOT,
        *,
        graph=None,
        ports: list[dict] | tuple[dict, ...] | None = None,
        catalogue_path: Path | None = None,
    ):
        self.project_root = Path(project_root)
        self._graph_value = graph
        self._ports_value = ports
        self._catalogue_file = (
            self.project_root / CATALOGUE_PATH
            if catalogue_path is None
            else Path(catalogue_path)
        )
        self._graph: _Graph | None = None
        self._ports: tuple[_Port, ...] | None = None
        self._catalogue_sha256: str | None = None
        self._catalogue_source: str | None = None
        self._searoute_version: str | None = None
        self._graph_source = "injected"
        self._nearest_nodes: dict[tuple[float, float], Any] = {}

    def build(self, initial: dict) -> dict:
        """Return the best authored voyage artifact for ``initial``.

        ``initial`` is read only. It needs ``lon``, ``lat`` and ``heading_deg``.
        A clear ``VoyageUnavailable`` is raised when no plausible network route
        exists; this method never substitutes a fixed route or a straight line.
        """
        plan = self._plan(initial)
        return self._materialise(plan.pairs[0], plan)

    def candidates(self, initial: dict, limit: int = 8) -> list[dict]:
        """Return up to ``limit`` deterministic voyage artifacts, best first."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        plan = self._plan(initial)
        return [self._materialise(pair, plan) for pair in plan.pairs[:limit]]

    def _load_graph(self) -> _Graph:
        if self._graph is not None:
            return self._graph
        if self._graph_value is not None:
            self._graph = _Graph.from_value(self._graph_value)
            return self._graph
        try:
            import searoute
            from searoute import get_graphs

            graph, _ = get_graphs()
            version = getattr(searoute, "__version__", None)
            self._graph = _Graph.from_value(graph)
            self._searoute_version = str(version) if version is not None else None
            self._graph_source = (
                "searoute embedded Eurostat SeaRoute MARNET "
                "(searoute/data/marnet_searoute.geojson)"
            )
        except ImportError as error:
            raise VoyageUnavailable(
                "searoute_unavailable",
                "searoute is not installed; install searoute==1.6.0 to plan a voyage",
                status={"dependency": "searoute", "required_version": "1.6.0"},
            ) from error
        except VoyageError:
            raise
        except Exception as error:
            raise VoyageUnavailable(
                "graph_unavailable",
                f"The searoute MARNET graph cannot be loaded: {error}",
                status={"dependency": "searoute", "required_version": "1.6.0"},
            ) from error

        if not self._searoute_version:
            raise VoyageUnavailable(
                "graph_version_unknown",
                "The searoute graph does not report a version",
                status={"dependency": "searoute"},
            )
        return self._graph

    def _load_ports(self) -> tuple[_Port, ...]:
        if self._ports is not None:
            return self._ports
        if self._ports_value is not None:
            raw = [dict(item) for item in self._ports_value]
            self._catalogue_source = "injected"
            self._catalogue_sha256 = hashlib.sha256(
                json.dumps(raw, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
        else:
            try:
                data = self._catalogue_file.read_bytes()
            except OSError as error:
                raise VoyageError(
                    f"Canonical port catalogue cannot be read: {self._catalogue_file}"
                ) from error
            self._catalogue_sha256 = hashlib.sha256(data).hexdigest()
            self._catalogue_source = str(
                self._catalogue_file.relative_to(self.project_root)
                if self._catalogue_file.is_relative_to(self.project_root)
                else self._catalogue_file
            )
            try:
                raw = json.loads(data)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise VoyageError(
                    "Canonical port catalogue is not valid JSON"
                ) from error
            if not isinstance(raw, list):
                raise VoyageError("Canonical port catalogue must be a JSON array")

        ports = []
        for item in raw:
            if not isinstance(item, Mapping):
                raise VoyageError("Each port catalogue entry must be an object")
            try:
                name = str(item["name"]).strip()
                country = str(item["country"]).strip()
                lon, lat = float(item["lon"]), float(item["lat"])
            except (KeyError, TypeError, ValueError) as error:
                raise VoyageError("A port needs name, country, lon and lat") from error
            if not name or not country:
                raise VoyageError("A port needs a non-empty name and country")
            if not (math.isfinite(lon) and math.isfinite(lat)):
                raise VoyageError(f"Port {name!r} has a non-finite coordinate")
            if not -90.0 <= lat <= 90.0:
                raise VoyageError(f"Port {name!r} has an invalid latitude")
            ports.append(_Port(name, country, lon, lat))
        if not ports:
            raise VoyageError("The port catalogue is empty")
        self._ports = tuple(ports)
        return self._ports

    def _initial(self, initial: dict) -> tuple[float, float, float, dict]:
        if not isinstance(initial, Mapping):
            raise VoyageError("initial must be a mapping")
        try:
            lon, lat = float(initial["lon"]), float(initial["lat"])
            heading = float(initial["heading_deg"])
        except (KeyError, TypeError, ValueError) as error:
            raise VoyageError(
                "initial needs numeric lon, lat and heading_deg"
            ) from error
        if not all(math.isfinite(value) for value in (lon, lat, heading)):
            raise VoyageError("initial lon, lat and heading_deg must be finite")
        if not -90.0 <= lat <= 90.0:
            raise VoyageError("initial latitude must be between -90 and 90 degrees")
        fixed = {
            key: initial[key]
            for key in ("time", "start", "speed_mps")
            if key in initial
        }
        return lon, lat, heading % 360.0, fixed

    def _snap(self, graph: _Graph, lon: float, lat: float) -> _Snap:
        node = graph.nearest_node(lon, lat)
        node_point = graph.coords[node]
        node_distance = _haversine_nm((lon, lat), node_point)

        best = None
        for first, second, weight in graph.edges:
            parameter, distance, projected = _project_to_edge(
                (lon, lat), graph.coords[first], graph.coords[second]
            )
            if best is None or distance < best[0] - 1e-9:
                best = (distance, first, second, weight, parameter, projected)
        if best is None or node_distance <= best[0] + 1e-9:
            point = (_clean_lon(node_point[0]), float(node_point[1]))
            return _Snap(point, node_distance, "nearest_network_node", node)

        distance, first, second, weight, parameter, projected = best
        if parameter <= 1e-9:
            first_point = graph.coords[first]
            return _Snap(
                (_clean_lon(first_point[0]), float(first_point[1])),
                _haversine_nm((lon, lat), first_point),
                "nearest_network_node",
                first,
            )
        if parameter >= 1.0 - 1e-9:
            second_point = graph.coords[second]
            return _Snap(
                (_clean_lon(second_point[0]), float(second_point[1])),
                _haversine_nm((lon, lat), second_point),
                "nearest_network_node",
                second,
            )

        first_point, second_point = graph.coords[first], graph.coords[second]
        point = (_clean_lon(projected[0]), float(projected[1]))
        return _Snap(
            point,
            distance,
            "nearest_edge_projection",
            SNAP,
            (first_point, second_point),
            (first, second),
        )

    def _with_snap(self, graph: _Graph, snap: _Snap) -> _Graph:
        """Add a virtual node when the start projects inside a network edge."""
        if snap.method != "nearest_edge_projection":
            return graph
        first, second = snap.edge
        first_node, second_node = snap.edge_nodes
        original = graph.adjacency[first_node][second_node]
        parameter, _, _ = _project_to_edge(snap.point, first, second)
        adjacency = {
            node: dict(neighbours) for node, neighbours in graph.adjacency.items()
        }
        adjacency[first_node].pop(second_node, None)
        adjacency[second_node].pop(first_node, None)
        adjacency[SNAP] = {
            first_node: max(original * parameter, 1e-12),
            second_node: max(original * (1.0 - parameter), 1e-12),
        }
        adjacency[first_node][SNAP] = adjacency[SNAP][first_node]
        adjacency[second_node][SNAP] = adjacency[SNAP][second_node]
        coords = dict(graph.coords)
        coords[SNAP] = snap.point
        order = tuple(graph.node_order) + (SNAP,)
        edges = tuple(graph.edges) + (
            (first_node, SNAP, adjacency[SNAP][first_node]),
            (SNAP, second_node, adjacency[SNAP][second_node]),
        )
        return _Graph(coords, adjacency, edges, order)

    def _plan(self, initial: dict) -> _Plan:
        lon, lat, heading, fixed = self._initial(initial)
        graph = self._load_graph()
        ports = self._load_ports()
        snap = self._snap(graph, lon, lat)
        try:
            return self._plan_at(
                graph,
                ports,
                snap,
                lon,
                lat,
                float(initial["heading_deg"]),
                heading,
                fixed,
            )
        except VoyageUnavailable as projected_error:
            if snap.method != "nearest_edge_projection":
                raise
            node = graph.nearest_node(lon, lat)
            point = (_clean_lon(graph.coords[node][0]), float(graph.coords[node][1]))
            node_snap = _Snap(
                point,
                _haversine_nm((lon, lat), point),
                "nearest_network_node",
                node,
            )
            try:
                return self._plan_at(
                    graph,
                    ports,
                    node_snap,
                    lon,
                    lat,
                    float(initial["heading_deg"]),
                    heading,
                    fixed,
                    fallback_from=projected_error.status.get("snap_method"),
                )
            except VoyageUnavailable as node_error:
                node_error.status["projected_snap"] = projected_error.status
                raise node_error from projected_error

    def _plan_at(
        self,
        graph: _Graph,
        ports: tuple[_Port, ...],
        snap: _Snap,
        initial_lon: float,
        initial_lat: float,
        initial_heading_deg: float,
        heading: float,
        fixed: dict,
        *,
        fallback_from: str | None = None,
    ) -> _Plan:
        routing = (
            self._with_snap(graph, snap)
            if snap.method == "nearest_edge_projection"
            else graph
        )
        distances, previous = _dijkstra(routing, snap.node)

        routes = []
        for port in ports:
            node = self._nearest_nodes.get((port.lon, port.lat))
            if node is None:
                node = graph.nearest_node(port.lon, port.lat)
                self._nearest_nodes[(port.lon, port.lat)] = node
            if node not in distances:
                continue
            path = self._path(previous, snap.node, node)
            first_hop = path[1] if len(path) > 1 else None
            branch_point = (
                routing.coords[first_hop]
                if first_hop is not None
                else (port.lon, port.lat)
            )
            branch_bearing = _bearing_deg(snap.point, branch_point)
            port_bearing = _bearing_deg(snap.point, (port.lon, port.lat))
            connector = _haversine_nm((port.lon, port.lat), routing.coords[node])
            routes.append(
                _PortRoute(
                    port=port,
                    node=node,
                    path=path,
                    path_nodes=frozenset(path),
                    weight_km=distances[node],
                    connector_nm=connector,
                    branch_bearing_deg=branch_bearing,
                    port_bearing_deg=port_bearing,
                    port_heading_delta_deg=_wrap_180(port_bearing - heading),
                    first_hop=first_hop,
                )
            )

        rear = [
            route
            for route in routes
            if abs(_wrap_180(route.port_heading_delta_deg - 180.0)) <= REAR_HALF_DEG
        ]
        forward = [
            route
            for route in routes
            if abs(route.port_heading_delta_deg) <= REAR_HALF_DEG
        ]
        pairs = []
        for origin in rear:
            for destination in forward:
                if origin.port == destination.port:
                    continue
                if (
                    origin.first_hop is not None
                    and origin.first_hop == destination.first_hop
                ):
                    continue
                if len(origin.path_nodes & destination.path_nodes) > 1:
                    continue
                separation = abs(
                    _wrap_180(
                        destination.branch_bearing_deg - origin.branch_bearing_deg
                    )
                )
                if separation < MIN_BRANCH_SEPARATION_DEG:
                    continue
                orientation = abs(
                    _wrap_180(origin.port_heading_delta_deg - 180.0)
                ) + abs(destination.port_heading_delta_deg)
                distance = origin.network_distance_nm + destination.network_distance_nm
                score = (
                    2.0 * orientation
                    + (180.0 - separation)
                    + DISTANCE_PENALTY_PER_NM * distance
                )
                pairs.append(_Pair(origin, destination, score, separation))

        if not pairs:
            raise VoyageUnavailable(
                "no_plausible_port_pair",
                "No reachable pair of distinct catalogue ports lies on opposite departure branches "
                "with the origin behind the initial heading and the destination ahead.",
                status={
                    "snapped_position": list(snap.point),
                    "snap_distance_nm": snap.distance_nm,
                    "snap_method": snap.method,
                    "reachable_ports": len(routes),
                    "catalogue_ports": len(ports),
                    "initial_heading_deg": heading,
                },
            )

        pairs.sort(
            key=lambda pair: (
                pair.score,
                pair.origin.port.name,
                pair.origin.port.country,
                pair.destination.port.name,
                pair.destination.port.country,
            )
        )
        return _Plan(
            snap,
            tuple(pairs),
            len(routes),
            routing,
            graph,
            initial_lon,
            initial_lat,
            initial_heading_deg,
            heading,
            fixed,
            fallback_from,
        )

    @staticmethod
    def _path(previous, start, end):
        if end == start:
            return (start,)
        path = [end]
        while path[-1] != start:
            try:
                path.append(previous[path[-1]])
            except KeyError as error:
                raise VoyageError("The graph path cannot be reconstructed") from error
        path.reverse()
        return tuple(path)

    def _track(self, pair: _Pair, plan: _Plan) -> list[tuple[float, float]]:
        points = []

        def append(point):
            lon, lat = _clean_lon(point[0]), float(point[1])
            if not points or points[-1] != (lon, lat):
                points.append((lon, lat))

        append((pair.origin.port.lon, pair.origin.port.lat))
        for node in reversed(pair.origin.path):
            append(plan.graph.coords[node])
        for node in pair.destination.path[1:]:
            append(plan.graph.coords[node])
        append((pair.destination.port.lon, pair.destination.port.lat))
        return points

    def _materialise(self, pair: _Pair, plan: _Plan) -> dict:
        track = self._track(pair, plan)
        distance_nm = sum(
            _haversine_nm(track[index - 1], track[index])
            for index in range(1, len(track))
        )
        crossings = [
            index
            for index in range(1, len(track))
            if abs(track[index][0] - track[index - 1][0]) > 180.0
        ]
        network_distance = (
            pair.origin.network_distance_nm + pair.destination.network_distance_nm
        )
        provenance = {
            "method": "weighted_dijkstra_through_snapped_network_position",
            "generation_method": "weighted_dijkstra_through_snapped_network_position",
            "authored": True,
            "source": self._graph_source,
            "searoute_version": self._searoute_version,
            "graph_source": self._graph_source,
            "graph_nodes": len(plan.base_graph.coords),
            "graph_edges": len(plan.base_graph.edges),
            "graph_weight_unit": "km",
            "weight_to_nm": KM_TO_NM,
            "distance_unit": "nm",
            "catalogue_path": self._catalogue_source,
            "catalogue_provenance": CATALOGUE_PROVENANCE,
            "catalogue_ports": len(self._load_ports()),
            "catalogue_sha256": self._catalogue_sha256,
            "coordinates": "WGS84 lon/lat",
            "snap_method": plan.snap.method,
            "snap_fallback_from": plan.snap_fallback_from,
            "snap_edge": (
                [list(point) for point in plan.snap.edge]
                if plan.snap.edge is not None
                else None
            ),
            "initial": {
                "lon": plan.initial_lon,
                "lat": plan.initial_lat,
                "heading_deg": plan.initial_heading_deg,
                **plan.preserved_initial,
            },
            "origin_port_bearing_deg": pair.origin.port_bearing_deg,
            "destination_port_bearing_deg": pair.destination.port_bearing_deg,
            "origin_departure_bearing_deg": pair.origin.branch_bearing_deg,
            "destination_departure_bearing_deg": pair.destination.branch_bearing_deg,
            "branch_separation_deg": pair.branch_separation_deg,
            "network_distance_nm": network_distance,
            "score": pair.score,
            "dateline_crossings": len(crossings),
            "dateline_crossing_indices": crossings,
        }
        return {
            "version": 1,
            "origin": pair.origin.port.as_dict(),
            "destination": pair.destination.port.as_dict(),
            "track": [[lon, lat] for lon, lat in track],
            "distance_nm": distance_nm,
            "snapped_position": list(plan.snap.point),
            "snap_distance_nm": plan.snap.distance_nm,
            "provenance": provenance,
            "authored": True,
        }
