"""Export a recorded S175 run through one catalogued storm for the 3D scene (web/game/).

The run uses the game's motion model, settings and cargo model. Orders follow a fixed demonstration
plan relative to the wave direction at the start (head seas, then beam seas, then stern-quartering seas),
so viewers see how heading changes the response. The file says so; it is not a player's game.

    python -m pipeline.export_scene biscay-20261022-21          # writes web/game/scenes/<id>.json
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import pandas as pd
import xarray as xr

from game.cargo import CargoModel
from game.scenario import ROOT
from game.simulation.environment import CopernicusEnvironment
from game.simulation.runtime import Command, Settings, Simulation
from game.simulation.vessel import load_s175
from game.simulation.wind import WindParameters
from pipeline.fetch_region import PRODUCTS, REGIONS, directory
from pipeline.storms import OUT as STORMS

OUT = ROOT / "web" / "game" / "scenes"
# The frozen scenario's vessel settings and windage are reused unchanged.
REFERENCE = json.loads((ROOT / "scenarios/menorca-20260909/scenario.json").read_text())
SPEED_MPS = 5.0  # about 10 kn: enough flow past the rudder to turn in 10–12 m seas (see STATUS.md)
# Storm scenes need more thrust than the calm Menorca scenario to hold that speed.
STORM_SETTINGS = dict(REFERENCE["settings"], max_thrust_n=1_500_000.0)
LEAD_S = 1200  # start this long before the peak
PLAN = [(0, 0), (480, 90), (1380, 135)]  # (from s, heading relative to the waves' origin)
DURATION_S = 1800
RECORD_S = 0.5
MARGIN = 0.5  # degrees kept between the start and the data edge
CREDIT = "Generated using E.U. Copernicus Marine Service Information"
# Cloud cover and precipitation are not in Copernicus Marine products. ERA5 (Copernicus Climate Change
# Service) has them; Open-Meteo serves ERA5 hourly by point without an account.
WEATHER_URL = "https://archive-api.open-meteo.com/v1/archive"
WEATHER_CREDIT = ("Weather data by Open-Meteo.com (CC BY 4.0), from ERA5, "
                  "Copernicus Climate Change Service (C3S)")


def environment(region: str, start: datetime, lon: float, lat: float, storm_id: str | None = None) -> CopernicusEnvironment:
    """Committed per-storm subsets (pipeline.storms --subsets) if present, else the region's raw year."""
    subset = STORMS / region / storm_id if storm_id else None
    raw = subset if subset is not None and subset.exists() else directory(region)
    pad = pd.Timedelta(hours=12)
    window = slice(pd.Timestamp(start.replace(tzinfo=None)) - pad, pd.Timestamp(start.replace(tzinfo=None)) + pad)
    data = {}
    for name in ("waves", "currents", "wind"):
        ds = xr.open_mfdataset(str(raw / f"{name}*.nc")).sortby("time")
        data[name] = ds.sel(time=window, longitude=slice(lon - 1, lon + 1), latitude=slice(lat - 1, lat + 1)).load()
    return CopernicusEnvironment(**data)


def sun_elevation(t: pd.Timestamp, lat: float, lon: float) -> float:
    """Solar elevation in degrees (NOAA approximation), t in UTC."""
    g = 2 * math.pi / 365 * (t.dayofyear - 1 + (t.hour + t.minute / 60 - 12) / 24)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g) - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    ha = math.radians(((t.hour * 60 + t.minute) + eqt + 4 * lon) / 4 - 180)
    la = math.radians(lat)
    return math.degrees(math.asin(math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)))


def daylight_start(region: str, storm: dict, lon: float, lat: float) -> datetime | None:
    """The 20-minute-step start within 12 h of the peak with the highest Hs at the start point while the sun
    stays above 10° for the whole scene; None if the storm has no such daylight."""
    peak = pd.Timestamp(storm["peak"].rstrip("Z"))
    subset = STORMS / region / storm["id"] / "waves.nc"
    waves = xr.open_dataset(subset if subset.exists() else sorted(directory(region).glob("waves_*.nc"))[0])
    best = None
    for start in pd.date_range(peak - pd.Timedelta(hours=12), peak + pd.Timedelta(hours=11), freq="20min"):
        if min(sun_elevation(start + pd.Timedelta(seconds=s), lat, lon) for s in (0, DURATION_S)) < 10:
            continue
        hs = float(waves.VHM0.interp(time=np.datetime64(start), longitude=lon, latitude=lat))
        if not np.isnan(hs) and (best is None or hs > best[1]):
            best = (start, hs)
    return None if best is None else best[0].to_pydatetime().replace(tzinfo=timezone.utc)


