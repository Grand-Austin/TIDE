"""Download Copernicus Marine subsets for the case area into data/raw/ (git-ignored).

Needs a Copernicus Marine account: run `copernicusmarine login` once on the machine.

    python -m pipeline.fetch forecast     # save today's Mediterranean wave forecast

Forecast archive: MEDSEA_ANALYSISFORECAST_WAV_006_017 keeps one rolling series in which each
forecast is overwritten by the next run, so a forecast issued for a past day can only be recovered
if it was saved at the time. Run `forecast` every day (it skips a day already saved). Each file holds
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

WAVE_VARIABLES = [
    "VHM0", "VMDR", "VTPK", "VTM02",
    "VHM0_WW", "VMDR_WW", "VTM01_WW",
    "VHM0_SW1", "VMDR_SW1", "VTM01_SW1",
]  # same variables as the multi-year wave file
FORECAST_WAVES = "cmems_mod_med_wav_anfc_4.2km_PT1H-i"  # MEDSEA_ANALYSISFORECAST_WAV_006_017


def fetch_forecast(out: Path, day: dt.date) -> Path:
    """Save the wave series from `day` 00:00 UTC to the end of the current forecast."""
    import copernicusmarine

    name = f"{FORECAST_WAVES}_{day:%Y%m%d}.nc"
    path = out / name
    if path.exists():
        print(f"{path} exists, skipping")
        return path
    # The display box costs little and keeps the forecast usable for the map; it cannot be fetched later.
    box = DISPLAY_BOX
    copernicusmarine.subset(
        dataset_id=FORECAST_WAVES,
        variables=WAVE_VARIABLES,
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    forecast = sub.add_parser("forecast", help="save today's MED wave forecast")
    forecast.add_argument("--out", type=Path, default=RAW / "forecast")
    args = parser.parse_args()

    if args.command == "forecast":
        args.out.mkdir(parents=True, exist_ok=True)
        fetch_forecast(args.out, dt.datetime.now(dt.timezone.utc).date())


if __name__ == "__main__":
    main()
