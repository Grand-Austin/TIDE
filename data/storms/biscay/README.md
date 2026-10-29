# Biscay storm subsets

One folder per storm in [`../biscay.json`](../biscay.json) (34 storms, 2025-10-29 to 2026-10-29), each with
`waves.nc`, `wind.nc` and `currents.nc`: the storm peak ±24 h, ±2° around the peak cell, clipped to
12°W–1°W, 42°N–52°N. Checksums and time ranges are in [`manifest.json`](manifest.json).

| File | Copernicus Marine product | Variables | Step |
| --- | --- | --- | --- |
| `waves.nc` | GLOBAL_ANALYSISFORECAST_WAV_001_027 (`cmems_mod_glo_wav_anfc_0.083deg_PT3H-i`) | VHM0, VMDR, VTPK, VHM0_WW, VHM0_SW1, VMDR_SW1 | 3 h |
| `wind.nc` | WIND_GLO_PHY_L4_NRT_012_004 (`cmems_obs-wind_glo_phy_nrt_l4_0.125deg_PT1H`) | eastward_wind, northward_wind | 3 h |
| `currents.nc` | GLOBAL_ANALYSISFORECAST_PHY_001_024 (`cmems_mod_glo_phy-cur_anfc_0.083deg_PT6H-i`) | uo, vo (surface) | 6 h |

Generated using E.U. Copernicus Marine Service Information. Past dates are analysis fields, not observations.

Rebuild them from a downloaded year (`python -m pipeline.fetch_region biscay`, needs `copernicusmarine login`):

```bash
uv run python -m pipeline.storms biscay --subsets
```

`pipeline.export_scene` reads these folders first, so a scene can be exported without the raw year or an
account. It also queries ERA5 cloud cover and precipitation from Open-Meteo, which needs internet access.
