# Agent package

`agent/` contains TIDE's role-scoped Agent harness, simulator adapter, MCP server, terminal host and
Builder. `game/` remains the authoritative runtime for environment sampling, motion, cargo, ship loss,
mission rules and Session state.

## 1. Implementation status

| Item | Status | Evidence or limit |
| --- | --- | --- |
| Shared LangGraph harness | **Implemented** | `agent/harness.py`; `tests/test_agent_harness.py` |
| Independent and live simulator service | **Implemented** | `agent/simulator.py`; `tests/test_agent_simulator.py` |
| MCP stdio server | **Implemented** | `agent/mcp.py`; `tests/test_agent_mcp.py` |
| Original TUI host with Unix socket | **Implemented** | `agent/tui.py`; MCP socket tests |
| Builder local mode | **Implemented** | `agent/builder.py`, `agent/routing.py`; Builder tests |
| Builder AI mode with injected transport | **Implemented and tested** | `tests/test_agent_builder.py::test_ai_mode_can_inspect_and_revise_without_external_calls` |
| Builder AI mode with a live provider | **Not live-verified** | No real provider run is recorded. Tests inject the transport. |
| Copilot | **Planned** | L1 reads released forecasts; L2 temporarily reads solutions and returns a red reference route. Separate, explicitly enabled route-following control may issue bounded heading/speed orders with immediate human override. See PLAN 7.15. |
| Solutions package | **Implemented; targeted checks passed** | One readable `package/solutions.json` with all distinct qualified routes. 57 Builder/budget/context checks and 12 exporter checks passed. |
| Tutor | **Planned** | No implementation. It changes no rules, score, cargo total or outcome. |
| Director | **Retained, disabled** | Historical stage 4 work. Entrypoints remain disabled. |
| Batch, cancel RPC, revision RPC | **Unimplemented** | Current bounds are step duration, stale-revision checks, operation receipts, close, human pause and process shutdown. |
| Qualified or published Biscay package | **Not available** | No current package has a qualified route. Publication is human-only. |
| Shared model transport and global attempt budget | **Implemented and verified** | `agent/transport.py`, `agent.rate_limit.py`; 14 rate tests pass, including lock timing and cross-process admission. |
| Coastal guard in TUI and MCP | **Core implemented; integration pending** | Fixed 5 nm guard, `game/navigation.py`, offline Natural Earth 10m land v5.1.1. Navigation tests pass; MCP/Builder peers active. |
| Authored planned voyage | **Core implemented; integration pending** | `agent/voyage.py` replaces the Le Havre–Halifax default. Voyage tests and the real Biscay artifact exist. |
| 3D coastline data, rendering or policy | **Explicitly out of scope** | The 3D page displays backend voyage context only. No navigation import or guard exists in `web/game`. |

The Builder is a scenario experimenter. It writes a review package. It does not approve or publish that
package. A `review_ready` label means only that local checks passed and a human may review it.

## 2. Design constraints

1. Use one small role-scoped LangGraph harness. Do not build a generic multi-Agent scheduler.
2. Do not create one service or model for every role. Roles are profiles and allowlists in the shared harness.
3. Keep physics, cargo, ship loss, mission rules and authoritative state in `game/`. An Agent requests a
   capability; it does not commit a calculated physical result.
4. Keep the original curses terminal TUI as the interactive surface. MCP controls a local `Session`, not a
   web page or animation.
5. Keep the browser outside the checkpoint and scoring path. This package adds no browser endpoint.
6. Keep Director disabled. Do not add a speculative future Director role.

## 3. Components

