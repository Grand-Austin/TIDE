# Prototype implementation plan: rules through Director

Status: implementation authorized on 29 October 2026. Track verified progress in PLAN.md; this document specifies the implementation and acceptance criteria.

This plan implements PLAN stages 3 and 4. Product intent and confirmed choices are in [Product.md](Product.md). Keep the existing numerical toolkit and scenario pipeline. Exclude the graphical demo, Copilot, Builder and Tutor implementations, new historical scenarios, and professional cargo/lashing simulation.

## 1. Game behaviour and initial design defaults

The player controls target heading and speed in a continuous simulation. No turn duration is required. The motion model performs turns and acceleration; the interface always distinguishes orders from actual vessel motion. Game-time rate changes pacing only. Manual and mandatory pauses freeze simulation time, damage and information releases immediately through local code.

Use the existing three-hour historical window and fixed S175 design-waterline motion parameters. The cargo template represents a plausible fully loaded container ship. Cargo loss never changes those motion parameters in this prototype.

### Cargo

The current rule uses 64 five-box deck stacks, median capacity 0.5g, log
dispersion 0.35 and one continuous second of overload. Endpoint body angular
terms supply the 11.25 m effective-height load. Sorted capacities and interval
start ticks avoid per-step scans of every stack and NumPy temporary arrays.
Stack state is plain JSON; existing grade boundaries and mandatory pauses remain.
The builder and frozen Menorca gameplay now use these settings. Old cargo saves
are rejected by algorithm identity. Comparisons and limits are in
[CARGO_DAMAGE_STUDY.md](CARGO_DAMAGE_STUDY.md).

#### Archived initial cargo design and calibration

The following settings and results describe the former algorithm in
`archive/game/cargo.py`. They do not configure current gameplay.

- Start with 800 units: 400 twenty-foot and 400 forty-foot containers, totalling 1,200 TEU. Deck cargo has 160 of each size; hold cargo has 240 of each size. Hold cargo is protected from overboard loss in this model.
- Give each unit a stable ID, TEU size, location category, accumulated damage, seeded vulnerability threshold and irreversible overboard state. Use simple lower/middle/upper deck exposure categories; do not model individual lashings, stack collapse or cargo-to-cargo contact.
- Drive damage from calculated roll, pitch and lateral acceleration. The initial game demand is the magnitude of the effective transverse acceleration, including gravity projected into body axes, divided by gravity. Apply deck-category multipliers of 1.0, 1.15 and 1.3. These are game coefficients, not measured securing strengths.
- Initial damage law: integrate `max(0, demand / 0.30 - 1)^2 / 60` over simulation seconds. Assign unit failure thresholds uniformly in `[0.8, 1.2]` once from a dedicated cargo seed. Units cross their thresholds deterministically; there is no new accident draw at each checkpoint or model call. Keep constants in the versioned cargo configuration.
- Use the motion model's accepted integration steps, not output-record intervals, to integrate damage. Pause and rejected numerical steps accumulate nothing. Report simultaneous losses as one event containing unit IDs and counts.
- Display counts and TEU loss. Initial grades use lost TEU divided by the initial total: grade 0 for zero loss; grades 1–5 for `(0, 0.5%]`, `(0.5%, 1%]`, `(1%, 3%]`, `(3%, 10%]`, and `>10%`. Pause on a grade increase, without an additional first-loss trigger.
- Validate these initial coefficients against mild and severe response fixtures and available real-data runs. Mild voyages may have no loss. Do not lower thresholds merely to force a dramatic outcome. Publish the resulting configuration and limits before live play.
- Real-data calibration (29 October 2026). With 0.30, no heading lost cargo in any tested sea, so handling had no consequence:
  - Seas tested: Menorca at Hs 3.6 m, Ushant at Hs 6.1 m, and the storm peak off Ushant at Hs 8.2 m, Tp 11.5 s.
  - Trials ran 20 minutes at 3 and 1.5 m/s, using the game cargo seed 1.
  - Peak 99th-percentile demand at the Ushant peak: beam seas 0.21 g, bow-quartering seas 0.17 g, head and following seas 0.12–0.13 g.

  New scenarios use 0.14. Lost TEU in 20 minutes at the Ushant peak:

  | Heading | Lost TEU at 0.14 |
  | --- | --- |
  | Beam seas | 18 |
  | Any other heading or speed | 0 |
  | Every heading at Hs 6.1 m | 0 |

  For comparison, 0.12 loses 220 TEU in beam seas within 20 minutes, which leaves no room to recover in a three-hour mission. 0.10 also punishes bow-quartering seas, with 159 TEU.

  The frozen Menorca scenario keeps its validated 0.30. Revalidate 0.14 on full-length storm missions.

  These cargo trials predate the full-coupling and mean-wave-surge repair documented in `MOTION_VALIDATION.md`. They are historical calibration evidence; the current motion requires renewed cargo balance checks.

