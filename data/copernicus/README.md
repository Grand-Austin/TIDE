# Copernicus Marine data: Mallorca–Menorca channel, summer 2026

Four NetCDF subsets downloaded from the Copernicus Marine Data Store on 2026-10-26. Together they provide the evidence layers (`hs`, `wave_dir`, `period`, `current_u`, `current_v`, `wind`, `sst`) that the earlier SeaSense case schema described, for the case area, from 1 July to 30 September 2026.

These are the raw inputs for `pipeline/`. Do not edit them. Write derived files elsewhere, for example `data/raw/` or `data/processed/`.

**This is the only data committed to git.** It is here so that anyone can run the pipeline without a Copernicus Marine account. Do not commit more `.nc` files. All further data (other periods, the larger display box, forecasts, in-situ data) is fetched with a script into `data/raw/`, which is git-ignored.

## Files

| File | Product | Dataset ID | Content | Time step | Grid | Size |
|---|---|---|---|---|---|---|
| `cmems_mod_med_wav_my_4.2km_PT1H-i_20260701-20260930.nc` | `MEDSEA_MULTIYEAR_WAV_006_012` (HCMR, MEDWAM3) | `cmems_mod_med_wav_my_4.2km_PT1H-i_202511` | Waves | 1 h, instantaneous | 1/24° | 44 MB |
| `cmems_mod_med_phy-cur_my_4.2km_PT1H-m_20260701-20260930.nc` | `MEDSEA_MULTIYEAR_PHY_006_004` (CMCC, MFS) | `cmems_mod_med_phy-cur_my_4.2km_PT1H-m_202511` | Surface currents | 1 h, hourly mean | 1/24° | 8.7 MB |
| `cmems_mod_med_phy-temp_my_4.2km_P1D-m_20260701-20260930.nc` | `MEDSEA_MULTIYEAR_PHY_006_004` (CMCC, MFS) | `cmems_mod_med_phy-temp_my_4.2km_P1D-m_202511` | Sea temperature | 1 day, daily mean | 1/24° | 0.4 MB |
| `cmems_obs-wind_glo_phy_nrt_l4_0.125deg_PT1H_20260701-20260930.nc` | `WIND_GLO_PHY_L4_NRT_012_004` (KNMI) | `cmems_obs-wind_glo_phy_nrt_l4_0.125deg_PT1H_202207` | 10 m wind | 1 h | 0.125° | 3 MB |

The files were renamed after download: the random request number in each original name was replaced with the date range. The original IDs are kept in the global attributes `subset:productId`, `subset:datasetId` and `subset:date`.

SHA-256:

```
06264d9fbb67670f5b472fa8ad928aacd68c04e8a6b32ef5366521e7a45d5db3  cmems_mod_med_wav_my_4.2km_PT1H-i_20260701-20260930.nc
6e6babc2c7f2384f4a443b41d5793399dd3238f5343a32423e576f47811ce5c1  cmems_mod_med_phy-cur_my_4.2km_PT1H-m_20260701-20260930.nc
1d0aed0abcc9d4fcfe6242d482993984bc2848da392e4e6296860b0efa332309  cmems_mod_med_phy-temp_my_4.2km_P1D-m_20260701-20260930.nc
48737addc83a58c42d1d9c027726f9f814aeba314c82f8fa0bb287da31b4398c  cmems_obs-wind_glo_phy_nrt_l4_0.125deg_PT1H_20260701-20260930.nc
```

## Acknowledgement and DOIs

