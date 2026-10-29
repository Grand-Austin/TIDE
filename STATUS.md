# Status

_Last updated: 2026-10-29_

**Stage:** PLAN stages 1–4 done on `experiment` (command-line game). Stage 5 partly done; stage 6 (frontend, Copilot) not started.
**Next:** agree the region and the frontend approach (see "Decisions needed"), then a playable storm scenario and the web frontend.
**Deadlines (CET; Finland is +1 h):** event opened Thu 29 Oct 16:00 · video due Sat 31 Oct 08:30.

## Branches

| Branch | What | State |
| --- | --- | --- |
| `main` | SeaSense route-choice prototype (PRs #4–#12) | Merged; kept as reference |
| `experiment` | TIDE simulation toolkit, scenario pipeline, rules, Director, terminal UI; fresh-clone fix, CI, storm dynamics fix, cargo calibration | Active line of work |
| `ci/fresh-clone` | Fast-forward merged into `experiment` on 2026-10-29 | Can be deleted |

## Decisions

Newest first. Proposed decisions move here once the team agrees.

| Date | Decision | Why | Decided by |
| --- | --- | --- | --- |
| 2026-10-29 | Storm steering: rudder yaw limit grows with (speed through water / 3 m/s)²; PID autopilot, with the integral active only once the ordered heading is reached; storm scenes use 1.5 MN thrust and 5 m/s | With a fixed 30 MN·m limit and a PD autopilot, the ship could not turn off head seas in any of four 9–12 m storms. At the 23 Jan 2026 peak a 90° turn now ends about 11° past the order after 15 min at 5 m/s. At 3 m/s the ship is still 39° short, so speed for steerage becomes part of the lesson | Team (Yan; Xuan's review not needed) |
| 2026-10-29 | Cargo `threshold_g` 0.30 → 0.14 for new scenarios; the frozen Menorca scenario keeps 0.30 | At 0.30 no heading lost cargo even at Hs 8.2 m. At 0.14, only beam seas at the Ushant peak lose cargo (18 TEU in 20 min); every other heading, and every heading at Hs 6.1 m, loses none. Evidence in `docs/PROTOTYPE_IMPLEMENTATION.md` | Yan (merged without review; needs Xuan's review) |
| 2026-10-29 | Surge, sway and yaw use Coriolis terms of horizontal motion only | In 8 m head seas, correlated heave and pitch pushed the ship from 3 to 10.3 m/s, past the model limit, so play stopped; the thruster could not brake it | Yan (merged without review; needs Xuan's review) |
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
- **Cargo calibration on full missions.** 0.14 g comes from 20-minute trials in one storm. Confirm it on a complete three-hour storm mission with `pipeline.validate_game` before the video.
- **Menorca validation reports.** `scenarios/menorca-20260909/validation.json` and `game-validation.json` were produced before the dynamics fix. Rerun them, or mark the scenario as superseded if the region changes.
- **Forecast archive.** Extend `.github/workflows/forecast-archive.yml` to the chosen region. Copernicus does not keep past forecast runs, so every day not archived is lost. Coordinate with Xuan's workflow changes on `experiment`.

## Owner actions

- [ ] Xuan: review the merged changes to `game/scenario.py` (vessel check), `game/simulation/dynamics.py` (Coriolis) and the cargo threshold.
- [ ] Repository admin: after the first CI run, decide whether `main` and `experiment` require CI to pass.
- [ ] Each teammate who runs scenarios: a free Copernicus Marine account and `copernicusmarine login`.

## Later / parked

- Daily scenario selection from the latest Copernicus analysis and forecast ("today's most dangerous sea").
- GitHub Pages deployment of static pages (storm animation, recorded replays); needs team agreement.
- Online play, leaderboard and teacher view (a database only becomes useful then).

## Log

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
