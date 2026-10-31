# Offline Data and Scenario Pipeline

This package collects environmental data and builds inputs for the current continuous-vessel prototype. Runtime physics stays in `game/`; it does not depend on the offline pipeline.

| Module | Responsibility |
| --- | --- |
| `fetch.py` | Copernicus daily snapshots, forecast archives and versioned scenario-window collection. |
| `downloads.py` | Shared download integrity metadata, atomic JSON writes and bounded retries. |
| `aemet.py` | Official coastal bulletins and historical warning collection. |
| `prepare_vessel.py` | Fetch and verify the pinned S175 source and call the runtime converter. |
| `scenario.py` | Analyze a specified historical window, build a scenario package, and validate trial strategies with the runtime simulation. |

See [DATA_PIPELINE.md](../docs/DATA_PIPELINE.md) for commands, existing-component reuse, data provenance and verification limits. Builder Agent support remains deferred.

## Retained analysis and export tools

The study and analysis scripts (`cargo_study.py`, `cargo_stacks.py`, `validate_storm.py`,
`validate_maneuvering.py`, `validate_toolkit.py`, `validate_turning.py`) and the earlier SeaSense tools
(`grid.py`, `routes.py`, `simulate.py`, `mine.py`, `validate_summer.py`, `export_cases.py`,
`export_map.py`) left the repository on 2026-10-31. Their tests moved with them. The files stay in the
local, ignored `archive/pipeline/` and `archive/tests/` directories and remain in git history. The
documents under `docs/` keep their results. Their fixed-route thresholds do not define the current
continuous S175 simulation.
