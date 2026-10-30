"""Export each catalogued storm's Copernicus wave height and wind fields for the 3D page's passage map.

Reads the committed per-storm subsets (pipeline.storms --subsets) and writes web/game/fields/<id>.json: the wave
grid, its 3-hourly times, and Hs and 10 m wind (east, north) as integers in tenths (null on land), wind interpolated
onto the wave grid. Replays draw these as a colour field and wind streamlines; live play does not, because the
full field would show the hidden escape region.

    python -m pipeline.export_fields biscay            # every storm in data/storms/biscay.json
    python -m pipeline.export_fields biscay <id> ...   # chosen storms
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from pipeline.fetch_region import PRODUCTS, REGIONS
from pipeline.storms import OUT as STORMS

OUT = Path(__file__).resolve().parents[1] / "web" / "game" / "fields"


def tenths(values: np.ndarray) -> list:
    return [None if np.isnan(v) else int(round(v * 10)) for v in values.ravel()]


def export(region: str, storm_id: str) -> Path:
    folder = STORMS / region / storm_id
    waves = xr.open_dataset(folder / "waves.nc").sortby("time")
    wind = xr.open_dataset(folder / "wind.nc").sortby("time").interp(
        time=waves.time, longitude=waves.longitude, latitude=waves.latitude)
    sea = np.isfinite(waves.VHM0.values)
    u = np.where(sea, wind.eastward_wind.values, np.nan)
    v = np.where(sea, wind.northward_wind.values, np.nan)
    field = {
        "storm": storm_id,
        "lon": [round(float(x), 4) for x in waves.longitude.values],
        "lat": [round(float(y), 4) for y in waves.latitude.values],
        "times": [f"{pd.Timestamp(t):%Y-%m-%dT%H:%M:%SZ}" for t in waves.time.values],
        "layout": "values[time][lat][lon] flattened, row-major; tenths of a metre (hs) or m/s (wind); null on land",
        "hs": [tenths(waves.VHM0.values[k]) for k in range(waves.sizes["time"])],
        "wind_e": [tenths(u[k]) for k in range(waves.sizes["time"])],
        "wind_n": [tenths(v[k]) for k in range(waves.sizes["time"])],
        "copernicus_products": {k: PRODUCTS[k] for k in ("waves", "wind")},
        "credit": "Generated using E.U. Copernicus Marine Service Information",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{storm_id}.json"
    path.write_text(json.dumps(field, separators=(",", ":")))
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("region", choices=REGIONS)
    parser.add_argument("storms", nargs="*")
    args = parser.parse_args()
    ids = args.storms or [s["id"] for s in json.loads((STORMS / f"{args.region}.json").read_text())["storms"]]
    for storm_id in ids:
        path = export(args.region, storm_id)
        print(f"{path.name}: {path.stat().st_size / 1e3:.0f} kB")


if __name__ == "__main__":
    main()
