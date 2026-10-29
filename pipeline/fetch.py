"""Download Copernicus Marine subsets for the case area into data/raw/ (git-ignored).

Needs a Copernicus Marine account: run `copernicusmarine login` once on the machine.

    python -m pipeline.fetch forecast            # save today's Mediterranean wave and current forecast
    python -m pipeline.fetch day 2026-09-09      # one historical day over the map box

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
import json
import tempfile
import xarray as xr
import numpy as np
from pipeline.downloads import retry, save_json, sha256

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
    "VHM0",
    "VMDR",
    "VTPK",
    "VTM02",
    "VHM0_WW",
    "VMDR_WW",
    "VTM01_WW",
    "VHM0_SW1",
    "VMDR_SW1",
    "VTM01_SW1",
]  # same variables as the multi-year wave file
FORECAST_WAVES = (
    "cmems_mod_med_wav_anfc_4.2km_PT1H-i"  # MEDSEA_ANALYSISFORECAST_WAV_006_017
)
# Surface currents; this dataset version is retired on 2027-01-12.
FORECAST_CURRENTS = (
    "cmems_mod_med_phy-cur_anfc_4.2km-2D_PT1H-m"  # MEDSEA_ANALYSISFORECAST_PHY_006_013
)
FORECASTS = {FORECAST_WAVES: WAVE_VARIABLES, FORECAST_CURRENTS: ["uo", "vo"]}
# Same datasets as data/copernicus/, fewer variables.
DAY_DATASETS = {
    "cmems_mod_med_wav_my_4.2km_PT1H-i": [
        "VHM0",
        "VMDR",
        "VTPK",
    ],  # MEDSEA_MULTIYEAR_WAV_006_012
    "cmems_mod_med_phy-cur_my_4.2km_PT1H-m": [
        "uo",
        "vo",
    ],  # MEDSEA_MULTIYEAR_PHY_006_004
    "cmems_obs-wind_glo_phy_nrt_l4_0.125deg_PT1H": [
        "eastward_wind",
        "northward_wind",
    ],  # WIND_GLO_PHY_L4_NRT_012_004
}


def validate_file(path, request):
    with xr.open_dataset(path) as ds:
        for variable in request["variables"]:
            if (
                variable not in ds
                or not ds[variable].size
                or not np.isfinite(ds[variable].values).any()
            ):
                raise ValueError(f"Missing downloaded variable {variable}")
        for coord in ("time", "latitude", "longitude"):
            axis = ds[coord].values
            if axis.ndim != 1 or len(axis) == 0:
                raise ValueError(f"Invalid {coord} axis")
            if coord == "time":
                valid = (
                    np.issubdtype(axis.dtype, np.datetime64)
                    and not np.isnat(axis).any()
                )
                increasing = (
                    np.all(np.diff(axis) > np.timedelta64(0, "ns")) if valid else False
                )
            else:
                valid = np.isfinite(axis).all()
                increasing = np.all(np.diff(axis) > 0)
            if not valid or not increasing:
                raise ValueError(f"Invalid {coord} axis")
        start = np.datetime64(request["start_datetime"].replace("Z", ""))
        if ds.time.values[0] > start or ds.time.values[-1] < start:
            raise ValueError("Downloaded time coverage misses request start")
        if request.get("end_datetime") and ds.time.values[-1] < np.datetime64(
            request["end_datetime"].replace("Z", "")
        ):
            raise ValueError("Downloaded time coverage misses request end")
        # Window requests include padding; actual requested box must remain covered.
        if "coverage_box" in request:
            west, east, south, north = request["coverage_box"]
            if (
                float(ds.longitude[0]) > west
                or float(ds.longitude[-1]) < east
                or float(ds.latitude[0]) > south
                or float(ds.latitude[-1]) < north
            ):
                raise ValueError("Downloaded spatial coverage misses scenario box")
        return {
            "variables": {v: ds[v].attrs.get("units") for v in request["variables"]},
            "coverage": {
                c: [str(ds[c].values[0]), str(ds[c].values[-1])]
                for c in ("time", "latitude", "longitude")
            },
            "source_attributes": {k: str(v) for k, v in ds.attrs.items()},
            "model_run_at": str(ds.attrs["forecast_reference_time"])
            if "forecast_reference_time" in ds.attrs
            else None,
            "issued_at": None,
        }


def collect(out, name, request):
    import copernicusmarine

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / name
    sidecar = path.with_suffix(".json")
    if path.exists() and not sidecar.exists():
        raise ValueError(
            f"Existing download has no integrity manifest: {path}; use a new output directory"
        )
    if path.exists() and sidecar.exists():
        cached = json.loads(sidecar.read_text())
        if cached.get("request") == request and cached.get("sha256") == sha256(path):
            validate_file(path, request)
            return path
        raise ValueError(
            f"Existing download differs from request or checksum: {path}; use a new output directory"
        )
    try:
        with tempfile.TemporaryDirectory(dir=out) as temporary:
            arguments = {k: v for k, v in request.items() if k != "coverage_box"}
            retry(
                lambda: copernicusmarine.subset(
                    **arguments,
                    output_directory=temporary,
                    output_filename=name,
                    overwrite=True,
                    disable_progress_bar=True,
                )
            )
            downloaded = Path(temporary) / name
            metadata = validate_file(downloaded, request)
            metadata.update(
                request=request,
                sha256=sha256(downloaded),
                retrieved_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                status="validated",
            )
            downloaded.replace(path)
            save_json(sidecar, metadata)
    except Exception as error:
        save_json(
            out / (name + ".failure.json"),
            {
                "status": "failed",
                "error_type": type(error).__name__,
                "request": request,
            },
        )
        raise
    return path


def fetch_forecast(out, day, dataset_id, variables):
    box = DISPLAY_BOX
    return collect(
        out,
        f"{dataset_id}_{day:%Y%m%d}.nc",
        dict(
            dataset_id=dataset_id,
            variables=variables,
            minimum_longitude=box.min_lon,
            maximum_longitude=box.max_lon,
            minimum_latitude=box.min_lat,
            maximum_latitude=box.max_lat,
            start_datetime=f"{day:%Y-%m-%d}T00:00:00",
        ),
    )


def fetch_day(out, day, box=MAP_BOX):
    return [
        collect(
            out,
            f"{dataset_id}_{day:%Y%m%d}.nc",
            dict(
                dataset_id=dataset_id,
                variables=variables,
                minimum_longitude=box.min_lon,
                maximum_longitude=box.max_lon,
                minimum_latitude=box.min_lat,
                maximum_latitude=box.max_lat,
                start_datetime=f"{day:%Y-%m-%d}T00:00:00",
                end_datetime=f"{day:%Y-%m-%d}T23:00:00",
                minimum_depth=0,
                maximum_depth=1.1,
            ),
        )
        for dataset_id, variables in DAY_DATASETS.items()
    ]


def fetch_window(request_path, out):
    from game.scenario import utc

    spec = json.loads(Path(request_path).read_text())
    spec = spec.get("collection", spec)
    start, end = utc(spec["start"]), utc(spec["end"])
    west, east, south, north = spec["bbox"]
    if end <= start or not -180 <= west < east <= 180 or not -85 < south < north < 85:
        raise ValueError("Invalid download time window or box")
    manifest = {}
    failures = []
    for layer, source in spec["sources"].items():
        if layer not in ("waves", "currents", "wind", "temperature"):
            raise ValueError("Unknown environmental layer")
        if not source.get("dataset_version"):
            raise ValueError("Historical downloads require dataset_version")
        step = 0.125 if layer == "wind" else 1 / 24
        a = start.replace(minute=0, second=0, microsecond=0)
        b = end.replace(minute=0, second=0, microsecond=0)
        if b < end:
            b += dt.timedelta(hours=1)
        if layer == "temperature":
            a = a.replace(hour=0)
            b = b.replace(hour=0)
        stamp = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S")
        request = dict(
            source,
            minimum_longitude=west - step,
            maximum_longitude=east + step,
            minimum_latitude=south - step,
            maximum_latitude=north + step,
            start_datetime=stamp(a),
            end_datetime=stamp(b),
            coverage_box=[west, east, south, north],
        )
        if layer in ("temperature", "currents"):
            request.update(minimum_depth=0, maximum_depth=1.1)
        try:
            path = collect(out, layer + ".nc", request)
            manifest[layer] = json.loads(path.with_suffix(".json").read_text())
        except Exception as error:
            failures.append(layer)
            manifest[layer] = {"status": "failed", "error_type": type(error).__name__}
        save_json(Path(out) / "manifest.json", manifest)
    if failures:
        raise RuntimeError("Failed environmental layers: " + ", ".join(failures))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    forecast = sub.add_parser(
        "forecast", help="save today's MED wave and current forecast"
    )
    forecast.add_argument("--out", type=Path, default=RAW / "forecast")
    day = sub.add_parser("day", help="save one reanalysis day over the map box")
    day.add_argument("date", type=dt.date.fromisoformat)
    day.add_argument("--out", type=Path, default=RAW / "day")
    window = sub.add_parser(
        "window", help="collect a versioned four-source scenario window"
    )
    window.add_argument("--request", type=Path, required=True)
    window.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "window":
        fetch_window(args.request, args.out)
    elif args.command == "forecast":
        args.out.mkdir(parents=True, exist_ok=True)
        today = dt.datetime.now(dt.timezone.utc).date()
        failures = []
        for dataset_id, variables in FORECASTS.items():
            try:
                fetch_forecast(args.out, today, dataset_id, variables)
            except Exception as error:
                failures.append(dataset_id)
                save_json(
                    args.out / (dataset_id + ".failure.json"),
                    {"status": "failed", "error_type": type(error).__name__},
                )
        if failures:
            raise SystemExit("Failed forecast sources: " + ", ".join(failures))
    elif args.command == "day":
        args.out.mkdir(parents=True, exist_ok=True)
        fetch_day(args.out, args.date)


if __name__ == "__main__":
    main()
