# TIDE Prototype Plan

Build the core capabilities first. Use scripts or a command-line interface until the game works, then add a frontend demo. Prototype has one vessel, one loading condition, and one historical scenario.

## Foundations

- [x] Define the product direction in [Product.md](docs/Product.md).
- [x] Define module responsibilities in [ARCHITECTURE.md](docs/ARCHITECTURE.md).
- [x] Establish the workspace template and module ownership in [README.md](README.md).


## 1. Foundational Simulation Toolkit

- [x] Sample committed Copernicus total-wave, current and optional wind fields with explicit coverage and missing-data stops.
- [x] Execute heading and speed commands through bounded teaching actuators and continuous S175 six-DOF motion.
- [x] Integrate vessel response and elapsed-time exposure through `Simulation.advance`, preserving wave phase and controller state.

Implementation and limitations: [SIMULATION.md](docs/SIMULATION.md).

### 1.1 Vessel reference and conversion
- [x] Pin MSS source, record SHA-256 hashes, attribution, design-waterline condition and FSD/NED conventions.
- [x] Prepare zero-speed force RAOs and equivalent matrices; validate mass, damping and restoring properties.
- [x] Separate source parameters from derived teaching damping and actuator assumptions.
- [x] Supply an optional headless Octave reference generator.

### 1.2 Environment, dynamics and integration
- [x] Test temporal/spatial interpolation, circular directions, land/missing cells and exact coverage boundaries.
- [x] Port the required MSS six-DOF calculations and synthesize seeded total-sea wave forces.
- [x] Verify current advection, bounded control, phase continuity, exposure, pause/stop rollback and split-run equivalence.
- [x] Run the full Python suite and a 600-second real-data smoke exercise.
- [x] Add Isherwood horizontal wind loads with sourced S175 demo parameters and daily SST evidence.
- [x] Supply a reproducible six-environment-group assessment with forcing and control ablations.

## 2. Scenario and Data Build

- [x] Select the Mallorca–Menorca channel, 9 September 2026 15:00–18:00 UTC, from committed historical fields.
- [x] Pin four environmental sources, S175 design-waterline parameters, numerical settings and checksums.
- [x] Assemble `scenarios/menorca-20260909` with an initial state, escape target, briefing and data manifest.
- [x] Validate four full-window strategies for seeds 1, 2 and 3; publish the passing numerical report and reference commands.

Implementation, reuse and verification: [DATA_PIPELINE.md](docs/DATA_PIPELINE.md). The package supplies offline trial settings; game rules remain in stage 3.


## 3. Game Rules and Events

- [ ] Implement the escape objective, time budget, and explicit game cargo-loss rules.
- [ ] Return updated state, metrics, events, and stopping reasons from each advance.
- [ ] Complete a scripted mission with repeated decisions and interpretable consequences, without a frontend or Agent.

## 4. Director Game Engine

- [ ] Connect the Director to scenario loading, validation, simulation, and game-rule tools.
- [ ] Run the action → simulation → event → next-action loop and record the results.
- [ ] Complete the mission through the Director using command-line input. Keep local execution functions minimal.

## 5. Scenario Construction Automation

- [x] Turn the manual preparation process into local analyze/build/validate commands.
- [x] Extend existing Copernicus downloads with versioned time-window collection, integrity checks, retries and partial-failure diagnostics.
- [x] Add AEMET bulletin/CAP collection and official-report evidence attachment; verify public bulletin collection and revision deduplication.
- [x] Strengthen forecast CI archiving and add three-hourly AEMET snapshot workflow definitions.
- [x] Document existing pipeline reuse and document-led supplemental-source priorities.
- [ ] Verify authenticated Copernicus and AEMET CAP downloads (local credentials unavailable).
- [ ] Verify remote scheduled workflows after repository configuration/deployment.
- [ ] Add Builder Agent assistance for candidate review, mission settings, and briefing material.
- [ ] Generate and validate a second playable scenario through the pipeline.

## 6. Frontend Demo and Copilot

- [ ] Display the map, vessel state, mission metrics, and checkpoints; connect direct controls and pause/resume.
- [ ] Connect Copilot text/voice input to the same heading and speed action format.
- [ ] Complete an end-to-end playthrough and show its recorded results.