| Component | Responsibility |
| --- | --- |
| `agent.harness` | Loads one role profile, enforces a tool allowlist and JSON schemas, runs a bounded plan/tool/revision loop, writes SQLite checkpoints and a credential-free audit stream. |
| `agent.builder` | Runs the controlled scenario experiment: search, candidate records, full Sessions, local revisions, final budget, final revalidation, solutions and package writing. |
| `agent.routing` | Produces deterministic SIMROUTE-style and VISIR-2-style command candidates. Results are estimates only. |
| `agent.solutions` | Builds `package/solutions.json` from the distinct qualified final trials: summary, reading guide, solver, timed orders, actual sampled track, escape time, TEU loss, ship result, limits and provenance. |
| `agent.simulator` | Opens real Sessions, owns independent trials, attaches to a live Session, forks complete paused state, applies bounded steps and runs timed plans. |
| `agent.mcp` | Official MCP stdio server with `create`, `read`, `step`, `fork`, `run_plan`, `plan_voyage` and `close`. |
| `agent.tui` | Hosts the actual terminal screen and exposes the same Session on a private Unix socket. It uses the shared coast guard. |
| `agent.transport` | Shared NIM model client. Reserves one global slot before every HTTP attempt, retries at most three total attempts and raises sanitized errors. |
| `agent.rate_limit` | SQLite-backed rolling-window limiter: 30 actual attempts per 60 s across roles, clients, threads and processes. |
| `agent.voyage` | Authored-voyage builder from the sea-route network and canonical `web/game/ports.json`. It reports the actual start, nearest network point, snap offset and branch. |
| `pipeline.navigation_data` | Preparation and verification of the offline pinned Natural Earth 10m land v5.1.1 package under `data/navigation`, plus initial-position validation. |
| `game.navigation` | Authoritative fixed 5 nm coast guard. It checks candidate edges and actual local execution, and fails closed on bad data. |
| `web/game/ports.json` | Canonical shared port list for voyage authoring and display. It is data, not a second port list. |
| `game/` | Authoritative environment, motion, cargo, ship, rules and Session implementation. |

## 4. Commands

Run commands from the repository root.

### 4.1 Builder, local deterministic mode

```bash
uv sync --locked
uv run python -m agent build \
  --scenario scenarios/biscay-20260123-06/scenario.json \
  --out data/runs/builder-biscay-20260123-06 \
  --mode local
```

Local mode prints to stderr that no model call and no API credential is used. It runs the deterministic
search, local revisions and full Sessions. It never reads `models.json` or the key file for a model call.

Actual options:

```text
--scenario PATH             source scenario file
--out DIR                   run directory; package is <out>/package
--mode {local,ai}           default local
--models PATH               default models.json
--keys PATH                 default config/api_keys.local.json
--max-trials INT            default 64 newly executed full Sessions
--max-candidates INT        default 12 candidates per search solver
--expert-limit INT          default 3 local expert proposals
--max-revisions INT         default 2 revision depths
--min-solutions INT         default 3 distinct qualified routes for a non-pending package
--resume                    resume an existing run directory
```

`--resume` restores `builder-state.json`. Local and AI runs both support it, although the CLI help text
mentions only AI checkpoints. Resume rejects a changed input, horizon, Builder configuration, evidence
fingerprint or mode. A terminal package returns its stored result without repeating trial cost.

CLI exit codes:

| Code | Result |
| ---: | --- |
| `0` | `review_ready`; review is still required |
| `3` | `pending`; a reason is recorded |
| `2` | Error |

### 4.2 Builder, controlled AI mode

```bash
uv run python -m agent build \
  --scenario scenarios/biscay-20260123-06/scenario.json \
  --out data/runs/builder-ai-biscay-20260123-06 \
  --mode ai \
  --models models.json \
  --keys config/api_keys.local.json
```

AI mode uses the `builder` role in `models.json`. The model may call only:

```text
record_plan
list_candidates
run_trial
submit_revision
inspect_trial
finalize_build
```

The model cannot edit fixed inputs, change qualification, approve a package or write outside the run
directory. Local code runs both required searches before the AI loop, executes trials, computes the final
budget, performs final revalidation and decides `review_ready` or `pending`. A model or tool failure becomes
`pending`; it is not a silent local completion.

The profile points to a provider and model. Active roles use NVIDIA NIM and the shared ignored key file
`config/api_keys.local.json`. The Builder profile is `builder_nim` with model
`nvidia/nemotron-3.5-lightning-30b-a3b`. The example file is `config/api_keys.example.json`. Do not put
credentials in `models.json`, logs, audit records or documentation.

Every model HTTP attempt, including a retry, reserves a slot in the global 30-attempts-per-rolling-60-seconds
budget. Section 13 gives the exact transport and SQLite behaviour. Local MCP physics and navigation do not use
that budget.