def export(storm_id: str, start: datetime | None = None) -> Path:
    region = storm_id.split("-")[0]
    storm = next(s for s in json.loads((STORMS / f"{region}.json").read_text())["storms"] if s["id"] == storm_id)
    west, east, south, north = REGIONS[region]
    lon = min(max(storm["lon"], west + MARGIN), east - MARGIN)
    lat = min(max(storm["lat"], south + MARGIN), north - MARGIN)
    if start is None:
        start = daylight_start(region, storm, lon, lat) or (
            datetime.fromisoformat(storm["peak"].replace("Z", "+00:00")) - pd.Timedelta(seconds=LEAD_S))
    sim = Simulation(load_s175(ROOT / "data/processed/s175"), environment(region, start, lon, lat, storm_id), seed=1,
                     settings=Settings(**STORM_SETTINGS), wind_parameters=WindParameters(**REFERENCE["wind_parameters"]))
    wave_from = sim.observe(sim.initial_state(start, lon, lat, heading_deg=0, speed_mps=SPEED_MPS)).wave_from_deg
    state = sim.initial_state(start, lon, lat, heading_deg=wave_from, speed_mps=SPEED_MPS)
    cargo_model = CargoModel(seed=1)
    cargo = cargo_model.initial_state()
    samples, events, orders = [], [], []
    record_ticks = round(RECORD_S / sim.settings.dt_s)

    def step(sample, dt):
        for event in cargo_model.advance(cargo, sample, dt):
            events.append({"t": round(sample.time_s, 2), "kind": event["kind"], "detail": event["detail"],
                           "units": event.get("unit_ids", [])})
        if round(sample.time_s / sim.settings.dt_s) % record_ticks == 0:
            # lon/lat need 6 decimals (about 0.1 m); 3 decimals moved the ship in 75–110 m jumps
            samples.append([round(v, 6 if i in (1, 2) else 3) for i, v in enumerate((
                sample.time_s, sample.lon, sample.lat, sample.heading_deg, sample.roll_deg, sample.pitch_deg,
                sample.heave_m, sample.speed_through_water_mps, sample.hs_m, sample.tp_s, sample.wave_from_deg,
                sample.wind_east_mps, sample.wind_north_mps, sample.current_east_mps, sample.current_north_mps,
                cargo["lost_teu"], cargo["grade"]))])
        return None

    stop = "duration_reached"
    for i, (at, relative) in enumerate(PLAN):
        until = PLAN[i + 1][0] if i + 1 < len(PLAN) else DURATION_S
        heading = (wave_from + relative) % 360
        orders.append({"t": at, "heading_deg": round(heading, 1), "speed_mps": SPEED_MPS, "relative_to_waves_deg": relative})
        result = sim.advance(state, Command(heading, SPEED_MPS), until - at, on_step=step)
        state = result.state
        if result.stopping_reason != "duration_reached":
            stop = result.stopping_reason
            break
    lost = sorted({u for e in events if e["kind"] == "cargo_lost" for u in e["units"]})
    scene = {
        "version": 1, "id": storm_id, "storm": storm, "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "start_lon_lat": [lon, lat], "vessel": {"name": "S175", "length_m": 175.0, "breadth_m": 25.4, "draft_m": 9.5},
        "cargo": {"algorithm": cargo_model.algorithm_id, "config": cargo_model.config, "deck_units": 320, "total_teu": 1200,
                  "units": [{"id": u["id"], "teu": u["teu"], "tier": u["category"]} for u in cargo["units"][:320]],
                  "lost_units": lost, "lost_teu": cargo["lost_teu"], "grade": cargo["grade"]},
        "orders": orders, "plan_note": "Demonstration orders relative to the initial wave direction; not a player's game.",
        "stop": stop,
        "fields": ["t", "lon", "lat", "heading", "roll", "pitch", "heave", "stw", "hs", "tp", "wave_from",
                   "wind_e", "wind_n", "current_e", "current_n", "lost_teu", "grade"],
        "samples": samples, "events": events,
        "sea_surface_note": "The 3D sea surface is synthesised from Hs, Tp and direction; individual waves are not data.",
        "copernicus_products": PRODUCTS, "credit": CREDIT,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{storm_id}.json"
    path.write_text(json.dumps(scene, separators=(",", ":")))
    return path


def add_weather(path: Path) -> Path:
    """Add hourly ERA5 cloud cover (%) and precipitation (mm/h) at the scene's start point to each sample."""
    scene = json.loads(path.read_text())
    if "cloud" in scene["fields"]:
        return path
    lon, lat = scene["start_lon_lat"]
    start = pd.Timestamp(scene["start"]).tz_convert(None)
    times = start + pd.to_timedelta([row[0] for row in scene["samples"]], unit="s")
    query = urlencode({"latitude": round(lat, 3), "longitude": round(lon, 3), "models": "era5",
                       "start_date": f"{times[0]:%Y-%m-%d}", "end_date": f"{times[-1]:%Y-%m-%d}",
                       "hourly": "cloud_cover,precipitation"})
    with urlopen(f"{WEATHER_URL}?{query}", timeout=60) as response:
        hourly = json.load(response)["hourly"]
    if any(v is None for v in hourly["cloud_cover"] + hourly["precipitation"]):
        # ERA5 is published about five days behind real time; leave the scene without weather
        print(f"{path.name}: no ERA5 weather yet for {times[0]:%Y-%m-%d}")
        return path
    hours = pd.to_datetime(hourly["time"]).astype("int64") / 1e9
    at = times.astype("int64") / 1e9
    # precipitation is the total of the hour ending at each stamp; cloud cover is instantaneous
    cloud = np.interp(at, hours, hourly["cloud_cover"])
    precip = np.array(hourly["precipitation"], dtype=float)[np.clip(np.searchsorted(hours, at), 0, len(hours) - 1)]
    for row, c, r in zip(scene["samples"], cloud, precip):
        row += [round(float(c)), round(float(r), 2)]
    scene["fields"] += ["cloud", "precip"]
    scene["weather"] = {"source": WEATHER_URL, "model": "era5", "point_lat_lon": [lat, lon], "credit": WEATHER_CREDIT}
    path.write_text(json.dumps(scene, separators=(",", ":")))
    return path


def write_index(region: str) -> Path:
    """List every catalogued storm for the scene picker, marking the ones with an exported scene."""
    catalogue = json.loads((STORMS / f"{region}.json").read_text())
    for storm in catalogue["storms"]:
        storm["scene"] = (OUT / f"{storm['id']}.json").exists()
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "index.json"
    path.write_text(json.dumps(catalogue, indent=1) + "\n")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("storms", nargs="*")
    parser.add_argument("--index", choices=REGIONS, help="only rewrite scenes/index.json for this region")
    parser.add_argument("--start", help="UTC start, e.g. 2026-01-23T11:40Z (default: 20 min before the peak)")
    parser.add_argument("--weather-only", action="store_true", help="add ERA5 cloud and rain to existing scenes")
    args = parser.parse_args()
    if args.index:
        print(write_index(args.index))
        return
    if args.weather_only:
        for storm_id in args.storms:
            print(add_weather(OUT / f"{storm_id}.json"))
        return
    for storm_id in args.storms:
        start = datetime.fromisoformat(args.start.replace("Z", "+00:00")) if args.start else None
        path = add_weather(export(storm_id, start))
        scene = json.loads(path.read_text())
        print(f"{path.name}: {len(scene['samples'])} samples, lost {scene['cargo']['lost_teu']} TEU, stop {scene['stop']}"
              f", {path.stat().st_size / 1e6:.2f} MB")
        write_index(storm_id.split("-")[0])


if __name__ == "__main__":
    main()
