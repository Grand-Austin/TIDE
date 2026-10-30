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
- [x] Historical stage 4 scope: implement only the Director role, with explicit operating instructions and a restricted tool set for scenario loading, command validation, advancement and released-state inspection. The Director entrypoint is now disabled; see stage 7.
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
- [x] Add Builder Agent support through the dedicated `agent/` package. Evidence: `agent/`; `tests/test_agent_builder.py`, `tests/test_agent_harness.py`, `tests/test_agent_mcp.py` and `tests/test_agent_simulator.py` as detailed in stage 7. This item verifies Builder support, not a published second scenario.
- [ ] Generate and validate a second playable scenario through the pipeline. No current Biscay scenario has a qualified Builder package.

## 6. Frontend Demo

- [x] Display the map, vessel state, mission metrics, and checkpoints; connect direct controls and pause/resume.
- [x] Complete an end-to-end playthrough and show its recorded results.

## 7. Agentic Systems

- **Status (2026-10-30 handoff):** The `agent/` package and the Builder are implemented and tested. The Builder writes review-staging packages and never publishes. No current Biscay package has a qualified route, so no second playable scenario exists yet. Real pipeline monitoring is the parent priority. This documentation pass defines planned Copilot and Tutor scope; it implements no role runtime, steering calibration or training.
- **Director:** Entrypoints remain disabled. Stage 4 records historical Director work. Do not treat Director as a current play path or add a speculative future Director role.
- **Roles:** Builder is the only implemented Agent role. Copilot and Tutor remain planned. The planned Copilot has advisory L1 and L2 hint modes and an explicitly enabled route-following control mode. The planned Tutor produces a concise settlement-page evaluation. No Copilot or Tutor tool set is implemented today. See section 7.15.
- **Authority:** The local `Session` remains authoritative for physics, cargo, ship loss, mission rules and state. Agents call scoped local tools; they do not calculate or commit a physical result.

**Historical baseline (2026-10-30):** Local branch `experiment` includes upstream `4101d2d`; 519 Python tests and 37 frontend tests passed at that time. The 519/37 counts are a historical baseline, not verification of the solutions-package changes. The original Agent stash `ef3ac67dbb487a9a1cbb472732c627335f3bf264` is retained. Agent work is not committed or pushed. Live NIM Builder execution and a qualified/published Biscay package remain operational follow-up; full Alboran benchmarking is out of scope.

Implementation and operating details are in [agent/README.md](agent/README.md). References and baseline provenance are in [agent/references/README.md](agent/references/README.md) and [agent/references/REPRODUCE.md](agent/references/REPRODUCE.md).

### 7.0 Checkbox policy

- `[x]` means the source exists and a named test or recorded run exercised it. The evidence is stated next to the item.
- `[ ]` means the work is unimplemented, planned, or implemented but not live-verified. The item text states which case applies.
- Do not mark a box from source inspection alone. The parent final suite may tick boxes whose named tests pass in that suite.
- Tests commonly use fake scenarios, fake simulators or injected model transports. Those tests verify control logic, not real sea-state outcome or provider behaviour.

### 7.1 Package, harness and model configuration

- [x] Put role-scoped Agent code in root `agent/`, separate from the authoritative `game/` runtime. Evidence: `agent/models.py`, `agent/harness.py`, `agent/simulator.py`, `agent/mcp.py`, `agent/tui.py`; `tests/test_agent_harness.py::test_role_allowlist_denies_tutor_tool`.
- [x] Implement one small LangGraph harness with role allowlists, strict JSON-schema tool validation, a mandatory first `record_plan`, finite model/tool-call/failure budgets and SQLite checkpoints. Evidence: `tests/test_agent_harness.py::test_plan_is_required_before_other_tools`, `::test_tool_call_cap_stops_with_pending`, `::test_resume_does_not_repeat_a_completed_costly_tool`.
- [x] Keep credentials out of audit data. Evidence: `tests/test_agent_harness.py::test_audit_records_tools_without_credentials`; the harness accepts only standard message fields and harness-owned audit facts.
- [x] Add `models.json` Builder profiles for NVIDIA NIM and explicit OpenRouter, plus an ignored local key file and a key-free example. Evidence: `models.json`, `config/api_keys.example.json`; `git check-ignore` confirms `config/api_keys.local.json` is ignored; `tests/test_agent_builder.py::test_ai_mode_can_inspect_and_revise_without_external_calls` uses an injected transport.
- [x] Provide deterministic local mode and controlled AI mode. Local mode makes no model call and reads no key. AI mode lets the model select, inspect, revise and finalize only through Builder tools; local code still decides qualification and packaging. Evidence: `agent/__main__.py`; `tests/test_agent_builder.py::test_ai_mode_can_inspect_and_revise_without_external_calls`.
- [ ] **IMPLEMENTED BUT NOT LIVE-VERIFIED:** Run `--mode ai` against a real Builder provider with a real key. No such run is recorded. Tests use injected transports and no external provider call.
- [ ] Implement Copilot advisory modes. Planned scope: an expanded-map hint button requests L1 (complete currently released forecast plus visible state, no solutions access) or L2 (temporary request-scoped solutions read that returns a route id, red reference line and short insight). Both are advisory only. See section 7.15.
- [ ] Implement Copilot route-following control. Planned scope: explicit route drawing, preview and grant; bounded heading and speed orders through authoritative Session/TUI MCP tools; deterministic local controller; immediate human override and outcome stops. It never edits scores or rules. See section 7.15.
- [ ] Implement Tutor. Planned scope: read the terminal game log and show a two-sentence, evidence-backed settlement-page evaluation with cited facts, no fabricated outcome and no rule or score changes. See section 7.15.

### 7.2 Simulator service and interaction hosts

- [x] Open a real `Session` from a project-contained scenario and retain its environment until `release()`. Evidence: `agent/simulator.py::open_session`; `tests/test_agent_simulator.py::test_open_session_releases_only_after_the_handle`.
- [x] Support an independent paused trial workspace and an optional live attachment. `SimulatorService` creates trial directories below one workspace. A live attachment uses trial id `live`. Evidence: `tests/test_agent_mcp.py::test_unix_socket_is_private_and_controls_the_same_live_session`.
- [x] Enforce bounded `step`: finite heading in `[0, 360)`, nonnegative speed, duration in `(0, 900]` seconds and an exact multiple of the simulation step; reject stale revisions; accept a nonempty `operation_id`. Evidence: `tests/test_agent_simulator.py::test_step_rejects_stale_revision_and_invalid_input`.
- [x] Advance in one-second chunks, release the Session lock between chunks, stop for a human pause, order change or outcome, and leave the trial paused. Evidence: `tests/test_agent_simulator.py::test_step_chunks_stay_readable_and_human_order_wins`, `::test_step_stops_when_the_player_pauses`, `::test_step_chunks_preserve_mandatory_event_stops`.
- [x] Return the recorded commit for a repeated `operation_id`, reject the same id with different arguments, and keep receipts only for the service process lifetime. Evidence: `tests/test_agent_simulator.py::test_duplicate_operation_returns_same_commit_and_conflicts_reject`.
- [x] Fork a complete paused Session into a new directory. The fork preserves seed, revision, cargo, ship, consumed events, order, controller state, exposure, motion, wave phase and previous wave rate, then isolates later changes. Evidence: `tests/test_agent_simulator.py::test_fork_preserves_complete_state_then_isolates`.
- [x] Run deterministic timed order plans to an outcome, apply exact order times on the fixed clock, record `plan_continuation` for report or cargo-grade stops, and stop at terminal or validity reasons. Evidence: `tests/test_agent_simulator.py::test_run_plan_stops_at_terminal_and_validity_stops`, `::test_run_plan_records_forecast_continuation`.
- [x] Reject scenario references outside the project and invalid orders before starting a trial. Evidence: `tests/test_agent_simulator.py::test_scenario_reference_must_stay_inside_project`, `::test_run_plan_rejects_invalid_orders_without_starting_a_trial`.
- [x] Host the actual original terminal screen in `agent.tui`, expose the same Session on a private Unix socket, reject an existing socket path, set mode `0600`, and let MCP attach with `--socket`. Evidence: `agent/tui.py`; `tests/test_agent_mcp.py::test_unix_socket_is_private_and_controls_the_same_live_session`, `::test_existing_socket_is_rejected_and_preserved`.
- [x] Provide MCP stdio tools `create`, `read`, `step`, `fork`, `run_plan`, `plan_voyage` and `close`. Default `--workspace` owns independent headless trials; `--socket` controls the TUI Session. Evidence: `tests/test_agent_mcp.py::test_mcp_stdio_direct_handshake_and_real_trial`, `::test_mcp_stdio_socket_handshake_calls_same_live_session`; later voyage integration is tracked in stage 8.
- [x] Keep the browser outside the Agent control and scoring path. This package adds no browser endpoint, checkpoint or score storage. Evidence: `grep` finds no browser module import in `agent/`; current `web/game` voyage changes are display-only and are covered in stage 8.
- [ ] **UNIMPLEMENTED:** Batch MCP calls, a separate cancel RPC and a revision RPC. Current bounds are finite duration, workspace/trial lifetime, service close, TUI pause/close, stale-revision rejection and operation receipts. Builder route revisions are local candidate edits, not MCP command revisions.