TopTier is a reference for interpreting motion-related hazards, not a direct loss-count formula. This version does not add a separate TopTier warning mechanic or claim that the current motion model reproduces nonlinear parametric roll.

### Escape region and evidence

- Build the region offline from the complete authored operating area and fixed historical wave fields. Initial construction uses `Hs >= 3 m` and a conservative outer convex envelope of the qualifying grid-cell footprints. This explicitly includes low-wave pockets inside the larger region. The threshold and envelope method are authored game rules.
- Freeze region polygons at one-minute simulation intervals; each polygon applies until the next timestamp. Validate that the initial point is inside, exterior valid-water exits exist, and representative executable strategies can escape. If those checks fail, revise the authored region and rerun validation before publication; never adjust the region during play to favour an outcome.
- Complete escape on the first accepted state outside the current region and inside the valid operating area. Leaving data coverage or entering missing/land cells is not escape. At the exact deadline, a valid escape takes precedence over timeout. Numerical/model validity failures return the last valid state and an explicit technical stop, not a fabricated sinking.
- Retain the old target point only as an offline reference strategy. Its existing arrival reports do not validate the new escape condition.
- Divide the authored area into a small fixed 3-by-3 set of report sectors. Publish game forecast bulletins at the start and each subsequent simulated hour, covering the next hour within the scenario window. Release only the current sector and its neighbours to the player and Director; moving into another sector may retrieve already-issued reports for that sector.
- Generate those bulletins offline from historical fields, using coarse ranges for wave height and wind plus wave direction/period and qualitative trends. Include issue time, validity, sector and the label `game_simulated_forecast`. Do not present them as contemporaneous AEMET evidence or expose raw future grids.
- Mark important updates in the published scenario, with a reason tied to the forecast change. Cargo-grade increases pause play. Director background attention selects delivery and discretionary pauses for issued reports; local mode and fallback use fixed delivery without weather pauses. Manual pause, mission completion, timeout and validity stops are handled locally. Consume each release/checkpoint once.

The numeric cargo and region settings above are initial implementation defaults. Their authoring validation is a required implementation step; they are not additional user-confirmed or professionally calibrated limits.

## 2. Algorithm and execution interfaces

Keep responsibilities within existing `game/simulation`, `game/rules` and `game/director` boundaries. Introduce shared types only where both algorithm implementations actually need them. Use explicit Python interfaces and a small registry of supported algorithm IDs; do not build dynamic plugin discovery or load arbitrary code from scenario JSON.

| Interface | Responsibility and contract |
| --- | --- |
| Motion backend | Initialize vessel state, accept target heading/speed, advance on a fixed numerical clock, and provide accepted response steps, position and stopping reasons. Adapt the existing `Simulation` without changing its numerical scheme. |
| Cargo algorithm | Initialize from the cargo template and seed; consume accepted response steps with SI units and elapsed time; return cargo state and loss/grade events. It must not import Director or provider code. |
| Local advance tool | Advance motion, cargo and mission rules consistently under the current approved command. Commit the last valid combined state and return visible state, metrics, events and the stop reason. |
| Information projection | Return only current local observations, player cargo status, applicable released bulletins and mission status. Exclude region geometry, vulnerability values, raw future fields and debug state. |
| Session storage | Save/load full numerical and cargo state, active order, released/consumed events, completed operation receipts, scenario/config hashes, seed and state revision. Refuse mismatched scenario or algorithm versions. |

Add an optional accepted-step callback to motion advancement so mission rules can stop at the appropriate integration tick. Preserve existing simulation-only callers. The callback operates on the local candidate game state; a failed step cannot commit cargo or event changes without the matching vessel state.

Maintain a single authoritative game state. Include wave phases/rates, held forces, fixed-clock tick, actuator controller integrals, exposure, stack capacities, unfinished overload intervals and consumed event IDs in snapshots. Use explicit JSON/array serialization with no pickle. Write the state revision and its operation receipts in the same atomic snapshot. Snapshot metadata links to the last complete log record so recovery can discard an incomplete tail without replaying a state mutation.

