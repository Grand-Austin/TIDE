# Verified solution routes and planned Copilot reference use

Builder writes one readable `package/solutions.json`. It stores solution routes, not graded hints, a hint grade or an area-coverage policy. The Builder keeps no offline graded-hint builder and no whole-area route builder. The planned expanded-map hint button uses these routes only through the Copilot L2 mode described below.

## Build and acceptance

The default candidate trial budget is 64 full Sessions. Each search family returns up to 12 candidates before bounded expert proposals and revisions. `--max-trials` and `--max-candidates` can increase this compute budget. The default minimum is three distinct final-verified solutions, configurable with `--min-solutions`. There is no maximum number of stored solutions: all distinct final-verified routes discovered within the compute budget and revalidated against the final scenario are retained. This is not a promise to enumerate all possible routes.

Automatic qualification requires a real full Session escape, ship survival, and at most 1% total TEU loss within the final mission budget. This global rule and the defaults remain unchanged: 64 trials, up to 12 candidates per search family, and a minimum of three distinct solutions. For ordinary builds, the budget is based on the fastest qualified route, with a 20% margin rounded up to 15 minutes. Source-search results are not automatically final-package evidence; alternatives must pass a complete final-package rerun. A failed alternative is omitted and reported. If fewer than the required minimum pass, the package is pending and preserves the valid routes already found. Different names for identical timed orders do not count as different routes.

## Scoped user retention for the approved package

A human can retain a selected final route after candidate comparison, even when that route exceeds the automatic 1% TEU-loss limit or lacks the automatic qualified record. The retention receipt has three scopes: `approved_by` must be `user`, `source_fingerprint` must match the current final evidence, and `candidate_names` must contain the selected candidate name. A model-facing tool cannot grant this approval.

The approved candidate still must escape in a real full Session, survive, validate final metrics and orders, use a finite elapsed time within budget, and match the exact final fingerprint. Approval changes no measured value. The stored route keeps its true loss and `automatic_qualified: false`; it is marked with `selection_basis: user_approved`. An automatic route keeps `selection_basis: automatic`. The package exports the top-level approval receipt, a factual `automatic_qualification_rule`, and a `governing_rule` that names the user exception. An empty, stale, or otherwise mismatched receipt is ignored and cannot relax the automatic rule.

For the `scenarios/sole-20261022-21` package, the user compared final candidates and selected candidate 9 and candidate 13 plus the lowest-TEU-loss route for north, east, east-to-southeast, northeast, northwest, and southwest. The approved final set is candidate IDs 6, 9, 10, 12, 13, 14, 16, and 17. East candidates 2 and 10 both lose 25 TEU; candidate 10 is retained because it is faster. The final budget is explicitly fixed by the user at 75 minutes (4500 s), not derived from the automatic fastest-route formula used for ordinary builds.

Construction is complete. Five candidates (6, 9, 10, 12 and 13) completed final 75-minute-scenario reruns. The user then stopped further revalidation. Candidates 14, 16 and 17 retain their original 120-minute-scenario full-trial evidence, with `final_validation: false` and explicit per-route provenance; their recorded elapsed times fit 75 minutes, but no new-budget rerun is claimed. The final file retains all eight user-selected references and reports partial revalidation. This approval is scoped to this package and does not change global defaults. The demonstration is loadable using qualifying final-budget escape evidence from 9 and 13.

The solution file contains a short purpose and reading guide, the fixed initial conditions and final budget, source fingerprint, minimum/found counts, and a list of solutions sorted by actual elapsed time then TEU loss. Each solution contains a factual summary, source solver, all applied timed heading/speed orders, actual sampled track when available, actual escape time, cargo/ship results and full-trial provenance. Missing intermediate track samples are labelled endpoints-only; the exporter must not invent a straight-line successful path. Bulky simulator logs remain in their trial directories.

## Planned Copilot reference use

The planned Copilot has two advisory modes. L1 receives the complete currently released Copernicus forecast payload for the scenario and valid window plus the current visible vessel state. L1 has no access to this file. It returns one brief, sharp, actionable insight. The full forecast is available through a tool or chunked retrieval, not a giant prose prompt. If the current forecast is simulator-derived, L1 must distinguish dataset historical truth from the issued forecast. L2 grants a temporary, request-scoped read permission to `package/solutions.json`; the permission ends when the request ends. The L2 tool selects a nearby suitable reference route from position, time, heading, speed, remaining budget, damage and the current-source fingerprint. It returns a `route_id` and reference waypoints. The frontend overlays a red reference line and a short insight. The file never becomes a persistent Copilot prompt.

L2 is advisory and makes no guarantee for an arbitrary current state. It must not claim that a route validated from the original initial state also guarantees escape from a different current state. An explicitly enabled route-following control mode is separate from L2 and may adjust heading and speed only through bounded authoritative Session or TUI MCP tools. See [Product.md](Product.md#53-explicit-route-following-control-planned) and [ARCHITECTURE.md](ARCHITECTURE.md#52-general-boundaries).

No whole-area or arbitrary-state coverage guarantee is made. The spatial/time coverage builder, graded Hint schema, graded Hint permission flow and Hint charging are not requirements in this scope. The planned hint button is a Copilot entry point, not a graded-hint system.

## Execution speed

Offline `run_plan` runs the real simulator without wall-clock pacing or animation. Do not increase the physical integration step to accelerate trials. Live TUI supports maximum game speed (`rate=0`); independent or forked trials are preferred for search so learner controls are not changed.

## Targeted verification

The exporter and manual-approval checks passed (29 in 0.70 s): 12 existing exporter checks and 17 scoped-approval boundary checks. They cover minimum count, no storage cap, duplicate removal, final-package revalidation, insufficient-route preservation, event-log track extraction, absent graded-hint artifacts, strict rejection above 1% TEU loss, scoped receipt matching, retained true losses, and unchanged automatic labels. No full-suite rerun, live NIM call or real Biscay end-to-end build was performed for this change.

## Operational status

The approved package records completion for the 75-minute budget, with partial revalidation. Real pipeline monitoring and a qualified end-to-end Builder run remain parent operational work. This document defines planned Copilot behavior only. Runtime Copilot and Tutor remain planned. No Copilot runtime, steering calibration or training was implemented or tested in this documentation task. See [PLAN.md](../PLAN.md#715-planned-copilot-and-tutor-scope-documentation-only-handoff) for the unchecked runtime and evaluation checklist.
