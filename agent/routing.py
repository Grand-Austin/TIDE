"""Deterministic route-candidate search over a scenario's scheduled escape region.

The search reimplements two published routing ideas for TIDE. It does not copy upstream code.

* ``simroute`` is an A* search with the Aertssen wave-speed penalty from SIMROUTE
  (Grifoll et al., 2022; https://github.com/ManelGrifoll/SIMROUTE). The upstream
  repository has no licence file, so this module contains no upstream source. The
  reference exercise uses SIMROUTE's Bowditch WEN_form=1; the TIDE adaptation uses
  WEN_form=2 because Bowditch gives sub-knot speeds for S175 in storm seas.
* ``visir2`` is a time-dependent Dijkstra search with the current composition
  SOG = w_parallel + sqrt(STW^2 - w_cross^2), following VISIR-2 (Mannarini et al.,
  2024; GPL-3.0-or-later, https://doi.org/10.5281/zenodo.10960842). It uses an
  Aertssen speed-loss proxy because no VISIR-2 performance curve exists for S175.

Both searches are TIDE adaptations, not original benchmark reproductions. They produce
command sequences for later full-physics validation. They do not replace TIDE dynamics.
Provenance and benchmark status are in ``agent/references/``.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import timedelta
import heapq
import math

import numpy as np

from game.navigation import CoastGuard
from game.rules.mission import inside
from game.scenario import utc

EARTH_RADIUS_NM = 3440.065
MIN_SPEED_KN = 0.5
MIN_SOG_KN = 0.5
MAX_GRID_CELLS = 42
TIME_KNOTS = 9
GOAL_SLICES = 5
COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
STEP_OFFSETS = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))
SIMROUTE_SOLVER = "simroute:astar_aertssen"
VISIR2_SOLVER = "visir2:td_dijkstra_aertssen_current"


@dataclass(frozen=True, slots=True)
class _Sea:
    hs_m: float
    tp_s: float
    dir_sin: float
    dir_cos: float
    current_east_mps: float
    current_north_mps: float

    @property
    def wave_from_deg(self):
        return math.degrees(math.atan2(self.dir_sin, self.dir_cos)) % 360


@dataclass(frozen=True)
class _Edge:
    dt_s: float
    course_deg: float
    heading_deg: float
    stw_kn: float
    sog_kn: float
    sea: _Sea


class _Grid:
    """Regular local grid anchored on the designer's fixed start position."""

    def __init__(self, bbox, start_lon, start_lat):
        west, east, south, north = (float(v) for v in bbox)
        self.start_lon, self.start_lat = float(start_lon), float(start_lat)
        if not west < east or not south < north:
            raise ValueError("Scenario area_bbox must have positive extent")
        if not (west <= start_lon <= east and south <= start_lat <= north):
            raise ValueError("Scenario start position lies outside area_bbox")
        self.dlon = max((east - west) / (MAX_GRID_CELLS - 1), (north - south) / (MAX_GRID_CELLS - 1), 0.005)
        self.dlat = self.dlon
        i0 = math.ceil((west - start_lon) / self.dlon - 1e-9)
        i1 = math.floor((east - start_lon) / self.dlon + 1e-9)
        j0 = math.ceil((south - start_lat) / self.dlat - 1e-9)
        j1 = math.floor((north - start_lat) / self.dlat + 1e-9)
        if i1 - i0 < 1 or j1 - j0 < 1:
            raise ValueError("Scenario area_bbox is too small for the routing grid")
        self.lon = start_lon + np.arange(i0, i1 + 1) * self.dlon
        self.lat = start_lat + np.arange(j0, j1 + 1) * self.dlat
        self.nx, self.ny = len(self.lon), len(self.lat)
        self.start = self.node(-i0, -j0)
        self.bbox = (west, east, south, north)

    def node(self, i, j):
        return j * self.nx + i

    def ij(self, node):
        j, i = divmod(int(node), self.nx)
        return i, j

    def lonlat(self, node):
        i, j = self.ij(node)
        return float(self.lon[i]), float(self.lat[j])

    def nearest(self, lon, lat):
        i = int(np.abs(self.lon - lon).argmin())
        j = int(np.abs(self.lat - lat).argmin())
        return self.node(i, j)

    def neighbours(self):
        out = []
        for j in range(self.ny):
            for i in range(self.nx):
                cell = []
                for di, dj in STEP_OFFSETS:
                    x, y = i + di, j + dj
                    if 0 <= x < self.nx and 0 <= y < self.ny:
                        cell.append(self.node(x, y))
                out.append(tuple(cell))
        return tuple(out)


