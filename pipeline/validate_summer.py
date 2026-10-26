"""Preliminary flip-day check on the summer 2026 data in data/copernicus/.

Simulates the three prototype routes for every day and every departure, and counts the days on which
the best route changes with departure time. Limits are illustrative and uncited, routes run
between harbour exits on sea cells (pipeline/routes.py). Treat the output as a feasibility check, not
as case statistics.

    python -m pipeline.validate_summer
    python -m pipeline.validate_summer --beam-sector     # flip days when the head/beam boundary moves
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from pipeline.routes import ROUTES, land_share
from pipeline.simulate import Limits, SeaState, SeaStateAt, best_route, legs_of, simulate, tipping_points

DATA = Path(__file__).resolve().parent.parent / "data" / "copernicus"
WAVES = DATA / "cmems_mod_med_wav_my_4.2km_PT1H-i_20260701-20260930.nc"
CURRENTS = DATA / "cmems_mod_med_phy-cur_my_4.2km_PT1H-m_20260701-20260930.nc"
WIND = DATA / "cmems_obs-wind_glo_phy_nrt_l4_0.125deg_PT1H_20260701-20260930.nc"

DAYS = pd.date_range("2026-07-01", "2026-09-30", freq="D")
DEPARTURES = np.arange(4.0, 14.01, 0.5)  # hours UTC, 06:00-16:00 local summer time
BEAM_FROM = [50.0, 55.0, 60.0, 65.0, 70.0]  # degrees off the bow
SETTINGS = [(1.0, 2.0), (1.25, 2.5), (1.5, 2.5)]  # (beam_hs, max_hs), illustrative
KN = 1.94384  # knots per m/s


class Grid:
    """Waves and currents on the shared 1/24 degree MED grid, as plain arrays for fast lookup."""

    def __init__(self, waves: xr.Dataset, currents: xr.Dataset):
        self.lon = waves.longitude.values.astype(float)
        self.lat = waves.latitude.values.astype(float)
        self.times = pd.DatetimeIndex(waves.time.values)
        self.hs, self.wave_from, self.period = (waves[v].values for v in ("VHM0", "VMDR", "VTPK"))
        self.u, self.v = currents.uo.values, currents.vo.values
        self.sea = ~np.isnan(self.hs[0])
        self._sea_cells = np.argwhere(self.sea)

    def cell(self, lon: float, lat: float) -> tuple[int, int]:
        i, j = int(np.abs(self.lat - lat).argmin()), int(np.abs(self.lon - lon).argmin())
        if self.sea[i, j]:
            return i, j
        k = ((self._sea_cells - [i, j]) ** 2).sum(1).argmin()
        return int(self._sea_cells[k][0]), int(self._sea_cells[k][1])

    def sea_at(self, day: pd.Timestamp) -> SeaStateAt:
        """Sea state for t in hours UTC on `day`: nearest sea cell, linear in time."""
        base = self.times.get_loc(day)

        def at(t: float, lon: float, lat: float) -> SeaState:
            i, j = self.cell(lon, lat)
            k = min(base + math.floor(t), len(self.times) - 2)
            w = t - math.floor(t)

            def lerp(a: np.ndarray) -> float:
                return float((1 - w) * a[k, i, j] + w * a[k + 1, i, j])

            d0, d1 = math.radians(self.wave_from[k, i, j]), math.radians(self.wave_from[k + 1, i, j])
            wave_from = math.degrees(
                math.atan2((1 - w) * math.sin(d0) + w * math.sin(d1), (1 - w) * math.cos(d0) + w * math.cos(d1))
            )
            u, v = lerp(self.u), lerp(self.v)
            return SeaState(
                hs=lerp(self.hs),
                wave_from=wave_from % 360,
                period=lerp(self.period),
                current_kn=math.hypot(u, v) * KN,
                current_to=math.degrees(math.atan2(u, v)) % 360,
            )

        return at


def best_by_departure(grid: Grid, day: pd.Timestamp, limits: Limits) -> list[tuple[float, str | None]]:
    at = grid.sea_at(day)
    return [
        (float(dep), best_route({k: simulate(p, float(dep), at, limits) for k, p in ROUTES.items()}))
        for dep in DEPARTURES
    ]


def route_flip_days(grid: Grid, limits: Limits) -> list[str]:
    """Days on which the best route changes from one route to another with departure time."""
    return [
        day.strftime("%m-%d")
        for day in DAYS
        if any(a and b for _, a, b in tipping_points(best_by_departure(grid, day, limits)))
    ]


def beam_sector(grid: Grid) -> None:
    """Route flips are sensitive to where head seas end and beam seas start (60 deg by default)."""
    print("Route-to-route flip days by head/beam boundary, no arrival deadline")
    for beam_hs, max_hs in SETTINGS:
        print(f"  beam_hs={beam_hs} max_hs={max_hs}")
        for beam_from in BEAM_FROM:
            days = route_flip_days(grid, Limits(beam_hs, max_hs, arrive_by=99.0, beam_from=beam_from))
            print(f"    beam from {beam_from:.0f} deg: {len(days)} {days}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--beam-sector", action="store_true", help="vary the head/beam boundary instead")
    args = parser.parse_args()
    waves, currents, wind = (xr.open_dataset(p) for p in (WAVES, CURRENTS, WIND))
    grid = Grid(waves, currents)
    if args.beam_sector:
        beam_sector(grid)
        return

    print("Route  nm    share of samples on land cells")
    for k, points in ROUTES.items():
        print(f"  {k}   {sum(l.nm for l in legs_of(points)):5.1f}  {land_share(points, grid.sea, grid.lon, grid.lat):.0%}")

    print("\nNo arrival deadline (flips caused by sea state only)")
    flips_by_setting = []
    for beam_hs, max_hs in SETTINGS:
        limits = Limits(beam_hs=beam_hs, max_hs=max_hs, arrive_by=99.0)
        route_flip, go_flip, no_go = [], [], 0
        for day in DAYS:
            seq = best_by_departure(grid, day, limits)
            flips = tipping_points(seq)
            if any(a and b for _, a, b in flips):
                route_flip.append(day.strftime("%m-%d"))
            elif flips:
                go_flip.append(day.strftime("%m-%d"))
            elif seq[0][1] is None:
                no_go += 1
        flips_by_setting.append(set(route_flip))
        print(f"  beam_hs={beam_hs} max_hs={max_hs}: route-to-route flip days {len(route_flip)} {route_flip}")
        print(f"    go/no-go flip days {len(go_flip)} {go_flip}; no-go all day {no_go}")
    print("  route flip at every setting:", sorted(set.intersection(*flips_by_setting)))

    limits = Limits(beam_hs=1.5, max_hs=2.5, arrive_by=19.0)
    not_shortest = deadline_flips = 0
    gaps = []
    for day in DAYS:
        at = grid.sea_at(day)
        etas = sorted(simulate(p, 8.0, at, limits).eta_hours for p in ROUTES.values())
        gaps.append(etas[1] - etas[0])
        seq = best_by_departure(grid, day, limits)
        not_shortest += dict(seq)[8.0] != "A"
        deadline_flips += bool(tipping_points(seq))
    print("\nbeam_hs=1.5 max_hs=2.5, arrive by 19:00 UTC")
    print(f"  shortest route A not best at 08:00 UTC departure: {not_shortest} of {len(DAYS)} days")
    print(f"  ETA gap fastest vs second route at 08:00 UTC: median {np.median(gaps) * 60:.0f} min")
    print(f"  days with any flip, including deadline flips: {deadline_flips} of {len(DAYS)}")

    print("\nDomain-mean wind peak on selected days (direction from)")
    w = wind.interp(longitude=waves.longitude, latitude=waves.latitude)
    speed = (np.hypot(w.eastward_wind, w.northward_wind) * KN).mean(["latitude", "longitude"])
    u, v = w.eastward_wind.mean(["latitude", "longitude"]), w.northward_wind.mean(["latitude", "longitude"])
    for day in ["2026-07-01", "2026-09-09", "2026-09-17"]:
        s = speed.sel(time=day)
        t = s.time.values[int(s.argmax(dim="time"))]
        wind_from = math.degrees(math.atan2(-float(u.sel(time=t)), -float(v.sel(time=t)))) % 360
        print(f"  {day}: {float(s.max()):.0f} kn at {pd.Timestamp(t):%H:%M} UTC, from {wind_from:.0f} deg")


if __name__ == "__main__":
    main()
