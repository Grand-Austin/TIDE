"""Strict, array-backed sampling of prepared Copernicus fields in UTC."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import math

import numpy as np
import xarray as xr


class EnvironmentError(ValueError):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class SeaState:
    hs: float
    tp: float
    wave_from_deg: float
    current_east_mps: float = 0.0
    current_north_mps: float = 0.0
    wind_east_mps: float = 0.0
    wind_north_mps: float = 0.0
    sst_c: float | None = None  # Daily environmental evidence; not a force input.

    def __post_init__(self):
        if not all(math.isfinite(value) for name, value in vars(self).items() if name != 'sst_c'):
            raise EnvironmentError('missing_environment', 'Environmental values must be finite')
        if self.sst_c is not None and not math.isfinite(self.sst_c):
            raise EnvironmentError('missing_environment', 'Sea-surface temperature must be finite when supplied')
        if self.hs < 0 or (self.hs > 0 and self.tp <= 0):
            raise EnvironmentError('missing_environment', 'Invalid wave height or period')
        # Calm water has no defined wave period; use a neutral positive value.
        if self.hs == 0 and self.tp <= 0:
            object.__setattr__(self, 'tp', 1.0)
        object.__setattr__(self, 'wave_from_deg', self.wave_from_deg % 360)


def _bracket(axis, value, reason):
    if value < axis[0] or value > axis[-1]:
        raise EnvironmentError(reason, 'Requested point lies outside environmental coverage')
    upper = int(np.searchsorted(axis, value, side='left'))
    if upper < len(axis) and axis[upper] == value:
        return ((upper, 1.0),)
    lower = upper - 1
    weight = float((value - axis[lower]) / (axis[upper] - axis[lower]))
    return ((lower, 1 - weight), (upper, weight))


class _Grid:
    def __init__(self, dataset, names, circular=None):
        self.axes = []
        for coord in ('time', 'latitude', 'longitude'):
            if coord not in dataset.coords or dataset[coord].dims != (coord,):
                raise ValueError(f'{coord} must be a one-dimensional coordinate')
            values = dataset[coord].values
            if coord == 'time':
                if not np.issubdtype(values.dtype, np.datetime64) or np.any(np.isnat(values)):
                    raise ValueError('Time coordinates must contain valid decoded timestamps')
                values = values.astype('datetime64[ns]').astype(np.int64)
            else:
                values = np.asarray(values, dtype=float)
            if len(values) == 0 or not np.all(np.isfinite(values)) or np.any(np.diff(values) <= 0):
                raise ValueError(f'{coord} must be finite and strictly increasing')
            self.axes.append(values)
        fields = []
        for name in names:
            field = dataset[name]
            if 'depth' in field.dims and field.sizes['depth'] == 1:
                field = field.isel(depth=0)
            if set(field.dims) != {'time', 'latitude', 'longitude'}:
                raise ValueError(f'Unsupported dimensions for {name}: {field.dims}')
            values = np.asarray(field.transpose('time', 'latitude', 'longitude').values, dtype=float)
            if name == circular:
                angle = np.deg2rad(values)
                fields.extend((np.sin(angle), np.cos(angle)))
            else:
                fields.append(values)
        self.values = np.stack(fields)

    def sample(self, time_ns, lon, lat):
        brackets = [_bracket(axis, value, reason) for axis, value, reason in zip(
            self.axes, (time_ns, lat, lon), ('time_boundary', 'domain_boundary', 'domain_boundary'))]
        result = np.zeros(self.values.shape[0])
        for t, wt in brackets[0]:
            for y, wy in brackets[1]:
                for x, wx in brackets[2]:
                    values = self.values[:, t, y, x]
                    if not np.all(np.isfinite(values)):
                        raise EnvironmentError('missing_environment', 'A contributing grid cell has missing data')
                    result += wt * wy * wx * values
        return result


class CopernicusEnvironment:
    def __init__(self, waves: xr.Dataset, currents: xr.Dataset, wind: xr.Dataset | None = None,
                 temperature: xr.Dataset | None = None):
        datasets = {name: ds for name, ds in (
            ('waves', waves), ('currents', currents), ('wind', wind), ('temperature', temperature)) if ds is not None}
        self._datasets = tuple(datasets.values())
        self.provenance = {name: dict(ds.attrs) for name, ds in datasets.items()}
        self._waves = _Grid(waves, ('VHM0', 'VTPK', 'VMDR'), circular='VMDR')
        self._currents = _Grid(currents, ('uo', 'vo'))
        self._wind = _Grid(wind, ('eastward_wind', 'northward_wind')) if wind is not None else None
        self._temperature = _Grid(temperature, ('thetao',)) if temperature is not None else None

    @classmethod
    def from_directory(cls, path):
        path = Path(path)
        filenames = {
            'waves': 'cmems_mod_med_wav_my_4.2km_PT1H-i_20260701-20260930.nc',
            'currents': 'cmems_mod_med_phy-cur_my_4.2km_PT1H-m_20260701-20260930.nc',
            'wind': 'cmems_obs-wind_glo_phy_nrt_l4_0.125deg_PT1H_20260701-20260930.nc',
            'temperature': 'cmems_mod_med_phy-temp_my_4.2km_P1D-m_20260701-20260930.nc',
        }
        datasets = {}
        try:
            for layer, name in filenames.items():
                if layer in ('wind', 'temperature') and not (path / name).exists():
                    continue
                datasets[layer] = xr.open_dataset(path / name)
            return cls(**datasets)
        except Exception:
            for dataset in datasets.values():
                dataset.close()
            raise

    def sample(self, time: datetime, lon: float, lat: float) -> SeaState:
        if not isinstance(time, datetime) or time.tzinfo is None or time.utcoffset() is None:
            raise ValueError('Sampling time must be an aware datetime')
        if not math.isfinite(lon) or not math.isfinite(lat):
            raise ValueError('Sampling coordinates must be finite')
        utc = time.astimezone(timezone.utc).replace(tzinfo=None)
        time_ns = np.datetime64(utc, 'ns').astype(np.int64)
        hs, tp, sine, cosine = self._waves.sample(time_ns, lon, lat)
        if math.hypot(sine, cosine) < 1e-10:
            raise EnvironmentError('missing_environment', 'Wave direction is ambiguous after interpolation')
        direction = math.degrees(math.atan2(sine, cosine)) % 360
        u, v = self._currents.sample(time_ns, lon, lat)
        wu, wv = self._wind.sample(time_ns, lon, lat) if self._wind is not None else (0.0, 0.0)
        # The daily-mean product is held for its UTC day, without hourly blending.
        day_ns = np.datetime64(utc, 'D').astype('datetime64[ns]').astype(np.int64)
        sst = float(self._temperature.sample(day_ns, lon, lat)[0]) if self._temperature is not None else None
        return SeaState(float(hs), float(tp), direction, float(u), float(v), float(wu), float(wv), sst)

    def close(self):
        for dataset in self._datasets:
            dataset.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
