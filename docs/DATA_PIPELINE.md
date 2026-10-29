# Scenario data pipeline

The first package uses the historical 9 September 2026 afternoon sea state in the Mallorca–Menorca channel. Its definition, briefing and source manifest live in `scenarios/menorca-20260909/`. It supplies mission settings for later game rules; it is not a scored game session.

## Existing components reused

| Existing component | Use in this change |
| --- | --- |
| `pipeline.fetch`, `WAVE_VARIABLES`, dataset IDs and boxes | Extend existing downloads with a time-window command, integrity metadata, retries and atomic publication. |
| `day`, `forecast`, `DAY_DATASETS` | Preserve commands and the three-source contract consumed by `pipeline.export_map`. Temperature enters through `window`. |
| Forecast archive workflow | Retain the daily schedule; use uv and locked dependencies and retain diagnostic files on failure. |
| `CopernicusEnvironment` | Reuse strict native-grid sampling, circular wave directions, daily SST and missing-data stops. No nearest-sea fallback. |
| S175 preparation and `Simulation.advance` | Reuse vessel preparation, wind loads, continuous control/wave state and exposure calculations for all trials. |
| Retained `mine`, `simulate`, route exports | Remain in `pipeline/` with their tests in `tests/` for historical analysis and export reuse. Their fixed-route threshold classifications do not define the new S175 scenario. |

See [the pipeline guide](../pipeline/README.md) for the retained analysis modules. They run in the default test suite. Automatic historical candidate screening for the new continuous-vessel task still requires adapting the selection criteria.

New responsibilities are `game.scenario` (runtime definition/loading), `pipeline.scenario` (offline analysis/build/validation), `pipeline.aemet` (official reports) and small shared download helpers. Runtime code does not import the offline pipeline.

## Commands

Run from the repository root after `uv sync --locked` and vessel preparation:

```sh
uv run python -m pipeline.scenario analyze scenarios/menorca-20260909/scenario.json
uv run python -m pipeline.scenario build scenarios/menorca-20260909/scenario.json --out data/processed/menorca
uv run python -m pipeline.scenario validate scenarios/menorca-20260909/scenario.json
uv run python -m pipeline.fetch window --request scenarios/menorca-20260909/scenario.json --out data/raw/menorca-window
uv run python -m pipeline.aemet bulletin
uv run python -m pipeline.aemet warnings --start 2026-09-09T00:00:00Z --end 2026-09-10T00:00:00Z
```

The definition contains both `collection` requests and explicit existing file references. `window` downloads all requested layers to a new directory; it does not silently replace the scenario's frozen file references. To adopt a download, update the explicit paths and checksums after reviewing its manifest. Native wind, ocean and daily-temperature grids remain separate. Wave partitions retain their native period definitions. Existing simulation loads continue to use total sea only.

`load_scenario(path)` returns a definition with `environment()`, `simulation(environment)` and `initial_state(simulation)` methods. File references are relative to the repository root; an explicit `root` argument supports another checkout. A prepared S175 model is required. Build output is deterministic and stores source metadata and hourly analysis. The validation command runs four strategies from the same initial state for seeds 1, 2 and 3, using independent processes. Commands update every 300 simulated seconds. Objective checks occur every 5 seconds. This is offline numerical trial logic, not the game-rule implementation.

Large trajectories and events stay under `data/runs/`. The reviewed package includes a compact validation summary with commands and source checksums. A reduced-speed or delayed strategy may miss the deadline; those failures remain in the report. Passing requires at least one arrival per seed and differing cumulative lateral-response exposure per seed. It does not assert which strategy is universally safest.

## Integrity and information time

Downloads use temporary files and validate requested variables, coordinate order and coverage before publication. Cache reuse requires an identical request and matching SHA-256. A file with missing metadata or a changed request is not overwritten: use a new output directory. Failed layers retain diagnostic JSON; window collection attempts the remaining layers and exits unsuccessfully if any source fails. Transient errors retry three times with bounded backoff. Authentication and coverage errors fail directly.

Every source retains its raw attributes, acquisition time, valid-time coverage, request/version and checksum. Unknown issue/model-run timestamps remain unknown. A retrieval date does not establish when a forecast was issued. The daily forecast command captures the current rolling product; it cannot recover an earlier release by supplying an old date.

