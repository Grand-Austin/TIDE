"""Write case files for the web app (docs/SCHEMA.md, section 1) from the summer 2026 data.

Which days become cases, and their titles, are chosen by people from data/candidates.json
(python -m pipeline.mine). Every number in a case file is computed here from Copernicus Marine data.

    python -m pipeline.export_cases     # writes web/public/cases/<id>.json
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from pipeline.grid import CURRENTS, DATA, KN, WAVES, WIND, Grid
from pipeline.mine import DEFAULT, day_flips, iso
from pipeline.routes import ROUTES
from pipeline.simulate import Result, legs_of, simulate
from pipeline.validate_summer import DEPARTURES

OUT = Path(__file__).resolve().parent.parent / "web" / "public" / "cases"
TEMPERATURE = DATA / "cmems_mod_med_phy-temp_my_4.2km_P1D-m_20260701-20260930.nc"
SPEED_KN = 6.0
TRACK_EVERY = 2  # keep every 2nd 5-minute sample: 10 minutes
ROUTE_NAMES = {"A": "Direct crossing", "B": "Northern loop", "C": "Southern lee"}
LIMITS_SOURCE = (
    "Teaching values anchored to the WMO sea-state code (Douglas scale); not limits for a real vessel. "
    "See docs/METHODS.md."
)
PRODUCTS = [
    "MEDSEA_MULTIYEAR_WAV_006_012", "cmems_mod_med_wav_my_4.2km_PT1H-i",
    "MEDSEA_MULTIYEAR_PHY_006_004", "cmems_mod_med_phy-cur_my_4.2km_PT1H-m", "cmems_mod_med_phy-temp_my_4.2km_P1D-m",
    "WIND_GLO_PHY_L4_NRT_012_004", "cmems_obs-wind_glo_phy_nrt_l4_0.125deg_PT1H",
]
# Chosen from data/candidates.json. Titles and the key evidence are editorial; the rest comes from the data.
CASES = {
    "c01": {"date": "2026-07-01", "title": "Three routes in one day", "goal": "beam_seas", "key_evidence": "wave_dir"},
    "c07": {"date": "2026-09-09", "title": "The Gregal morning", "goal": "time_window", "key_evidence": "wind"},
}


def layer(unit: str, values: np.ndarray, decimals: int) -> dict:
    """`values` indexed [time][lat][lon]; NaN (land) becomes null."""
    rounded = np.round(values.astype(float), decimals)
    return {
        "unit": unit,
        "values": [[[None if np.isnan(v) else (int(v) if decimals == 0 else float(v)) for v in row] for row in t] for t in rounded],
    }


def case_grid(day: pd.Timestamp, waves: xr.Dataset, currents: xr.Dataset, wind: xr.Dataset, temp: xr.Dataset) -> dict:
    span = slice(day, day + pd.Timedelta(hours=23))
    w, c = waves.sel(time=span), currents.sel(time=span)
    wi = wind.sel(time=span).interp(longitude=waves.longitude, latitude=waves.latitude)
    sea = ~np.isnan(w.VHM0.values)
    u, v = wi.eastward_wind.values, wi.northward_wind.values
    sst = temp.thetao.isel(depth=0).sel(time=day).values
    return {
        "lon": [round(float(x), 5) for x in waves.longitude.values],
        "lat": [round(float(y), 5) for y in waves.latitude.values],
        "times": [pd.Timestamp(t).strftime("%Y-%m-%dT%H:%M:%SZ") for t in w.time.values],
        "layers": {
            "hs": layer("m", w.VHM0.values, 2),
            "wave_dir": layer("deg", w.VMDR.values, 0),
            "period": layer("s", w.VTPK.values, 1),
            "current_u": layer("m/s", c.uo.values, 2),
            "current_v": layer("m/s", c.vo.values, 2),
            "wind": layer("kn", np.where(sea, np.hypot(u, v) * KN, np.nan), 1),
            "wind_dir": layer("deg", np.where(sea, np.degrees(np.arctan2(-u, -v)) % 360, np.nan), 0),
            "sst": layer("degC", np.where(sea, np.broadcast_to(sst, sea.shape), np.nan), 1),
        },
    }


def sim(day: pd.Timestamp, r: Result) -> dict:
    return {
        "arrival": iso(day, r.arrival),
        "beam_hours": round(r.beam_hours, 2),
        "over_hours": round(r.over_hours, 2),
        "max_hs": round(r.max_hs, 2),
        "late": r.late,
        "safe": r.safe,
        "track": [
            {"t": iso(day, s.t), "lon": round(s.lon, 4), "lat": round(s.lat, 4), "hs": round(s.hs, 2),
             "category": s.category, "bad": s.beam_exceeded or s.over_max}
            for s in r.samples[::TRACK_EVERY]
        ],
    }


def main() -> None:
    waves, currents, wind, temp = (xr.open_dataset(p) for p in (WAVES, CURRENTS, WIND, TEMPERATURE))
    grid = Grid(waves, currents)
    OUT.mkdir(parents=True, exist_ok=True)
    for case_id, meta in CASES.items():
        day = pd.Timestamp(meta["date"])
        at = grid.sea_at(day)
        case = {
            "id": case_id,
            **meta,
            "vessel": {"speed_kn": SPEED_KN},
            "limits": {
                "beam_hs": DEFAULT.beam_hs, "max_hs": DEFAULT.max_hs, "beam_from": DEFAULT.beam_from,
                "arrive_by": iso(day, DEFAULT.arrive_by), "source": LIMITS_SOURCE,
            },
            "routes": {
                k: {"name": ROUTE_NAMES[k], "points": [list(p) for p in pts], "nm": round(sum(l.nm for l in legs_of(pts)), 1)}
                for k, pts in ROUTES.items()
            },
            "grid": case_grid(day, waves, currents, wind, temp),
            "forecast_grid": None,
            "sims": {
                iso(day, float(dep)): {k: sim(day, simulate(pts, float(dep), at, DEFAULT, SPEED_KN)) for k, pts in ROUTES.items()}
                for dep in DEPARTURES
            },
            "flips": [{"at": iso(day, f.at), "from": f.from_, "to": f.to, "reason": f.reason} for f in day_flips(at, DEFAULT)],
            "copernicus_products": PRODUCTS,
        }
        path = OUT / f"{case_id}.json"
        path.write_text(json.dumps(case, separators=(",", ":"), allow_nan=False))
        flips = ", ".join(f"{f['at'][11:16]} {f['from'] or '-'}->{f['to'] or '-'} ({f['reason']})" for f in case["flips"])
        print(f"{path.relative_to(OUT.parents[2])}: {path.stat().st_size / 1e6:.2f} MB; flips UTC: {flips}")


if __name__ == "__main__":
    main()
