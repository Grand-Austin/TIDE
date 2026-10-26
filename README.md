# SeaSense

*Solve real cases at sea. Build the judgment future mariners need.*

SeaSense is a decision-training platform for navigation students, built for the Copernicus Marine Service Hackathon 2026 (theme: advancing ocean research, knowledge, skills and innovation). Each case is a real historical day in the Mallorca–Menorca channel, mined from Copernicus Marine data. Students investigate the evidence, choose a route and departure time, then replay what the sea actually did and find the point where their decision should have changed.

> SeaSense is a training tool. It is not a navigation or safety decision tool.

## Repository layout

```
pipeline/          Python: fetch Copernicus data, simulate routes, mine cases, export case files
tests/             pytest tests for pipeline code
data/copernicus/   Copernicus Marine NetCDF subsets used by the pipeline (see its README)
web/prototype/     clickable prototype with illustrative (not real) data
docs/SCHEMA.md     JSON contracts between pipeline, web and api
docs/ARCHITECTURE.md  components, data flow, AI coach design, deployment, known limits
```

Planned: `web/` app, `api/` (AI coach and play-test log), `docs/METHODS.md`.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest
```

Copernicus Marine data needs a free account. Log in once with `copernicusmarine login`; credentials stay on your machine and must never be committed.

## Copernicus Marine products

Committed subsets for the Mallorca–Menorca channel, 1 July to 30 September 2026. Details, variables and caveats are in [`data/copernicus/README.md`](data/copernicus/README.md).

| Layer | Product | Dataset |
|---|---|---|
| Waves | `MEDSEA_MULTIYEAR_WAV_006_012` | `cmems_mod_med_wav_my_4.2km_PT1H-i_202511` |
| Currents | `MEDSEA_MULTIYEAR_PHY_006_004` | `cmems_mod_med_phy-cur_my_4.2km_PT1H-m_202511` |
| Sea temperature | `MEDSEA_MULTIYEAR_PHY_006_004` | `cmems_mod_med_phy-temp_my_4.2km_P1D-m_202511` |
| Wind | `WIND_GLO_PHY_L4_NRT_012_004` | `cmems_obs-wind_glo_phy_nrt_l4_0.125deg_PT1H_202207` |

## Team

Aoxue Li, Jiahe Su, Qian Sun, Weixuan Ding, Yan Jiang

## License

MIT
