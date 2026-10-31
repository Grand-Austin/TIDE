import json
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from shapely.geometry import Polygon, box

from agent.routing import RouteSearch
from game.navigation import (
    DEFAULT_CLEARANCE_NM,
    CoastGuard,
    CoastalEnvironment,
    NavigationDataError,
    guard_environment,
)
from game.scenario import utc
from game.simulation.environment import EnvironmentError, SeaState
from game.simulation.runtime import Command, Settings, Simulation

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "navigation"
START = datetime(2026, 9, 9, 8, tzinfo=timezone.utc)
NM_M = 1852.0
METRES_PER_DEGREE = 111_319.49079327357


class _ConstantSea:
    def __init__(self):
        self.sea = SeaState(0.0, 8.0, 0.0)
        self.calls = 0
        self.closed = False

    def sample(self, time, lon, lat):
        self.calls += 1
        return self.sea

    def close(self):
        self.closed = True


def _synthetic_vessel():
    mass = np.diag([1e6, 1e6, 1e6, 1e8, 1e8, 1e8])
    return SimpleNamespace(
        main=dict(g=9.81, m=1e6, k44=10, k55=10, k66=10, Lpp=50, B=10, T=3,
                  rho=1025, C_B=0.6, name="synthetic"),
        MRB=mass,
        MA=mass * 0.1,
        M=mass * 1.1,
        Minv=np.linalg.inv(mass * 1.1),
        D=np.diag([1e4, 1e4, 1e4, 1e6, 1e6, 1e6]),
        G=np.diag([0, 0, 1e6, 1e8, 1e8, 0]),
        nonlinear_456=np.zeros(3),
        freqs=np.linspace(0.1, 4, 40),
        headings=np.arange(0, 2 * np.pi, np.pi / 6),
        force_rao=np.full((6, 40, 12), 1000 + 0j),
        max_speed_mps=10.0,
    )


def test_coastal_points_near_islands_and_open_water_use_real_dataset():
    guard = CoastGuard((3.5, 3.95, 39.7, 40.15))
    assert guard.allowed(3.65, 40.05)  # authored scenario start, west of Menorca
    assert not guard.allowed(4.0, 39.95)  # Menorca land
    assert not guard.allowed(4.0, 40.0)  # near-coast water, inside 5 NM
    assert guard.allowed(3.5, 40.0)  # open water west of the island


def test_clearance_boundary_is_five_nautical_miles():
    land = box(0.2, -0.5, 1.0, 0.5)  # vertical coast at lon 0.2
    guard = CoastGuard((-1.0, 1.0, -0.5, 0.5), land=land)
    distance = lambda nm: 0.2 - nm * NM_M / METRES_PER_DEGREE
    assert guard.clearance_nm == DEFAULT_CLEARANCE_NM == 5.0
    assert guard.clearance_m == pytest.approx(5.0 * NM_M)
    assert guard.allowed(distance(5.05), 0.0)
    assert not guard.allowed(distance(4.95), 0.0)
    assert not guard.allowed(distance(5.0), 0.0)


def test_polygon_interiors_and_islands_are_not_solid_land():
    island = box(0.2, -0.3, 0.8, 0.3)
    lake = box(0.38, -0.12, 0.62, 0.12)
    guard = CoastGuard((-1.0, 1.0, -1.0, 1.0), land=Polygon(island.exterior, [lake.exterior]))
    assert not guard.allowed(0.25, 0.1)  # island land
    assert guard.allowed(0.5, 0.0)  # lake interior more than 5 NM from shore
    assert guard.allowed(0.9, 0.9)  # open water
    assert not guard.segment_allowed((0.1, 0.0), (0.9, 0.0))
    assert guard.segment_allowed((0.1, 0.6), (0.9, 0.6))


def test_segment_rejects_land_crossing_even_when_both_endpoints_are_safe():
    guard = CoastGuard((-1.0, 1.0, -0.5, 0.5),
                       land=box(0.4, -0.1, 0.5, 0.1))
    start, end = (0.1, 0.0), (0.8, 0.0)
    assert guard.allowed(*start) and guard.allowed(*end)
    assert not guard.segment_allowed(start, end)
    assert guard.segment_allowed((0.1, 0.3), (0.8, 0.3))
    assert not guard.segment_allowed((float("nan"), 0.0), (0.8, 0.0))


