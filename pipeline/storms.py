"""Find storms in a downloaded region year (pipeline.fetch_region) and write a small catalogue.

A storm step is a 3-hourly time at which the 90th percentile of Hs reaches 6 m in any sub-area;
steps no more than 12 h apart belong to the same storm. Each storm keeps its peak cell and the
sea state there.

    python -m pipeline.storms biscay             # writes data/storms/biscay.json
    python -m pipeline.storms biscay --subsets   # also data/storms/biscay/<id>/{waves,wind,currents}.nc

The subsets (peak ±24 h, ±2° around the peak cell) are small enough to commit, so teammates can export
scenes and build scenarios without the year of raw data or a Copernicus Marine account.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from pipeline.fetch_region import PRODUCTS, REGIONS, directory

OUT = Path(__file__).resolve().parents[1] / "data" / "storms"
KN = 1.94384
STORM_HS = 6.0
MERGE = pd.Timedelta(hours=12)
SUB_AREAS = {
    "biscay": {
        "Finisterre": (-12, -8.5, 41.5, 45),
        "Celtic Sea": (-11, -5, 49.5, 52),
        "Ushant": (-8, -3, 47, 50.5),
        "Biscay": (-9, -1.5, 43.5, 47),
    },
}


def value(x, decimals=1):
    x = float(x)
    return None if np.isnan(x) else round(x, decimals)


def storms(region: str) -> list[dict]:
    raw = directory(region)
    waves = xr.open_mfdataset(str(raw / "waves_*.nc")).sortby("time").load()
    wind = xr.open_mfdataset(str(raw / "wind_*.nc")).sortby("time").load()
    speed = np.hypot(wind.eastward_wind, wind.northward_wind) * KN
    p90 = pd.DataFrame({
        name: waves.VHM0.sel(longitude=slice(w, e), latitude=slice(s, n)).quantile(0.9, dim=["latitude", "longitude"]).to_series()
        for name, (w, e, s, n) in SUB_AREAS[region].items()
    })
    steps = p90.index[(p90 >= STORM_HS).any(axis=1)]
    groups, current = [], [steps[0]]
    for t in steps[1:]:
        if t - current[-1] <= MERGE:
            current.append(t)
        else:
            groups.append(current)
            current = [t]
    groups.append(current)
    result = []
    for group in groups:
        window = waves.sel(time=slice(group[0], group[-1]))
        peak = pd.Timestamp(window.VHM0.max(["latitude", "longitude"]).idxmax().values)
        snap = waves.sel(time=peak)
        i, j = np.unravel_index(int(np.nanargmax(snap.VHM0.values)), snap.VHM0.shape)
        cell = snap.isel(latitude=i, longitude=j)
        before = waves.VHM0.sel(time=slice(peak - pd.Timedelta(hours=24), peak)).max(["latitude", "longitude"])
        result.append({
            "id": f"{region}-{peak:%Y%m%d-%H}",
            "start": f"{group[0]:%Y-%m-%dT%H:%M}Z",
            "end": f"{group[-1]:%Y-%m-%dT%H:%M}Z",
            "hours": int((group[-1] - group[0]).total_seconds() // 3600) + 3,
            "peak": f"{peak:%Y-%m-%dT%H:%M}Z",
            "area": p90.loc[group[0]:group[-1]].max().idxmax(),
            "lon": value(cell.longitude, 3), "lat": value(cell.latitude, 3),
            "hs_m": value(cell.VHM0), "tp_s": value(cell.VTPK), "wave_from_deg": value(cell.VMDR, 0),
            "wind_sea_m": value(cell.VHM0_WW), "swell_m": value(cell.VHM0_SW1), "swell_from_deg": value(cell.VMDR_SW1, 0),
            "rise_24h_m": value(float(cell.VHM0) - float(before.min())),
            "max_wind_kn": value(speed.sel(time=peak, method="nearest").max(), 0),
        })
    return result


SUBSET_HOURS = 24
SUBSET_DEG = 2.0
LAYERS = ("waves", "wind", "currents")


def write_subsets(region: str) -> Path:
    catalogue = json.loads((OUT / f"{region}.json").read_text())
    raw = directory(region)
    data = {name: xr.open_mfdataset(str(raw / f"{name}_*.nc")).sortby("time") for name in LAYERS}
    pad = pd.Timedelta(hours=SUBSET_HOURS)
    files = {}
    for storm in catalogue["storms"]:
        peak = pd.Timestamp(storm["peak"].rstrip("Z"))
        folder = OUT / region / storm["id"]
        folder.mkdir(parents=True, exist_ok=True)
        for name, ds in data.items():
            sub = ds.sel(time=slice(peak - pad, peak + pad),
                         longitude=slice(storm["lon"] - SUBSET_DEG, storm["lon"] + SUBSET_DEG),
                         latitude=slice(storm["lat"] - SUBSET_DEG, storm["lat"] + SUBSET_DEG)).load()
            path = folder / f"{name}.nc"
            sub.to_netcdf(path, encoding={v: {"zlib": True, "complevel": 4} for v in sub.data_vars})
            files[f"{storm['id']}/{name}.nc"] = {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size,
                "time": [f"{pd.Timestamp(sub.time.values[0]):%Y-%m-%dT%H:%MZ}", f"{pd.Timestamp(sub.time.values[-1]):%Y-%m-%dT%H:%MZ}"],
            }
    manifest = OUT / region / "manifest.json"
    manifest.write_text(json.dumps({
        "window": f"storm peak ±{SUBSET_HOURS} h, ±{SUBSET_DEG:g}° around the peak cell, clipped to the region",
        "copernicus_products": PRODUCTS, "credit": "Generated using E.U. Copernicus Marine Service Information",
        "files": files,
    }, indent=1) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("region", choices=REGIONS)
    parser.add_argument("--subsets", action="store_true", help="also write per-storm data subsets")
    args = parser.parse_args()
    found = storms(args.region)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{args.region}.json"
    path.write_text(json.dumps({
        "region": args.region, "bbox": REGIONS[args.region],
        "definition": f"90th-percentile Hs >= {STORM_HS:g} m in a sub-area; steps <= 12 h apart merged; 3-hourly data",
        "sub_areas": SUB_AREAS[args.region], "copernicus_products": PRODUCTS,
        "note": "max_wind_kn is the regional maximum at the peak time, not necessarily at the peak cell.",
        "storms": found,
    }, indent=1) + "\n")
    print(f"{path}: {len(found)} storms")
    if args.subsets:
        print(write_subsets(args.region))


if __name__ == "__main__":
    main()
