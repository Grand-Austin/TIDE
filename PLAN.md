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

### 1.3 Severe-sea motion repair and MSS comparison

- [x] Restore full MSS Coriolis coupling and test free-body inertial acceleration and the applied force's no-work property.
- [x] Execute pinned MSS in GNU Octave with identical matrices, states, currents, applied-force histories and RK4 steps; verify derivatives, trajectories, matrix conversion and shared wave components.
- [x] Resolve long-period spectral underresolution with a fixed logarithmic grid; check spectral energy and S175 force RMS against dense quadrature, while retaining genuine quality warnings.
- [x] Add mean longitudinal wave drift from the existing S175 source table, with explicit units, directional/speed interpolation and source-edge flags. Verify the calculation independently in Octave.
- [x] Run synthetic head/quarter/beam/following-sea limit tests, multiple speeds and seeds, exact force replay, and detailed validity-stop diagnostics. Retain environmental coverage checks and genuine numerical checks.
- [x] Remove TIDE's uncalibrated attitude cutoff from runtime and scenario settings. Verify motion above 30 degrees and replay the formerly blocked 16 m beam-seas interval against original MSS.
- [x] Remove the TIDE-only instantaneous speed and command-speed ceilings; extend synthetic trials to 12, 14 and 16 knots. Keep out-of-table speed lookup as a nonblocking quality flag.
- [x] Reassess cargo after the motion changes with matched-response tests, pause/save/restore checks and a controlled live storm run; retain earlier mission reports as historical evidence.

## 2. Scenario and Data Build

- [x] Select the Mallorca–Menorca channel, 9 September 2026 15:00–18:00 UTC, from committed historical fields.
- [x] Pin four environmental sources, S175 design-waterline parameters, numerical settings and checksums.
- [x] Assemble `scenarios/menorca-20260909` with an initial state, escape target, briefing and data manifest.
- [x] Validate four full-window strategies for seeds 1, 2 and 3; publish the passing numerical report and reference commands.

Implementation, reuse and verification: [DATA_PIPELINE.md](docs/DATA_PIPELINE.md). The package supplies offline trial settings; game rules remain in stage 3.


## 3. Game Rules and Events

Confirmed product decisions are recorded in [Product.md](docs/Product.md), sections 1.3, 2.2, 3.4 and 4. Execution order, interfaces and initial defaults are specified in [PROTOTYPE_IMPLEMENTATION.md](docs/PROTOTYPE_IMPLEMENTATION.md). Checked items below are implemented and verified; validation evidence is in [GAME_VERIFICATION.md](docs/GAME_VERIFICATION.md). Implementation authorized on 29 October 2026. Checkboxes are updated only after the corresponding checks pass.

### 3.1 Replaceable simulation algorithms and cargo

- [x] Define independent motion and cargo algorithm contracts with explicit units, state and version identifiers; reuse the existing S175 motion implementation.
- [x] Add a plausible full-load container-ship template, including protected hold cargo, exposed deck units, and simple stowage categories.
- [x] Assign seeded vulnerability thresholds once, accumulate damage from calculated responses over simulation time, and record each unit's irreversible overboard transition.
- [x] Calibrate cargo grades and response-to-damage parameters; retain fixed vessel mass, centre of gravity and hydrodynamic coefficients after loss.
- [x] Verify identical consequences across advance segmentation, pause/resume and time-rate changes, with no repeated losses or damage during pauses.

### 3.1.1 Cargo and ship damage after motion repair

- [x] Research six real container-loss occurrences, separate box counts from TEU, and normalize by actual loaded units where available.
- [x] Compare 0.14g and 0.30g cargo against a location-load / distributed-capacity candidate in 33 synthetic S175 runs and 24 prescribed-roll cases; repeat four motion cases at half the integration step.
- [x] Compare a 64-stack / five-box candidate across C50=0.4–0.6g, continuous-overload gates and fixed cargo seeds. Verify streaming/batch agreement and measure streaming time and batch memory cost.
- [x] Record algorithm comparisons, accident context and model limits in [CARGO_DAMAGE_STUDY.md](docs/CARGO_DAMAGE_STUDY.md). Keep the current gameplay algorithm pending integration validation.
- [x] Implement the selected C50=0.5g position-load stack rule, compact JSON state and grade events; verify pause, segmentation and save/restore. Update scenario defaults and archive earlier cargo code.
- [x] Implement total ship loss from a per-excursion roll table and cumulative roll/structural/following-sea exposure. End the mission and lose all remaining cargo. Document accident evidence and authored limits in `docs/SHIP_DAMAGE_STUDY.md`.

