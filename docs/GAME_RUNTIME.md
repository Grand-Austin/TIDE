# Command-line game prototype

The game uses the committed historical fields and prepared S175 vessel. Prepare the vessel using `uv run python -m pipeline.prepare_vessel` if `data/processed/s175` is absent. Install the locked environment with `uv sync`.

## Credentials and models

Copy `config/api_keys.example.json` to `config/api_keys.local.json` and replace the placeholder for the provider you use. The local file is ignored by Git. Never put credentials in `models.json`, a scenario or a shared run log.

`models.json` contains named providers and model profiles, plus the Director role binding. NVIDIA NIM is the default. OpenRouter is selected explicitly by changing that binding and supplying its own key/model profile. The game calls provider HTTP endpoints directly; it does not require pi.

## Play

```
uv run python -m game
uv run python -m game --engine director
uv run python -m game --session data/runs/my-session
uv run python -m game --session data/runs/my-session --resume
```

Interactive play uses the fullscreen live terminal UI and requires terminal input and output. The old line-command/JSON interface has been removed. `--script` remains available for automated verification. The screen requires at least 80 columns by 24 rows. Shrinking below that size pauses the game; enlarging it does not automatically resume sailing.

The live screen shows UTC time, elapsed and remaining game time, requested and achieved simulation rates, latitude and longitude, actual and target heading and speed in knots, current observed significant wave height (`Hs`), wave period, wind, vessel roll and pitch, and retained cargo as a percentage of the initial TEU. `Hs` is the observed significant wave height, not an individual instantaneous wave height.

The forecast panel contains only already released reports labelled `game_simulated_forecast`. Use the arrow keys to scroll through the panel. These authored game reports are evidence, not historical official bulletins.

### Terminal controls

Shortcuts apply outside command input. Letters typed into command input remain text.

| Input | Effect |
| --- | --- |
| `j` | Lower the requested rate through 1, 5, 20, 60 and `max`. |
| `k` | Raise the requested rate through 1, 5, 20, 60 and `max`. |
| `p` | Pause immediately and open the target heading and speed editor. The editor is prefilled with the current targets. |
| `Tab` | Switch between the heading and speed fields in the pause editor. |
| `Enter` | Validate and apply both target heading and speed, then resume. |
| `p` in the pause editor | Apply both targets and resume. |
| `Esc` in the pause editor | Leave the editor without applying it and remain paused. |
| `/` | Open the command input. Opening the command input does not pause the game. |
| `Enter` in command input | Execute the command. |
| `Esc` in command input | Cancel the command without executing it. |
| `Ctrl+C` | Save the session and exit. |

Initial and restored sessions begin paused with the target editor open. Slash commands remain available in the fullscreen UI and include `/heading 155`, `/speed 6`, `/rate 20`, `/pause`, `/resume`, `/status`, `/reports`, `/save`, `/retry` and `/quit`. `/heading` and `/speed` pause the game and open the target editor. `/quit` pauses, saves and exits. `/save` saves a coherent snapshot. `/resume` resumes the game. `/status` and `/reports` refer to the live panels above.

Orders change targets, not instantaneous vessel states. Existing turning and acceleration limits apply. A screen resize never skips physics steps. The requested rate is a target and does not guarantee the achieved rate, because the achievable rate depends on the computer and current workload. The physics timestep remains fixed at every rate; `max` performs every step as quickly as possible.

Cargo-grade increases pause the game locally. In Director mode, background reviews choose when to deliver issued reports and whether to request attention; pure local mode delivers on the fixed schedule without weather-triggered pauses. Leaving the whole authored changing hazard region in valid waters completes escape. Cargo loss is scored separately.

## Scripted verification

A stage-3 script runs the same algorithms without an Agent. An action array contains `heading_deg`, `speed_knots` and `until_s` for each consecutive order. Scripted checkpoints explicitly record a keep-order decision. Example:

```json
[
  {"heading_deg": 155, "speed_knots": 5.8315, "until_s": 10800}
]
```

```
uv run python -m game --script actions.json --session data/runs/scripted
```

The runner writes `result.json`, `state.json` and `events.jsonl`. State includes numerical wave/controller continuity, per-unit cargo state, consumed events and operation receipts. Resuming requires the same frozen scenario, algorithms, configuration and seed. The Director also uses `director.sqlite` to checkpoint its workflow; it does not use graph state as the physical truth.

## Cargo assumptions

The full-load template represents 800 mixed-size containers and 1,200 TEU; 320 units are on deck and 480 in holds. It is a game template, not an S175 cargo manifest. Response-driven damage and initial seeded vulnerabilities determine irreversible overboard losses. Mild motion may cause none. Lost TEU as a fraction of initial total determines the cargo grade. Losses do not modify the fixed vessel loading condition. See the implementation plan for the initial game coefficients and grade boundaries.

## Local engine and fallback

`--engine local` is the default; `--engine director` explicitly enables the experimental Director. `--engine local` runs the full interactive game through deterministic local orchestration, without initializing a model or reading API/model configuration. The manual controls, rules, physics and snapshots are identical.

In Director mode, initialization failures, exhausted model budgets and runtime model errors automatically fall back for the current step. If no movement occurred and the player has not cancelled or replaced the order, local orchestration can perform the still-authorized advance. If progression already committed, the fallback only presents its result and waits. It never advances the same segment twice or automatically resumes after a player pause/checkpoint. Each transition is logged as `director_fallback`; the terminal announces it. No alternative paid provider is selected automatically.

The current Director is deliberately a constrained workflow executor. These tasks can also be performed by the local engine. The prototype demonstrates both paths; it does not establish an advantage for LLM orchestration.

## Background Director attention

`game/director/harness/ATTENTION.md` specifies the report-delivery policy and `manage_attention` schema. The Director sees already-issued, currently applicable report candidates, while the player sees only delivered reports. It can defer, publish while sailing continues, or publish supporting evidence and request a pause. Reports are immutable.

One background review runs at a time, on new report candidates or every 300 simulated seconds. The clock continues while the model responds. Results older than 600 simulated seconds, results superseded by a player action, unknown report IDs and unsupported evidence are rejected. Discretionary pauses have a 300-second cooldown. Failed reviews fall back to fixed delivery, with no discretionary pause. Notifications appear in the terminal while input remains available.

This is the Director's current discretionary capability; mechanical advance/finish calls remain tightly constrained. Decision quality still needs comparison with the local baseline and human assessment.