### 7.3 Builder fixed inputs and calibrated outputs

- [x] Freeze designer inputs by hashing the source scenario, its gameplay file and every declared environment layer. Reject output paths that overlap an input, a scenario directory or published `scenarios/`, including through symlinks. Evidence: `agent/builder.py::_hash_inputs`, `::_check_output_safety`; `tests/test_agent_builder.py::test_inputs_are_immutable_and_package_is_complete`, `::test_output_overlap_is_rejected_before_any_write`.
- [x] Permit Builder changes only to mission budget, mission end and derived gameplay/report schedules in `work/` and `package/` copies. Never edit the source scenario. Evidence: `agent/builder.py::_with_budget`, `::_materialize`; `tests/test_agent_builder.py::test_boundary_seventy_minute_route_uses_ninety_minute_budget` checks the original input remains unchanged.
- [x] Check gameplay schedule consistency: regions start at zero, cover the budget, reports end exactly at the budget, and the wave source checksum matches. Evidence: `agent/builder.py::_check_gameplay`; Builder tests call the real schedule validator through the injected gameplay builder.
- [x] Keep escape, physics, damage model, vessel and seed fixed. Qualification never rewrites those inputs. Evidence: fingerprint checks and `test_inputs_are_immutable_and_package_is_complete`.

### 7.4 Candidate search and baseline provenance

- [x] Implement deterministic TIDE route search `simroute:astar_aertssen` and `visir2:td_dijkstra_aertssen_current`. Both return persistent `{t, heading_deg, speed_knots}` orders, an empirical `estimated_s`, path metadata and an exhaustion diagnostic. Evidence: `agent/routing.py`; `tests/test_agent_routing.py::test_route_candidates_are_deterministic_and_have_persistent_commands`, `::test_missing_data_barrier_forces_a_detour_on_water`, `::test_moving_region_and_time_varying_current_use_actual_time`.
- [x] Treat every search result as a candidate estimate only. The Builder never qualifies from `estimated_s`. Only a full `SimulatorService.run_plan` Session can qualify a route. Evidence: `agent/models.py::is_qualified`; `tests/test_agent_builder.py::test_99_percent_threshold_and_fastest_qualified` uses full fake Sessions and rejects a faster unqualified candidate.
- [x] Call both required search methods for each run and record returned, accepted and rejected counts plus errors. A search error makes the package pending even if another route qualified. Evidence: `agent/builder.py::_search_candidates`, `::_run_local`; `tests/test_agent_builder.py::test_search_failure_is_pending_not_impossible_claim`.
- [x] The Builder and CLI use 64 trials and 12 candidates per search solver. Expert proposals stay at 3 and revision depth stays at 2. Verified by the targeted defaults and revision checks. Evidence: `agent/builder.py::_expert_candidates`, `::_revision_candidates`, `::submit_revision`; `tests/test_agent_builder.py::test_local_revision_runs_after_failure`.
- [x] Record SIMROUTE Palma provenance as an independent reproduction, not upstream execution and not a TIDE adaptation. The pinned Palma case reproduces 9.402636822 h, 14.973725553 h and 16.928160302 h from the published 9.40 h, 14.97 h and 16.93 h rows. No upstream SIMROUTE source is copied. Evidence: `agent/references/benchmarks.json`, `agent/references/REPRODUCE.md`; reference-run report at `/private/tmp/tide-pi-reference-resumed-20261030.log`.
- [x] Record VISIR-2 official analytic provenance correctly: the original external source ran the Techy Example 3 dynamic-current case and passed the official 3% criterion with measured `deltaL = 1.271898%` and `deltaT* = 2.567122%`. This is not a TIDE solver result. Evidence: `agent/references/visir2-run.json`, `agent/references/benchmarks.json` status `official_analytic_current_reproduced`.
- **Out of scope (user decision, 2026-10-30):** Do not implement or run the full VISIR-2 Alboran numerical benchmark. Keep the VISIR-2-inspired candidate search and validate routes with TIDE Sessions. The completed small analytic reference remains provenance only.
- [x] Reconcile the reference-provenance assertion after the peer correction. `tests/test_agent_routing.py::test_published_benchmark_provenance_is_not_mislabelled` now expects `visir2.status == "official_analytic_current_reproduced"` and `alboran_full_numerical_status == "not_run"`. Evidence: current test file and `agent/references/benchmarks.json`; the focused Agent run passed this test.
- [x] Apply TIDE adaptations without claiming that either adaptation is an upstream benchmark reproduction. The gameplay models use Aertssen speed loss because Bowditch gives sub-knot speeds for S175 in storm seas. Evidence: `agent/routing.py` module docstring; `agent/references/README.md`; `tests/test_agent_routing.py::test_simroute_bowditch_reference_edge_matches_published_case`, `::test_visir_current_composition_distinguishes_heading_from_course`.
- **Design boundary:** TopTier remains a risk reference, not an executable pathfinder or qualifier; no implementation is required.

### 7.5 Search horizon, coverage and final budget

- [x] Use the designer's input mission budget as the search horizon, not a preliminary candidate time. Build a search `start`/`end` plan and check all environment layers over the full window and authored bounding box. Evidence: `agent/builder.py::select_budget`, `::check_environment_coverage`; `tests/test_agent_builder.py::test_budget_formula_uses_verified_winner_time` asserts `search_horizon_s == 3600`.
- [x] If the input horizon exceeds source coverage, retry once with the largest whole-minute horizon that fits. Record `adjusted_from_s` and the reason. Evidence: `tests/test_agent_builder.py::test_search_horizon_shrinks_to_available_coverage`.
- [x] When no adequate horizon exists, return `pending` before any trial and state `inadequate_environmental_time_coverage_for_search_horizon`. Evidence: `tests/test_agent_builder.py::test_environment_coverage_pending_does_not_run_trials`.
- [x] Set the final budget from the fastest qualified actual full-Session time `T` as `max(900, 900 * ceil(1.2 * T / 900))`. Examples: `T = 3000 s` (50 min) gives `3600 s` (60 min); `T = 4200 s` (70 min) gives `5400 s` (90 min). Evidence: `agent/builder.py::planned_budget`; `tests/test_agent_builder.py::test_budget_formula_uses_verified_winner_time`, `::test_boundary_seventy_minute_route_uses_ninety_minute_budget`.
- [x] Choose the fastest qualified route by actual elapsed time, then loss and name as deterministic tie-breakers. Evidence: `tests/test_agent_builder.py::test_99_percent_threshold_and_fastest_qualified`.
- [x] Check final-budget coverage again before revalidation. Fail to `pending` if the final budget is outside source coverage. Evidence: `agent/builder.py::_finalize`; budget tests exercise `final_coverage`.