Historical reconstruction drives simulation but is not automatically released to the learner. Reports remain separate evidence and never overwrite physical forcing. Build scans the configured report archive and attaches only contemporaneous reports or warnings. A bulletin downloaded today is not evidence for September. Reports retain their original issue/validity times; an eventual Director must enforce their release times.

## AEMET archive

The public Balearic bulletin XML is fetched from the official coastal page's download link. Original bytes, declared encoding, producer and attribution are preserved. Local source times use `Europe/Madrid`, including summer time; normalized times use UTC. The archive retains all subzones including Canal de Menorca (`8040740`), general situation, warning text and trend. Revision names contain issue time and a full content checksum. Identical local requests deduplicate; each clean CI runner still uploads its snapshot.

Historical CAP requests require `AEMET_API_KEY` in the environment. It is sent in a header and excluded from URLs and diagnostic files. The collector follows the API data URL, parses CAP XML or ZIP contents in memory, and selects matching Balearic areas/time intervals. An archive with no matching warnings differs from an HTTP or parsing failure. Missing historical bulletins are recorded as missing, not inferred from CAP or reanalysis.

The AEMET workflow checks every three hours; the Copernicus workflow retains its daily 10:00 UTC schedule. Both upload raw data and metadata even after collection failures. GitHub artifacts expire after 90 days; this is not permanent storage. Retrieve runs with `gh run download RUN_ID --dir data/raw/archive-RUN_ID`. Merge AEMET directories by immutable filenames to deduplicate snapshots. Workflow execution and secrets must be configured in the remote repository; local checks do not establish remote scheduling success.

## Supplemental sources: document-led backlog

The selection follows `archive/Data_needs.md` §§2, 6 and current `docs/Product.md` §2. Archived physics assumptions are superseded by the current simulation implementation.

| Priority/source | Purpose and historical coverage | Access and model changes |
| --- | --- | --- |
| P0: existing Copernicus partitions and forecasts | Analyze already-downloaded wind sea and primary swell; preserve separate forecast releases from now on. | Existing Marine account. Do not add partition heights to total sea or double-count tidal currents. |
| P0: AEMET official bulletins/CAP | Professional sea-area forecast text and warnings. CAP archive documents coverage since 18 June 2018; historical coastal-bulletin retrieval is not established. | Public XML plus API key for CAP. Evidence only; no new force model. |
| P1: ECMWF ERA5 or a suitable issued forecast | Gusts, pressure, precipitation, temperature or visibility where a verified product supplies them. ERA5 supplies hourly historical reconstruction from 1940, not contemporaneously issued forecasts. | CDS account/licence for ERA5. Confirm each variable and accumulation interval. Gust maxima are not sustained wind vectors. Additional atmospheric loads need explicit model work. |
| P1: SOCIB/Puertos observations | Check event-time wave/current/wind series at actual station coordinates with quality flags. Coverage must be checked per station. Mahón is outside the committed domain. | Verify archive access and redistribution terms. Extend model extraction to station coordinates before comparison. Station observations do not validate the entire channel. |
| P1/P2: EMODnet bathymetry/coastline | Review spatial context and depth applicability; 2024 DTM covers the Mediterranean. | Retain vertical datum and source quality. Research grids do not certify navigational clearance. The runtime has no shallow-water, grounding or harbour model. |

Official follow-up references:

- AEMET coastal page: https://www.aemet.es/es/eltiempo/prediccion/maritima?area=bal1&opc1=0&opc2=martot&opc3=1
- AEMET API: https://opendata.aemet.es/AEMET_OpenData_specification.json
- ERA5: https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels
- ECMWF gust semantics: https://confluence.ecmwf.int/pages/viewpage.action?pageId=414588701
- SOCIB: https://thredds.socib.es/thredds/catalog/mooring/waves_recorder/catalog.html
- Puertos: https://www.puertos.es/servicios/oceanografia/faqs
- EMODnet: https://emodnet.ec.europa.eu/en/bathymetry

To publish reviewed numerical evidence with a build, pass
`--validation data/runs/menorca-20260909/validation.json` to `build`.
The builder rejects failed reports and reports for a different definition checksum.