### 4.3 Terminal host and live MCP

Start the actual terminal screen with a private socket:

```bash
uv run python -m agent.tui \
  --scenario scenarios/menorca-20260909/scenario.json \
  --session data/runs/agent-tui \
  --socket /private/tmp/tide-sim.sock
```

`agent.tui` restores automatically when `--session/state.json` exists. It has no separate resume flag. The
socket must not already exist; the server rejects and preserves an existing path. The bound socket uses mode
`0600`. Closing the TUI saves the Session.

Attach MCP to that same live Session:

```bash
uv run python -m agent.mcp --socket /private/tmp/tide-sim.sock
```

The live trial id is exactly `live`. MCP cannot close the live trial; the terminal host owns it. A live step
requires a paused Session, a current revision and a nonempty operation id.

Start independent headless trials instead:

```bash
uv run python -m agent.mcp --workspace data/runs/agent-sim
```

Independent mode creates paused trials below the workspace. It does not move the live Session.

### 4.4 MCP stdio client configuration

This example matches the actual stdio server. It does not modify a user's global configuration.

```json
{
  "mcpServers": {
    "tide-simulator": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/TIDE",
        "run",
        "python",
        "-m",
        "agent.mcp",
        "--workspace",
        "/absolute/path/to/TIDE/data/runs/agent-sim"
      ]
    }
  }
}
```

For attach mode, replace the final two arguments with `"--socket", "/private/tmp/tide-sim.sock"`.

### 4.5 Reference baseline commands

These commands run reference material outside the TIDE source tree. They need network access on the first
run and pinned artifacts. They do not use TIDE's simulator or a published scenario.

```bash
uv run python -m agent.references.reproduce simroute --cache /private/tmp/tide-reference-cache --offline
uv run python -m agent.references.reproduce visir2 \
  --cache /private/tmp/tide-reference-cache --offline \
  --result-json agent/references/visir2-run.json
```

## 5. Builder fixed inputs and permitted calibration

The designer fixes:

- Initial time, position, heading and speed.
- Historical environment, operating area and hidden adverse-weather region.
- Vessel, physics, cargo-damage and ship-loss model.
- Random seed.
- Escape condition and mission rules.

The Builder may change only these quantities inside its output copy:

- Mission time budget.
- Mission end timestamp.
- Derived gameplay and report schedules.

It hashes the source scenario, source gameplay, every environment layer and the vessel files. It rejects an
output path that overlaps an input, a scenario directory, published `scenarios/` or a symlink to them. It
writes `work/` and `package/` copies only. A changed frozen input makes the package fail.

## 6. Candidate search, estimates and qualification

Both search methods always run:

1. `simroute:astar_aertssen`: A* with an Aertssen speed-loss penalty, no current and midpoint timing.
2. `visir2:td_dijkstra_aertssen_current`: time-dependent Dijkstra with the VISIR-2 current composition,
   Aertssen speed-loss proxy, and separate heading and ground course.

Both methods are TIDE adaptations. They are not upstream benchmark executions. Their `estimated_s` values
order and time candidates only. A search result is never proof of escape, survival or cargo retention.

A full `SimulatorService.run_plan` Session is the only qualification proof. A qualified route must satisfy
all of these:

1. Outcome is exactly `escaped`.
2. `ship_lost` is false.
3. Result metrics are finite and consistent.
4. Elapsed time is within the budget.
5. Lost TEU is at most 1% of total TEU, inclusive.

The Builder keeps game scoring, judgement and star rules unchanged. It adds no zero-loss rule and no 95%
deck-retention rule. A faster candidate that fails the gate does not qualify. The fastest qualified route
sets the final budget, ordered by actual elapsed time, then loss, then candidate name. Every other distinct
qualified route is still retained in the solutions file.

Both required searches are called even when a route qualifies. A search error makes the package `pending`.

## 7. Coverage, search horizon and final budget

The search horizon is the source scenario's mission budget. The Builder checks all environment layers over
the full window and the authored bounding box before trials.

If source coverage is shorter than the input budget, the Builder retries with the largest whole-minute
horizon that fits and records `adjusted_from_s`. If no adequate horizon exists, the result is `pending`
before any trial.