Keep the existing numerical scenario schema and version its new independently pinned gameplay artifact; update the sole scenario, loader, builder and affected fixtures together. Pin the cargo template, region schedule and report schedule in its manifest. Retain previous validation only as a labelled historical numerical baseline; produce a new report for the new definition.

## 3. Command-line runtime and Director

### Text interface and clock

Provide `uv run python -m game` as the full-screen terminal entrypoint and a separate scripted runner for deterministic testing. Both use the same local algorithms and rules. The default engine is local; `--engine director` opts into experimental orchestration. The old line/JSON interface and `--plain` entrypoint are removed.

Outside command input, `j/k` change the requested time rate and `p` pauses into a prefilled target heading/speed editor. `Tab` switches fields; `Enter` or `p` validates both targets and resumes. `/` opens a command field without stopping time. Commands include `heading <degrees>`, `speed <knots>`, `pause`, `resume`, `rate <multiplier>`, `status`, `reports`, `save`, `quit`, and `retry` after an Agent failure. Accept headings in `[0, 360)` and nonnegative speeds within the loaded vessel's supported command range. Convert knots to SI once at the interface. Show units explicitly.

Start paused with briefing and initial state visible. Targets persist until replaced. A new target issued while running first requests a local pause, then is validated and applied at a state boundary; the player explicitly resumes. The input reader and pause signal remain responsive during numerical work or a model request. A late model result cannot undo a later user pause or order.

Initial rates are 1x, 5x, 20x and 60x, plus an explicit fastest-possible mode for tests. Keep the existing fixed numerical timestep at every rate. When computation cannot sustain the requested rate, display the achieved rate; never skip physics steps or perform a catch-up burst after a pause.

Refresh simulation UTC/elapsed/remaining time, latitude/longitude, target and actual heading, target and actual speeds in knots, roll/pitch, local sea conditions and retained TEU percentage. The forecast panel shows only released reports and supports scrolling. Current Hs means significant wave height, not instantaneous surface elevation. Keep detailed response traces in the session log. No map, graphical renderer or Copilot is required.

### LangGraph boundary

Use a small LangGraph `StateGraph` with local SQLite checkpoints. Model the Director cycle as context preparation → constrained model/tool calls → inspect authoritative result → continue an authorized run, wait for player, or finish. Allow no arbitrary planner, filesystem tool, shell tool, subagent spawning or unrestricted reflection loop.

The graph coordinates progression through local tools. A long-running advancement tool follows the current player command until a local checkpoint, manual pause or terminal event; numerical steps are never graph nodes. No separate local controller duplicates the Director's gameplay choices. The clock/executor only carries out the authorized run and mandatory stops.

Graph state contains session ID, revision, allowed phase/tools, pending decision, released events and bounded conversation context. It does not contain the authoritative hidden game state. Rebuild model context from the current permitted projection rather than forwarding full simulation logs.

Expose a minimal tool set: load/resume the selected session, read visible status/reports, validate/apply the pending player order, advance under that order, and request the next player decision. The model cannot supply alternative manoeuvres, create forecasts, edit regions, change cargo parameters or choose a mission outcome. Tools validate the player action ID and expected state revision.

Every state-changing invocation has a stable operation ID. Repeated requests return the recorded result; stale revisions are rejected. Graph recovery must restore committed results rather than advance again. After an interrupted advance, recover its last committed state as paused and require a new player resume before further progression. Limit tool rounds to prevent a weak model from looping indefinitely.

Use LangGraph's graph/checkpoint packages without a general multi-Agent supervisor or hosted Agent service. Use a single compatible chat-completions transport for NIM and OpenRouter. Manage any required dependencies through `uv` and commit the lockfile during implementation.

### Model configuration and failures

