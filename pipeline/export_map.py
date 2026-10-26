"""Export one day of waves, currents and wind for the animated map (web/map-spike/).

Reads the files saved by `python -m pipeline.fetch day <date>` and writes compact JSON: values are scaled
integers on regular grids, `null` on land.

    python -m pipeline.export_map 2026-09-09
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from pipeline.fetch import DAY_DATASETS, RAW
from pipeline.routes import ROUTES

OUT = Path(__file__).resolve().parent.parent / "web" / "map-spike" / "data"
CREDIT = (
    "Generated using E.U. Copernicus Marine Service Information; "
    "https://doi.org/10.25423/CMCC/MEDSEA_MULTIYEAR_WAV_006_012_MEDWAM3I, "
    "https://doi.org/10.48670/mds-00375, https://doi.org/10.48670/moi-00305"
)


def grid(ds: xr.Dataset, layers: dict[str, tuple[str, float]]) -> dict:
    """Regular grid with each layer as integers (value * scale), indexed [time][lat * nx + lon]."""
    lon, lat = ds.longitude.values, ds.latitude.values
    out = {
        "lon0": round(float(lon[0]), 5), "lat0": round(float(lat[0]), 5),
        "dlon": round(float(np.diff(lon).mean()), 6), "dlat": round(float(np.diff(lat).mean()), 6),
        "nx": len(lon), "ny": len(lat),
    }
    for name, (var, scale) in layers.items():
        values = ds[var].squeeze(drop=True).values.reshape(ds.sizes["time"], -1) * scale
        out[name] = {
            "scale": scale,
            "values": [[None if np.isnan(v) else int(round(v)) for v in row] for row in values],
        }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("date", type=dt.date.fromisoformat)
    args = parser.parse_args()

    waves, currents, wind = (xr.open_dataset(RAW / "day" / f"{d}_{args.date:%Y%m%d}.nc") for d in DAY_DATASETS)
    data = {
        "date": f"{args.date:%Y-%m-%d}",
        "times": [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in pd.DatetimeIndex(waves.time.values)],
        "waves": grid(waves, {"hs": ("VHM0", 100), "dir": ("VMDR", 1), "period": ("VTPK", 10)}),
        "current": grid(currents, {"u": ("uo", 100), "v": ("vo", 100)}),
        "wind": grid(wind, {"u": ("eastward_wind", 10), "v": ("northward_wind", 10)}),
        "routes": ROUTES,
        "datasets": list(DAY_DATASETS),
        "credit": CREDIT,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{args.date:%Y-%m-%d}.json"
    path.write_text(json.dumps(data, separators=(",", ":")))
    print(f"wrote {path} ({path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
