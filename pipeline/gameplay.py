"""Build frozen game regions and explicitly simulated regional forecasts."""

import argparse
import math
from datetime import timedelta
from pathlib import Path

import numpy as np
import xarray as xr
from scipy.spatial import ConvexHull

from game.cargo import CargoModel
from game.scenario import digest, load_scenario, utc
from pipeline.scenario import write_json


def build_gameplay(scenario):
    d = scenario.definition
    w, e, s, n = d["area_bbox"]
    start = utc(d["start"])
    budget = d["mission"]["budget_s"]
    region_hs = d.get("gameplay_settings", {}).get("region_hs_m", 3.0)
    regions = []
    reports = []
    source = d["environment"]["waves"]
    if digest(scenario.root / source["path"]) != source["sha256"]:
        raise ValueError("Wave checksum mismatch")
    with xr.open_dataset(scenario.root / source["path"]) as ds:
        subset = ds.sel(longitude=slice(w, e), latitude=slice(s, n))
        lons, lats = subset.longitude.values, subset.latitude.values
        dx = float(np.diff(lons).mean()) / 2
        dy = float(np.diff(lats).mean()) / 2
        for t in range(0, budget + 1, 60):
            at = np.datetime64((start + timedelta(seconds=t)).replace(tzinfo=None))
            hs = subset.VHM0.interp(time=at).values
            points = []
            for i, j in np.argwhere(np.isfinite(hs) & (hs >= region_hs)):
                points.extend(
                    [
                        [float(lons[j] + a * dx), float(lats[i] + b * dy)]
                        for a, b in ((-1, -1), (-1, 1), (1, 1), (1, -1))
                    ]
                )
            polygon = (
                np.array(points)[ConvexHull(points).vertices].tolist()
                if len(points) >= 3
                else []
            )
            regions.append({"time_s": t, "polygon": polygon})
    with scenario.environment() as env:
        for issued in range(0, budget, 3600):
            end = min(budget, issued + 3600)
            for x in range(3):
                for y in range(3):
                    lon = w + (x + 0.5) * (e - w) / 3
                    lat = s + (y + 0.5) * (n - s) / 3
                    samples = []
                    for t in (issued, end):
                        try:
                            samples.append(
                                env.sample(start + timedelta(seconds=t), lon, lat)
                            )
                        except ValueError:
                            pass
                    if not samples:
                        continue
                    hs = [a.hs for a in samples]
                    winds = [
                        math.hypot(a.wind_east_mps, a.wind_north_mps) for a in samples
                    ]
                    reports.append(
                        {
                            "id": f"forecast-{issued}-{x}-{y}",
                            "sector": [x, y],
                            "issued_s": issued,
                            "issued_at": (
                                start + timedelta(seconds=issued)
                            ).isoformat(),
                            "valid_to_s": end,
                            "valid_from": (
                                start + timedelta(seconds=issued)
                            ).isoformat(),
                            "valid_to": (start + timedelta(seconds=end)).isoformat(),
                            "label": "game_simulated_forecast",
                            "provenance": "Offline historical reconstruction; not an official historical forecast",
                            "hs_range_m": [
                                math.floor(min(hs) * 2) / 2,
                                math.ceil(max(hs) * 2) / 2,
                            ],
                            "wind_range_mps": [
                                math.floor(min(winds) / 2.5) * 2.5,
                                math.ceil(max(winds) / 2.5) * 2.5,
                            ],
                            "wave_from_deg": round(samples[0].wave_from_deg / 45)
                            * 45
                            % 360,
                            "wave_period_s": round(samples[0].tp),
                            "trend": "increasing" if hs[-1] > hs[0] + 0.2 else "steady",
                            "important": issued > 0 and max(hs) >= 4,
                            "reason": "Forecast wave height at least 4 m"
                            if max(hs) >= 4
                            else "Routine update",
                        }
                    )
    return {
        "version": 1,
        "region_method": f"Hs>={region_hs:g}m convex cell-footprint envelope; held on one-minute schedule",
        "source_sha256": source["sha256"],
        "regions": regions,
        "reports": reports,
        "cargo": CargoModel().config,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("scenario", type=Path)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    write_json(a.out, build_gameplay(load_scenario(a.scenario)))


if __name__ == "__main__":
    main()