def _prepare_adjacency(grid, guard):
    """Static edge geometry, including the two side cells of a diagonal step."""
    prepared = []
    neighbours = grid.neighbours()
    for node in range(grid.nx * grid.ny):
        i, j = grid.ij(node)
        lon, lat = grid.lonlat(node)
        row = []
        for neighbour in neighbours[node]:
            x, y = grid.ij(neighbour)
            other_lon, other_lat = grid.lonlat(neighbour)
            if not guard.segment_allowed((lon, lat), (other_lon, other_lat)):
                continue
            distance, course = _distance_bearing((lon, lat), (other_lon, other_lat))
            side_a = side_b = -1
            if i != x and j != y:
                side_a, side_b = grid.node(x, j), grid.node(i, y)
                if not (guard.allowed(*grid.lonlat(side_a)) and
                        guard.allowed(*grid.lonlat(side_b))):
                    continue
            row.append((neighbour, (distance, course, side_a, side_b)))
        prepared.append(tuple(row))
    return tuple(prepared)


class _FieldGrid:
    """Finite wave and current fields on the routing grid at bounded time knots."""

    def __init__(self, environment, grid, start_time, horizon_s, guard):
        n_time = min(TIME_KNOTS, max(2, int(math.ceil(horizon_s / 300.0)) + 1))
        self.times = np.linspace(0.0, float(horizon_s), n_time)
        self.step = float(self.times[1] - self.times[0])
        shape = (n_time, grid.ny, grid.nx)
        self.hs = np.full(shape, np.nan)
        self.tp = np.full(shape, np.nan)
        self.dir_sin = np.full(shape, np.nan)
        self.dir_cos = np.full(shape, np.nan)
        self.current_east = np.full(shape, np.nan)
        self.current_north = np.full(shape, np.nan)
        for k, elapsed in enumerate(self.times):
            when = start_time + timedelta(seconds=float(elapsed))
            for j, lat in enumerate(grid.lat):
                for i, lon in enumerate(grid.lon):
                    if not guard.allowed(float(lon), float(lat)):
                        continue
                    try:
                        sea = environment.sample(when, float(lon), float(lat))
                        values = (sea.hs, sea.tp, sea.wave_from_deg,
                                  sea.current_east_mps, sea.current_north_mps)
                    except (ValueError, OSError):
                        continue  # missing data, land or a time/domain boundary
                    if not all(math.isfinite(v) for v in values):
                        continue
                    angle = math.radians(values[2])
                    self.hs[k, j, i] = values[0]
                    self.tp[k, j, i] = values[1]
                    self.dir_sin[k, j, i] = math.sin(angle)
                    self.dir_cos[k, j, i] = math.cos(angle)
                    self.current_east[k, j, i] = values[3]
                    self.current_north[k, j, i] = values[4]

    @property
    def horizon_s(self):
        return float(self.times[-1])

    def sample(self, node, elapsed_s):
        if not math.isfinite(elapsed_s) or elapsed_s < 0 or elapsed_s > self.horizon_s + 1e-9:
            return None
        j, i = divmod(int(node), self.hs.shape[2])
        upper = min(len(self.times) - 1, max(1, int(elapsed_s / self.step) + 1))
        lower = upper - 1
        span = self.times[upper] - self.times[lower]
        fraction = 0.0 if span <= 0 else min(1.0, max(0.0, (elapsed_s - self.times[lower]) / span))
        values = []
        for field in (self.hs, self.tp, self.dir_sin, self.dir_cos,
                      self.current_east, self.current_north):
            a, b = field[lower, j, i], field[upper, j, i]
            if not (math.isfinite(a) and math.isfinite(b)):
                return None
            values.append(float(a + (b - a) * fraction))
        if math.hypot(values[2], values[3]) < 1e-10:
            return None
        return _Sea(*values)