- Keep `models.json` versioned, with separate provider definitions, named model profiles and role-to-profile bindings. A profile contains provider, model ID, temperature, top-p, output-token limit, timeout and tool-round limit. Provider-specific request options are explicit, not guessed by the Director.
- Default Director to NVIDIA NIM, endpoint `https://integrate.api.nvidia.com/v1`, model `nvidia/nemotron-3.5-lightning-30b-a3b`. Add an explicitly selected OpenRouter provider at `https://openrouter.ai/api/v1`; verify its selected model ID and tool support before enabling that profile. Do not assume identical catalog IDs across providers.
- Initial bounded-call defaults: temperature 0.2, top-p 0.95, 1,024 output tokens, 30-second request timeout and at most four model/tool rounds per decision. Verify provider support and tool-call reliability rather than treating low temperature as a safety boundary.
- Provide an ignored `config/api_keys.local.json` and a committed placeholder example. Read provider credentials locally; never include them in prompts, logs, snapshots, errors or documentation. The key supplied in conversation is reserved for implementation-stage testing and is not stored in this plan.
- Retry transient transport failures at most twice with backoff and provider retry guidance. Return validation errors to the model only within the tool-round budget. Do not retry a state mutation as a fresh operation. On persistent failure, freeze game time and offer retry or save/exit. Provider selection remains manual. On Director failure, use per-step local orchestration without repeating committed progression or overriding a player cancellation; `--engine local` bypasses model initialization entirely.
- Persist model identity/settings and released tool inputs/results for audit. Store game snapshots separately from graph checkpoints with shared session/revision references. Resume verifies consistency and begins paused.

## 4. Implementation order and acceptance

1. **Contracts and cargo.** Add the accepted-response interface and standalone cargo algorithm/template. Test calm zero loss, mild zero loss, severe loss, exposure duration, protected hold cargo, reproducible vulnerability, no repeated loss, exact grade boundaries and split-run equivalence.
2. **Scenario rules and information.** Build the frozen region/report schedules and update the package. Test external escape, internal low-wave pockets, changing boundaries, timeout, simultaneous outcomes, data/land stops, sector selection and release-time filtering. Confirm at least one executable escape for seeds 1, 2 and 3 and retain unsuccessful strategies in the report.
3. **Local mission execution.** Combine motion and rules without an Agent. Test pause before/during advancement, atomic state changes, grade/report checkpoints, deduplication, persistent commands, target-versus-actual motion and pacing invariance. Run a complete scripted mission using committed environmental data.
4. **Storage and terminal controls.** Test exact continuation across save/exit/load, missing or mismatched artifacts, interrupted snapshot/log writes, stdin responsiveness, cancellation during model latency, and achieved-versus-requested time rates. Exercise the text interface without any graphical dependency.
5. **Director integration.** Test the LangGraph flow with scripted model responses: valid tools, unknown tools, malformed arguments, unauthorized manoeuvres, stale/duplicate operations, exceeded round limits, timeout/retry, graph recovery and hidden-information exclusion. Confirm every forced decision remains paused until explicit player resume.
6. **Live verification and documentation.** After implementation is authorized, use the supplied NIM credential for a bounded tool-call smoke test and a complete command-line mission. Record calls, latency, settings and game outcome; do not claim live success from fixtures. Run the existing Python suite and relevant new tests. Update PLAN checkboxes only for demonstrated outcomes and document any remaining provider/data limitations.

Do not change the physical model to manufacture cargo loss. Exercise severe cargo behaviour with declared test inputs when the historical scenario produces only mild response. Real-data acceptance can legitimately finish with zero cargo loss.

## 5. Sources and limits

- [LangGraph overview](https://reference.langchain.com/python/langgraph/overview): graph orchestration and local checkpoint support.
- [LangGraph execution and recovery semantics](https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/functional-api.mdx): interrupted work may execute again; game operations need independent deduplication.
- [NVIDIA model card](https://build.nvidia.com/nvidia/nemotron-3.5-lightning-30b-a3b/modelcard): intended model identity; endpoint behaviour still needs live verification.
- [MARIN TopTier](https://www.marin.nl/en/jips/toptier): motion-risk references, not calibration of this game cargo algorithm.

Planning did not run a new full-window simulation, install LangGraph, call a model API or validate the supplied credential. Existing numerical reports remain evidence for the current motion toolkit only.

## Authorized additions during implementation

- The default `--engine local` provides fully local interactive orchestration; Director is opt-in through `--engine director`. Director failures fall back for the current step, respecting committed progress and player cancellation.
- Background Director attention chooses delivery and pause timing from already-issued report candidates. Simulation continues during assessment. One review runs at a time; initial review/cooldown defaults are 300 simulation seconds and maximum decision age is 600 seconds.
- Harness instructions and native schemas live in `game/director/harness/`; they are loaded at runtime. Local validation enforces permissions independently of prompt compliance.