### 7.6 Qualification and mandatory final revalidation

- [x] Qualify only when the Session outcome is exactly `escaped`, `ship_lost` is false, result metrics are finite and consistent, elapsed time is within budget, total TEU is positive, and lost TEU is at most 1% of total TEU inclusive. Evidence: `agent/models.py::is_qualified`; `tests/test_agent_builder.py::test_99_percent_threshold_and_fastest_qualified`, `::test_invalid_result_metrics_never_qualify`.
- [x] Keep game scoring, judgement and star rules unchanged. The Builder gate adds no zero-loss rule and no 95% deck-retention rule. Evidence: qualification constant `LOSS_LIMIT = 0.01`; no scoring module imports or edits in `agent/`.
- [x] Regenerate `package/scenario.json` and `package/gameplay.json` with the final budget and run one fresh final full Session against the package path. Evidence: `agent/builder.py::_finalize`; `tests/test_agent_builder.py::test_final_revalidation_uses_final_definition`, `::test_final_definition_is_revalidated_and_can_fail`.
- [x] Mark the package `review_ready` only when final revalidation qualifies, inputs are unchanged and both searches have no error. Otherwise write `pending` with reasons. Evidence: `agent/builder.py::_finalize`; tests above and `test_search_failure_is_pending_not_impossible_claim`.
- [x] Never claim that a pending result proves the mission impossible. Evidence: `build-report.json` limitation text; `test_search_failure_is_pending_not_impossible_claim`.

### 7.7 Output artifacts, locations and fingerprints

- [x] Write the run record, state, trial cache and AI checkpoint under the selected `<out>/` directory, and write package artifacts under `<out>/package/`. Evidence: `agent/builder.py`; `tests/test_agent_builder.py::test_inputs_are_immutable_and_package_is_complete`, `::test_repeated_run_directory_requires_explicit_resume` (reads the state path), `::test_resume_restores_trials_and_revisions_after_tool_failure`; AI checkpoint evidence is in `tests/test_agent_harness.py::test_resume_does_not_repeat_a_completed_costly_tool`.
- [x] Write `scenario.json`, `gameplay.json`, `validation.json` and `build-report.json` to the package. This recorded item covers the earlier artifact set and excludes the later solutions file. Copy display `weather.json` only when its hourly data covers the final budget; otherwise omit it and record why. Evidence: `tests/test_agent_builder.py::test_inputs_are_immutable_and_package_is_complete`, `::test_weather_is_copied_only_inside_source_coverage`, `::test_weather_is_omitted_when_final_budget_exceeds_coverage`.
- [ ] **PENDING PEER (new `agent/solutions.py`):** Write one readable `package/solutions.json` with every distinct qualified route: summary, solver, timed orders, actual sampled track, escape time, TEU loss, ship result, limits and provenance. See [docs/SOLUTIONS.md](docs/SOLUTIONS.md).
- [x] The Builder writes `solutions.json` from final revalidated trials, applies the default minimum of three with `--min-solutions`, and has no `--hint-branch-limit`. A `pending` package keeps its valid routes. Verified by the targeted Builder checks.
- [x] Include detailed route orders, outcomes, elapsed time, TEU loss, ship loss, qualification reason, trial id, session path and evidence fingerprint in `validation.json` strategies. Evidence: `agent/builder.py::_strategy`; `tests/test_agent_builder.py::test_99_percent_threshold_and_fastest_qualified`.
- [x] Record an evidence fingerprint over the scenario definition, gameplay checksum, environment file hashes, vessel files and declared vessel identity, plus SHA-256 for the simulation and Agent implementation files listed in `agent.models.ALGORITHM_FILES`. Evidence: `agent/models.py::evidence_fingerprint`, `::_algorithm_hashes`; every Builder test reads the resulting `metadata.builder.evidence_fingerprint`.
- [x] Store `review_ready` and `pending` as review labels with approval `none`, `published: false`, `human_review_required: true`. Evidence: `agent/builder.py::_validation`; `tests/test_agent_builder.py::test_99_percent_threshold_and_fastest_qualified`.
- [x] Report current fingerprints inside the artifacts rather than treating a hash written in documentation as permanent. At this handoff, `evidence_fingerprint` is the authority for a run; its `algorithms` map contains the source SHA-256 values for the exact implementation.

### 7.8 Pending outcomes and publication boundary

- [x] Use `pending` for no qualified route, exhausted trial budget, coverage failure, required-search failure, invalid result metrics, final revalidation failure and tool or model failure. Preserve reasons in `pending_reasons`. Evidence: `agent/builder.py::_pending`; Builder tests cover each class above.
- [x] Return CLI exit code `0` for `review_ready`, `3` for `pending`, and `2` for an error. Print that review is required. Evidence: `agent/__main__.py`.
- [x] Refuse to publish. The Builder never writes to `scenarios/`; human review is mandatory. Evidence: `_check_output_safety`, `validation.json` metadata, `tests/test_agent_builder.py::test_output_overlap_is_rejected_before_any_write`.
- [ ] **PENDING OPERATIONAL CHECK:** Produce and human-review a real qualified Biscay Builder package. No such package exists in this checkout. `data/runs/builder-smoke` is an old local smoke artifact and is not current qualification evidence.
- [ ] **PENDING OPERATIONAL CHECK:** Publish a second playable scenario after human review. Do not claim one now.

### 7.9 Recovery, idempotency and actual limits

- [x] Persist Builder state to `<out>/builder-state.json` and reject an occupied output directory without explicit resume. Evidence: `tests/test_agent_builder.py::test_repeated_run_directory_requires_explicit_resume`, `::test_resume_returns_terminal_result_without_duplicate_cost`.
- [x] Reject resume when the input, horizon, Builder configuration, algorithm, vessel, environment or gameplay evidence changed. Evidence: `tests/test_agent_builder.py::test_resume_rejects_changed_input_context`; `agent/builder.py::_check_run_directory`.
- [x] Restore trial records and revisions after a transient tool failure; a completed terminal package resumes without repeating trial cost. Evidence: `tests/test_agent_builder.py::test_resume_restores_trials_and_revisions_after_tool_failure`, `::test_resume_returns_terminal_result_without_duplicate_cost`.
- [x] Resume an AI harness from its SQLite checkpoint without repeating a completed costly tool call, and reject a mismatched resume context. Evidence: `tests/test_agent_harness.py::test_resume_does_not_repeat_a_completed_costly_tool`, `::test_resume_context_mismatch_is_rejected`.
- [x] Use AI checkpoints in `<out>/builder.sqlite`, the harness audit in `<out>/audit.jsonl`, and the Builder state in `<out>/builder-state.json`. Evidence: `agent/builder.py::_run_ai`; Builder AI test checks the audit file.
- [ ] **IMPLEMENTED BUT NOT DIRECTLY TESTED:** `FileTrialCache` reuses a cached full-Session record across a restart by a key over the evidence fingerprint and orders. No focused cache-restart test exists. Treat it as an optimization, not as proof of a new run.
- [ ] **LIMIT:** `step` operation receipts are in memory only. A new service process cannot recognize an operation id committed by an older process. Do not rely on operation receipts across restart.
- [ ] **LIMIT:** There is no output-directory lock. Do not run two Builders concurrently in the same `<out>` or the same workspace trial directory.
- [ ] **LIMIT:** The trial budget counts newly executed full Sessions, not cache hits. The default is 64. `--max-trials` changes it. Harness rounds and tool calls are separately capped.

### 7.10 MCP, TUI and security boundaries