def _bowditch_loss_kn(hs_m, wave_from_deg, course_deg):
    """Bowditch penalty from SIMROUTE's WEN_form=1, in feet of significant height."""
    theta = (wave_from_deg - course_deg) % 360
    if 45 <= theta <= 135 or 225 <= theta <= 315:
        factor = 0.0165
    elif 135 < theta < 225:
        factor = 0.0083
    elif 0 <= theta < 45 or 315 < theta <= 360:
        factor = 0.0248
    else:
        factor = 0.0
    return factor * (hs_m * 3.2808) ** 2


def _aertssen_loss_kn(speed_kn, hs_m, wave_from_deg, course_deg, length_m):
    """Aertssen penalty from SIMROUTE's WEN_form=2, applied to a TIDE speed choice."""
    theta = min((wave_from_deg - course_deg) % 360, 360 - (wave_from_deg - course_deg) % 360)
    if hs_m < 2.5:
        m = n = 0
    elif hs_m < 4.0:
        m, n = (900, 2) if theta <= 30 else (700, 2) if theta <= 60 else (350, 1) if theta <= 150 else (100, 0)
    elif hs_m < 5.5:
        m, n = (1300, 6) if theta <= 30 else (1000, 5) if theta <= 60 else (500, 3) if theta <= 150 else (200, 1)
    elif hs_m < 7.5:
        m, n = (2100, 11) if theta <= 30 else (1400, 8) if theta <= 60 else (700, 5) if theta <= 150 else (400, 2)
    else:
        m, n = (3600, 18) if theta <= 30 else (2300, 12) if theta <= 60 else (1000, 7) if theta <= 150 else (700, 3)
    return speed_kn * (m / length_m + n) / 100.0


def _wave_speed_kn(speed_kn, sea, course_deg, model, length_m):
    if model == "bowditch":
        loss = _bowditch_loss_kn(sea.hs_m, sea.wave_from_deg, course_deg)
    else:
        loss = _aertssen_loss_kn(speed_kn, sea.hs_m, sea.wave_from_deg, course_deg, length_m)
    return max(0.0, speed_kn - loss)


def _current_composition(speed_kn, sea, course_deg, length_m):
    """VISIR-2 current composition. Returns STW, SOG and the heading to make good the course."""
    stw = _wave_speed_kn(speed_kn, sea, course_deg, "aertssen", length_m)
    if stw < MIN_SPEED_KN:
        return None
    east, north = sea.current_east_mps * 1.9438444924406, sea.current_north_mps * 1.9438444924406
    radians = math.radians(course_deg)
    along = east * math.sin(radians) + north * math.cos(radians)
    cross = east * math.cos(radians) - north * math.sin(radians)
    if abs(cross) >= stw:
        return None
    sog = along + math.sqrt(stw * stw - cross * cross)
    if sog < MIN_SOG_KN:
        return None
    water_east = sog * math.sin(radians) - east
    water_north = sog * math.cos(radians) - north
    if math.hypot(water_east, water_north) < 1e-9:
        return None
    heading = math.degrees(math.atan2(water_east, water_north)) % 360
    return stw, sog, heading


def _distance_bearing(a, b):
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    dlon = lon2 - lon1
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    distance = 2 * EARTH_RADIUS_NM * math.asin(min(1.0, math.sqrt(h)))
    bearing = math.degrees(math.atan2(math.sin(dlon) * math.cos(lat2),
                                      math.cos(lat1) * math.sin(lat2) -
                                      math.sin(lat1) * math.cos(lat2) * math.cos(dlon))) % 360
    return distance, bearing


def _mean_sea(field, a, b, elapsed_s):
    first, second = field.sample(a, elapsed_s), field.sample(b, elapsed_s)
    if first is None or second is None:
        return None
    sin = (first.dir_sin + second.dir_sin) / 2
    cos = (first.dir_cos + second.dir_cos) / 2
    if math.hypot(sin, cos) < 1e-10:
        return None
    return _Sea((first.hs_m + second.hs_m) / 2,
                (first.tp_s + second.tp_s) / 2, sin, cos,
                (first.current_east_mps + second.current_east_mps) / 2,
                (first.current_north_mps + second.current_north_mps) / 2)


