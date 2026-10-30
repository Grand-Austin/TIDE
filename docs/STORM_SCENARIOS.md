# Storm scenarios: build rules and validation report

`pipeline/storm_scenario.py` turns a catalogued storm (`data/storms/<region>.json`, data subsets in
`data/storms/<region>/<id>/`) into a playable escape scenario in `scenarios/<id>/`. A scenario is published only
after human review of a report that contains a qualifying route. Qualification requires escape, ship survival and
at least 99% of total TEU retained. See [Builder experiment loop and qualification](#builder-experiment-loop-and-qualification).

## Current status at handoff

The Builder source and its tests are in place. The four earlier `scenarios/biscay-*` packages were removed; the
current storm package is `scenarios/sole-20261022-21`, built by the Builder from the 2026-10-22 peak, with eight
user-approved routes. The report shape shown below is historical and predates the Builder qualification gate.
A local `data/runs/builder-smoke` directory is an old smoke artifact, not qualification evidence. The peer `agent/solutions.py` writer and the parent Builder and CLI changes are present in source. The targeted
tests and the parent final suite remain pending; do not treat this documentation as test evidence.

Both search families are present as TIDE adaptations, not upstream benchmark executions. Their estimated times
are candidates only. A full Session is mandatory for qualification. The SIMROUTE Palma case is an independent
reproduction; the VISIR-2 official analytic-current case passed in external original code; the full Alboran
numerical case was not run. See [agent/README.md](../agent/README.md) and
[agent/references/REPRODUCE.md](../agent/references/REPRODUCE.md).

Core coast-guard and voyage source now exists. Peer handoff reported 22 navigation/voyage/live Python tests
passed in 2.37 s; a docs-time rerun passed 23 in 4.01 s. Node reported 37 passed, and a docs-time run also
passed 37. A real TUI Menorca short play remained outside the 5 nm guard for 70 s. The actual Biscay 20260123
voyage is Brest to Vigo, 612.34 nm, 11.37 nm snap offset and 22 authored points. MCP and Builder integration
peers are still active.

## Build rules

- **Start time.** The 20-minute step within 12 h of the storm peak with the highest Hs at the start point while the
  sun stays above 10° (`pipeline.export_scene.daylight_start`). If those daylight seas are below 60 % of the
  storm's peak Hs, the scenario starts 20 minutes before the peak, at night.
- **Hidden region.** `pipeline.gameplay`'s rule: the convex envelope of cells with Hs at or above `region_hs_m`,
  frozen per minute. `region_hs_m` is the start's Hs minus 1 m, rounded down to 0.5 m (the storm core).
- **Start position.** The storm's peak cell, moved towards the nearest region edge until it is 15 km inside if it
  was deeper than 25 km. Edges along the data box and edges with land just beyond them are not exits.
- **Mission.** Two hours, 5 m/s initial speed, head seas initially. Storm settings: dt 0.1 s and wave forces every
  0.2 s. Physical mode uses source propeller/rudder limits; the legacy 1.5 MN teaching cap does not apply.
- **Steering.** `actuator_model: s175_maneuvering` separates low-frequency manoeuvring from the original
  S175 six-DOF wave response. Its heading controller commands a physical rudder, with ±35° order,
  5°/s rate and 160 RPM maximum ahead order. The prepared S175 damping remains unchanged on the wave
  response; it is not also applied to the empirical manoeuvring model. The fixed 30 MN m limit belongs
  to the retained teaching mode. See [TURNING_MODEL.md](TURNING_MODEL.md) for equations and limitations.
  Changed configurations require new complete mission validation.
- **Weather.** Hourly ERA5 cloud cover and precipitation at the start point (Open-Meteo) in `weather.json`.
- **Designer-fixed inputs.** The designer fixes the initial time and state, environmental data and region, physics,
  cargo and ship-damage model, and seed. The Builder treats them as immutable and records their versions and
  checksums.
- **Builder boundary.** The Builder may calibrate only the time budget, end timestamp and derived schedules inside
  an output copy; the escape condition remains fixed. It must not edit the fixed inputs or overwrite a published
  package. Its output goes to review staging.
- **Coast guard.** TUI and MCP local play use a fixed 5 nautical mile land buffer from offline pinned Natural Earth
  10m land v5.1.1 under `data/navigation`. Candidate route edges and actual local execution use the shared
  `game/navigation.py` check. Missing or corrupt data fails closed. An unsafe initial position makes the Builder
  result `pending` with a clear reason; the start is never relocated. This scope does not change physics,
  environment, cargo or damage rules.
- **Planned voyage.** A scenario authors a display-only voyage from the sea-route network and canonical
  `web/game/ports.json`. It preserves the actual start, reports the nearest-network snap offset, uses a weighted
  shortest path and keeps distinct opposite branches oriented by the initial heading. It is plausible authored
  context, not a historical log. Port approach is not an escape condition. The 3D page may display the voyage but
  contains no coastline data or navigation policy.
- **Builder AI model access.** Active Builder AI uses the shared ignored `config/api_keys.local.json` and NVIDIA NIM
  with model `nvidia/nemotron-3.5-lightning-30b-a3b`. All Agent model HTTP attempts share 30 attempts per rolling
  60 seconds through `data/runs/agent-rate-limit.sqlite`. Retries count. The cap is not profile-configurable. Local
  Builder search, local Sessions, MCP physics and navigation do not consume the model budget. The rate-limit scope
  passed the timestamp-after-lock acceptance test. The 519-test suite is the historical baseline for earlier
  work, not verification of the solutions-package changes.

Changing these motion settings requires new strategy runs and validation reports; do not update report hashes
without rerunning the strategies. Frozen scene replays in `web/game/scenes/` retain their recorded motion and
must be regenerated before using them as evidence for the restored settings.

## Validation report: `scenarios/<id>/validation.json`

`python -m pipeline.storm_scenario <id> --check` writes the published-pipeline report. The Builder writes a
separate report inside its review package. Candidate solvers, including TIDE SIMROUTE-style and VISIR-2-style
adaptations, add strategies in a compatible shape.

The example below shows the earlier published-pipeline report shape. It does not record the Builder qualification
gate or TEU retention. Earlier Biscay reports can lose more than 1% of total TEU, so do not reuse them as Builder
qualification evidence.

```json
{
  "version": 1,
  "scenario": "biscay-20260123-06",
  "definition_sha256": "<sha256 of scenario.json>",
  "gameplay_sha256": "<sha256 of gameplay.json>",
  "rule": "passed when at least one strategy escapes without losing the ship",
  "passed": true,
  "reference": "avoid_beam",
  "strategies": [
    {
      "name": "avoid_beam",
      "solver": "pipeline.storm_scenario: steer for the nearest edge, keep waves off the beam (50-130 deg)",
      "orders": [{"t": 0.0, "heading_deg": 259.9, "speed_knots": 9.7}],
      "outcome": "escaped",
      "elapsed_s": 3654.7,
      "lost_teu": 0,
      "grade": 0,
      "ship_lost": false,
      "session": "data/runs/check-biscay-20260123-06/avoid_beam-20261030-063430"
    }
  ]
}
```

- `orders` are the session's `player_order` records: game time, heading and ordered speed. Replaying them with
  `python -m game --script` reproduces the run for the same seed.
- `reference` is the passing strategy with the least cargo lost, then the shortest time. Debriefs may draw on it.
  Builder reports also record the fastest qualified route, because the budget uses that time.
- Builder qualification records add the method family, retained TEU fraction, qualification result and a checksum
  for the simulation implementation. An unqualified candidate stays in the report with its rejection reason.
- A changed `scenario.json`, `gameplay.json` or simulation implementation invalidates the report (checksums).

## Navigation and voyage acceptance

A scenario passes the new navigation checks only when all of these conditions hold:

1. The pinned Natural Earth package is present offline and every checksum matches.
2. The scenario initial position is at least 5 nautical miles from land. Otherwise the Builder returns `pending`
   with a navigation reason and does not move the start.
3. Candidate edges do not touch or cross the 5 nm buffer. Actual local execution applies the same check and stops
   or rejects before committing an unsafe position.
4. Save, resume, fork, TUI and MCP use the same guard and dataset.
5. The authored voyage keeps the actual start, records the snap offset, preserves distinct opposite branches and
   is labelled authored context.
6. Port approach does not change escape, qualification, score, judgement or stars.
7. The 3D page receives voyage display data only. It contains no coastline rendering, data or policy.

Core local checks now have passing evidence: navigation/voyage/live Python tests, Node voyage/port tests, the real
TUI Menorca 5 nm short play, the data verify-only checksum and the real Biscay voyage artifact. MCP and Builder
integration remain with the parent. The rate-limit acceptance test and full suite are also pending. The earlier
450-test suite on HEAD `4101d2d` is a pre-scope baseline, not final evidence.

## Builder artifact shape

A real Builder run writes below the selected output directory:

```text
<out>/run.json                  run status, mode, thread id and package path
<out>/builder-state.json        resumable candidates, trials, search, coverage and reason records
<out>/trial-cache.json          restart-safe completed Session records keyed by evidence and orders
<out>/builder.sqlite            LangGraph checkpoint in AI mode
<out>/audit.jsonl               credential-free harness audit in AI mode
<out>/work/scenario.json        search-horizon work copy
<out>/work/gameplay.json
<out>/trials/<trial_id>/        real Session state and event log
<out>/package/scenario.json     final budget and final gameplay reference
<out>/package/gameplay.json     final region and report schedule
<out>/package/validation.json   strategies, metadata, checksums and final revalidation
<out>/package/solutions.json    one readable file with every distinct qualified route
<out>/package/build-report.json Builder report, checks, trials, limitations and weather status
<out>/package/weather.json      optional display copy, only inside its source coverage
```

`validation.json` includes each strategy's detailed orders, outcome, elapsed seconds, lost and total TEU, ship
loss, grade, qualified flag, reason, trial id, Session path and evidence fingerprint. Its `metadata.builder`
block records approval `none`, `published: false`, `human_review_required: true`, the search horizon, both search
results, search and final coverage, the final budget, evidence fingerprint and pending reasons. The Builder
report records the same search, trials, winner, final revalidation, checksum refresh and limitations.

The evidence fingerprint covers the scenario definition, gameplay checksum, every environment layer's declared
and actual SHA-256, vessel files and declared vessel identity, and SHA-256 of the implementation files in
`agent.models.ALGORITHM_FILES`. This includes `game/simulation/*`, `game/session.py`, `game/cargo.py`, `game/ship.py`,
`game/rules/mission.py`, `pipeline/gameplay.py` and the Agent routing, simulator, Builder and harness files. A
report with a different fingerprint is stale even when its prose still looks correct. Do not hardcode a hash from
this document; read the current artifact.

## Builder experiment loop and qualification

The Builder is a scenario experimenter, not only a review assistant. It separates fixed design inputs from
calibrated scenario settings and keeps every result reviewable.

1. **Generate candidates.** Reproduce the SIMROUTE baseline and the VISIR-2 baseline. Then generate TIDE
   adaptations of those methods for the local environment, vessel and rules. TopTier is only a risk reference; it
   is not a pathfinder.
2. **Prove with a full Session.** A route is only a candidate until a complete Session runs it. Partial routing,
   a risk score or a short trial does not prove feasibility.
3. **Qualify the route.** The route must escape the whole changing region, keep the ship and retain at least 99%
   of total TEU. Keep all game scoring, judgement and star rules unchanged. Do not add a zero-loss condition or a
   95% deck-retention threshold.
4. **Handle no result.** If no route qualifies, mark the scenario pending and send it to manual review. Do not
   claim that escape is impossible.
5. **Set the budget.** Let `T` be the fastest qualified full-Session time in seconds. Use:
   `budget_s = max(900, 900 * ceil(1.2 * T / 900))`. The budget is rounded to 15-minute blocks with a 20% margin
   and must fit inside the historical data coverage. Examples: `T = 3000 s` (50 min) gives `3600 s`
   (60 min); `T = 4200 s` (70 min) gives `5400 s` (90 min). The input mission budget is the search horizon, not
   the final budget.
6. **Regenerate and rerun.** Apply the budget and any derived schedule in a new output copy, regenerate the final
   package and rerun the final full Session. Do not validate old evidence against the changed package.
7. **Stage outputs.** Write `scenario.json`, `gameplay.json`, `validation.json`, `solutions.json` and a
   Builder report to the review-staging directory. Include every distinct qualified route, its orders, actual
   full-Session metrics and provenance. Include rejected candidates and the reason for each rejection. The final
   Session must run against the regenerated package path, not the work copy.
8. **Report versions and checksums.** The report identifies the scenario, gameplay, validation and solutions
   evidence and the simulation implementation versions. It includes checksums for each item that affects the result.
9. **Require human review.** The Builder cannot publish a package. A reviewer compares the staged material with
   the fixed inputs, route evidence, metrics and checksums before publication.

## Solutions package

`package/solutions.json` is one readable file for human review and for the planned Copilot. It stores solution
routes, not graded hints. The L1/L2/L3 Hint feature, `hints.json`, `hints.md`, Builder replay and branch
expansion, the whole-area coverage target and the `--hint-branch-limit` option are removed by user decision. The
detailed contract is in [SOLUTIONS.md](SOLUTIONS.md).

The file starts with a short purpose and reading guide. It then gives the fixed initial conditions, the final
budget, the source fingerprint and the minimum and found counts. The solutions are sorted by actual elapsed time
and then by TEU loss. Each solution gives a factual summary, the source solver, all applied timed heading and
speed orders, the actual sampled track when available, the actual escape time, the cargo and ship results, its
limits and the full-trial provenance. Missing intermediate track samples are labelled endpoints-only; the
exporter must not invent a straight-line successful path. Bulky simulator logs stay in the trial directories.

The default minimum is three distinct qualified routes, set by `--min-solutions`. There is no maximum stored
count: every distinct qualified route discovered within the compute budget and revalidated against the final
scenario is retained. Different names for identical timed orders do not count as different routes. The Builder
does not promise to enumerate all possible routes.

The default compute budget is 64 full Sessions. Each search solver returns up to 12 candidates before the three
bounded expert proposals and two revision depths. `--max-trials` and `--max-candidates` can raise this budget. The
budget stays finite and configurable.

Let `T` be the fastest qualified actual full-Session time. The final budget is
`max(900, 900 * ceil(1.2 * T / 900))`, rounded to 15-minute blocks with a 20% margin. Every stored slower
alternative must also pass a complete final-package rerun inside that final budget. A failed alternative is
omitted and reported. If fewer than the required minimum pass, the package is `pending` and keeps the valid routes
that were found.

The planned Copilot L1 reads released forecasts without solutions access. L2 receives temporary per-request
access to these routes, then returns a nearby suitable reference as a red map line and a short insight.
Both modes advise only and make no guarantee for an arbitrary current state. A separate, explicitly enabled
route-following mode may adjust heading and speed through bounded local tools, with immediate human override.
The Tutor will show a brief final-log-based evaluation at settlement. These runtimes and controller calibration
remain planned; see [PLAN 7.15](../PLAN.md#715-planned-copilot-and-tutor-scope-documentation-only-handoff).

Offline `run_plan` runs the real simulator without wall-clock pacing or animation. It keeps the physical
integration step, so trials are not accelerated by a larger step. The live TUI already supports maximum game
speed (`rate=0`).