- [x] Run MCP over official stdio with exact tools `create`, `read`, `step`, `fork`, `run_plan`, `plan_voyage` and `close`. Evidence: `tests/test_agent_mcp.py::test_mcp_stdio_direct_handshake_and_real_trial` performs a real handshake and a real scenario create, step, read and close.
- [x] Default MCP mode owns an independent headless workspace; `--socket` attaches to the `agent.tui` Session. The live trial id is `live`. Evidence: `tests/test_agent_mcp.py::test_mcp_stdio_socket_handshake_calls_same_live_session`; `agent/mcp.py`, `agent/tui.py`.
- [ ] **IMPLEMENTED BUT NOT DIRECTLY TESTED:** Reject closing the live trial through MCP because the terminal host owns it. Source: `agent/simulator.py::close_trial`. No focused assertion covers this refusal.
- [x] Reject an existing socket path and preserve it; use mode `0600` for the live socket. Evidence: `tests/test_agent_mcp.py::test_existing_socket_is_rejected_and_preserved`, `::test_unix_socket_is_private_and_controls_the_same_live_session`.
- [x] Bound RPC lines to 1,000,000 bytes and handle EOF without deadlock. Evidence: `tests/test_agent_mcp.py::test_socket_client_bounds_request_lines`, `::test_socket_client_eof_does_not_deadlock`.
- [x] Keep the live Session paused before MCP mutation, reject a sailing live Session, reject stale revisions and stop an agent step when the human pauses or orders. Evidence: MCP and simulator tests above.
- [x] Add no browser control or safety path. The terminal, MCP and Builder use local Session and file paths only. Evidence: `grep` finds no browser module import in `agent/`; stage 8 records the separate display-only UI voyage changes and their lack of coast-guard code.
- [ ] **LIMIT:** No MCP cancellation RPC, no batch tool and no command-revision RPC. Use close, human pause, finite step duration and service shutdown. This is an actual gap against the earlier target wording.

### 7.11 Solutions package and planned Copilot/Tutor scope

- **Contract:** [docs/SOLUTIONS.md](docs/SOLUTIONS.md) defines one readable `package/solutions.json`. It replaces the aborted graded Hint v2 draft. There is no graded L1/L2/L3 hint economy, no offline graded-hint builder, no `hints.json` or `hints.md`, no Builder replay or branch expansion, no whole-area coverage target, no maximum stored-solution cap and no `--hint-branch-limit` option. The planned expanded-map hint button is a Copilot entry point. L1 and L2 are guidance modes, not a graded-hint schema or charge.
- **Content:** a short purpose and reading guide, the fixed initial conditions, the final budget, the source fingerprint, the minimum and found counts, and every distinct qualified route sorted by actual elapsed time then TEU loss. Each route gives a factual summary, the source solver, all timed orders, the actual sampled track when available, the actual escape time, the TEU loss, the ship result, its limits and the full-trial provenance. Missing intermediate samples are labelled endpoints-only; the exporter must not invent a path.
- **Policy:** the default minimum is three distinct qualified routes, set by `--min-solutions`. There is no storage cap. If fewer than the minimum pass, the package is `pending` and keeps the valid routes found. Every slower alternative must also pass inside the final budget. Compute is finite and configurable. The file does not promise that all possible routes are enumerated.
- **Copilot:** planned two-mode design. L1 gets the complete currently released Copernicus forecast payload for the scenario and valid window plus visible vessel state, with no solutions access, and returns one brief, sharp insight. L2 gets a temporary, request-scoped solutions read permission, selects a suitable route by position, time, heading, speed, remaining budget, damage and current-source fingerprint, and returns a route id, reference waypoints, a red line and a short insight. The permission is revoked after the request, no persistent solution prompt remains, and L2 makes no guarantee for an arbitrary current state. Both modes are advisory only. Route-following control is a separate opt-in mode defined in section 7.15.

Documentation done in this pass:

- [x] Removed the active graded Hint L1/L2/L3 description from [Product.md](docs/Product.md), [STORM_SCENARIOS.md](docs/STORM_SCENARIOS.md), [ARCHITECTURE.md](docs/ARCHITECTURE.md) and [agent/README.md](agent/README.md).
- [x] Replaced it with the single `package/solutions.json` contract in the diagrams, component tables, artifact trees, CLI options and acceptance sections.
- [x] Removed the `docs/HINT_EVIDENCE.md` links and linked [docs/SOLUTIONS.md](docs/SOLUTIONS.md) instead.
- [x] Recorded the defaults of 64 trials and 12 candidates per solver, the minimum of three solutions, the final-budget rule, the slower-alternative rule and the offline execution-speed rule.
- [x] Removed the `agent/hints.py` snapshot row and the stale hash assertions.

Implementation verified:

- [x] `agent/solutions.py` builds `package/solutions.json` to the contract in [docs/SOLUTIONS.md](docs/SOLUTIONS.md).
- [x] The Builder and CLI use defaults 64 and 12, `--min-solutions`, no `--hint-branch-limit`, solutions writing, and preservation of found routes on `pending`.
- [x] `agent/hints.py` and its hint-specific tests are removed.
- [x] Targeted checks passed: 57 Builder/budget/context checks in 2.11 s and 12 exporter checks in 0.49 s. The existing 519/37 counts remain historical. No live NIM Builder run was performed.

### 7.12 Verification commands used for this handoff

```bash
UV_CACHE_DIR=/private/tmp/tide-uv-cache uv run python -m pytest \
  tests/test_agent_builder.py tests/test_agent_harness.py tests/test_agent_mcp.py \
  tests/test_agent_routing.py tests/test_agent_simulator.py \
  tests/test_builder_budget_regression.py tests/test_routing_reference_runner.py -q
```

The earlier Builder resume assertion failure in this file was resolved by the Builder peer. The parent must use
the final suite result, not an intermediate count.

Baseline before the navigation, voyage and rate-limit scopes: the full suite on experiment HEAD `4101d2d` passed
**450 tests in 31.03 s**. Do not call that baseline the final result for the new features.

Historical reference runs:

```text
/private/tmp/tide-pi-runtime-resumed-20261030.log
  61 passed for simulator, MCP, terminal and Session tests; whole suite then 450 passed.
/private/tmp/tide-pi-routing-resumed-20261030.log
  8 routing tests passed; measured about 3-4 s per method on biscay-20251218-03.
/private/tmp/tide-pi-reference-resumed-20261030.log
  VISIR-2 official analytic case passed; SIMROUTE Palma independent reproduction passed.
```

### 7.13 Outstanding operational checks for the parent

- **Historical baseline:** Complete repository suite before the solutions-package work: **519 passed in 27.99 s**. Frontend: **37 passed** with `node --test web/game/game.test.js`. `git diff --check` passed. These counts do not verify the solutions-package work or the planned role design.
- [ ] **PARENT PRIORITY:** Record real pipeline monitoring for the active scenario, including source availability, coverage windows, data-quality reports and archive status. This documentation pass did not run the pipeline or change pipeline code.
- [ ] Run one real Builder local search and full Session on a current Biscay scenario with the current source. Record `pending` or `review_ready`, never call a search estimate a qualification.
- [ ] If a real route qualifies, review the staged package and decide publication. Publication is human-only.
- [ ] Run `--mode ai` with a configured live provider only when a reviewer supplies credentials outside Git. Do not infer live provider success from injected-transport tests.
- [x] Keep Director disabled. Verified by the terminal and Agent regression suite.
- [ ] Implement and verify Copilot and Tutor runtime, including steering calibration. All runtime items remain unchecked in section 7.15.

## 7.14 Final chosen scope: solutions package

This section replaces the aborted Hint v2 handoff draft. The user chose one readable solutions package.

Done in this documentation pass:

- [x] Read [AGENTS.md](AGENTS.md) and [docs/SOLUTIONS.md](docs/SOLUTIONS.md) before editing.
- [x] [Product.md](docs/Product.md): removed the old graded L1/L2/L3 Hint feature, `hints.json`/`hints.md`, whole-area coverage and hint permission/charging. Added the solutions package and the planned Copilot reference-route use. Section 5 now defines the later advisory L1/L2 modes and the opt-in control mode.
- [x] [STORM_SCENARIOS.md](docs/STORM_SCENARIOS.md): replaced the Hints section, updated the artifact tree, CLI-related build rules and the final-budget acceptance rule.
- [x] [ARCHITECTURE.md](docs/ARCHITECTURE.md): replaced the hints evidence module and diagram node with the solutions package, and updated the Copilot boundary.
- [x] [agent/README.md](agent/README.md): updated the status table, component table, CLI options, artifact tree and the solutions section. Removed the `agent/hints.py` snapshot row and stale hash note.
- [x] [agent/builder_harness/INSTRUCTIONS.md](agent/builder_harness/INSTRUCTIONS.md): the Builder writes the verified solutions file and must not claim full route enumeration.
- [x] [PLAN.md](PLAN.md): replaced the superseded v2 items with this done and pending checklist.
- [x] Removed the `docs/HINT_EVIDENCE.md` links. The parent deleted that aborted draft.

Pending:

- [x] Verified exporter and Builder defaults, minimum count, final-package revalidation, no storage cap, duplicate removal, pending route preservation, real-format event track extraction and absent graded-hint artifacts.
- [x] Targeted result: 57 checks passed in 2.11 s for `test_agent_builder.py`, `test_builder_budget_regression.py` and `test_builder_context.py`; 12 checks passed in 0.49 s for `test_agent_solutions.py`. No full-suite rerun. `git diff --check` passed.
- [ ] **PARENT:** Record any live NIM Builder run. No live provider run is recorded now.

Known limits at this handoff:

- Parent reviewed the changes and 69 targeted checks passed. No real NIM or Biscay end-to-end run was performed in this change.
- No current Biscay package has a qualified route, so no solutions package exists yet.
- Copilot and Tutor runtime, steering calibration and extra-trial flows remain unimplemented. See section 7.15.
- The solutions file does not promise that all possible routes are enumerated.

### 7.15 Planned Copilot and Tutor scope: documentation-only handoff

**Status:** the design is documented. Runtime and evaluation are not implemented. Real pipeline monitoring remains the parent priority. No Copilot, Tutor, steering calibration, training or related test ran in this documentation pass.

**Documentation completed in this pass**

- [x] Kept `package/solutions.json` as the only Builder route artifact. There is no offline graded-hint builder, no whole-area route builder and no graded hint economy.
- [x] Defined Copilot advisory L1 and L2 modes, the expanded-map hint-button entry point, released-source labels, temporary permissions, the red reference overlay and refusal limits.
- [x] Defined the explicit route-following control mode, its advisory-versus-control distinction and its supersession of the earlier blanket steering prohibition.
- [x] Defined the Tutor settlement-page output and linked [Product.md](docs/Product.md), [ARCHITECTURE.md](docs/ARCHITECTURE.md) and [SOLUTIONS.md](docs/SOLUTIONS.md).
- [x] Kept shared NVIDIA NIM, the ignored `config/api_keys.local.json`, the fixed 30/60 attempt budget and the disabled Director unchanged.

**Copilot L1 implementation and evaluation**

- [ ] Add the expanded-map hint button near the expanded-map controls and current mission guidance.
- [ ] Build the L1 context adapter from the complete currently released Copernicus forecast payload for the scenario and valid window plus the current visible vessel state.
- [ ] Expose the full forecast through a tool or chunked retrieval so the model does not receive or repeat a giant prose payload.
- [ ] Enforce brief, sharp output: one actionable insight, a hard sentence or token limit, no forecast restatement and no generic advice.
- [ ] Label dataset historical truth separately from an issued forecast when the current forecast is simulator-derived.
- [ ] Test prompts iteratively against fixed scenarios and require brevity and sharpness review. Record prompt versions and results.
- [ ] Prove L1 cannot see unreleased bulletins, solutions, hidden regions or future realized fields. Future valid times inside the already released forecast are permitted.
- [ ] Verify L1 has no solutions access and cannot return a route id or reference waypoints.

**Copilot L2 implementation and evaluation**

- [ ] Implement a temporary, request-scoped read permission for `package/solutions.json`. Grant on request and revoke on completion, cancellation, timeout or error.
- [ ] Prove the permission does not become a persistent prompt or a standing tool capability.
- [ ] Implement route selection with spatial proximity, time, heading, speed, remaining budget, damage and current-source fingerprint matching.
- [ ] Return `route_id` and reference waypoints, draw the red reference line in the frontend and show the short insight.
- [ ] Define refusal cases: no near or suitable route, fingerprint mismatch, stale or unreleased source, expired permission and an arbitrary current state with no safe continuation.
- [ ] Verify L2 never claims that an initial-state-validated route guarantees escape from the current state.
- [ ] Add permission-lifecycle, red-line overlay, route-selection and refusal tests.

**Route-following control implementation and evaluation**

- [ ] Add expanded-map route drawing, a preview step and an explicit start grant for the opt-in control mode.
- [ ] Route every heading and speed order through bounded authoritative Session or TUI MCP tools. Enforce per-step bounds, source checks and revision checks.
- [ ] Implement deterministic local path following and fast scientific lookups from actual vessel motion, wave-relative heading, roll and pitch response, cargo and ship algorithm load metrics, speed-dependent manoeuvring, steering and rpm limits, and sea, coast, environment, time and cargo constraints. Do not let the model invent physics.
- [ ] Use verified response tables and simple fast calculations with short trial comparisons. Test that slower is not assumed safer and that no universal safe heading is assumed.
- [ ] Track target distance, heading and speed with explicit risk tradeoffs.
- [ ] Implement immediate human pause or order override, outcome stops and control release.
- [ ] Prove control cannot edit scores, rules, cargo totals or recorded outcomes.
- [ ] Calibrate with a few full-speed forked TUI/MCP trials, compare rule variants on held-out cases and freeze a versioned rule document after the comparison.
- [ ] Record stage comparisons for control variants and a runtime override test that interrupts active control.
- [ ] Run full-speed forked TUI/MCP trials before any claim of controller validity. Do not claim validation from documentation.

**Tutor implementation and evaluation**

- [ ] Implement a terminal game-log adapter that reads real decisions, timestamps, commands, events and outcomes.
- [ ] Add settlement-page UI for a simple evaluation, by default two short sentences: one key decision and one improvement.
- [ ] Require cited real facts from the log and prohibit fabricated outcomes or unsupported claims.
- [ ] Add a factuality fallback: on missing evidence or model error, show a deterministic log-based summary or a clear unavailable message.
- [ ] Verify the Tutor cannot edit rules, scores, judgement, stars, cargo totals or the recorded outcome.
- [ ] Test concise-output limits, missing-log handling, citation correctness and fallback behaviour.

**Cross-cutting checks**

- [ ] Use the shared ignored `config/api_keys.local.json` and NVIDIA NIM transport for any future role call. Keep the fixed 30/60 attempt budget and no provider fallback.
- [ ] Re-verify that Director stays disabled when Copilot or Tutor work begins, and enable no role by default.
- [ ] Run the relevant role acceptance checks and record results before ticking runtime items. Do not rerun unrelated suites without a regression reason.
- [ ] Record real pipeline monitoring separately. This role documentation does not verify the pipeline.

## 8. Coastal Navigation Guard and Planned Voyage Context

- **Status:** Core navigation and voyage source now exists. The peer handoff reports 22 navigation/voyage/live Python tests passed in 2.37 s; the docs-time rerun passed 23 in 4.01 s. Node reported 37 passed; the docs-time `node --test game.test.js` run also passed 37. A real original-TUI Menorca short play remained outside the 5 nm guard for 70 s. `pipeline.navigation_data --verify-only` succeeded with archive SHA-256 `e547d749445eaa0964aba76738090ec88f5e63c4585122170f98c67a7ea922dc`. The actual VoyagePlanner run for Biscay 20260123 produced Brest to Vigo, 612.34 nm, snap offset 11.37 nm, 22 track points, authored, with no relocation. MCP and Builder integration peers are still active; their checkboxes remain for the parent. The 450-test baseline was taken before this scope and is not a final result.
- **Priority:** Original curses TUI and MCP play come first for coast avoidance. The 3D page must not own coastline data, rendering or safety policy.
- **Fixed rule:** The operational guard is a fixed 5 nautical mile buffer from land. It is not a player choice, Agent choice or scenario tuning value.
- **Data:** Use offline, pinned Natural Earth 10m land v5.1.1 data under `data/navigation`. Do not download land at game time.
- **No physics changes:** Do not change environment sampling, vessel physics, cargo damage, ship loss or scoring for this scope.

