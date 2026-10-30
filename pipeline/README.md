# Offline Data and Scenario Pipeline

This package collects environmental data and builds inputs for the current continuous-vessel prototype. Runtime physics stays in `game/`; it does not depend on the offline pipeline.

| Module | Responsibility |
| --- | --- |
| `fetch.py` | Copernicus daily snapshots, forecast archives and versioned scenario-window collection. |
| `downloads.py` | Shared download integrity metadata, atomic JSON writes and bounded retries. |
| `aemet.py` | Official coastal bulletins and historical warning collection. |
| `prepare_vessel.py` | Fetch and verify the pinned S175 source and call the runtime converter. |
| `validate_toolkit.py` | Assess current simulation responses to environmental and control changes. |
| `scenario.py` | Analyze a specified historical window, build a scenario package, and validate trial strategies with the runtime simulation. |
| `cargo_study.py`, `cargo_stacks.py` | Compare the live stack cargo rule with archived algorithms on recorded motion, including runtime benchmarks and batch checks. |

See [DATA_PIPELINE.md](../docs/DATA_PIPELINE.md) for commands, existing-component reuse, data provenance and verification limits. Builder Agent support remains deferred.

## Retained analysis and export tools

The earlier tools remain in their original locations for reference and reuse:

| Modules | Responsibility |
| --- | --- |
| `grid.py`, `routes.py` | Earlier grid lookup, authored routes and land-mask checks. |
| `simulate.py` | Fixed-route passage simulation and route ranking. |
| `mine.py`, `validate_summer.py` | Historical route-switch scanning and summer-data assessment. |
| `export_cases.py`, `export_map.py` | Earlier browser case and environmental-map exports. |

Their tests remain in `tests/` and run with the default suite. The historical scanning, statistics and export code can inform future scenario construction. Its fixed-route thresholds do not define the current continuous S175 simulation. The legacy [SCHEMA.md](../docs/SCHEMA.md) does not constrain current scenario packages.
