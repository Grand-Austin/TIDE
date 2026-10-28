# Scenario Packages

Store small, curated scenario definitions and briefing material here. Start with one manually assembled scenario; automate construction after it runs successfully.

Each scenario should identify its historical area and time window, source data, vessel and loading condition, initial state, mission settings, information-release rules, and model versions. Reference large environmental and model files instead of duplicating them in this directory.

Use one subdirectory per scenario when the first package is created. Its exact format will follow implementation needs. No example package is supplied because the data and vessel integration have not yet been validated.

The pipeline builds packages. The Director loads them. Simulation results and play logs belong in `data/runs/`, not in the source package.