Let `T` be the fastest qualified actual full-Session time in seconds. The final budget is:

```text
budget_s = max(900, 900 * ceil(1.2 * T / 900))
```

| Actual `T` | Final budget |
| ---: | ---: |
| 3000 s (50 min) | 3600 s (60 min) |
| 4200 s (70 min) | 5400 s (90 min) |

The Builder checks coverage for the final budget, regenerates `package/scenario.json` and
`package/gameplay.json`, and runs a fresh final full Session against the package path. An old package is
never validated against a changed budget or schedule. Final revalidation failure yields `pending`.

All distinct qualified routes are kept, not only the fastest. Every stored slower alternative must also pass
its complete final-package rerun inside the final budget; otherwise it is omitted and reported. The default
minimum is three routes. If fewer than the minimum pass, the package is `pending` and keeps the valid routes
already found.

## 8. Baseline provenance

### 8.1 SIMROUTE

- Reference case: Palma de Mallorca to Barcelona, 2020-01-20 09:00 UTC, 16.1 kn, 1.5 nm grid,
  `WEN_form=1` (Bowditch).
- Pinned snapshot commit: `7d9a3381b8078013f63c3007c10f60dcf4f19f93`.
- Licence: no licence file in the inspected repository. No upstream code is copied.
- Status: `independent_reproduction`.
- Measured: 9.402636822 h / 151.382453 nm without waves; 14.973725553 h / 161.557640 nm with waves;
  16.928160302 h / 151.382453 nm for minimum distance with waves.
- Published: 9.40 h / 151.38 nm; 14.97 h / 161.56 nm; 16.93 h / 151.38 nm.

The TIDE search uses `WEN_form=2` (Aertssen) because Bowditch gives sub-knot speeds for the S175 in storm
seas. The reference exercise keeps faithful Bowditch.

### 8.2 VISIR-2

- Paper: `https://doi.org/10.5194/gmd-17-4355-2024`.
- Code: Zenodo record `10960842`, GPL-3.0-or-later. No VISIR-2 code is copied into TIDE.
- Official case run: Techy Example 3, dynamic analytic current.
- Measured: `L/L0 = 1.017587668235335`, `T/T0 = 1.0568394582308234`.
- Reference: `L/L0 = 1.00480754`, `T/T0 = 1.03038814`.
- Deltas: `1.271898%` and `2.567122%` against a declared 3% tolerance. Result: PASSED.
- Status: `official_analytic_current_reproduced`.
- Full Alboran numerical benchmark: **out of scope by user decision (2026-10-30)**. TIDE uses the VISIR-2-inspired search and full-Session route validation.

The separate TIDE `visir2` search is an adaptation, not an execution of the upstream model.

## 9. Package artifacts and fingerprints

The run directory has this shape:

```text
<out>/
  run.json                  run status, mode, thread id and package path
  builder-state.json        resumable source of Builder records
  trial-cache.json          completed full-Session records by evidence+orders key
  builder.sqlite            LangGraph checkpoints in AI mode
  audit.jsonl               credential-free harness audit in AI mode
  work/
    scenario.json           search-horizon work copy
    gameplay.json
  trials/<trial_id>/        real Session state and event log
  package/
    scenario.json           final budget and final gameplay reference
    gameplay.json           final region and report schedule
    validation.json         strategies, metadata and checksums
    solutions.json          one readable file with every distinct qualified route
    build-report.json       Builder report, checks, trials and limitations
    weather.json            optional display copy, only inside source coverage
```

`validation.json` contains:

