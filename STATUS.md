# Status

_Last updated: 2026-10-29_

**Stage:** PLAN stages 1–4 done on `experiment` (command-line game). Stage 5 partly done; stage 6 (frontend, Copilot) not started.
**Next:** agree the region and the frontend approach (see "Decisions needed"), then a playable storm scenario and the web frontend.
**Deadlines (CET; Finland is +1 h):** event opened Thu 29 Oct 16:00 · video due Sat 31 Oct 08:30.

## Branches

| Branch | What | State |
| --- | --- | --- |
| `main` | SeaSense route-choice prototype (PRs #4–#12) | Merged; kept as reference |
| `experiment` | TIDE simulation toolkit, scenario pipeline, rules, Director, terminal UI | Active line of work |
| `ci/fresh-clone` | Fresh-clone fix, CI, CONTRIBUTING.md, STATUS.md (from `experiment`) | Pushed; no PR yet |

## Decisions

Newest first. Proposed decisions move here once the team agrees.

| Date | Decision | Why | Decided by |
| --- | --- | --- | --- |
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
- **Frontend.** Proposal: a web page (MapLibre map, instruments, controls, optional three.js roll/pitch view) talking to `game/session.py` through a small local HTTP server from the standard library. Unity and Blender are out for this event: a C# rewrite or bridge would not fit in about 40 hours. Needs Xuan.
- **Which storms become scenarios.** Candidates:
  - 2026-10-22, rapid growth;
  - 2026-01-08, 71 kn wind;
  - 2026-01-23, 114 h long;
  - 2025-12-18, swell-dominated, Tp 19 s.
- **Cargo calibration** for 6–12 m seas, so that good and bad handling produce clearly different losses.
- **Forecast archive.** Extend `.github/workflows/forecast-archive.yml` to the chosen region. Copernicus does not keep past forecast runs, so every day not archived is lost. Coordinate with Xuan's workflow changes on `experiment`.

## Owner actions

- [ ] Xuan: review the vessel-check change on `ci/fresh-clone`.
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