def test_guard_metadata_pins_version_source_checksum_and_units():
    guard = CoastGuard((3.5, 3.95, 39.7, 40.15))
    manifest = json.loads((DATA / "manifest.json").read_text())
    metadata = guard.metadata()
    assert metadata["version"] == manifest["version"] == "5.1.1"
    assert metadata["source"] == manifest["source"]["url"]
    assert metadata["checksum"] == manifest["archive"]["sha256"]
    assert metadata["clearance_nm"] == 5.0
    assert metadata["clearance_m"] == 9260.0
    assert metadata["units"]["coordinates"].startswith("WGS84")
    assert metadata["fixture"] is False
    metadata["clearance_nm"] = 0
    assert guard.metadata()["clearance_nm"] == 5.0


def test_missing_or_corrupt_data_fails_loudly(tmp_path):
    with pytest.raises(NavigationDataError):
        CoastGuard((0.0, 1.0, 0.0, 1.0), data_path=tmp_path)

    manifest = json.loads((DATA / "manifest.json").read_text())
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "ne_10m_land.zip").write_bytes(b"corrupt")
    with pytest.raises(NavigationDataError):
        CoastGuard((0.0, 1.0, 0.0, 1.0), data_path=tmp_path)


def test_archive_member_paths_are_rejected(tmp_path):
    import hashlib
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../escape.shp", b"x")
    data = buffer.getvalue()
    manifest = {
        "schema_version": 1,
        "dataset": "synthetic",
        "version": "5.1.1",
        "license": "test",
        "source": {"url": "https://example.invalid/land.zip"},
        "archive": {"path": "ne_10m_land.zip", "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest()},
        "members": [],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "ne_10m_land.zip").write_bytes(data)
    with pytest.raises(NavigationDataError, match="unsafe member"):
        CoastGuard((0.0, 1.0, 0.0, 1.0), data_path=tmp_path)


def test_pinned_archive_checksum_matches_manifest():
    import hashlib

    manifest = json.loads((DATA / "manifest.json").read_text())
    actual = hashlib.sha256((DATA / "ne_10m_land.zip").read_bytes()).hexdigest()
    assert actual == manifest["archive"]["sha256"]


def test_environment_wrapper_rejects_before_source_sample_and_does_not_own_source():
    bbox = (-1.0, 1.0, -0.5, 0.5)
    guard = CoastGuard(bbox, land=box(0.4, -0.1, 0.5, 0.1))
    source = _ConstantSea()
    wrapped = guard_environment(source, bbox, coast_guard=guard)
    assert isinstance(wrapped, CoastalEnvironment)
    assert wrapped.guard is guard
    assert wrapped.sample(START, 0.1, 0.0) is source.sea
    calls = source.calls
    with pytest.raises(EnvironmentError) as error:
        wrapped.sample(START, 0.45, 0.0)
    assert error.value.reason == "coast_clearance"
    assert source.calls == calls
    wrapped.close()
    assert source.closed is False


def test_full_simulation_stops_and_rolls_back_before_forbidden_entry():
    bbox = (-0.5, 0.5, -0.2, 0.2)
    guard = CoastGuard(bbox, land=box(0.10, -0.2, 0.5, 0.2))
    settings = Settings(dt_s=0.1, wave_period_s=0.1, record_period_s=1.0)
    guarded_sim = Simulation(
        _synthetic_vessel(),
        guard_environment(_ConstantSea(), bbox, coast_guard=guard),
        settings=settings,
        wind_parameters=None,
    )
    state = guarded_sim.initial_state(START, 0.0, 0.0, heading_deg=90.0, speed_mps=20.0)
    result = guarded_sim.advance(state, Command(90.0, 20.0), 120.0)
    assert result.stopping_reason == "coast_clearance"
    assert result.advanced_s < 120.0
    assert result.state.tick == round(result.advanced_s / settings.dt_s)
    assert guarded_sim.position(result.state)[0] < 0.10
    assert state.tick == 0  # the caller's state was not mutated

    raw = Simulation(_synthetic_vessel(), _ConstantSea(), settings=settings,
                     wind_parameters=None)
    guarded = Simulation(_synthetic_vessel(), guard_environment(
        _ConstantSea(), bbox, coast_guard=guard), settings=settings, wind_parameters=None)
    raw_state = raw.initial_state(START, 0.0, 0.0, heading_deg=90.0, speed_mps=20.0)
    guarded_state = guarded.initial_state(START, 0.0, 0.0, heading_deg=90.0, speed_mps=20.0)
    raw_result = raw.advance(raw_state, Command(90.0, 20.0), 10.0)
    guarded_result = guarded.advance(guarded_state, Command(90.0, 20.0), 10.0)
    np.testing.assert_array_equal(raw_result.state.x, guarded_result.state.x)


def test_unsafe_initial_state_fails_without_relocating_the_ship():
    bbox = (-0.5, 0.5, -0.2, 0.2)
    guard = CoastGuard(bbox, land=box(0.0, -0.2, 0.5, 0.2))
    source = _ConstantSea()
    sim = Simulation(_synthetic_vessel(), guard_environment(source, bbox, coast_guard=guard),
                     wind_parameters=None)
    with pytest.raises(EnvironmentError) as error:
        sim.initial_state(START, 0.05, 0.0, heading_deg=90.0, speed_mps=20.0)
    assert error.value.reason == "coast_clearance"
    assert source.calls == 0


def test_tui_main_passes_guarded_environment_to_simulation(monkeypatch, tmp_path):
    import game.__main__ as cli

    source = _ConstantSea()
    captured = {}

    class Scenario:
        definition = {
            "gameplay": {"path": "unused", "sha256": "unused"},
            "area_bbox": [-1.0, 1.0, -0.5, 0.5],
            "seed": 1,
        }

        def gameplay(self):
            return {"version": 1}

        @contextmanager
        def environment(self):
            yield source

        def simulation(self, environment, seed=None):
            captured["environment"] = environment
            return object()

    class Session:
        def __init__(self, scenario, simulation, gameplay, directory, *, seed=None,
                     restore=False):
            captured["session"] = (simulation, directory)

        def visible(self):
            return {"outcome": None}

    monkeypatch.setattr(cli, "load_scenario", lambda path: Scenario())
    monkeypatch.setattr(cli, "Session", Session)
    script = tmp_path / "script.json"
    script.write_text("[]")
    session_dir = tmp_path / "session"
    monkeypatch.setattr(sys, "argv", [
        "game", "--scenario", str(tmp_path / "scenario.json"),
        "--script", str(script), "--session", str(session_dir),
    ])
    cli.main()
    environment = captured["environment"]
    assert isinstance(environment, CoastalEnvironment)
    assert environment.source is source
    assert environment.guard.metadata()["clearance_nm"] == 5.0
    assert (session_dir / "result.json").exists()


@dataclass(frozen=True)
class _RouteSea:
    hs: float
    tp: float
    wave_from_deg: float
    current_east_mps: float = 0.0
    current_north_mps: float = 0.0


class _RouteEnvironment:
    def __init__(self, start):
        self.start = start

    def sample(self, time, lon, lat):
        return _RouteSea(1.0, 8.0, 270.0, 0.2, 0.0)


class _RouteScenario:
    def __init__(self, definition, environment):
        self.definition, self.environment_source = definition, environment

    def environment(self):
        return nullcontext(self.environment_source)


def _rectangle(west, east, south, north):
    return [[west, south], [west, north], [east, north], [east, south]]


def test_route_search_masks_synthetic_land_and_crossing_edges():
    definition = {
        "start": "2025-01-01T00:00:00Z",
        "end": "2025-01-01T03:00:00Z",
        "initial": {"lon": 0.2, "lat": 0.3, "heading_deg": 90.0, "speed_mps": 5.0},
        "mission": {"budget_s": 10800, "target_lon_lat": [1.0, 0.3], "radius_m": 1000},
        "area_bbox": [0.0, 1.0, 0.0, 0.6],
        "wind_parameters": {"loa_m": 175.0},
    }
    scenario = _RouteScenario(definition, _RouteEnvironment(utc(definition["start"])))
    gameplay = {"version": 1,
                "regions": [{"time_s": 0, "polygon": _rectangle(0.0, 0.5, 0.0, 0.6)}]}
    guard = CoastGuard((0.0, 1.0, 0.0, 0.6), land=box(0.30, 0.25, 0.34, 0.35))
    assert not guard.segment_allowed((0.2, 0.3), (0.5, 0.3))

    for method in ("simroute", "visir2"):
        search = RouteSearch(scenario, gameplay, coast_guard=guard)
        candidates = search.search(method, max_candidates=3)
        assert candidates, search.diagnostic
        assert search.diagnostic["coast_guard"]["fixture"] is True
        for candidate in candidates:
            points = [(point["lon"], point["lat"]) for point in candidate["path"]]
            assert all(guard.allowed(*point) for point in points)
            assert all(guard.segment_allowed(a, b) for a, b in zip(points, points[1:]))