```json
{
  "version": 1,
  "scenario": "<id>",
  "definition_sha256": "<sha256>",
  "gameplay_sha256": "<sha256>",
  "passed": false,
  "reference": null,
  "status": "pending",
  "strategies": [
    {
      "name": "<candidate>",
      "solver": "<solver>",
      "orders": [{"t": 0.0, "heading_deg": 90.0, "speed_knots": 9.0}],
      "outcome": "escaped",
      "elapsed_s": 1234.5,
      "lost_teu": 12,
      "total_teu": 1200,
      "grade": 3,
      "ship_lost": false,
      "qualified": true,
      "metrics_valid": true,
      "reason": "qualified",
      "session": "<trial directory>",
      "trial_id": "<trial id>",
      "evidence_fingerprint": "<sha256>",
      "final_validation": false
    }
  ],
  "metadata": {
    "builder": {
      "approval": "none",
      "human_review_required": true,
      "published": false,
      "pending_reasons": [],
      "qualification_rule": "escape in a full session, keep the ship afloat, and lose at most 1 % of the initial TEU",
      "search": {},
      "search_horizon_s": 7200,
      "budget": {},
      "coverage": {},
      "final_coverage": {},
      "evidence_fingerprint": {}
    }
  }
}
```

`evidence_fingerprint` is a stable digest over:

- The full scenario definition.
- The gameplay checksum.
- Every environment layer: declared and actual SHA-256, with a match flag.
- Vessel `vessel.npz` and `provenance.json`, plus declared vessel source identity.
- SHA-256 of every implementation file in `agent.models.ALGORITHM_FILES`, including the `game/` simulation,
  Session, cargo, ship and mission files and the Agent routing, simulator, Builder and harness files.

The fingerprint is the authority for whether evidence is current. A report with a different fingerprint is
stale, even if its text still looks plausible. Do not copy a fingerprint from documentation.

For handoff identification only, an earlier documentation snapshot recorded these source hashes. They are
historical and are not current assertions:

| File | SHA-256 |
| --- | --- |
| `agent/builder.py` | `9093b1c40f0ebe090e5aa554f990341aa8297351f3179c886232b1b6ca33b487` |
| `agent/harness.py` | `a0928ddfac30ab517e0aaf1cb02f164029d477450878eb56648511199131d136` |
| `agent/mcp.py` | `675dd50e090c4e148c859507f5a52eae2c27ee6fa0338c5e0b814fe7ad16e048` |
| `agent/models.py` | `61a3dcc34a181abd5e24378d45ee8de642e74e174b4cb0908114e1ab40f3f0d4` |
| `agent/rate_limit.py` | `2ba51fc1b11e4636c152c05f4175a2156d40c8c7655f04d433ad84f8003b218e` |
| `agent/routing.py` | `cf1fccc6135e21e3cf913dea03bf43f40fafc1fe303a7cd7a313c2dc9d3a16e3` |
| `agent/simulator.py` | `0e43ae78a95831068dee37e94b3ad2998d5442a1ced2a800f88745ed5b6b1453` |
| `agent/transport.py` | `5202b67a3045a0c2da20a10405f94cf036b7bde2a620f017fa8d0759ad5b184b` |
| `agent/tui.py` | `073e054e1556482641cb0278fdc81c3994b2a2abc40100aa41e988c4db16ecce` |
| `agent/voyage.py` | `7906dac5723542fc5a731227134bd9d82cf2dea776caa518781eee3625a5eb9d` |
| `agent/builder_harness/tools.json` | `9c1ea5b2a02f4958dafffe9e92434ed970fe6542cdc2a318eaa2c1084ec955b9` |
| `game/navigation.py` | `43bde25d09b09a5f9997ae223df6dc710d480993877d41565ed0a4210eab361b` |
| `pipeline/navigation_data.py` | `c7253876a6749eae418672bc15b192c08d7a6dd01a726f3ebda83abb209bcc3c` |

Use the fingerprint inside a current artifact, not this table, for a new run. The current artifact fingerprint is authoritative.

## 10. Recovery and idempotency: actual limits

1. `builder-state.json` stores candidates, revision depth, trial records, search metadata, coverage, final
   validation, pending reasons and context. Resume rejects a changed context.
2. The trial cache uses a digest over the evidence fingerprint and the full order list. A cache hit does not
   increment the executed-trial count. There is no focused cache-restart test; treat it as an optimization.
3. AI checkpoints live in `builder.sqlite`. The harness restores a thread without repeating a completed
   costly tool. A completed checkpoint returns its stored result.
4. `step` operation receipts live in the simulator process only. A restarted process cannot recognize an
   operation id from an earlier process. Do not use receipts as durable transaction records.
5. There is no output-directory lock. Do not run two Builders in the same `<out>` or two services in the same
   trial workspace.