### 8.0 Dedicated source tasks

- [x] `game/navigation.py`: authoritative fixed 5 nm land-buffer service, shared point/edge/segment checks, offline data loader, fail-closed errors and local-execution guard. Evidence: `tests/test_navigation.py`.
- [x] `pipeline/navigation_data.py`: pinned Natural Earth 10m land v5.1.1 acquisition/verification, offline `data/navigation` preparation, provenance and initial-position validation. Evidence: `data/navigation/manifest.json`, `data/navigation/README.md`, parent verify-only run.
- [x] `agent/voyage.py`: scenario-authored planned voyage from the sea-route network and canonical ports, with actual start, nearest point, snap offset, weighted shortest path and oriented distinct branches. Evidence: `tests/test_agent_voyage.py`; actual Biscay 20260123 Brest–Vigo artifact.
- [x] `web/game/ports.json`: canonical shared port list used by the pipeline, Agent and UI. Evidence: `tests/test_agent_voyage.py::test_canonical_catalogue_is_plain_json_array_from_the_game_list`.
- [x] Backend and UI voyage propagation: one voyage view for the original TUI and the 3D page. The 3D page receives display data only and has no coastline data, rendering or policy. Evidence: `tests/test_live_voyage.py`; `node --test web/game/game.test.js` 37 passed.

### 8.1 Shared navigation source and fail-closed data package

- [x] Implement `game/navigation.py` as the single authoritative coast-guard service for TUI and MCP. It exposes point and edge/segment checks against the fixed 5 nm land buffer. Candidate generation and actual local execution call this shared service. Evidence: `tests/test_navigation.py`, `tests/test_mcp_navigation.py`.
- [x] Add the pinned Natural Earth 10m land v5.1.1 source package under `data/navigation`, with provenance, licence, SHA-256 checksums and offline preparation. Evidence: `data/navigation/manifest.json`; `tests/test_navigation.py::test_pinned_archive_checksum_matches_manifest`; parent verify-only run.
- [x] Validate the dataset at load time: required files, checksums, nonempty geometry, finite coordinates and a usable index. Missing, corrupt or unreadable data fails closed. Evidence: `tests/test_navigation.py::test_missing_or_corrupt_data_fails_loudly`, `::test_archive_member_paths_are_rejected`.
- [x] Forbid a silent empty-land fallback. Evidence: the fail-closed tests above and `test_environment_wrapper_rejects_before_source_sample_and_does_not_own_source`.
- [x] Keep the guard distance in one fixed constant and record it in navigation status. Evidence: `test_guard_metadata_pins_version_source_checksum_and_units`; `test_clearance_boundary_is_five_nautical_miles`.
- [x] Keep navigation changes out of environment, physics, cargo, ship-loss and scoring behaviour. Evidence: raw and guarded simulations produce identical safe trajectories in `test_full_simulation_stops_and_rolls_back_before_forbidden_entry`; the guard only wraps environment sampling.

### 8.2 Candidate and actual-execution checks

- [x] Check candidate route edges against the 5 nm land buffer. Reject an edge that touches or crosses the buffer. Evidence: `tests/test_navigation.py::test_route_search_masks_synthetic_land_and_crossing_edges`.
- [x] Check actual local execution through the same `game.navigation` service. Stop or reject before an unsafe position is committed. Evidence: `test_full_simulation_stops_and_rolls_back_before_forbidden_entry`, `tests/test_mcp_navigation.py::test_guard_stops_at_the_last_safe_state_and_rejects_the_next_step`.
- [x] Keep candidate validation separate from qualification. Source preserves the distinction, confirmed by `tests/test_builder_context.py` in the final 519-test run.
- [x] Preserve committed Session state on rejection. Evidence: the full-simulation test asserts the caller state was not mutated; the MCP test asserts the blocked state fingerprint is unchanged.
- [x] Make save, resume, fork, TUI and MCP step/fork use the same navigation rule and dataset. Local fork coverage exists in `tests/test_mcp_navigation.py`, and the final MCP/Builder regression suite passed.
- [x] Define deterministic boundary behaviour for an edge exactly at 5 nm, a crossing segment and an already unsafe state. Evidence: `test_clearance_boundary_is_five_nautical_miles`, `test_segment_rejects_land_crossing_even_when_both_endpoints_are_safe`, `test_unsafe_initial_state_fails_without_relocating_the_ship`.

### 8.3 Unsafe initial state

- [x] Check the scenario initial position before interactive local execution. Local evidence exists in `test_unsafe_initial_state_fails_without_relocating_the_ship`; `tests/test_builder_context.py` verifies the search-time check.
- [x] If the initial state is unsafe, return Builder status `pending` with `navigation_initial_state_unsafe` and no relocation. Evidence exists in `tests/test_builder_context.py::test_unsafe_initial_position_is_pending_without_relocation`; confirmed by the final full suite.
- [x] For TUI or MCP startup, fail closed with a clear local error when the initial state is unsafe. Evidence exists in `tests/test_mcp_navigation.py::test_unsafe_initial_state_fails_clearly_without_creating_a_trial`; confirmed by the final full suite.
- [x] Record the initial position, guard distance and failure reason in validation or run evidence. Source exists; confirmed by the Builder context artifact tests.

### 8.4 `pipeline/navigation_data.py`

- [x] Implement `pipeline/navigation_data.py` for the pinned Natural Earth 10m land v5.1.1 artifact and offline `data/navigation` package. Evidence: `data/navigation/README.md`; parent verify-only run.
- [x] Verify the archive with SHA-256 before extraction. Record licence, attribution, source URL and version. Evidence: `data/navigation/manifest.json`; `test_pinned_archive_checksum_matches_manifest`.
- [x] Provide deterministic preparation and `--verify-only`. The parent verify-only run succeeded with SHA-256 `e547d749445eaa0964aba76738090ec88f5e63c4585122170f98c67a7ea922dc`.
- [x] Add scenario input validation for the initial position. Builder source and `test_builder_context` exist; confirmed by `tests/test_builder_context.py`.
- [x] Treat `web/game/ports.json` as the canonical shared port list. Evidence: `test_canonical_catalogue_is_plain_json_array_from_the_game_list`; 223 entries.

### 8.5 `agent/voyage.py`: authored planned voyage

- [x] Replace the Le Havre–Halifax default with a scenario-authored voyage. Evidence: `tests/test_live_voyage.py`; actual Biscay 20260123 Brest–Vigo artifact.
- [x] Build the voyage from the sea-route network and canonical ports with a weighted shortest path after snapping the actual start. Evidence: `test_weighted_dijkstra_beats_hop_count_and_uses_the_snapped_position`, `test_real_biscay_smoke_uses_marnet_and_canonical_catalogue`.
- [x] Preserve the actual start and record the snap offset in nautical miles. Evidence: `test_route_passes_through_an_edge_projection_not_only_a_node`; actual 11.37 nm offset.
- [x] Keep distinct opposite branches and orient them by the initial heading. Evidence: `test_ports_are_distinct_and_on_opposite_heading_branches`.
- [x] Label the result as authored context, not a historical log. Evidence: `test_real_biscay_smoke_uses_marnet_and_canonical_catalogue`; `tests/test_builder_context.py` labels `historical_track: false`.
- [x] Ensure port approach never changes escape, survival, TEU retention, scoring, judgement or stars. Source separation and Builder context tests exist; confirmed by the final full suite.
- [x] Keep the coast guard and voyage display separate. Evidence: `game/navigation.py` and `agent/voyage.py` are separate; no voyage code calls the limiter or modifies coast policy.