def _edge(grid, field, a, b, elapsed_s, speed_kn, method, length_m, geometry=None):
    if geometry is None:
        lon1, lat1 = grid.lonlat(a)
        lon2, lat2 = grid.lonlat(b)
        distance_nm, course = _distance_bearing((lon1, lat1), (lon2, lat2))
        side_a = side_b = -1
        i1, j1 = grid.ij(a)
        i2, j2 = grid.ij(b)
        if i1 != i2 and j1 != j2:  # do not cut a land corner diagonally
            side_a, side_b = grid.node(i2, j1), grid.node(i1, j2)
    else:
        distance_nm, course, side_a, side_b = geometry
    if distance_nm <= 0:
        return None
    sea = _mean_sea(field, a, b, elapsed_s)
    if sea is None:
        return None
    if side_a >= 0 and (field.sample(side_a, elapsed_s) is None or
                        field.sample(side_b, elapsed_s) is None):
        return None

    if method == "simroute":
        stw = _wave_speed_kn(speed_kn, sea, course, "aertssen", length_m)
        if stw < MIN_SPEED_KN:
            return None
        first_dt = distance_nm / stw * 3600
        midpoint = _mean_sea(field, a, b, elapsed_s + first_dt / 2)
        if midpoint is None:
            return None
        stw = _wave_speed_kn(speed_kn, midpoint, course, "aertssen", length_m)
        sog, heading, sea = stw, course, midpoint
    else:
        first = _current_composition(speed_kn, sea, course, length_m)
        if first is None:
            return None
        sea = _mean_sea(field, a, b, elapsed_s + distance_nm / first[1] * 1800)
        if sea is None:
            return None
        second = _current_composition(speed_kn, sea, course, length_m)
        if second is None:
            return None
        stw, sog, heading = second
    if sog < MIN_SPEED_KN:
        return None
    dt = distance_nm / sog * 3600
    if elapsed_s + dt > field.horizon_s + 1e-6:
        return None
    return _Edge(dt, course, heading, stw, sog, sea)


def _region_polygon(gameplay, elapsed_s):
    regions = gameplay["regions"]
    times = [r["time_s"] for r in regions]
    return regions[max(0, bisect_right(times, elapsed_s) - 1)]["polygon"]


