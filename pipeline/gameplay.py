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


POINT_DEG = 22.5  # forecast directions on the 16-point compass, as a bulletin gives them
SHIFT_DEG = 20.0  # a wind turning at least this much within the forecast hour is reported as veering or backing


def _point(deg):
    return round(deg / POINT_DEG) * POINT_DEG % 360


def _wind_from(sample):
    return math.degrees(math.atan2(-sample.wind_east_mps, -sample.wind_north_mps)) % 360


def _shift(samples):
    """Veering: the wind's direction turns clockwise over the hour; backing: anticlockwise."""
    turn = (_wind_from(samples[-1]) - _wind_from(samples[0]) + 540) % 360 - 180
    return "veering" if turn >= SHIFT_DEG else "backing" if turn <= -SHIFT_DEG else "steady"


def scaled_bbox(area, bounds, scale):
    """A bbox about the area, scaled up, clipped to the data, and kept at the scaled span where the data allows.

    The west edge of the Biscay data stops the field short of the scaled area, so the missing span goes to the
    east instead. The hazard region keeps the area; only the released forecast field grows.
    """
    w, e, s, n = area
    bw, be, bs, bn = bounds
    span_w, span_n = (e - w) * scale, (n - s) * scale
    cx, cy = (w + e) / 2, (s + n) / 2
    x0, x1 = max(cx - span_w / 2, bw), min(cx + span_w / 2, be)
    y0, y1 = max(cy - span_n / 2, bs), min(cy + span_n / 2, bn)
    if x1 - x0 < span_w:
        if x0 <= bw + 1e-9:
            x1 = min(be, x0 + span_w)
        else:
            x0 = max(bw, x1 - span_w)
    if y1 - y0 < span_n:
        if y0 <= bs + 1e-9:
            y1 = min(bn, y0 + span_n)
        else:
            y0 = max(bs, y1 - span_n)
    return [round(v, 6) for v in (x0, x1, y0, y1)]



def build_gameplay(scenario):
    d = scenario.definition
    w, e, s, n = d["area_bbox"]
    start = utc(d["start"])
    budget = d["mission"]["budget_s"]
    region_hs = d.get("gameplay_settings", {}).get("region_hs_m", 3.0)
    report_scale = float(d.get("gameplay_settings", {}).get("report_scale", 1.0))
    regions = []
    reports = []
    source = d["environment"]["waves"]
    if digest(scenario.root / source["path"]) != source["sha256"]:
        raise ValueError("Wave checksum mismatch")
    with xr.open_dataset(scenario.root / source["path"]) as ds:
        bounds = [float(ds.longitude.min()), float(ds.longitude.max()),
                  float(ds.latitude.min()), float(ds.latitude.max())]
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
    # The forecast field reaches beyond the operating area, so its edge does not outline the hidden region.
    report_bbox = scaled_bbox([w, e, s, n], bounds, report_scale) if report_scale > 1 else [w, e, s, n]
    rw, re, rs, rn = report_bbox
    with scenario.environment() as env:
        for issued in range(0, budget, 3600):
            end = min(budget, issued + 3600)
            for x in range(3):
                for y in range(3):
                    lon = rw + (x + 0.5) * (re - rw) / 3
                    lat = rs + (y + 0.5) * (rn - rs) / 3
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
                            "wave_from_deg": _point(samples[0].wave_from_deg),
                            "wind_from_deg": _point(_wind_from(samples[0])),
                            "wind_shift": _shift(samples),
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
        "report_bbox": report_bbox,
        "report_scope": d.get("gameplay_settings", {}).get("report_scope", "nearby"),
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