### 3.2 Escape region and released information

- [x] Extend scenario construction to derive, review and freeze a changing adverse-weather region from historical fields. Require exit from the whole region; exclude internal low-wave pockets as completion areas.
- [x] Enforce the time budget and valid operating domain. Assess escape and cargo loss separately; cargo loss alone does not terminate play.
- [x] Author explicitly labelled game forecast releases for the current and neighbouring sea areas when historical forecasts are unavailable. Record issue time, validity and provenance.
- [x] Expose local observations, cargo status and released reports while keeping region boundaries, vulnerability thresholds and unreleased future fields private.
- [x] Rebuild the first scenario and revalidate representative strategies against the new rules; do not reuse target-disk trial success as proof of region escape.

### 3.3 Continuous advancement and events

- [x] Return updated vessel/cargo state, metrics, events and stopping reasons through one local advancement tool, with consistent state commits and immediate mandatory stops.
- [x] Keep heading and speed active until changed. Add game-time rate controls and immediate manual pause/resume without requiring player-specified durations.
- [x] Detect mandatory cargo/mission/validity events locally; validate Director attention requests before pausing, and resume only on player confirmation. Deduplicate events across internal advance segments.
- [x] Complete a scripted mission without an Agent, including cargo consequences, timed reports, escape, timeout and invalid-domain cases.

### 3.4 Playable terminal screen

- [x] Replace the line/JSON interface with the default full-screen TUI. Refresh UTC, requested/achieved time rate, coordinates, actual/target heading and speed, observed sea conditions and retained cargo.
- [x] Add a released-forecast panel with scrolling, `j/k` time-rate shortcuts, immediate `p` pause and a prefilled heading/speed editor, plus `/` commands and save-on-quit.
- [x] Fix the helm editor after a deferred resume immediately pauses again; add a regression.
- [x] Verify target validation, input isolation, pause/resume, resizing and saved-session restoration. Keep `--script` for automated verification; remove the `--plain` entrypoint.

## 4. Director Game Engine

### 4.1 Model configuration and constrained tools

- [x] Add asynchronous Director judgment for issued report delivery and evidence-backed attention pauses; keep sailing during assessment, reject stale/cancelled decisions, and fall back to fixed report delivery.

- [x] Add versioned Director skill instructions, explicit workflow recipes and a shared tool registry; load them into the harness and verify authority/information boundaries.

- [x] Use a small LangGraph StateGraph for Director orchestration, with local SQLite checkpoints; keep simulation ticks, immediate pause and authoritative game state outside the graph.
- [x] Add extensible `models.json` profiles for provider, model and per-Agent settings, plus a separate ignored local API-key JSON and a key-free example.
- [x] Use NVIDIA NIM by default, targeting Nemotron 3.5 Lightning 30B A3B; support explicit OpenRouter selection without automatic provider fallback.
- [x] Implement only the Director role, with explicit operating instructions and a restricted tool set for scenario loading, command validation, advancement and released-state inspection.
- [x] Enforce player command authority and information access in local tools. Reject invented tool names, invalid arguments and duplicate state-changing requests.

### 4.2 Command-line play, recovery and verification

- [x] Provide text-only coordinates, vessel targets and actual response, sea observations, weather briefings, cargo status and game time; support direct controls without Copilot or graphics.
- [x] Connect continuous local execution to the Director's action → simulation → event → decision loop without model calls on numerical ticks or a second game-flow controller.
- [x] Automatically fall back to local orchestration for the current step after Director failures, without duplicate advancement or overriding player pause; default to the local engine without model configuration or API keys, with `--engine director` as an explicit experiment.
- [x] Record commands, released evidence, model/tool calls and outcomes without credentials. Save complete state for exit/resume, including wave phase, controller state, cargo damage and consumed events.
- [x] Verify malformed responses, interrupted calls, stale/duplicate requests, hidden-information boundaries, and exact save/resume continuation with deterministic model fixtures.
- [x] Complete a command-line mission with the configured live Director and record provider/model settings, outcomes and limitations. Live verification requires a configured API key.

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
