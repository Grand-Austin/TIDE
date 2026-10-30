"""Download a year of 3-hourly waves, wind and surface currents for one region (Copernicus Marine, global anfc).

Monthly compressed NetCDF files go to data/raw/<region>-year/ (git-ignored). Existing months are skipped,
so a failed run can be repeated. Needs `copernicusmarine login`.

    python -m pipeline.fetch_region biscay
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import copernicusmarine as cm
import pandas as pd

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
REGIONS = {
    # name: (west, east, south, north)
    "biscay": (-12, -1, 42, 52),
}
LAYERS = {
    # name: (dataset_id, variables, time stride to reach 3 h, extra selection)
    "waves": ("cmems_mod_glo_wav_anfc_0.083deg_PT3H-i", ["VHM0", "VMDR", "VTPK", "VHM0_WW", "VHM0_SW1", "VMDR_SW1"], 1, {}),
    "wind": ("cmems_obs-wind_glo_phy_nrt_l4_0.125deg_PT1H", ["eastward_wind", "northward_wind"], 3, {}),
    "currents": ("cmems_mod_glo_phy-cur_anfc_0.083deg_PT6H-i", ["uo", "vo"], 1, dict(minimum_depth=0, maximum_depth=1)),
}
PRODUCTS = {
    "waves": "GLOBAL_ANALYSISFORECAST_WAV_001_027",
    "wind": "WIND_GLO_PHY_L4_NRT_012_004",
    "currents": "GLOBAL_ANALYSISFORECAST_PHY_001_024",
}


def directory(region: str) -> Path:
    return RAW / f"{region}-year"


def months(start: pd.Timestamp, end: pd.Timestamp):
    edges = [start, *pd.date_range(start.normalize() + pd.offsets.MonthBegin(1), end, freq="MS"), end]
    return list(zip(edges[:-1], edges[1:]))


def fetch(region: str, start: pd.Timestamp, end: pd.Timestamp, names=LAYERS) -> None:
    west, east, south, north = REGIONS[region]
    box = dict(minimum_longitude=west, maximum_longitude=east, minimum_latitude=south, maximum_latitude=north)
    out = directory(region)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    for name in names:
        dataset_id, variables, stride, extra = LAYERS[name]
        for a, b in months(start, end):
            path = out / f"{name}_{a:%Y%m%d}-{b:%Y%m%d}.nc"
            if path.exists():
                continue
            ds = cm.open_dataset(dataset_id=dataset_id, variables=variables, **box, **extra,
                                 start_datetime=a.isoformat(), end_datetime=(b - pd.Timedelta(minutes=1)).isoformat())
            ds = ds.isel(time=slice(0, None, stride)).load()
            tmp = path.with_suffix(".tmp")
            ds.to_netcdf(tmp, encoding={v: {"zlib": True, "complevel": 4} for v in ds.data_vars})
            tmp.replace(path)
            print(f"{path.name}: {dict(ds.sizes)} {path.stat().st_size / 1e6:.0f} MB  [{time.time() - t0:.0f}s]", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("region", choices=REGIONS)
    parser.add_argument("--start", default="2025-10-29")
    parser.add_argument("--end", default="2026-10-29")
    args = parser.parse_args()
    fetch(args.region, pd.Timestamp(args.start), pd.Timestamp(args.end))


if __name__ == "__main__":
    main()