6. The Builder can resume only from its own mode and context. It cannot adopt a changed scenario, budget
   horizon, algorithm, vessel, environment or gameplay artifact.
7. `run_plan` starts a new Session. It is deterministic for the same scenario, seed and orders, but it is not
   a substitution for the final package revalidation.

## 11. Simulator, TUI and MCP behavior

### 11.1 Step

`step(trial_id, heading_deg, speed_knots, duration_s, expected_revision, operation_id)`:

- Requires a finite heading in `[0, 360)`, nonnegative speed, duration in `(0, 900]` seconds and an exact
  multiple of the simulation step.
- Rejects a stale `expected_revision` and a nonempty operation id reused with different arguments.
- Returns the stored commit for a duplicate operation id in the same process.
- Refuses to move a sailing live Session. Pause it first.
- Advances in about one-second chunks, releases the Session lock between chunks and stops if the player
  pauses, changes the order or reaches an outcome.
- Leaves the trial paused, unless a newer human epoch already owns it.

### 11.2 Fork

`fork(trial_id)` saves the source Session, copies its `state.json` and `events.jsonl` into a new directory,
reopens it in restore mode and keeps it isolated. The fork preserves:

- Seed and revision.
- Motion state and wave phase, including previous wave rate.
- Controller state and exposure.
- Cargo and ship state.
- Consumed event set and active order.

Later steps on one trial do not change the other. Operation receipts are not copied.

### 11.3 MCP tools

| Tool | Arguments | Result |
| --- | --- | --- |
| `create` | `scenario_path` | New paused independent trial with `trial_id` and view |
| `read` | `trial_id` | Authoritative Session view and state fingerprint |
| `step` | `trial_id`, `heading_deg`, `speed_knots`, `duration_s`, `expected_revision`, `operation_id` | New view or stored duplicate commit |
| `fork` | `trial_id` | New isolated paused trial |
| `run_plan` | `scenario_path`, `orders` | Outcome, metrics, session path and view |
| `plan_voyage` | `scenario_path` | Read-only authored voyage from the fixed scenario initial state; starts no trial |
| `close` | `trial_id` | Closes one non-live trial |

`trial_id` is `live` only in attach mode. `close("live")` is rejected. Scenario paths must remain inside the
project root. RPC request and response lines are limited to 1,000,000 bytes.

### 11.4 Cancellation and revision boundaries

There is no batch tool, no cancel RPC and no MCP command-revision RPC. Use these actual controls instead:

- A human pause or order change stops an in-flight step.
- `close` releases one independent trial.
- Closing the TUI or service releases live or owned trials.
- Finite step duration, trial count, harness rounds and tool-call caps bound compute.
- `expected_revision` rejects a stale mutation.
- Builder route revisions create local candidates. They are not MCP command revisions.

### 11.5 Security boundaries

- Project path containment applies to scenarios and trial directories.
- The live Unix socket is private to the user (`0600`) and an existing path is not overwritten.
- MCP and TUI have no browser checkpoint or score storage.
- No API key enters the audit stream.
- The model cannot approve, publish, edit fixed inputs or bypass local qualification.

## 12. Solutions package and planned Copilot

The Builder writes one readable `package/solutions.json`. It stores solution routes, not graded hints. The
L1/L2/L3 Hint feature, `hints.json`, `hints.md`, Builder replay and branch expansion, the whole-area coverage
target, the maximum stored-solution cap and `--hint-branch-limit` are removed by user decision. The detailed
contract is [../docs/SOLUTIONS.md](../docs/SOLUTIONS.md).

The file starts with a short purpose and reading guide. It then gives the fixed initial conditions, the final
budget, the source fingerprint and the minimum and found counts. Solutions are sorted by actual elapsed time and
then by TEU loss. Each solution must include:

1. A factual summary and the source solver.
2. All applied timed heading and speed orders.
3. The actual sampled track when available. Missing intermediate samples are labelled endpoints-only; the
   exporter must not invent a straight-line successful path.
4. The actual escape time, TEU loss and ship result.
5. Its limits and the full-trial provenance. Bulky simulator logs stay in the trial directories.

