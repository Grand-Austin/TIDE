# Status

_Last updated: 2026-10-30_

**Stage:** PLAN stages 1–4 done on `experiment` (command-line game). Stage 5 partly done; stage 6 (frontend, Copilot) not started.
**Next:** agree the region and the frontend approach (see "Decisions needed"), then a playable storm scenario and the web frontend.

**Motion repair:** full MSS coupling is restored. Source-supported mean longitudinal wave drift and a logarithmic spectrum grid address the reproduced head-seas speed stop and resolution warning. All 196 tests pass; live GNU Octave comparisons, 17 initial and 27 high-speed synthetic pressure tests are recorded in [MOTION_VALIDATION.md](docs/MOTION_VALIDATION.md). Local uncommitted TUI work remains preserved. The temporary experiment review has been removed after its scope was resolved.
**Cargo:** The selected C50=0.5g stack rule now runs in live sessions. Endpoint body angular terms, sorted-capacity interval tracking and compact JSON state replace the old category dose. Old implementations/tests retain their structure under `archive/`. The suite passes 220 active tests; the CLI and a 20-minute severe-sea Session passed. The optional historical matrix was stopped after 9/12 completed trials; completed results remain under `data/runs/stack-cargo-missions-current/partial-validation.json`. Numerical results remain under ignored `data/runs/`; [CARGO_DAMAGE_STUDY.md](docs/CARGO_DAMAGE_STUDY.md) keeps the comparison tables.
**Ship loss:** Implemented per-excursion roll probability and cumulative roll/structural exposure. Ship loss terminates the mission and loses all remaining cargo. The active suite passes 270 tests. The revised roll rule starts at 20 degrees with a 30 s normalized budget; a new fast following-sea dose detects 3 of 44 existing motion tapes, while 41 do not trigger loss; an actual S175 first-step total loss and restored terminal state passed. See [SHIP_DAMAGE_STUDY.md](docs/SHIP_DAMAGE_STUDY.md); results stay in `data/runs/ship-damage/`.
**Deadlines (CET; Finland is +1 h):** event opened Thu 29 Oct 16:00 · video due Sat 31 Oct 08:30.

## Branches

