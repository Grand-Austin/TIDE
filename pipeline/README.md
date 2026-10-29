# Offline Data and Scenario Pipeline

This package currently downloads environmental data, simulates fixed routes, mines candidate days, and exports files for the earlier browser prototypes. Existing commands and imports remain unchanged.

`uv run python -m pipeline.prepare_vessel` also fetches and verifies the pinned
S175 reference data and calls the new runtime's data converter. It does not run
the legacy route simulator. See [SIMULATION.md](../docs/SIMULATION.md).

Evolve it into the scenario-construction pipeline as core capabilities become available:

1. Collect source data into `data/raw/` and reuse the curated subsets in `data/copernicus/`.
2. Prepare model inputs in `data/processed/` and select candidate windows.
3. Combine a candidate with vessel data, initial state, mission settings, and a briefing.
4. Call `game/simulation/` and `game/rules/` for numerical trials and checks.
5. Publish a reviewed definition and references under `scenarios/`.

Scenario Builder Agent assistance belongs in this workflow. Local code performs collection, scanning, numerical trials, and packaging. The Agent assists with interpretation, review, mission settings, and supporting material.

Reusable runtime physics belongs in `game/`, not in a second implementation inside the pipeline. The scenario format remains to be defined; the legacy [SCHEMA.md](../docs/SCHEMA.md) does not constrain it.