Every stored route was discovered in the finite compute budget and revalidated against the final scenario. A
route that fails the complete final-package rerun is omitted and reported. Different names for identical timed
orders do not count as different routes. The file does not promise that all possible routes are enumerated.

The default minimum is three distinct qualified routes, set by `--min-solutions`. There is no maximum stored
count. If fewer than the minimum pass, the package is `pending` and keeps the valid routes that were found. The
default compute budget is 64 full Sessions and 12 candidates per search solver, with three expert proposals and
two revision depths. `--max-trials` and `--max-candidates` can raise the budget. Compute stays finite and
configurable. Local search remains deterministic under the same configuration.

Let `T` be the fastest qualified actual full-Session time. The final budget is
`max(900, 900 * ceil(1.2 * T / 900))`. Every stored slower alternative must also pass a complete final-package
rerun inside that final budget. A failed alternative is omitted and reported.

The planned Copilot L1 reads complete released forecasts and visible state without solutions access. L2
temporarily reads this file for one request, selects a suitable reference route and returns a red map line
with a short insight. Both modes advise only. A separate route-following mode requires a drawn route,
preview and explicit control grant before bounded heading/speed orders. Human pause or orders override it.
See [PLAN 7.15](../PLAN.md#715-planned-copilot-and-tutor-scope-documentation-only-handoff).
Original-start validation does not guarantee escape from a different current state. The Tutor will read
the final log and show a two-sentence settlement evaluation. Both roles remain planned. Raw future realized
grids and the full solutions file must not be injected into every learner conversation.

Offline `run_plan` runs the real simulator without wall-clock pacing or animation. It keeps the physical
integration step. The live TUI already supports maximum game speed (`rate=0`).

## 13. Shared model access and global attempt budget

### 13.1 Configuration and shared path

- All application Agents use `config/api_keys.local.json` and NVIDIA NIM. The active Builder role is
  `builder_nim` with model `nvidia/nemotron-3.5-lightning-30b-a3b`.
- `agent.transport.ChatClient` is the shared model transport. `game/director/models.py` re-exports the same
  `ChatClient`, `ModelError` and `load_profile`, but Director entrypoints remain disabled.
- The shared transport has no automatic provider fallback. Copilot and Tutor are planned and have no active
  model transport.

### 13.2 Global attempt policy

- At most 30 actual model HTTP attempts may start in any rolling 60 seconds.
- The budget is shared across every Agent role, client, thread and process in the project.
- The fixed store is `data/runs/agent-rate-limit.sqlite`. It is ignored by Git and stores attempt timestamps only.
- Profiles, tool arguments and command-line options cannot raise 30/60 or choose another database.
- Every HTTP attempt, including each retry, consumes one slot. A model call makes at most three attempts.
- Network errors retry after 1 s and 2 s. HTTP 429 and 5xx use a bounded `Retry-After` value or the same
  backoff. Other HTTP errors and malformed responses fail with sanitized errors.
- If shared limiter state is unavailable, the client fails closed before HTTP.

### 13.3 Local execution is not rate-limited

MCP tools, physics, navigation, route search and voyage planning are local operations. They do not call the
limiter and do not consume the model attempt budget. The model budget applies only to outgoing model HTTP
attempts.

### 13.4 Current evidence and limits

`tests/test_agent_rate_limit.py` has 12 passing focused tests. They cover the 31st-attempt wait, rolling window,
retry consumption, shared clients/threads/profiles, fixed NIM Builder profile, fail-closed limiter failure and
compatibility re-export.

The timestamp-after-lock acceptance test now passes. All 14 rate-limit tests pass, including separate-process admission. The 519-test Python suite and 37 frontend tests are the historical baseline for earlier work; they do not verify the solutions-package changes. The parent records the final count after code review.

Local request receipts remain process-local. The rate-limit database is not an audit log. A live Builder AI
provider run is still unverified; no real provider call is recorded.

## 14. Coastal guard and authored voyage

This scope prioritizes the original TUI and MCP. The 3D page must not contain coastline data, coastline
rendering or navigation policy.

### 14.1 Fixed coast guard

- The local Session uses one fixed 5 nautical mile land buffer. It is not a player, Agent or scenario tuning
  value.
- `game/navigation.py` is the shared authoritative guard. Candidate route edges and actual local execution
  must both call it.
- The land source is offline Natural Earth 10m land v5.1.1, pinned under `data/navigation` with version,
  provenance, licence and SHA-256 checksums. The game must not fetch land at runtime.
- Missing, corrupt, unreadable or empty navigation data fails closed. Do not fall back to open water.
- A candidate edge that touches or crosses the buffer is rejected before a trial. Actual execution stops or
  rejects before committing an unsafe position.
- The committed state is not relocated. An unsafe initial state is a Builder `pending` result or a clear TUI
  or MCP startup error. The start stays unchanged.
- This scope changes no environment, physics, cargo, ship-loss or scoring source.

### 14.2 Scenario-authored voyage

- `agent/voyage.py` replaces the Le Havre–Halifax default with a scenario-authored voyage.
- The voyage uses the sea-route network and the canonical shared port list in `web/game/ports.json`.
- The actual scenario start is preserved. The builder snaps to the nearest network point and reports the snap
  offset in nautical miles.
- The route is a weighted shortest path. Distinct opposite branches remain visible and are oriented by the
  initial heading.
- The result is labelled plausible authored context, not a historical log or observed track.
- Port approach is not an escape condition. It never changes escape, survival, TEU retention, score,
  judgement or stars.
- The coast guard and voyage display are separate. A voyage line near a port does not weaken the guard.

### 14.3 Propagation boundary

- The backend supplies a voyage view to the original TUI and the 3D page.
- The 3D page may display backend voyage context only. It must not load coastline geometry, render a coast
  layer, calculate a buffer or enforce a safety rule.
- A missing voyage must not block play or invent a route.
- The TUI and MCP remain authoritative for local play and safety.

The core navigation and voyage code now exists. Peer handoff reported 22 navigation/voyage/live Python tests
passed in 2.37 s; a docs-time rerun passed 23 in 4.01 s. Node reported 37 passed; a docs-time
`node --test web/game/game.test.js` also passed 37. A real TUI Menorca short play remained outside the 5 nm
guard for 70 s. The data verify-only check succeeded with SHA-256
`e547d749445eaa0964aba76738090ec88f5e63c4585122170f98c67a7ea922dc`. The actual Biscay 20260123 voyage is
Brest to Vigo, 612.34 nm, 11.37 nm snap offset and 22 authored points, with no relocation.

MCP and Builder integration peers are still active. Their checkboxes stay with the parent. No `web/game` file
imports `game.navigation`, Natural Earth or `data/navigation`; the 3D side has display context only.

## 15. Verification records

This documentation pass ran no tests. It makes no new test claim. The parent records the targeted test count
after code review. The records below are from earlier passes and are historical.

Historical focused Agent command:

```bash
UV_CACHE_DIR=/private/tmp/tide-uv-cache uv run python -m pytest \
  tests/test_agent_builder.py tests/test_agent_harness.py tests/test_agent_mcp.py \
  tests/test_agent_routing.py tests/test_agent_simulator.py \
  tests/test_builder_budget_regression.py tests/test_routing_reference_runner.py -q
```

Historical scope records:

```text
tests/test_navigation.py tests/test_agent_voyage.py tests/test_live_voyage.py
  23 passed in 4.01 s (peer handoff: 22 passed in 2.37 s)

web/game node --test game.test.js
  37 passed, 0 failed

tests/test_agent_rate_limit.py tests/test_rate_limit_acceptance.py
  14 passed
```

Baseline before the navigation, voyage and rate-limit scopes: the full suite on experiment HEAD `4101d2d` passed
**450 tests in 31.03 s**. The 519 Python and 37 frontend counts remain the historical baseline only.

Historical runtime report `/private/tmp/tide-pi-runtime-resumed-20261030.log` records 61 focused tests and a
then-current whole suite of 450 passed.

Verify the CLI directly after the parent and peer changes:

```bash
uv run python -m agent --help
uv run python -m agent build --help
uv run python -m agent.mcp --help
uv run python -m agent.tui --help
```

Do not quote a historical whole-suite count as the current count. The parent final suite is authoritative.
