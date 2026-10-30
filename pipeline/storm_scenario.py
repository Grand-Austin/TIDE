"""Build a playable escape scenario from a catalogued storm (data/storms/<region>/<id>/).

Start time and position come from the storm's 3D scene (web/game/scenes/<id>.json). The adverse-weather
region is pipeline.gameplay's rule: the convex envelope of cells with Hs >= region_hs_m, frozen per minute
and hidden from the player. In a storm a fixed 3 m threshold covers the whole sea, so the threshold is
the storm core, within 1 m of the start's Hs. If the start lies more than 25 km inside, it moves towards
the nearest edge to 15 km inside, which a ship at 5 m/s can leave within the two-hour budget.
`--check` then plays two scripted strategies to confirm.

    python -m pipeline.storm_scenario biscay-20260123-06            # scenarios/<id>/{scenario,gameplay}.json
    python -m pipeline.storm_scenario biscay-20260123-06 --check    # also run the scripted strategies
    python -m pipeline.storm_scenario biscay-20261022-21 --start-at-peak --out data/runs/peak-demo/source

Peak demonstrations keep the historical peak time instead of preferring daylight.
Display weather uses real ERA5 cloud cover and precipitation.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
from scipy.spatial import ConvexHull

from game.scenario import ROOT, digest, load_scenario, utc
from pipeline.export_scene import MARGIN, STORM_SETTINGS, daylight_start
from pipeline.fetch_region import REGIONS
from pipeline.gameplay import build_gameplay
from pipeline.scenario import write_json
from pipeline.storms import OUT as STORMS

OUT = ROOT / "scenarios"
REFERENCE = json.loads((OUT / "menorca-20260909/scenario.json").read_text())
BUDGET_S = 7200
SPEED_MPS = 5.0
INSIDE_KM = (10.0, 25.0)
START_KM = 15.0
# dt 0.1 s and wave forces every 0.2 s run about 2.5x faster than 0.05/0.1 with the same roll and damage
# (checked at the 23 Jan 2026 peak: max roll 15.0 vs 15.1 deg, deck damage within 3 %)
# Keep the quicker autopilot (0.08 rad/s, reference turn rate up to 1 deg/s), with the prepared vessel's
# damping and the original fixed yaw-moment limit. Earlier turn measurements used different physics settings.
SETTINGS = dict(STORM_SETTINGS, dt_s=0.1, wave_period_s=0.2)


def km(lon, lat, lon0, lat0):
    return (lon - lon0) * 111.32 * math.cos(math.radians(lat0)), (lat - lat0) * 110.54


def region(waves: xr.Dataset, time: datetime, threshold: float, bbox):
    w, e, s, n = bbox
    sub = waves.sel(longitude=slice(w, e), latitude=slice(s, n))
    hs = sub.VHM0.interp(time=np.datetime64(time.replace(tzinfo=None))).values
    lons, lats = sub.longitude.values, sub.latitude.values
    dx, dy = float(np.diff(lons).mean()) / 2, float(np.diff(lats).mean()) / 2
    points = [[float(lons[j] + a * dx), float(lats[i] + b * dy)]
              for i, j in np.argwhere(np.isfinite(hs) & (hs >= threshold)) for a, b in ((-1, -1), (-1, 1), (1, 1), (1, -1))]
    return np.array(points)[ConvexHull(points).vertices] if len(points) >= 3 else np.empty((0, 2))


def depth_inside(polygon, lon, lat, bbox=None, sea=None):
    """Distance (km) from the point to the polygon edge and the nearest edge point; None if outside.
    Edges that run along the data box only mark where the data stop, not where the storm ends, so they
    are no exit; nor is an edge with land just beyond it (sea(lon, lat) is False there)."""
    from game.rules.mission import inside
    if len(polygon) < 3 or not inside(polygon.tolist(), lon, lat):
        return None
    best = (math.inf, None)
    for a, b in zip(polygon, np.roll(polygon, -1, axis=0)):
        if bbox is not None and any(abs(a[k] - edge) < 0.1 and abs(b[k] - edge) < 0.1
                                    for k, edges in ((0, bbox[:2]), (1, bbox[2:])) for edge in edges):
            continue
        ax, ay = km(a[0], a[1], lon, lat)
        bx, by = km(b[0], b[1], lon, lat)
        t = max(0.0, min(1.0, -(ax * (bx - ax) + ay * (by - ay)) / max((bx - ax) ** 2 + (by - ay) ** 2, 1e-9)))
        px, py = ax + t * (bx - ax), ay + t * (by - ay)
        d = math.hypot(px, py)
        beyond = (lon + px * (1 + 3 / max(d, 1e-6)) / (111.32 * math.cos(math.radians(lat))), lat + py * (1 + 3 / max(d, 1e-6)) / 110.54)
        if sea is not None and not sea(*beyond):
            continue
        if d < best[0]:
            best = (d, (lon + px / (111.32 * math.cos(math.radians(lat))), lat + py / 110.54))
    return best


def build(
    storm_id: str, start_km: float = START_KM, *,
    start_at_peak: bool = False, out_dir: Path | None = None,
) -> Path:
    region_name = storm_id.split("-")[0]
    storm = next(s for s in json.loads((STORMS / f"{region_name}.json").read_text())["storms"] if s["id"] == storm_id)
    # start where and when pipeline.export_scene would: the peak cell, in the storm's best daylight window
    west, east, south, north = REGIONS[region_name]
    lon = min(max(storm["lon"], west + MARGIN), east - MARGIN)
    lat = min(max(storm["lat"], south + MARGIN), north - MARGIN)
    folder = STORMS / region_name / storm_id
    waves = xr.open_dataset(folder / "waves.nc")
    hs_at = lambda t: float(waves.VHM0.interp(time=np.datetime64(t.replace(tzinfo=None)), longitude=lon, latitude=lat))
    start = utc(storm["peak"]) if start_at_peak else daylight_start(region_name, storm, lon, lat)
    if start is None or not hs_at(start) >= 0.6 * storm["hs_m"]:
        # the storm is still building while the sun is up: play its peak at night instead
        start = utc(storm["peak"]) - timedelta(minutes=20)
    bbox = [round(float(waves.longitude.min()) + 0.15, 3), round(float(waves.longitude.max()) - 0.15, 3),
            round(float(waves.latitude.min()) + 0.15, 3), round(float(waves.latitude.max()) - 0.15, 3)]
    hs0 = float(waves.VHM0.interp(time=np.datetime64(start.replace(tzinfo=None)), longitude=lon, latitude=lat))
    # storm core: sea within 1 m of the start's Hs; if the start is deep inside, move it towards the
    # nearest edge so that it sits START_KM inside and the two-hour budget is a fair fight
    threshold = math.floor((hs0 - 1.0) * 2) / 2
    polygon = region(waves, start, threshold, bbox)
    at = np.datetime64(start.replace(tzinfo=None))
    sea = lambda x, y: bool(np.isfinite(waves.VHM0.sel(longitude=x, latitude=y, method="nearest").interp(time=at)))
    found = depth_inside(polygon, lon, lat, bbox, sea)
    if found is None:
        raise SystemExit(f"{storm_id}: the start is not inside the Hs >= {threshold:g} m region")
    if found[0] > max(INSIDE_KM[1], start_km):
        f = (found[0] - start_km) / found[0]
        lon, lat = lon + (found[1][0] - lon) * f, lat + (found[1][1] - lat) * f
        found = depth_inside(polygon, lon, lat, bbox, sea)
        if found is None or not sea(lon, lat):
            raise SystemExit(f"{storm_id}: moving the start towards the edge ends on land or outside the region")
        hs0 = float(waves.VHM0.interp(time=np.datetime64(start.replace(tzinfo=None)), longitude=lon, latitude=lat))
    inside_km, exit_point = found
    angle = np.deg2rad(waves.VMDR.sel(longitude=lon, latitude=lat, method="nearest").interp(time=at))
    scene_from = float(np.rad2deg(np.arctan2(np.sin(angle), np.cos(angle)))) % 360  # head seas to begin with
    out = Path(out_dir).resolve() if out_dir is not None else OUT / storm_id
    out.mkdir(parents=True, exist_ok=True)
    end = start + timedelta(seconds=BUDGET_S)
    environment = {
        name: {"path": str((folder / f"{name}.nc").relative_to(ROOT)), "sha256": digest(folder / f"{name}.nc"),
               "role": "historical_reconstruction", "available_to_player": False}
        for name in ("waves", "currents", "wind")
    }
    definition = {
        "schema_version": 1, "id": storm_id,
        "title": f"Escape the {storm['area']} storm, {storm['peak'][:10]}",
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"), "seed": 1,
        "initial": {"lon": round(lon, 4), "lat": round(lat, 4), "heading_deg": round(scene_from, 1), "speed_mps": SPEED_MPS},
        "mission": {"budget_s": BUDGET_S, "target_lon_lat": [round(exit_point[0], 4), round(exit_point[1], 4)],
                    "radius_m": 1000, "review_interval_s": 300},
        "vessel": REFERENCE["vessel"], "settings": SETTINGS, "wind_parameters": REFERENCE["wind_parameters"],
        "reports": REFERENCE["reports"], "trials": {}, "area_bbox": bbox,
        "gameplay_settings": {"region_hs_m": threshold, "start_inside_km": round(inside_km, 1),
                              "note": "target_lon_lat is the nearest region edge at the start; the region moves with the storm"},
        "collection": {"source": str(folder.relative_to(ROOT)), "storm": storm,
                       "start_selection": "storm_peak" if start_at_peak else "daylight_or_peak_lead"},
        "environment": environment,
    }
    path = out / "scenario.json"
    write_json(path, definition)
    write_weather(out / "weather.json", lat, lon, start, end)
    gameplay = build_gameplay(load_scenario(path))
    write_json(out / "gameplay.json", gameplay)
    definition["gameplay"] = {"path": str((out / "gameplay.json").relative_to(ROOT)), "sha256": digest(out / "gameplay.json")}
    write_json(path, definition)
    print(f"{path}: region Hs >= {threshold:g} m, start {inside_km:.1f} km inside, Hs at start {hs0:.1f} m")
    return path


def write_weather(path: Path, lat: float, lon: float, start: datetime, end: datetime) -> None:
    """Hourly ERA5 cloud cover and precipitation at the start point for the whole mission (Open-Meteo)."""
    from urllib.parse import urlencode
    from urllib.request import urlopen
    from pipeline.export_scene import WEATHER_CREDIT, WEATHER_URL
    query = urlencode({"latitude": round(lat, 3), "longitude": round(lon, 3), "models": "era5",
                       "start_date": f"{start:%Y-%m-%d}", "end_date": f"{end:%Y-%m-%d}", "hourly": "cloud_cover,precipitation"})
    with urlopen(f"{WEATHER_URL}?{query}", timeout=60) as response:
        hourly = json.load(response)["hourly"]
    hours = pd.to_datetime(hourly["time"]).as_unit("ns").asi8 / 1e9
    rows = []
    for k in range(int((end - start).total_seconds() // 3600) + 1):
        at = (start + timedelta(hours=k)).timestamp()
        i = int(np.clip(np.searchsorted(hours, at), 0, len(hours) - 1))
        rows.append({"t_s": k * 3600, "cloud": round(float(np.interp(at, hours, hourly["cloud_cover"]))),
                     "precip": round(float(hourly["precipitation"][i]), 2)})
    write_json(path, {"source": WEATHER_URL, "model": "era5", "point_lat_lon": [lat, lon], "credit": WEATHER_CREDIT, "hourly": rows})


def check(path: Path, strategy: str, out: Path) -> dict:
    """Play one scripted strategy with the local engine: 'exit' steers for the nearest edge, 'avoid_beam' does the
    same but keeps the waves off the beam, 'head' holds head seas."""
    from game.session import Session
    from pipeline.scenario import displacement
    scenario = load_scenario(path)
    gameplay = json.loads((ROOT / scenario.definition["gameplay"]["path"]).read_text())
    with scenario.environment() as env:
        session_dir = out / f"{strategy}-{datetime.now():%Y%m%d-%H%M%S}"
        s = Session(scenario, scenario.simulation(env), gameplay, session_dir)
        d = scenario.definition
        target, budget = d["mission"]["target_lon_lat"], d["mission"]["budget_s"]
        heading = d["initial"]["heading_deg"]
        s.set_order(heading, SPEED_MPS * 3600 / 1852)
        s.authorize_resume()
        next_order = 0
        while not s.outcome and s.state.exposure.elapsed_s < budget:
            t = s.state.exposure.elapsed_s
            if strategy in ("exit", "avoid_beam") and t >= next_order:
                _, heading = displacement(s.sim.position(s.state), target)
                if strategy == "avoid_beam":
                    # the heading nearest the exit bearing that keeps the waves off the beam (50–130° off the bow)
                    wave_from = s.visible()["vessel"]["wave_from_deg"]
                    off = lambda h: abs((wave_from - h + 180) % 360 - 180)
                    heading = min((h % 360 for h in range(int(heading) - 180, int(heading) + 181, 5)
                                   if not 50 <= off(h) <= 130), key=lambda h: abs((h - heading + 180) % 360 - 180))
                s.set_order(heading, SPEED_MPS * 3600 / 1852)  # an order pauses the session
                s.authorize_resume()
                next_order = t + 300
            s.advance_segment(min(5, budget - t))
            if s.pause_signal.is_set() and not s.outcome:
                if s.reason not in ("weather_report", "cargo_grade_increased", "paused"):
                    break
                s.authorize_resume()
        v = s.visible()
        return {"strategy": strategy, "outcome": s.outcome, "reason": s.reason, "elapsed_s": v["elapsed_s"],
                "lost_teu": v["cargo"]["lost_teu"], "grade": v["cargo"]["grade"], "session": str(session_dir)}


SOLVERS = {
    "exit": "pipeline.storm_scenario: steer for the nearest region edge at the start, re-aimed every 5 min",
    "avoid_beam": "pipeline.storm_scenario: as exit, but keep the waves off the beam (50-130 deg off the bow)",
    "head": "pipeline.storm_scenario: hold head seas",
}


def write_validation(storm_id: str, results: list[dict]) -> Path:
    """validation.json in the shape of docs/STORM_SCENARIOS.md; orders come from each session's log."""
    out = OUT / storm_id
    strategies = []
    for r in results:
        log = Path(r["session"]) / "events.jsonl"
        orders = [{"t": e["time_s"], "heading_deg": round(e["data"]["heading_deg"], 1),
                   "speed_knots": round(e["data"]["speed_mps"] * 3600 / 1852, 2)}
                  for e in map(json.loads, log.read_text().splitlines()) if e["kind"] == "player_order"]
        strategies.append({"name": r["strategy"], "solver": SOLVERS.get(r["strategy"], r["strategy"]), "orders": orders,
                           "outcome": r["outcome"] or r["reason"], "elapsed_s": round(r["elapsed_s"], 1),
                           "lost_teu": r["lost_teu"], "grade": r["grade"], "ship_lost": r["outcome"] == "ship_lost",
                           "session": str(Path(r["session"]).relative_to(ROOT))})
    passing = [x for x in strategies if x["outcome"] == "escaped" and not x["ship_lost"]]
    reference = min(passing, key=lambda x: (x["lost_teu"], x["elapsed_s"]))["name"] if passing else None
    path = out / "validation.json"
    write_json(path, {"version": 1, "scenario": storm_id, "definition_sha256": digest(out / "scenario.json"),
                      "gameplay_sha256": digest(out / "gameplay.json"),
                      "rule": "passed when at least one strategy escapes without losing the ship",
                      "passed": bool(passing), "reference": reference, "strategies": strategies})
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("storms", nargs="+")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--start-at-peak", action="store_true", help="start at the storm peak instead of preferring daylight")
    parser.add_argument("--out", type=Path, help="write one source scenario to this staging directory")
    parser.add_argument("--start-km", type=float, default=START_KM,
                        help="how far inside the region the start sits; lower it when no strategy escapes")
    args = parser.parse_args()
    if args.out is not None and len(args.storms) != 1:
        parser.error("--out requires exactly one storm")
    if args.out is not None and args.check:
        parser.error("--check writes a legacy published report; use the Builder to validate a staging scenario")
    for storm_id in args.storms:
        path = build(storm_id, args.start_km, start_at_peak=args.start_at_peak,
                     out_dir=args.out)
        if args.check:
            results = [check(path, strategy, ROOT / "data/runs" / f"check-{storm_id}") for strategy in ("exit", "avoid_beam")]
            for r in results:
                print(json.dumps(r), flush=True)
            print(write_validation(storm_id, results))


if __name__ == "__main__":
    main()
