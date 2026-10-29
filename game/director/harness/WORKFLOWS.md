# Director workflows v1

The CLI selects and integrity-checks the scenario, loads/restores the authoritative state, obtains player orders and manages credentials. Those are trusted harness responsibilities, not model-generated actions.

| Harness state | Required tool | Expected result | Next action |
| --- | --- | --- | --- |
| Initial briefing or restored session, phase `decision` | `request_decision` | Current released state, paused | Wait for explicit player input/resume. |
| Player has authorized phase `resume` | `advance` | Updated state and a local stopping reason | Inspect the returned outcome; do not issue a second advance. |
| Advance stopped without terminal outcome | `request_decision` | Released evidence and paused state | Wait; player may retain or change the order. |
| Advance returned terminal outcome | `finish_mission` | Same authoritative result, paused | End this graph run; no more motion. |
| Explicit status/report inspection | `read_status` / `read_reports` | Released data only | These never authorize motion. |
| Validation error | Correct allowed call | Success or another error | Stop when the call budget is exhausted. |
| Network/model/graph failure | Local step fallback | Preserve committed progress and player cancellation | Complete only the still-authorized step, then pause for input. |

## Stopping reasons

- `duration_reached` is an internal numerical segment boundary. The advance tool continues internally; do not ask the player after every segment.
- `cargo_grade_increased` requires a player decision. Forecast issue events create available report candidates; ATTENTION.md controls their delivery and discretionary pauses. `director_attention` records an accepted evidence-backed pause.
- `paused` means the player's manual pause. Do not resume automatically.
- `escaped` and `timeout` are terminal outcomes supplied by the rules. Call `finish_mission`.
- `domain_boundary`, `time_boundary`, `missing_environment`, `model_limit` and `numerical_failure` are technical or applicability stops. Present the recorded reason without inventing an accident.

## Recovery

The game snapshot is authoritative. Graph state contains only permitted context and operation references. A committed advance is not repeated during graph recovery. A run interrupted before completion restores paused; a new explicit player resume is required to advance further.

Multiple simultaneous tool calls, unknown tools, non-empty arguments, stale revisions and revoked resume permissions are rejected. A retry cannot override these checks. Repeated calls with the same operation identity return the recorded result.

The deterministic fallback is implemented outside the model. It checks the starting physical tick and player control generation before continuing an unexecuted advance. Model instructions cannot activate or disable it. An explicit local engine mode runs without this skill or a model.
