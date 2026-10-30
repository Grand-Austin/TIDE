# Routing references and benchmark status

`benchmarks.json` pins the inspected sources, versions, URLs and hashes. It separates reproduced reference results from TIDE adaptations and from original benchmarks that were not run.

## SIMROUTE

- Source: https://github.com/ManelGrifoll/SIMROUTE
- Inspected commits: `7d9a3381b8078013f63c3007c10f60dcf4f19f93` (Palma case and report) and `e4fd57129f0eda34f7d3fb73278a2644f04d0b45` (current head).
- Licence: no licence file exists in either inspected tree. **No upstream code is copied into TIDE.**
- Reference case: Palma de Mallorca to Barcelona, 2020-01-20 09:00 UTC, 16.1 kn, grid step 1.5 nm, `WEN_form=1` (Bowditch).
- Published report: 9.40 h / 151.38 nm without waves, 14.97 h / 161.56 nm with waves, 16.93 h / 151.38 nm minimum distance with waves.
- Independent reproduction: 9.402636822 h, 14.973725553 h and 16.928160302 h with the same distances. The reference status is `independent_reproduction`.

The current SIMROUTE head is a later refactor and does not read the 2021 Palma case files directly. The benchmark snapshot above is therefore the pinned reference. TIDE does not run that external case during normal tests, because its wave array is not tracked here.

## VISIR-2

- Paper: https://doi.org/10.5194/gmd-17-4355-2024
- Code: https://doi.org/10.5281/zenodo.10960842 (Zenodo record 10960842, 2024-04-11).
- Licence: GPL-3.0-or-later. TIDE is MIT. **No VISIR-2 code is redistributed in TIDE.**
- Inspected algorithm: time-dependent Dijkstra, dynamic edge delays, current composition `SOG = w_parallel + sqrt(STW^2 - w_cross^2)`, and vessel performance curves for wave speed loss.
- Official small analytic-current benchmark: the original VISIR-2 code reproduced Techy Example 3 externally. Status: `official_analytic_current_reproduced`.
- Measured: `L/L0 = 1.017587668235335`, `T/T0 = 1.0568394582308234`.
- Official reference: `L/L0 = 1.00480754`, `T/T0 = 1.03038814`.
- Measured deltas: `deltaL = 1.271898%`, `deltaT* = 2.567122%`. Tolerance: 3% for both. Result: **PASSED**.
- The official console verdict was `Curr analytic PASSED`. The captured logs and command durations are in `visir2-run.json`.
- Source archive SHA-256: `62a3a79bb668e38459e35ca44114d07cdde12802df14ba0ca44bca75b5ed4a74`.
- Analytic input SHA-256: `01cd73822cef63e1f740848624b352f5b3931eddc4c34d7f3fcc75be7c3f9784`.
- Alboran current-and-wind full numerical benchmark: **NOT RUN**. Status: `not_run`. It needs the official graph, S175 performance models and large external environmental products.
- The runner command and offline rerun are in `REPRODUCE.md`. This pass is evidence for the official small analytic benchmark only.

## TIDE adaptations

Both searches use bounded local grids, bounded time knots and deterministic tie-breaking. They return command candidates only. Empirical speed models estimate route timing; they do not replace TIDE physics. Neither adaptation reproduces the complete upstream feature set.

- `simroute:astar_aertssen`: A* with SIMROUTE's Aertssen `WEN_form=2` speed loss, no current, and a midpoint time predictor. The reference exercise keeps faithful Bowditch `WEN_form=1`. Aertssen is used in play because Bowditch gives sub-knot speeds for S175 in storm seas.
- `visir2:td_dijkstra_aertssen_current`: time-dependent Dijkstra with the published current composition and an Aertssen proxy for the unavailable VISIR-2 S175 performance curve. Ground course and water heading are reported separately.

## Assumptions and limits

1. The search starts at the designer's fixed initial position and scenario start time.
2. Exit cells come from several scheduled region polygons, not only the nearest initial edge.
3. A route stops at its first point outside the region at the estimated arrival time.
4. Missing wave/current samples, land and domain edges are rejected. Diagonal moves also require both side cells.
5. Grid and time resolution are capped. The route is an approximation; the full session validates the commands.
6. An empty search result means the bounded search was exhausted. It is not proof that no route exists.
