# Game prototype verification — 29 October 2026

PLAN stages 3 and 4 are implemented. The terminal supports Director and pure local modes. This report distinguishes deterministic mission verification from live model observations.

## Automated checks

The current tree passes 196 tests, including cargo/region, session, Director, TUI and motion regressions. The motion repair restores full MSS coupling, adds source-supported mean longitudinal wave drift and resolves long-period quadrature underresolution. Real Octave comparisons, 17 initial and 27 high-speed synthetic pressure tests and remaining physical limits are documented in [MOTION_VALIDATION.md](MOTION_VALIDATION.md).

The full-screen local interface was exercised in a real PTY using the historical scenario: initial paused editor, target heading/speed entry, live UTC/position/motion updates, `p` pause, `j/k` rate changes, save and quit. Saved-session restoration was checked at 80x24. The screen uses only player-visible observations and released forecast reports. The line/JSON interactive entrypoint has been removed; `--script` remains the automated verification path. This UI check did not rerun a full mission or call the live Director.

The asynchronous tests deliberately block model assessment while advancing physics, reject late decisions after player control changes, and verify delivery-without-pause, explicit pause, idempotency and fixed-delivery fallback. These are fixture tests of the harness contracts, not proof of semantic judgment quality.

## Frozen historical scenario: deterministic baseline

These full-mission results predate the dynamics change in `4880170`. They remain historical evidence, not validation of the revised equations. A new full-mission run is required before using them to assess the current model.

After the subsequent motion repair, one current direct-strategy mission with seed 1 escaped at 9,139.25 seconds with zero cargo loss. Its run is `data/runs/motion-repair/menorca/direct-1/`. The full strategy/seed matrix below has not been rerun with the repaired forcing.

The package contains 181 one-minute convex region envelopes and 24 explicitly labelled game forecast records. Existing environmental checksums and S175 numerical settings remain unchanged. New gameplay data has its own version and checksum; numerical scenario schema remains version 1.

`scenarios/menorca-20260909/game-validation.json` records all 12 completed full-window game trials from before the final attention-policy update, with the current scenario-definition checksum. The frozen environmental and region/cargo algorithms are unchanged; final delivery/attention behaviour is covered by targeted regression tests. Seeds 1, 2 and 3 each have a successful escape.

| Strategy | Three-seed outcome | Elapsed simulation seconds |
| --- | --- | --- |
| Direct target steering | All escaped | 9,142.95–9,144.85 |
| Initial detour then target steering | All escaped | 9,173.25–9,179.50 |
| Reduced speed | All timed out | 10,800 |
| Delayed departure | All timed out | 10,800 |

All 12 trials had zero game cargo loss. Existing motion responses are mild relative to the declared cargo coefficients. Severe-response fixtures exercise overboard loss and grade checkpoints; no artificial amplification was added to force losses in the historical mission. These runs validate feasibility and time consequences, not cargo balance or professional securing limits.

Full session logs and snapshots are local under `data/runs/game-verification/`. Script decisions explicitly retained or revised orders. The earlier target-disk validation remains a labelled numerical baseline and does not establish success under the new region rules.

## Live NIM observations

The supplied credential was stored only in ignored `config/api_keys.local.json` with owner-only permissions. The key-free example is committed separately. No keys are sent in model messages or written to run logs.

Native tool calling passed with `nvidia/nemotron-3.5-lightning-30b-a3b`, thinking disabled, temperature 0.2 and a 1,024-token output budget.

A real terminal mission ran through two issued weather checkpoints, manual heading/speed changes, save/exit/resume, and the three-hour timeout. Its fixed player headings did not escape the convex region; the Director preserved the timeout rather than replacing it with success. Terminal recovery successfully called `finish_mission`. The local record is `data/runs/live-director-20261029/`.

Early live calls sometimes returned prose without a native tool call. The final harness supplies a restricted next-tool schema for mechanical orchestration and repairs malformed responses within a finite budget. Exhaustion now falls back to local orchestration for the current step. The earlier trial predates the final background-attention policy, so its automatic weather checkpoints are historical integration evidence rather than a claim about the final discretionary policy.

The final attention workflow was separately tested against real NIM using already-issued report candidates and current ship observations. It returned a valid defer/no-pause decision in approximately 1.26 seconds. The record is `data/runs/attention-live/result.json`. This verifies the model/tool/application connection; semantic timing quality remains unmeasured.

The default pure local CLI startup was verified with nonexistent model and key paths. No model configuration or credentials were read. Failure fixtures verify no duplicated advancement, no automatic resumption after player cancellation, and no discretionary weather pause during fixed-delivery fallback.

## Limits

OpenRouter has a configurable entry and credential placeholder; no live OpenRouter call was performed. Its model ID must be selected and verified before use. Forecasters, cargo coefficients and full-load stowage categories are game assumptions. No frontend, Copilot, Builder or Tutor is included. Maximum time scaling remains limited by the computer's ability to execute every physical step.

The Director's mechanical workflow can be replaced by the local engine. Its discretionary contribution is report delivery and attention timing. This delivery does not establish an educational or reliability advantage over the fixed local baseline.

An additional 12-trial numerical rerun was started and stopped at partial snapshots under `data/runs/game-final-verification/`; it is not counted as completed verification. Final report-release semantics were instead tested directly at the issue boundary: fixed delivery does not force a weather pause, Director mode keeps undelivered candidates private, and asynchronous assessment does not stop physics.