def _bearing_sector(lon, lat, start_lon, start_lat):
    _, bearing = _distance_bearing((start_lon, start_lat), (lon, lat))
    return COMPASS[int((bearing + 22.5) // 45) % 8]


def _inside_bbox(bbox, lon, lat):
    west, east, south, north = bbox
    return west <= lon <= east and south <= lat <= north


def _goal_nodes(grid, field, gameplay, budget_s, guard):
    """Boundary exits from several region times, with directional diversity."""
    start_lon, start_lat = grid.lonlat(grid.start)
    coslat = math.cos(math.radians(start_lat))
    rows, seen = [], set()
    for slice_index in range(GOAL_SLICES):
        elapsed = budget_s * slice_index / (GOAL_SLICES - 1)
        polygon = _region_polygon(gameplay, elapsed)
        if len(polygon) < 3:
            continue
        centre_lon = sum(p[0] for p in polygon) / len(polygon)
        centre_lat = sum(p[1] for p in polygon) / len(polygon)
        for first, second in zip(polygon, polygon[1:] + polygon[:1]):
            for fraction in (0.25, 0.75):
                point_lon = first[0] + (second[0] - first[0]) * fraction
                point_lat = first[1] + (second[1] - first[1]) * fraction
                out_lon = (point_lon - centre_lon) * coslat
                out_lat = point_lat - centre_lat
                norm = math.hypot(out_lon, out_lat)
                if norm < 1e-12:
                    continue
                for step in (0.9, 1.5):
                    lon = point_lon + out_lon / norm * grid.dlon * step / coslat
                    lat = point_lat + out_lat / norm * grid.dlat * step
                    if not _inside_bbox(grid.bbox, lon, lat):
                        continue
                    node = grid.nearest(lon, lat)
                    if node in seen:
                        continue
                    seen.add(node)
                    node_lon, node_lat = grid.lonlat(node)
                    if inside(polygon, node_lon, node_lat):
                        continue
                    if not guard.allowed(node_lon, node_lat):
                        continue
                    if field.sample(node, elapsed) is None:
                        continue
                    distance, _ = _distance_bearing((start_lon, start_lat), (node_lon, node_lat))
                    rows.append({"node": node, "lon": node_lon, "lat": node_lat, "time_s": elapsed,
                                 "slice": slice_index, "distance_nm": distance,
                                 "sector": _bearing_sector(node_lon, node_lat, start_lon, start_lat)})
    if not rows:
        return []
    # Keep the closest goal in each direction and time slice, then spread the shortlist.
    best = {}
    for row in rows:
        key = (row["sector"], row["slice"])
        if key not in best or row["distance_nm"] < best[key]["distance_nm"]:
            best[key] = row
    candidates = sorted(best.values(), key=lambda r: (r["distance_nm"], r["sector"], r["time_s"]))
    selected, sectors, slices = [], {}, {}
    for row in candidates:
        if len(selected) >= 8:
            break
        if sectors.get(row["sector"], 0) < 2 and slices.get(row["slice"], 0) < 2:
            selected.append(row)
            sectors[row["sector"]] = sectors.get(row["sector"], 0) + 1
            slices[row["slice"]] = slices.get(row["slice"], 0) + 1
    for row in candidates:
        if len(selected) >= 8:
            break
        if row not in selected:
            selected.append(row)
    return sorted(selected, key=lambda r: (r["distance_nm"], r["sector"], r["time_s"]))


def _search_astar(grid, adjacency, field, target, speed_kn, length_m):
    count, nodes = 0, len(adjacency)
    cost = np.full(nodes, np.inf)
    parent = np.full(nodes, -1, dtype=int)
    closed = np.zeros(nodes, dtype=bool)
    cost[grid.start] = 0.0
    target_lon, target_lat = grid.lonlat(target)

    def heuristic(node):
        lon, lat = grid.lonlat(node)
        return _distance_bearing((lon, lat), (target_lon, target_lat))[0] / speed_kn * 3600

    heap = [(heuristic(grid.start), count, grid.start)]
    while heap:
        _, _, node = heapq.heappop(heap)
        if closed[node]:
            continue
        closed[node] = True
        if node == target:
            path, cursor = [node], node
            while cursor != grid.start:
                cursor = int(parent[cursor])
                if cursor < 0:
                    return None, None
                path.append(cursor)
            return path[::-1], cost
        for neighbour, geometry in adjacency[node]:
            if closed[neighbour]:
                continue
            edge = _edge(grid, field, node, neighbour, float(cost[node]), speed_kn,
                         "simroute", length_m, geometry)
            if edge is None:
                continue
            arrival = float(cost[node]) + edge.dt_s
            if arrival < cost[neighbour]:
                cost[neighbour] = arrival
                parent[neighbour] = node
                heapq.heappush(heap, (arrival + heuristic(neighbour), count, neighbour))
                count += 1
    return None, None


def _search_dijkstra(grid, adjacency, field, target, speed_kn, length_m):
    count, nodes = 0, len(adjacency)
    cost = np.full(nodes, np.inf)
    parent = np.full(nodes, -1, dtype=int)
    cost[grid.start] = 0.0
    heap = [(0.0, count, grid.start)]
    while heap:
        arrival, _, node = heapq.heappop(heap)
        if arrival > cost[node]:
            continue
        if node == target:
            path, cursor = [node], node
            while cursor != grid.start:
                cursor = int(parent[cursor])
                if cursor < 0:
                    return None, None
                path.append(cursor)
            return path[::-1], cost
        for neighbour, geometry in adjacency[node]:
            edge = _edge(grid, field, node, neighbour, arrival, speed_kn,
                         "visir2", length_m, geometry)
            if edge is None:
                continue
            candidate = arrival + edge.dt_s
            if candidate < cost[neighbour]:
                cost[neighbour] = candidate
                parent[neighbour] = node
                heapq.heappush(heap, (candidate, count, neighbour))
                count += 1
    return None, None


def _candidate(method, grid, field, path, cost, speed_kn, gameplay, length_m):
    if path is None or len(path) < 2:
        return None
    segments = []
    for a, b in zip(path, path[1:]):
        edge = _edge(grid, field, a, b, float(cost[a]), speed_kn, method, length_m)
        if edge is None:
            return None
        segments.append((a, b, float(cost[a]), edge))
    exit_index = None
    for index in range(1, len(path)):
        lon, lat = grid.lonlat(path[index])
        if not inside(_region_polygon(gameplay, float(cost[path[index]])), lon, lat):
            exit_index = index
            break
    if exit_index is None:
        return None
    path, segments = path[:exit_index + 1], segments[:exit_index]
    if not segments:
        return None
    orders, points = [], []
    for a, b, elapsed, edge in segments:
        lon, lat = grid.lonlat(a)
        points.append({"t": round(elapsed, 1), "lon": round(lon, 5), "lat": round(lat, 5),
                       "course_deg": round(edge.course_deg, 1),
                       "heading_deg": round(edge.heading_deg, 1),
                       "hs_m": round(edge.sea.hs_m, 2),
                       "current_east_mps": round(edge.sea.current_east_mps, 3),
                       "current_north_mps": round(edge.sea.current_north_mps, 3)})
        order = {"t": round(elapsed, 1), "heading_deg": round(edge.heading_deg, 1),
                 "speed_knots": round(speed_kn, 2)}
        if not orders or (orders[-1]["heading_deg"], orders[-1]["speed_knots"]) != (
                order["heading_deg"], order["speed_knots"]):
            orders.append(order)
    exit_lon, exit_lat = grid.lonlat(path[-1])
    points.append({"t": round(float(cost[path[-1]]), 1), "lon": round(exit_lon, 5),
                   "lat": round(exit_lat, 5), "course_deg": points[-1]["course_deg"],
                   "heading_deg": points[-1]["heading_deg"], "hs_m": points[-1]["hs_m"],
                   "current_east_mps": points[-1]["current_east_mps"],
                   "current_north_mps": points[-1]["current_north_mps"]})
    distance = sum(_distance_bearing(grid.lonlat(a), grid.lonlat(b))[0]
                   for a, b in zip(path, path[1:]))
    return {
        "solver": SIMROUTE_SOLVER if method == "simroute" else VISIR2_SOLVER,
        "method": method,
        "orders": orders,
        "estimated_s": round(float(cost[path[-1]]), 1),
        "speed_knots": round(speed_kn, 2),
        "distance_nm": round(distance, 2),
        "path": points,
        "exit": {"lon": round(exit_lon, 5), "lat": round(exit_lat, 5),
                 "time_s": round(float(cost[path[-1]]), 1),
                 "sector": _bearing_sector(exit_lon, exit_lat, *grid.lonlat(grid.start))},
    }


class RouteSearch:
    """Generate deterministic TIDE route candidates for one scenario and gameplay bundle."""

    def __init__(self, scenario, gameplay, *, coast_guard=None):
        self.scenario = scenario
        self.gameplay = gameplay
        self.coast_guard = coast_guard
        self.diagnostic = {}

    def search(self, method, *, max_candidates=6):
        if method not in ("simroute", "visir2"):
            raise ValueError("method must be exactly 'simroute' or 'visir2'")
        if not isinstance(max_candidates, int) or max_candidates < 1:
            raise ValueError("max_candidates must be a positive integer")
        self.diagnostic = {"method": method, "status": "searching", "searches": 0,
                           "reason": None}
        definition = self.scenario.definition
        start_lon = float(definition["initial"]["lon"])
        start_lat = float(definition["initial"]["lat"])
        budget_s = float(definition["mission"]["budget_s"])
        start_time = utc(definition["start"])
        base_kn = float(definition["initial"]["speed_mps"]) * 3600 / 1852
        length_m = float(definition.get("wind_parameters", {}).get("loa_m", 175.0))
        if base_kn <= MIN_SPEED_KN:
            self.diagnostic.update(status="exhausted",
                                   reason="Initial speed is too low for empirical routing.")
            return []
        speeds = []
        for speed in (base_kn, base_kn * 1.2, base_kn * 0.75):
            speed = round(speed, 2)
            if speed >= MIN_SPEED_KN and speed not in speeds:
                speeds.append(speed)
        guard = self.coast_guard or CoastGuard(definition["area_bbox"])
        self.diagnostic["coast_guard"] = guard.metadata()
        if not guard.allowed(start_lon, start_lat):
            self.diagnostic.update(
                status="exhausted",
                reason="The initial position is on land or inside the fixed coast-clearance buffer.")
            return []
        grid = _Grid(definition["area_bbox"], start_lon, start_lat)
        adjacency = _prepare_adjacency(grid, guard)
        with self.scenario.environment() as environment:
            field = _FieldGrid(environment, grid, start_time, budget_s, guard)
        goals = _goal_nodes(grid, field, self.gameplay, budget_s, guard)
        self.diagnostic.update(goals=len(goals), goal_times=sorted({round(g["time_s"], 1) for g in goals}),
                               grid={"nx": grid.nx, "ny": grid.ny, "cell_deg": grid.dlon},
                               speeds_knots=speeds)
        if not goals:
            self.diagnostic.update(
                status="exhausted",
                reason="No finite open-water exit cell was found. This is not a proof of no solution.")
            return []

        found = []
        for goal_index, goal in enumerate(goals):
            for speed_index, speed in enumerate(speeds):
                if goal_index >= 2 and speed_index > 0:  # extra goals at base speed only
                    continue
                if method == "simroute":
                    path, cost = _search_astar(grid, adjacency, field, goal["node"], speed, length_m)
                else:
                    path, cost = _search_dijkstra(grid, adjacency, field, goal["node"], speed, length_m)
                self.diagnostic["searches"] += 1
                candidate = _candidate(method, grid, field, path, cost, speed, self.gameplay, length_m)
                if candidate is not None:
                    candidate["target_time_s"] = round(goal["time_s"], 1)
                    candidate["target_sector"] = goal["sector"]
                    found.append(candidate)
        unique, signatures = [], set()
        for candidate in sorted(found, key=lambda c: (c["estimated_s"], c["distance_nm"],
                                                       c["exit"]["sector"], c["speed_knots"])):
            signature = tuple((o["t"], o["heading_deg"], o["speed_knots"]) for o in candidate["orders"])
            if signature not in signatures:
                signatures.add(signature)
                unique.append(candidate)
        selected, exits = [], set()
        for candidate in unique:  # one route per escape cell first
            key = (candidate["exit"]["lon"], candidate["exit"]["lat"])
            if key not in exits:
                exits.add(key)
                selected.append(candidate)
                if len(selected) == max_candidates:
                    break
        for candidate in unique:  # then add distinct speeds or paths to the same exit
            if len(selected) == max_candidates:
                break
            if candidate not in selected:
                selected.append(candidate)
        selected = selected[:max_candidates]
        for index, candidate in enumerate(selected, start=1):
            candidate["name"] = (f"{method}-{index:02d}-{candidate['exit']['sector'].lower()}-"
                                 f"{candidate['speed_knots']:g}kt")
            candidate["grid_cell_deg"] = round(grid.dlon, 5)
            candidate["model"] = {
                "simroute": "A* on an 8-cell grid; Aertssen speed loss; no current; midpoint time predictor",
                "visir2": "time-dependent Dijkstra; Aertssen speed-loss proxy; current composition; SOG on course",
            }[method]
            candidate["source"] = "TIDE adaptation, not an original benchmark reproduction"
        self.diagnostic.update(
            status="ok" if selected else "exhausted",
            candidates=len(selected),
            reason=None if selected else
            "Every searched route stayed inside the scheduled region or crossed missing data. "
            "This is not a proof of no solution.")
        return selected
