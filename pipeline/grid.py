"""Copernicus Marine summer 2026 files in data/copernicus/ and fast sea-state lookup on their grid."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from pipeline.simulate import SeaState, SeaStateAt

DATA = Path(__file__).resolve().parent.parent / "data" / "copernicus"
WAVES = DATA / "cmems_mod_med_wav_my_4.2km_PT1H-i_20260701-20260930.nc"
CURRENTS = DATA / "cmems_mod_med_phy-cur_my_4.2km_PT1H-m_20260701-20260930.nc"
WIND = DATA / "cmems_obs-wind_glo_phy_nrt_l4_0.125deg_PT1H_20260701-20260930.nc"
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