| Branch | What | State |
| --- | --- | --- |
| `main` | SeaSense route-choice prototype (PRs #4–#12) | Merged; kept as reference |
| `experiment` | TIDE simulation toolkit, scenario pipeline, rules, Director, terminal UI; fresh-clone fix, CI, storm dynamics fix, cargo calibration | Active line of work |
| `ci/fresh-clone` | Fast-forward merged into `experiment` on 2026-10-29 | Can be deleted |
| `blender/3d-demo` | Blender S175 and container models; 3D replay page (`web/game`) playing Yan's Biscay storm scenes and recorded sessions; falling-container animation | In progress, local only; a separate line from `web/3d-scene`, not merged |

## Decisions

Newest first. Proposed decisions move here once the team agrees.

| Date | Decision | Why | Decided by |
| --- | --- | --- | --- |
| 2026-10-29 | Remove instantaneous and target-speed ceilings | The source speed-table endpoint is data coverage, not an MSS motion limit. Abnormal motion remains available to future ship/cargo damage rules | Xuan explicitly requested removal |
| 2026-10-29 | Remove the TIDE-only roll/pitch cutoff and its scenario configuration | The original MSS example has no 30-degree calibration or stopping threshold. The 16 m beam-seas rerun completes 20 minutes and reaches 35.89 degrees roll | Xuan explicitly requested removal |
| 2026-10-29 | Restore full MSS coupling; use original S175 mean-surge drift data and a resolved fixed spectral grid | Matched-input Octave runs reproduce the first-order-only model's acceleration. The source-supported mean load removes the tested head-seas speed stops without changing physical limits or teaching thrust | Xuan requested repair; implementation and evidence in `docs/MOTION_VALIDATION.md` |
| 2026-10-29 | Retain cross-machine vessel-source acceptance; do not expand algorithm-version migration in this prototype | Prepared-byte identity is unsuitable as the only cross-machine startup check; the current work targets motion behaviour | Xuan's review discussion |
| 2026-10-29 | `blender/3d-demo` and `web/3d-scene` stay two lines: one tests the Blender models, the other the hand-built three.js scene. `blender/3d-demo` copies the four storm scenes from `web/3d-scene` unchanged and reads its scene format | Compare both looks before choosing; no merge conflicts in `web/game` | Parke |
| 2026-10-29 | Deck stowage: units fill slots category by category and only where a box stands below; deck-318 and deck-320 drop one tier | The categories need 160, 161 and 159 half-slots of 162, so filling each tier on its own left deck-320 hanging over an empty slot | Parke (on `blender/3d-demo`) |
| 2026-10-29 | Night scenes are graded blue with a soft cold ambient and dim warm deck lights; navigation lights at their usual places | All four storm scenes start at night (peak − 20 min); real sun position, but a black frame shows nothing. Ambient and deck lights are a viewing aid, not data | Parke (on `blender/3d-demo`) |
| 2026-10-29 | 3D demo uses three.js 0.170 from a CDN importmap (no package.json) and its own Gerstner sea, not web-ocean | web-ocean is WebGPU-only, heavy (6–8 ms/frame on an RTX 5070 Ti) and can only read wave height back from the GPU; an analytic sea gives the water height anywhere, which floating containers and splashes need. Hs follows the data; Tp and direction stay at the first sample for now | Parke (on `blender/3d-demo`) |
| 2026-10-29 | S175 model is low-poly at 1:1 scale, origin at the centre of gravity (2.55 m aft of midships, waterline), bow −Z in glTF; containers are separate ISO-size models placed by `web/game/cargo-layout.js` | Pose comes straight from the simulation; each deck unit can fall on its own `cargo_lost` event. Hull lines and 15.4 m depth are illustrative, not the real S175 offsets | Parke (on `blender/3d-demo`) |
| 2026-10-29 | 3D weather comes only from data: sun position from time and place, whitecaps from Copernicus wind, cloud and rain from ERA5 through Open-Meteo (no account; CC BY 4.0, credited on screen) | Rain had been guessed from wind and the sky was fixed. Two teammates were refused by the Climate Data Store sign-up as robots | Team (Yan) |
| 2026-10-29 | Storm steering: rudder yaw limit grows with (speed through water / 3 m/s)²; PID autopilot, with the integral active only once the ordered heading is reached; storm scenes use 1.5 MN thrust and 5 m/s | With a fixed 30 MN·m limit and a PD autopilot, the ship could not turn off head seas in any of four 9–12 m storms. At the 23 Jan 2026 peak a 90° turn now ends about 11° past the order after 15 min at 5 m/s. At 3 m/s the ship is still 39° short, so speed for steerage becomes part of the lesson | Team (Yan; Xuan's review not needed) |
| 2026-10-29 | Cargo `threshold_g` 0.30 → 0.14 for new scenarios; the frozen Menorca scenario keeps 0.30 | At 0.30 no heading lost cargo even at Hs 8.2 m. At 0.14, only beam seas at the Ushant peak lose cargo (18 TEU in 20 min); every other heading, and every heading at Hs 6.1 m, loses none. Evidence in `docs/PROTOTYPE_IMPLEMENTATION.md` | Yan (merged without review; needs Xuan's review) |
| 2026-10-29 | Superseded: horizontal-only Coriolis replacement | It reduced acceleration but broke the full no-work structure. Full coupling is restored by the motion repair above | Earlier Yan change; replaced after Xuan's review |
| 2026-10-29 | Merge the fresh-clone fix, CI and the storm fixes straight into `experiment`, without a PR | Time before the video; the user asked for it | Yan |
| 2026-10-29 | Accept a prepared S175 file whose provenance matches the pinned MSS source, not only the pinned bytes | Prepared bytes differed between machines with identical source files, so the game failed on every fresh clone | Yan (on `ci/fresh-clone`; needs Xuan's review) |
| 2026-10-29 | Add CI: locked setup, vessel preparation, tests and a scripted game run on every push and PR | Catch "works on one machine" before teammates or the video recording hit it | Yan (on `ci/fresh-clone`) |
| 2026-10-29 | No database or extra cloud service during the event | Gridded data belongs in files; Copernicus already serves it on demand; fewer accounts to manage | Yan |
| 2026-10-29 | Large data stays out of git; each teammate downloads with a script; web pages use small exported JSON | GitHub limits files to 100 MB; a year of fields is about 0.8 GB | Yan |
| 2026-10-29 | Direction is TIDE (continuous ship handling, escape and cargo loss), replacing the SeaSense route-choice prototype | Defined by Weixuan in `docs/Product.md`; richer decisions and consequences | Team |
| 2026-10-27 | One PR per complete, working feature | Small PRs were hard to review (#10 and #11 merged the same commit) | Team (requested by Jiahe) |
| 2026-10-25 | No AI co-author or attribution lines in commits and PRs | Team preference | Team |
| 2026-10-25 | Never fabricate data or story numbers; label anything authored | Credibility with Copernicus and ocean-science judges | Team |

## Decisions needed

- **Daylight storm scenes.** The four scenes start 20 min before each peak, which is night every time. Proposal: export daylight windows within each storm's ±24 h subset, on the repaired motion model. Needs Yan (`pipeline.export_scene`).
- **Cargo loss in the 22 Oct scene.** Beam seas strip all 320 deck units (480 TEU) in about 15 minutes at `threshold_g` 0.14. Check with Xuan whether that calibration should hold after the motion repair.

- **Region.** Proposal: Bay of Biscay and the Western Approaches (12°W–1°W, 42°N–52°N) instead of the Balearics.
  - The Balearic summer 2026 data peaks at Hs 4.25 m, which is the current scenario itself.
  - Over 2025-10-29 → 2026-10-29 the Biscay area had 34 storms (90th-percentile Hs ≥ 6 m in a sub-area, 3-hourly global wave analysis/forecast), mostly November–April. The strongest was Hs 12.2 m off Ushant on 2026-10-22 21Z.
  - The IBI regional products cover it at about 3 km, hourly.
  - Proposed by Yan; needs the team, especially Xuan, whose scenario builder assumes the current area.
- **Steering above 5 m/s.** At 7 m/s a 90° turn overshoots by about 30°. With stern-quartering seas the ship then surfs past 10 m/s through the water, and the run stops at `model_limit`. Storm scenes use 5 m/s until this is handled.
- **Frontend.** Direction from the user (29 Oct): 3D, after Uncharted Waters (follow camera over the sea) and Seafarer: The Ship Sim (container ship).
  - Built on `web/3d-scene`: three.js, one box per deck cargo unit, Copernicus sea state at the ship, replaying recorded runs.
  - Not yet connected to `game/session.py` for live play. Unity and Blender are not used. A Blender glTF ship model can replace the procedural hull later.
- **Which storms become scenarios.** Candidates:
  - 2026-10-22, rapid growth;
  - 2026-01-08, 71 kn wind;
  - 2026-01-23, 114 h long;
  - 2025-12-18, swell-dominated, Tp 19 s.
- **Menorca validation reports.** `scenarios/menorca-20260909/validation.json` and `game-validation.json` were produced before the dynamics fix. Rerun them, or mark the scenario as superseded if the region changes.
- **Motion fidelity.** Source-consistent arithmetic and synthetic survival do not establish experimental storm accuracy; comparison requires suitable vessel-response evidence.
- **Forecast archive.** Extend `.github/workflows/forecast-archive.yml` to the chosen region. Copernicus does not keep past forecast runs, so every day not archived is lost. Coordinate with Xuan's workflow changes on `experiment`.

## Owner actions

- [x] Review the vessel compatibility fix and the Coriolis replacement; retain source compatibility and restore the original coupling.
- [x] Verify the selected cargo rule against reference responses, live storm inputs and pause/save/restore checks.
- [ ] Repository admin: after the first CI run, decide whether `main` and `experiment` require CI to pass.
- [ ] Each teammate who runs scenarios: a free Copernicus Marine account and `copernicusmarine login`.

## Later / parked

- Daily scenario selection from the latest Copernicus analysis and forecast ("today's most dangerous sea").
- GitHub Pages deployment of static pages (storm animation, recorded replays); needs team agreement.
- Online play, leaderboard and teacher view (a database only becomes useful then).

## Log

- 2026-10-30 — Live stack cargo implementation:
  - Selected C50=0.5g, log dispersion 0.35 and one-second continuous overload. Used exact endpoint body angular terms and sorted-capacity prefix tracking without per-step NumPy arrays.
  - Preserved grade events and stable unit IDs, and verified JSON save/restore during unfinished overload. Updated frozen gameplay, its checksum and the scenario builder defaults; old cargo snapshots are rejected by algorithm identity.
  - Archived previous cargo implementations and tests by original directory structure. The runtime module has no version suffixes or archived-code dependency.

- 2026-10-30 — Stack cargo candidate:
  - Added five-box joint failure with fixed stack capacities, consecutive-overload counters and protected hold cargo. Scanned C50=0.4/0.5/0.6g and retained the independent candidate at matched 0.5g for comparison.
  - Replayed the existing motion traces without changing dynamics. Checked streaming/batch equivalence, four half-step cases and local runtime/allocation costs.
  - Kept the study in English with accident background and numerical comparisons. Kept generated numerical evidence under ignored `data/runs/`, with comparison tables in the study.

- 2026-10-30 — Cargo model sensitivity study:
  - Added an offline experiment and numerical evidence for 0.14g, 0.30g, local cargo-point acceleration, dispersed securing capacity and peak/cumulative ablations.
  - Separated the 2016 APL England 37-box occurrence from the 2020 50-box occurrence; used final investigation counts and actual loaded-box denominators.
  - Defined later cargo and ship-damage integration boundaries. Kept current scenarios, runtime equations and session state unchanged.

- 2026-10-29 — Fix deferred-resume pause editing:
  - Reset the screen pause-transition flag when a pending resume starts. An immediate second pause now reopens the heading/speed editor.
  - Added a regression covering the old worker finishing between display updates.

- 2026-10-29 — Remove project-defined speed ceilings:
  - Removed runtime speed rejection and the target-speed ceiling. Source-table endpoint lookup remains a nonblocking quality flag.
  - Completed 27 synthetic 12/14/16-knot trials, including reruns of all seven former speed stops. The high-speed matched-force MSS replay passes.
  - Extreme following-sea outputs reach about 50 knots; these remain simulation outputs for later ship/cargo damage work, not calibrated ship performance.

- 2026-10-29 — Remove project-defined angle cutoff:
  - Deleted `max_attitude_deg` and its rejection branch, including the setting in the authored Menorca scenario. Retained the MSS equations, environmental coverage checks and genuine numerical/singularity checks.
  - Reran the formerly blocked 16 m beam-seas case: 1,200 s completed, peak roll 35.89 degrees. Earlier below-cutoff runs remain unchanged.

- 2026-10-29 — Motion repair requested by Xuan:
  - Preserved the local working tree and backed up the previous prepared vessel. Restored full MSS Coriolis coupling and added applied-force physical regressions.
  - Executed GNU Octave 11.3.0 reference calculations with identical states, matrices, currents, forces and RK4, including exact storm-force replays.
  - Replaced uniform spectral cells with fixed logarithmic cells and added original S175 diagonal mean longitudinal drift. Kept actuator and applicability limits unchanged.
  - At that stage, passed 186 tests and completed 16 of 17 synthetic 20-minute trials; the remaining case reached a TIDE-only 30-degree cutoff. That cutoff was later removed as recorded above. Removed the temporary experiment review; current evidence is in `docs/MOTION_VALIDATION.md`.

- 2026-10-29 — Local integration review requested by Xuan:
  - Fast-forwarded `experiment` through `4880170` and restored the uncommitted TUI, docs and tests. Kept the recovery stash and a separate local backup.
  - Removed the obsolete `--plain` instruction from the new contribution guide; the full-screen TUI remains the sole interactive mode.
  - Passed 168 tests. Compared old and new dynamics under controlled 8.2 m head seas; both completed 20 minutes, with recorded peak speed reduced from 8.184 to 3.911 m/s. The spectrum-resolution warning remains.
  - Recorded the then-open reproducibility questions; the subsequent review retained cross-machine startup support and excluded migration work. Full historical storm calibration remains unverified locally.
- 2026-10-29 — Parke:
  - Built the S175 and container models in Blender (`assets/blender/s175.blend`, exports in `web/game/assets/`).
  - Added the 3D replay page and `pipeline.export_replay`; a full Menorca run (beam-sea leg at 100°) peaks at 2.6° roll and loses no cargo, so the video needs a storm scenario.
  - 3D replay now plays the four Biscay storm scenes from `web/3d-scene` (scene picker); cloud cover and rain come from their ERA5 columns.
  - Lost containers slide off the low side, fall, splash, float on the analytic sea and sink (`web/game/cargo-fall.js`, no three.js, reusable; optional slow motion).
  - Fixed the deck stowage so no box hangs over a gap; Blender preview re-placed from the same rule.
  - Found: all four storm scenes are at night and were exported before Xuan's motion repair (`b115b91`); the 22 Oct scene loses all 480 deck TEU in 15 min of beam seas. See "Decisions needed".

- 2026-10-29 — Yan:
  - Reviewed `experiment`; found that the game fails on fresh clones (vessel checksum).
  - Fixed it and added CI on `ci/fresh-clone`.
  - Scanned Copernicus waves for Europe (past 30 days, then one year).
  - Downloaded one year of Biscay waves, wind and currents locally.
  - Built a 34-storm catalogue.
  - Added this file and CONTRIBUTING.md (AGENTS.md is git-ignored for personal agent files).
  - Tested cargo loss in the Ushant storm. The threshold was too high, and head seas stopped the model through a spurious surge force.
  - Fixed the dynamics, recalibrated the cargo threshold to 0.14 g and added a regression test.
  - Merged everything into `experiment`.