The data is used under the [Copernicus Marine Service licence](https://marine.copernicus.eu/user-corner/service-commitments-and-licence). Section 2.4 of the licence asks that the source be credited with the DOIs of the products used, in one of these forms:

- **This folder** (redistribution of the products, §2.4(b)): "E.U. Copernicus Marine Service Information" followed by the DOIs below.
- **Case files, the web app and the pitch** (value-added products, §2.4(a)): "Generated using E.U. Copernicus Marine Service Information" followed by the DOIs below. The credit should be clearly visible on the website's home page or on the page where users access the data.
- **Reports and papers** (§2.4(c)): "This study has been conducted using E.U. Copernicus Marine Service Information" followed by the DOIs below.

| Product | DOI |
|---|---|
| `MEDSEA_MULTIYEAR_WAV_006_012`, interim part (MEDWAM3I) | [10.25423/CMCC/MEDSEA_MULTIYEAR_WAV_006_012_MEDWAM3I](https://doi.org/10.25423/CMCC/MEDSEA_MULTIYEAR_WAV_006_012_MEDWAM3I) |
| `MEDSEA_MULTIYEAR_PHY_006_004`, interim part (E3R1I) | [10.48670/mds-00375](https://doi.org/10.48670/mds-00375) |
| `WIND_GLO_PHY_L4_NRT_012_004` | [10.48670/moi-00305](https://doi.org/10.48670/moi-00305) |

Summer 2026 is not yet in the reprocessed part of either MED reanalysis. The global attributes confirm this: the files record their source as `MEDWAM3i` and `MFS E3R1I`. The DOIs above are therefore the interim-dataset DOIs given in the product descriptions (`copernicusmarine describe`, 2026-10-26). If data from before the interim period is added later, cite the reanalysis DOIs as well. These are [10.25423/cmcc/medsea_multiyear_wav_006_012](https://doi.org/10.25423/cmcc/medsea_multiyear_wav_006_012) for waves and [10.25423/CMCC/MEDSEA_MULTIYEAR_PHY_006_004_E3R1](https://doi.org/10.25423/CMCC/MEDSEA_MULTIYEAR_PHY_006_004_E3R1) for physics.

Ready-to-paste credit line:

> Generated using E.U. Copernicus Marine Service Information; https://doi.org/10.25423/CMCC/MEDSEA_MULTIYEAR_WAV_006_012_MEDWAM3I, https://doi.org/10.48670/mds-00375, https://doi.org/10.48670/moi-00305

## Reproducing the download

Log in once with `copernicusmarine login`. The commands below request the same datasets, variables, box and period as the committed files. The bounds are padded by less than half a grid cell, so the default `inside` selection returns exactly the committed grid. The original downloads were made through the Data Store web interface, which writes files with the same layout.

```bash
OUT=data/raw/summer-2026

copernicusmarine subset -i cmems_mod_med_wav_my_4.2km_PT1H-i --dataset-version 202511 \
  -v VHM0 -v VMDR -v VTPK -v VTM02 \
  -v VHM0_WW -v VMDR_WW -v VTM01_WW -v VHM0_SW1 -v VMDR_SW1 -v VTM01_SW1 \
  --minimum-longitude 2.9 --maximum-longitude 4.1 --minimum-latitude 39.59 --maximum-latitude 40.28 \
  -t 2026-06-30T23:00:00 -T 2026-09-30T23:00:00 \
  -o $OUT -f cmems_mod_med_wav_my_4.2km_PT1H-i_20260701-20260930.nc

copernicusmarine subset -i cmems_mod_med_phy-cur_my_4.2km_PT1H-m --dataset-version 202511 \
  -v uo -v vo \
  --minimum-longitude 2.9 --maximum-longitude 4.1 --minimum-latitude 39.59 --maximum-latitude 40.28 \
  -t 2026-06-30T23:00:00 -T 2026-09-30T23:00:00 \
  -o $OUT -f cmems_mod_med_phy-cur_my_4.2km_PT1H-m_20260701-20260930.nc

copernicusmarine subset -i cmems_mod_med_phy-temp_my_4.2km_P1D-m --dataset-version 202511 \
  -v thetao -v bottomT -z 0 -Z 1.5 \
  --minimum-longitude 2.9 --maximum-longitude 4.1 --minimum-latitude 39.59 --maximum-latitude 40.28 \
  -t 2026-06-30 -T 2026-09-30 \
  -o $OUT -f cmems_mod_med_phy-temp_my_4.2km_P1D-m_20260701-20260930.nc

copernicusmarine subset -i cmems_obs-wind_glo_phy_nrt_l4_0.125deg_PT1H --dataset-version 202207 \
  -v eastward_wind -v northward_wind \
  --minimum-longitude 2.55 --maximum-longitude 4.45 --minimum-latitude 39.3 --maximum-latitude 40.6 \
  -t 2026-06-30T23:00:00 -T 2026-09-30T23:00:00 \
  -o $OUT -f cmems_obs-wind_glo_phy_nrt_l4_0.125deg_PT1H_20260701-20260930.nc
```

Two things can make a re-download differ from the committed files:

- The interim parts of the MED reanalyses can be reprocessed later, so values may change while the grid stays the same.
- A new dataset version may appear. Keep `--dataset-version` pinned to reproduce these files.

## Coverage

**Time.** All timestamps are UTC.

- Waves, currents and wind: hourly from `2026-06-30T23:00` to `2026-09-30T23:00` (2209 steps). This gives 92 full days, 1 July to 30 September. The three hourly time axes are identical.
- Temperature: daily from `2026-06-30` to `2026-09-30` (93 steps), each stamped at 00:00.

**Space.**

- MED grid (waves, currents, temperature): longitude 2.9167–4.0833° E (29 points), latitude 39.6042–40.2708° N (17 points), spacing 1/24° (about 4.2 km). The coordinates are identical across the three files.
- Wind grid: longitude 2.5625–4.4375° E (16 points), latitude 39.3125–40.5625° N (11 points), spacing 0.125°. It fully contains the MED box.

The box covers the eastern coast of Mallorca (Alcúdia, Cap de Formentor, Capdepera), western Menorca (Ciutadella) and the channel between them.

**Land mask.** 112 of the 493 MED grid cells are land and contain `NaN`. The mask is the same in all three MED files and does not change over time. The wind file has no `NaN`, because the L4 product is filled over land with model data.

Land mask with north at the top (`#` = land):

```
.............................
.............................
.............................
.............................
.............................
...........................##   Menorca, west end
.....................########
......................#######
...####...............#######
#######.....................#
#######......................
#######......................
#######...##.................
##############...............   Mallorca, north-east
##############...............
#############................
############.................
```

## Variables

### Waves (`..._wav_...`)

All variables have dimensions `(time, latitude, longitude)`, type float32.

| Variable | Meaning | Unit | Range in file (mean) |
|---|---|---|---|
| `VHM0` | Significant wave height Hm0 | m | 0.02–4.25 (0.59) |
| `VMDR` | Mean wave direction, **coming from** | deg true | 0–360 |
| `VTPK` | Peak period Tp | s | 1.2–11.2 (4.9) |
| `VTM02` | Mean period Tm02 | s | 1.0–7.4 (3.2) |
| `VHM0_WW`, `VMDR_WW`, `VTM01_WW` | Wind-wave height, direction (coming from), Tm01 period | m, deg, s | |
| `VHM0_SW1`, `VMDR_SW1`, `VTM01_SW1` | Primary swell height, direction (coming from), Tm01 period | m, deg, s | |

### Currents (`..._phy-cur_...`)

| Variable | Meaning | Unit | Range in file (mean) |
|---|---|---|---|
| `uo` | Eastward surface current | m/s | -0.59–0.77 (0.09) |
| `vo` | Northward surface current | m/s | -0.67–0.56 (0.02) |

Current speed: mean 0.35 kn, 99th percentile 0.99 kn, maximum 1.51 kn.

### Temperature (`..._phy-temp_...`)

| Variable | Dims | Meaning | Unit | Range in file |
|---|---|---|---|---|
| `thetao` | `(time, depth, latitude, longitude)` | Potential temperature at depth 1.018 m. Use it as SST. | °C | 24.3–30.5 |
| `bottomT` | `(time, latitude, longitude)` | Sea-floor temperature. Not used by SeaSense. | °C | 12.9–28.7 |

The domain-mean SST peaks at 30.0 °C. On a typical day the spatial spread is only about 1.4 °C, which suits the plan to use SST as a distractor layer.

### Wind (`..._wind_...`)

| Variable | Meaning | Unit | Range in file (mean) |
|---|---|---|---|
| `eastward_wind` | Stress-equivalent 10 m wind, eastward component | m/s | -11.8–14.4 (-0.8) |
| `northward_wind` | Stress-equivalent 10 m wind, northward component | m/s | -17.0–11.5 (-0.3) |

Wind speed: mean 7.3 kn, 99th percentile 22.5 kn, maximum 33.3 kn. The components give the direction the wind blows **toward**. The meteorological direction the wind comes **from** is `atan2(-u, -v)`.

## Mapping to the earlier case-schema layers

| Case-file layer | Source | Conversion |
|---|---|---|
| `hs` (m) | `VHM0` | none |
| `wave_dir` (deg, from) | `VMDR` | none; the convention already matches SCHEMA |
| `period` (s) | `VTPK` | none; peak period is the usual choice in seamanship. `VTM02` is the alternative. |
| `current_u`, `current_v` (m/s) | `uo`, `vo` | none. For `simulate.SeaState`: `current_kn = hypot(u, v) * 1.94384`, `current_to = degrees(atan2(u, v)) % 360` |
| `wind` (kn) | `eastward_wind`, `northward_wind` | interpolate to the MED grid (no NaN results), then speed = `hypot(u, v) * 1.94384` |
| `sst` (°C) | `thetao` at `depth=0` | daily value; use the same value for every hour of that day |

Every case file should list the product and dataset IDs above in `copernicus_products`.

## Loading

```python
import xarray as xr

base = "data/copernicus/"
wav = xr.open_dataset(base + "cmems_mod_med_wav_my_4.2km_PT1H-i_20260701-20260930.nc")
cur = xr.open_dataset(base + "cmems_mod_med_phy-cur_my_4.2km_PT1H-m_20260701-20260930.nc")
tem = xr.open_dataset(base + "cmems_mod_med_phy-temp_my_4.2km_P1D-m_20260701-20260930.nc")
wnd = xr.open_dataset(base + "cmems_obs-wind_glo_phy_nrt_l4_0.125deg_PT1H_20260701-20260930.nc")

sea = xr.merge([wav[["VHM0", "VMDR", "VTPK"]], cur])            # same grid and time axis
wind = wnd.interp(longitude=wav.longitude, latitude=wav.latitude)
sst = tem.thetao.isel(depth=0)
```

Requirements: `xarray`, `netCDF4` and `scipy` (for `.interp`), all declared in `pyproject.toml`.

## Caveats for pipeline code

1. **Land cells are NaN.** On this grid, the prototype routes in `web/prototype/index.html` put 15–24 % of their samples on land cells, mostly in Alcúdia bay and off Cap de Capdepera. Redraw the routes along the real coastline, start and end them at waypoints outside the harbours, and never let a route sample a NaN cell.
2. **Mixed sources.** Waves, currents and temperature are multi-year (reanalysis or interim) model products. Wind is a near-real-time product that blends scatterometer observations with a model, because no multi-year wind product covered summer 2026 at download time. State this in the methods notes.
3. **Stress-equivalent wind** is not exactly the wind measured on board. Treat it as an evidence layer only.
4. **Different averaging.** Currents are hourly means and waves are hourly instantaneous values. Both are stamped on the hour. SST is a daily mean.
5. **No forecasts.** Every file is analysis or observation data. Set `forecast_grid` to `null` and explain the simplification in the methods notes.
6. **One season only.** The files cover one summer, which is the calm season; northerly (Tramontana) events are more frequent in autumn and winter. Statistics drawn from these files should say "summer 2026 only". For multi-year statistics, download the same four datasets for more years. That is about 220 MB per year for all four.

## Preliminary validation (2026-10-26)

> **Preliminary.** This is a feasibility check, not case statistics. It uses the prototype routes (which cross land cells) and thresholds that have no citation yet. Do not quote these numbers in the pitch or in the methods notes.

Reproduce with `python -m pipeline.validate_summer` (about 30 s). The run used `pipeline/simulate.py` with these files and the three prototype routes, with a nearest-sea-cell fallback for land samples. It covered all 92 days, with departures every 30 minutes from 04:00 to 14:00 UTC (06:00–16:00 local), no arrival deadline, a vessel speed of 6 kn and **illustrative, uncited** limits.

| beam_hs / max_hs | Days when the best route switches to another route | Days that only switch between go and no-go | No-go all day |
|---|---|---|---|
| 1.0 m / 2.0 m | 9 | 5 | 6 |
| 1.25 m / 2.5 m | 5 | 2 | 4 |
| 1.5 m / 2.5 m | 3 | 2 | 2 |

- **Days that flip at all three settings:** 2026-07-01 and 2026-09-09.
- **Wind on flip days:** on 2026-09-09 the domain-mean wind reached 25 kn from about 30°, and on 2026-09-17 it reached 18 kn from about 45°. The wind data therefore supports the north-wind story of the cases.
- **Shortest route:** at an 08:00 UTC departure, the shortest route (A) was not the best on 7 of 92 days.

With an arrival deadline of 19:00 UTC, almost every day "flips" in the afternoon only because late departures cannot arrive in time. `mine.py` should therefore exclude flips caused only by the deadline, or choose the deadline on purpose.

These numbers will change once the routes are redrawn and the thresholds are cited.
