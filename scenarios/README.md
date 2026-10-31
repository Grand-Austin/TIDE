# Scenario Packages

Store small, curated scenario definitions and briefing material here. Start with one manually assembled scenario; automate construction after it runs successfully.

Each scenario should identify its historical area and time window, source data, vessel and loading condition, initial state, mission settings, information-release rules, and model versions. Reference large environmental and model files instead of duplicating them in this directory.

Use one subdirectory per scenario. The first definition uses a versioned JSON manifest with explicit environmental and vessel references; its build products include a briefing and data manifest.

The pipeline builds packages. The Director loads them. Simulation results and play logs belong in `data/runs/`, not in the source package.

The current storm package is [sole-20261022-21](sole-20261022-21/README.md).
Its `scenario.json` pins inputs and model settings. See
[DATA_PIPELINE.md](../docs/DATA_PIPELINE.md) for reproducible build and trial commands.
