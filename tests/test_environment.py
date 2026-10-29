from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
import xarray as xr

from game.simulation.environment import CopernicusEnvironment, EnvironmentError, SeaState

START = datetime(2026, 9, 9, tzinfo=timezone.utc)


def datasets():
    coords = dict(time=np.array(['2026-09-09', '2026-09-09T01:00'], dtype='datetime64[ns]'),
                  latitude=[40., 41.], longitude=[3., 4.])
    a = np.arange(8, dtype=float).reshape(2, 2, 2)
    make = lambda values: (('time', 'latitude', 'longitude'), values)
    waves = xr.Dataset(dict(VHM0=make(a), VTPK=make(a+4), VMDR=make(np.full_like(a, 30))), coords=coords)
    currents = xr.Dataset(dict(uo=make(a/10), vo=make(a/20)), coords=coords)
    return waves, currents


def test_spatial_temporal_interpolation_and_utc():
    env = CopernicusEnvironment(*datasets())
    sea = env.sample(START+timedelta(minutes=30), 3.5, 40.5)
    assert sea.hs == pytest.approx(3.5)
    assert sea.tp == pytest.approx(7.5)
    assert sea.current_east_mps == pytest.approx(.35)
    assert sea.current_north_mps == pytest.approx(.175)
    assert sea.wave_from_deg == pytest.approx(30)
    assert env.sample(START.astimezone(timezone(timedelta(hours=3))),3,40) == env.sample(START,3,40)


def test_circular_direction_and_ambiguous_opposites():
    waves, currents = datasets()
    waves.VMDR[0] = 359
    waves.VMDR[1] = 1
    sea = CopernicusEnvironment(waves,currents).sample(START+timedelta(minutes=30),3.5,40.5)
    assert min(sea.wave_from_deg,360-sea.wave_from_deg) < 1e-10
    waves.VMDR[0] = 0
    waves.VMDR[1] = 180
    with pytest.raises(EnvironmentError, match='ambiguous'):
        CopernicusEnvironment(waves,currents).sample(START+timedelta(minutes=30),3.5,40.5)


def test_exact_node_ignores_nan_neighbors_and_exact_last_boundary():
    waves, currents = datasets()
    waves.VHM0[0,0,1] = np.nan
    env = CopernicusEnvironment(waves,currents)
    assert env.sample(START,3,40).hs == 0
    assert env.sample(START+timedelta(hours=1),4,41).hs == 7
    with pytest.raises(EnvironmentError) as e:
        env.sample(START,3.5,40)
    assert e.value.reason == 'missing_environment'


@pytest.mark.parametrize('time,lon,lat,reason', [
    (START,2.9,40,'domain_boundary'),
    (START,3,41.1,'domain_boundary'),
    (START-timedelta(seconds=1),3,40,'time_boundary'),
    (START+timedelta(hours=2),3,40,'time_boundary'),
])
def test_bounds(time,lon,lat,reason):
    with pytest.raises(EnvironmentError) as e:
        CopernicusEnvironment(*datasets()).sample(time,lon,lat)
    assert e.value.reason == reason


def test_wind_separate_grid_and_singleton_depth():
    waves,currents=datasets()
    currents=currents.expand_dims(depth=[0])
    wind=xr.Dataset({name:(('time','latitude','longitude'),np.full((2,2,2),value))
                     for name,value in [('eastward_wind',4.),('northward_wind',-3.)]},
                    coords=dict(time=waves.time,latitude=[39.,42.],longitude=[2.,5.]))
    sea=CopernicusEnvironment(waves,currents,wind).sample(START,3,40)
    assert sea.wind_east_mps == pytest.approx(4)
    assert sea.wind_north_mps == pytest.approx(-3)


def test_validation_and_calm_period():
    waves,currents=datasets()
    with pytest.raises(ValueError,match='aware'):
        CopernicusEnvironment(waves,currents).sample(START.replace(tzinfo=None),3,40)
    with pytest.raises(ValueError,match='increasing'):
        CopernicusEnvironment(waves.isel(longitude=[1,0]),currents)
    with pytest.raises(EnvironmentError):
        SeaState(-1,8,0)
    with pytest.raises(EnvironmentError):
        SeaState(1,0,0)
    assert SeaState(0,0,370).tp == 1
    assert SeaState(0,1,370).wave_from_deg == 10


def temperature_dataset():
    return xr.Dataset(
        {'thetao': (('time', 'depth', 'latitude', 'longitude'),
                    np.array([[[[20., 22.], [24., 26.]]], [[[30., 32.], [34., 36.]]]]))},
        coords={'time': np.array(['2026-09-09', '2026-09-10'], dtype='datetime64[ns]'),
                'depth': [1.018], 'latitude': [40., 41.], 'longitude': [3., 4.]},
        attrs={'source': 'daily temperature test'},
    )


def test_daily_temperature_is_held_for_utc_day_and_changes_next_day():
    waves, currents = datasets()
    times = np.array(['2026-09-09', '2026-09-10T01:00'], dtype='datetime64[ns]')
    waves = waves.assign_coords(time=times)
    currents = currents.assign_coords(time=times)
    with CopernicusEnvironment(waves, currents, temperature=temperature_dataset()) as env:
        morning = env.sample(START+timedelta(hours=8), 3.5, 40.5)
        evening = env.sample(START+timedelta(hours=23), 3.5, 40.5)
        next_day = env.sample(START+timedelta(days=1), 3.5, 40.5)
        assert morning.sst_c == evening.sst_c == pytest.approx(23)
        assert next_day.sst_c == pytest.approx(33)
        assert env.provenance['temperature']['source'] == 'daily temperature test'


def test_temperature_optional_finite_and_missing_cells():
    assert CopernicusEnvironment(*datasets()).sample(START, 3, 40).sst_c is None
    assert SeaState(1, 8, 0, sst_c=26).sst_c == 26
    for value in (np.nan, np.inf):
        with pytest.raises(EnvironmentError, match='temperature'):
            SeaState(1, 8, 0, sst_c=value)
    temperature = temperature_dataset()
    temperature.thetao[0, 0, 0, 0] = np.nan
    with pytest.raises(EnvironmentError) as error:
        CopernicusEnvironment(*datasets(), temperature=temperature).sample(START, 3, 40)
    assert error.value.reason == 'missing_environment'


def test_directory_without_wind_still_loads_temperature(tmp_path):
    waves, currents = datasets()
    waves.to_netcdf(tmp_path/'cmems_mod_med_wav_my_4.2km_PT1H-i_20260701-20260930.nc')
    currents.to_netcdf(tmp_path/'cmems_mod_med_phy-cur_my_4.2km_PT1H-m_20260701-20260930.nc')
    temperature_dataset().to_netcdf(tmp_path/'cmems_mod_med_phy-temp_my_4.2km_P1D-m_20260701-20260930.nc')
    with CopernicusEnvironment.from_directory(tmp_path) as env:
        sea = env.sample(START, 3, 40)
        assert sea.sst_c == pytest.approx(20)
        assert sea.wind_east_mps == sea.wind_north_mps == 0
        assert 'wind' not in env.provenance
        assert 'temperature' in env.provenance
