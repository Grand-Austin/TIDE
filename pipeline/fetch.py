"""Download Copernicus Marine subsets for the case area into data/raw/ (git-ignored).

Needs a Copernicus Marine account: run `copernicusmarine login` once on the machine.

    python -m pipeline.fetch forecast            # save today's Mediterranean wave and current forecast
    python -m pipeline.fetch day 2026-09-09      # one reanalysis day over the map box, for the map

Forecast archive: MEDSEA_ANALYSISFORECAST_WAV_006_017 and MEDSEA_ANALYSISFORECAST_PHY_006_013 keep
one rolling series in which each forecast is overwritten by the next run, so a forecast issued for a
past day can only be recovered if it was saved at the time. `.github/workflows/forecast-archive.yml`
runs `forecast` daily. Run `forecast` every day (it skips a day already saved). Each file holds
the series as downloaded on that day: today's hours plus the forecast hours available then, about
ten days ahead.
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path
from typing import NamedTuple

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"


class Box(NamedTuple):
    min_lon: float
    max_lon: float
    min_lat: float
    max_lat: float


# Computation box: identical grid to the committed summer 2026 files (data/copernicus/README.md).
COMPUTE_BOX = Box(2.9, 4.1, 39.59, 40.28)
# Display box: map background only.
DISPLAY_BOX = Box(2.3, 4.5, 39.2, 40.5)
# Map box: the Balearic Islands, for the animated map (web/map-spike/).
MAP_BOX = Box(1.1, 4.7, 38.5, 40.6)

WAVE_VARIABLES = [
    "VHM0", "VMDR", "VTPK", "VTM02",
    "VHM0_WW", "VMDR_WW", "VTM01_WW",
    "VHM0_SW1", "VMDR_SW1", "VTM01_SW1",
]  # same variables as the multi-year wave file
FORECAST_WAVES = "cmems_mod_med_wav_anfc_4.2km_PT1H-i"  # MEDSEA_ANALYSISFORECAST_WAV_006_017
# Surface currents; this dataset version is retired on 2027-01-12.
FORECAST_CURRENTS = "cmems_mod_med_phy-cur_anfc_4.2km-2D_PT1H-m"  # MEDSEA_ANALYSISFORECAST_PHY_006_013
FORECASTS = {FORECAST_WAVES: WAVE_VARIABLES, FORECAST_CURRENTS: ["uo", "vo"]}
# Same datasets as data/copernicus/, fewer variables.
DAY_DATASETS = {
    "cmems_mod_med_wav_my_4.2km_PT1H-i": ["VHM0", "VMDR", "VTPK"],  # MEDSEA_MULTIYEAR_WAV_006_012
    "cmems_mod_med_phy-cur_my_4.2km_PT1H-m": ["uo", "vo"],  # MEDSEA_MULTIYEAR_PHY_006_004
    "cmems_obs-wind_glo_phy_nrt_l4_0.125deg_PT1H": ["eastward_wind", "northward_wind"],  # WIND_GLO_PHY_L4_NRT_012_004
}


def fetch_forecast(out: Path, day: dt.date, dataset_id: str, variables: list[str]) -> Path:
    """Save the series from `day` 00:00 UTC to the end of the current forecast."""
    import copernicusmarine

    name = f"{dataset_id}_{day:%Y%m%d}.nc"
    path = out / name
    if path.exists():
        print(f"{path} exists, skipping")
        return path
    # The display box costs little and keeps the forecast usable for the map; it cannot be fetched later.
    box = DISPLAY_BOX
    copernicusmarine.subset(
        dataset_id=dataset_id,
        variables=variables,
        minimum_longitude=box.min_lon,
        maximum_longitude=box.max_lon,
        minimum_latitude=box.min_lat,
        maximum_latitude=box.max_lat,
        start_datetime=f"{day:%Y-%m-%d}T00:00:00",
        output_directory=out,
        output_filename=name,
        netcdf_compression_level=1,
        disable_progress_bar=True,
    )
    print(f"saved {path}")
    return path


def fetch_day(out: Path, day: dt.date, box: Box = MAP_BOX) -> list[Path]:
    """Save one UTC day of waves, surface currents and wind over `box`."""
    import copernicusmarine

    paths = []
    for dataset_id, variables in DAY_DATASETS.items():
        path = out / f"{dataset_id}_{day:%Y%m%d}.nc"
        paths.append(path)
        if path.exists():
            print(f"{path} exists, skipping")
            continue
        copernicusmarine.subset(
            dataset_id=dataset_id,
            variables=variables,
            minimum_longitude=box.min_lon,
            maximum_longitude=box.max_lon,
            minimum_latitude=box.min_lat,
            maximum_latitude=box.max_lat,
            start_datetime=f"{day:%Y-%m-%d}T00:00:00",
            end_datetime=f"{day:%Y-%m-%d}T23:00:00",
            minimum_depth=0,
            maximum_depth=1.1,  # surface level only; ignored by datasets without depth
            output_directory=out,
            output_filename=path.name,
            disable_progress_bar=True,
        )
        print(f"saved {path}")
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    forecast = sub.add_parser("forecast", help="save today's MED wave and current forecast")
    forecast.add_argument("--out", type=Path, default=RAW / "forecast")
    day = sub.add_parser("day", help="save one reanalysis day over the map box")
    day.add_argument("date", type=dt.date.fromisoformat)
    day.add_argument("--out", type=Path, default=RAW / "day")
    args = parser.parse_args()

    if args.command == "forecast":
        args.out.mkdir(parents=True, exist_ok=True)
        today = dt.datetime.now(dt.timezone.utc).date()
        for dataset_id, variables in FORECASTS.items():
            fetch_forecast(args.out, today, dataset_id, variables)
    elif args.command == "day":
        args.out.mkdir(parents=True, exist_ok=True)
        fetch_day(args.out, args.date)


if __name__ == "__main__":
    main()