### 8.6 Backend and UI propagation

- [x] Propagate the authored voyage from the scenario/backend to the original TUI and the 3D page through a voyage view model. Evidence: `tests/test_live_voyage.py`; Node 37 passed.
- [x] Let the 3D page display backend voyage context without coastline geometry or navigation policy. Evidence: `grep` finds no `game.navigation`, Natural Earth or `data/navigation` import in `web/game`; Node tests cover voyage and port display only.
- [x] Label the voyage separately from coast-guard status. Evidence: Node test `the voyage note names the plan and the off-track distance as authored context`; `tests/test_builder_context.py` asserts `port_arrival_is_escape: false`.
- [x] Keep existing scenarios compatible with an absent voyage. Evidence: `tests/test_live_voyage.py::test_a_legacy_definition_without_a_voyage_reports_no_plan`; Node test `no authored voyage draws no planned route`.
- [x] Keep actual start, snap offset and branch identity in the propagated payload. Evidence: actual Biscay artifact and `assert_authored_voyage` in `tests/test_mcp_navigation.py`.
- [x] Add no new browser scoring, checkpoint, safety or simulation authority. Evidence: no navigation import in `web/game`; Node tests are display/context tests.

### 8.7 Acceptance checks for source owners

- [x] Dataset tests: pinned version and checksums; missing file, corrupt archive, empty geometry and checksum mismatch all fail closed. Evidence: `tests/test_navigation.py`.
- [x] Guard tests: unsafe point, unsafe edge, crossing segment, exact 5 nm boundary and safe open-water case. Evidence: `tests/test_navigation.py`.
- [x] Candidate tests: unsafe route edges are rejected before a full Session; a safe candidate still reaches a full Session. Route-search evidence exists; `tests/test_builder_context.py` passed.
- [x] Execution tests: actual local advancement stops before an unsafe commit and remains recoverable; TUI and MCP share the result. Local simulation evidence exists; `tests/test_mcp_navigation.py` passed.
- [x] Initial-state tests: unsafe start gives Builder `pending` without relocation and with no trial search. Evidence exists in `tests/test_builder_context.py`; confirmed by the final full suite.
- [x] Voyage tests: actual start, snap offset, weighted shortest path, nearest network point, opposite branches and initial-heading orientation. Evidence: `tests/test_agent_voyage.py`.
- [x] Provenance tests: authored context, not historical; port approach does not alter escape or qualification. Source and Builder context tests exist; confirmed by the final full suite.
- [x] Separation tests: no coastline data or policy in 3D; voyage display works only from backend data; missing voyage does not break play. Evidence: `grep` imports plus Node 37 passed and `test_live_voyage`.
- [x] Regression tests: no physics, environment, cargo, ship-loss or scoring change and no scenario digest change except documented voyage fields. Final 519-test suite passed; physics, environment, cargo, ship-loss and existing scenario files have no changes in this work.
- [x] Final full suite after all new scopes. Do not use the 450-test pre-scope baseline as the final result.

### 8.8 Handoff checks for the parent

- [x] Tick section 8 items only after their named source and test evidence exists.
- [x] Record the final full-suite count and duration after concurrent source owners finish.
- [x] Verify the pinned Natural Earth package and its provenance before accepting navigation evidence.
- [x] Run at least one real TUI or MCP local mission past a coast case, or record the exact pending/stopping reason if no safe route exists.
- [x] Confirm the 3D page has no coastline rendering, data or policy and that voyage display is backend-fed only.
- [x] Preserve the recorded VISIR-2 analytic success. The full Alboran case is explicitly out of scope by user decision.


**Final section 8 evidence:** `tests/test_navigation.py`, `tests/test_agent_voyage.py`, `tests/test_builder_context.py`, `tests/test_mcp_navigation.py` and `tests/test_live_voyage.py` passed in the full suite. The original guarded Menorca TUI was launched, sailed, paused and saved; the Natural Earth archive passed `--verify-only`. Real Biscay voyage context: Brest → Vigo, 612.34 nm, 11.37 nm start-to-network offset. This is authored context, not a qualified escape or published scenario. No coastline layer or guard was added to 3D.

## 9. Shared Agent Model Access and Global Attempt Budget

- **Status:** New scope after the navigation and voyage handoff. `agent/rate_limit.py`, `agent/transport.py` and the
  legacy Director re-export exist. Most focused rate-limit tests pass, but one timestamp acceptance test still
  fails. Treat the scope as pending until that test and the parent final suite pass.
- **Shared configuration:** Every application Agent uses `config/api_keys.local.json` and the NVIDIA NIM provider.
  The active Builder profile is `builder_nim` with model `nvidia/nemotron-3.5-lightning-30b-a3b`.
- **Shared budget:** At most **30 actual model HTTP attempts** may start in any rolling **60 seconds**. The budget is
  shared across roles, clients, threads and processes in the project.
- **Shared store:** The fixed SQLite file is `data/runs/agent-rate-limit.sqlite`. The database stores attempt
  timestamps only, never credentials, prompts, responses or role data.
- **No local throttling:** Local MCP calls, physics, route search, navigation checks and voyage planning are not
  model HTTP attempts. They must not consume the Agent model budget.

### 9.1 Configuration and transport path

- [x] Put the shared transport and limiter in `agent/transport.py` and `agent/rate_limit.py`. The Builder imports
  the legacy-compatible `ChatClient`, which is a re-export of the same class. Evidence:
  `tests/test_agent_rate_limit.py::test_compatibility_module_reexports_the_same_transport_symbols`.
- [x] Default model credentials to `config/api_keys.local.json` and expose no automatic provider fallback. Evidence:
  `agent/transport.py::DEFAULT_KEYS_PATH`; `tests/test_agent_rate_limit.py::test_fixed_policy_and_project_paths`.
- [x] Keep the active Builder role on NVIDIA NIM with model `nvidia/nemotron-3.5-lightning-30b-a3b`. Evidence:
  `models.json`; `tests/test_agent_rate_limit.py::test_default_key_path_and_shared_nim_builder_profile`.
- [x] Keep the retained Director compatible through `game/director/models.py`, which re-exports `ChatClient`,
  `ModelError` and `load_profile`. Director entrypoints remain disabled. Evidence: compatibility test above.
- [x] Make the rate-limit cap, window and database path fixed. Model profiles, tool arguments and CLI options
  cannot raise 30/60 or select another database. Evidence:
  `tests/test_agent_rate_limit.py::test_profile_fields_cannot_raise_the_cap_or_select_another_database`,
  `::test_internal_limiter_cannot_be_configured_above_the_global_cap`.
- [x] Keep the shared database out of Git. Evidence: `git check-ignore` resolves
  `data/runs/agent-rate-limit.sqlite` to the `data/runs/` ignore rule.
- [x] Confirm the active Builder uses the shared key path, NIM provider and transport. Future roles must use this same transport.
  Current active Agent role is Builder only; Copilot and Tutor are planned and Director is disabled.

### 9.2 Attempt accounting and retries

- [x] Reserve one shared slot immediately before each HTTP POST, including every retry. A retry consumes another
  slot. Evidence: `tests/test_agent_rate_limit.py::test_retry_attempts_consume_the_shared_budget`.
- [x] Enforce the rolling window, not a fixed-minute bucket. The 31st attempt in a full 60 s window waits for the
  oldest attempt to expire. Evidence: `::test_thirty_first_attempt_waits_for_the_rolling_window`,
  `::test_window_is_rolling_not_a_fixed_minute_bucket`.
- [x] Share one atomic SQLite state across clients, threads and profiles. Evidence:
  `::test_clients_threads_and_profiles_share_one_atomic_database`.
- [x] Make at most three HTTP attempts per model call: the first call plus two retries. Network errors retry after
  1 s and 2 s. HTTP 429 and 5xx use a clamped `Retry-After` value or the same backoff. Evidence:
  `::test_three_failed_attempts_still_stop_with_a_sanitized_error`; `agent/transport.py`.
- [x] Fail closed when shared limiter state is unavailable. Raise a sanitized `ModelError` before any HTTP request.
  Evidence: `::test_limiter_failure_fails_closed_before_http`.
- [x] Do not create a reservation before an actual HTTP attempt. A closed or unused client writes no database row.
  Evidence: `::test_no_reservation_until_an_http_attempt_is_made`.
- [x] Timestamp admission after acquiring the SQLite write lock. Evidence: `tests/test_rate_limit_acceptance.py::test_attempt_time_is_taken_after_the_database_lock` passed. Separate-process contention also passed: four processes requested 40 slots and only 30 were admitted.
- [ ] **LIMIT:** The limiter waits in chunks of at most 0.5 s. SQLite busy timeout is 5 s. If the shared database
  cannot be read or written, the request fails closed rather than proceeding unthrottled.
- [ ] **LIMIT:** Attempt timestamps use wall-clock seconds. Rows survive restart until they age out of the rolling
  window. A project-wide clock change can distort admission timing; do not treat this file as an audit log.

### 9.3 Local execution is not rate-limited

- [x] Keep the limiter inside `agent.transport.ChatClient` only. Evidence: no `agent.rate_limit` import in
  `agent.simulator`, `agent.mcp`, `agent.tui`, `game/navigation` or `agent/voyage`.
- [x] Keep local MCP tools `create`, `read`, `step`, `fork`, `run_plan`, `plan_voyage` and `close` outside the model
  attempt budget. Evidence: MCP tests run real local trials and voyage planning without a model transport.
- [x] Keep physics, environment, navigation and voyage compute outside the budget. The limiter is not called by
  local simulation or navigation code paths.
- [x] Local MCP/physics paths run without a model request or quota database. Evidence: `tests/test_mcp_navigation.py` and `tests/test_agent_mcp.py` passed; these modules do not import the limiter.

### 9.4 Focused evidence and current limits

Docs-time commands and results:

```bash
uv run python -m pytest tests/test_agent_rate_limit.py tests/test_rate_limit_acceptance.py -q
# 14 passed in 0.41 s, including lock timing and cross-process contention.

uv run python -m pytest tests/test_navigation.py tests/test_agent_voyage.py tests/test_live_voyage.py -q
# 23 passed in 4.01 s in this environment.
# Peer handoff reported 22 passed in 2.37 s; this rerun selected 23 tests.

cd web/game && node --test game.test.js
# 37 passed, 0 failed.
```

Rate-limit verification is offline: fake clocks and mocked HTTP verify accounting; the parent acceptance test also checks separate processes against the same SQLite database. No external model request was needed.

### 9.5 Parent handoff checks

- [x] Fix timestamp-after-lock admission and pass all 14 rate-limit tests.
- [ ] Run a real bounded Builder AI call only with the configured NIM key outside Git. Record attempts, retries and
  provider response. Do not infer live provider success from injected transports.
- [x] Final full suite: **519 passed in 27.99 s**; frontend: **37 passed**. This is the historical baseline only; the parent records the final count after code review.
- [x] Confirm the shared database path is `data/runs/agent-rate-limit.sqlite`, is ignored by Git, and contains only
  attempt timestamps.
- [x] Local MCP/physics paths do not import or call the limiter; MCP protocol, guarded stepping and voyage tests pass without a model quota database. The limiter is applied only before model HTTP attempts.
- [x] Director remains disabled; terminal tests pass with the shared transport compatibility imports.

## 10. Single Biscay annual-peak demonstration run (2026-10-30)

This is an operational run, separate from the planned Copilot/Tutor work in section 7.15.

- [x] Import the user archive `/Users/austin/Documents/Project/biscay-year.zip`: 39 monthly NetCDF files under `data/raw/biscay-year/`. ZIP reads verified member CRCs. Import and coverage receipts are `import.json` and `coverage.json` in that directory.
- [x] Scan all 13 wave files for the annual maximum: Hs 12.1999997273 m at 2026-10-22 21:00 UTC, longitude -10.75, latitude 48.5. Only this storm is processed.
- [x] Verify the existing wave/wind/current curated subsets for `biscay-20261022-21` match the imported archive exactly. Receipt: `data/runs/biscay-peak-demo/data-verification.json`.
- [x] Add `pipeline.storm_scenario --start-at-peak --out DIR` for a peak-time source in staging. Fix relative output-path handling. The normal daylight selection and published scenario directories remain unchanged.
- [x] Build `data/runs/biscay-peak-demo/source/scenario.json`; prove the 12.2 m peak and the initial position are inside the initial convex hull. Initial Hs is 11.3759 m, region threshold is 11 m, and the start passes the 5 NM coast guard. Receipt: `scene-check.json`.
- [x] Include actual ERA5 clouds and precipitation via Open-Meteo, with a retained raw `weather-source.json`. Coverage is 10800 s. Initial cloud cover is 100%, precipitation is 1.4 mm/h. Copies are present beside the working scenario and candidate review.
- [x] Fix the pandas datetime-resolution bug in both scenario and 3D scene weather exporters: explicitly normalize timestamps to nanoseconds before Unix-second interpolation. Two focused weather tests passed in 0.83 s. Do not substitute a repeated last-hour value for actual weather.
- [x] Run the real configured NVIDIA NIM Builder tool loop and initial full Sessions. The user then requested a fixed candidate review; autonomous AI finalization was intentionally stopped, so this is not evidence of an autonomous final-package success.
- [x] Complete all original 18 candidates without adding revisions or expanding to 64. Reuse completed trials with matching context and evidence fingerprints. The operator driver is `data/runs/biscay-peak-demo/run_candidates.py`; log is `candidate-review.log`.
- [x] Keep the temporary batch scope local to this run. Global defaults remain 64 candidate trials, 12 candidates per solver and minimum 3 final-qualified solutions.
- [x] Preserve all 18 actual full-Session results, commands, sampled tracks, damage and provenance. All 18 escaped and survived. Only candidates 9 and 13 satisfy the original <=1% TEU-loss gate (5/1200 TEU each, 2940 s). Other candidates remain available for manual review; their qualification labels are not changed.
- [x] Write a direction-labelled comparison to `data/runs/biscay-peak-demo/candidate-review/README.md` and `candidate-routes.json`. Multiple-leg directions and commanded speeds are explicit. Status is `awaiting_manual_selection`; no routes have been selected or published.
- [x] Record the explicit user selection: retain 9 and 13, plus the lowest-loss route in each other requested direction. Final IDs are 6, 9, 10, 12, 13, 14, 16 and 17. East 2/10 tie on loss; retain faster 10. Receipt: `scenarios/sole-20261022-21/approval.json`.
- [x] Set the explicit user budget to 75 minutes (4500 s), rebuild gameplay and retain all eight routes in `scenarios/sole-20261022-21/solutions.json`. This does not change global Builder defaults or the automatic 1% loss rule.
- [x] Complete final-budget reruns for 6, 9, 10, 12 and 13. Stop the remaining reruns immediately on user instruction. Routes 14, 16 and 17 retain original 120-minute-scenario full-trial evidence, explicitly labelled `final_validation: false`; their recorded escape times fit 75 minutes, without claiming new-budget rerun completion.
- [x] End construction with a user-approved, loadable local demonstration and preserved real weather, peak geometry, voyage, gameplay, validation, reports and eight reference routes. Existing scenarios were not overwritten. No additional simulation or external publication is requested.
- [x] Add scoped manual-retention exporter support and preserve actual losses and automatic qualification labels. 29 focused exporter/manual-approval checks passed in 0.70 s; no full-suite rerun.
