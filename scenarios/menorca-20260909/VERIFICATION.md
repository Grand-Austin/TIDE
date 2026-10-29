# Numerical verification

Local verification completed on 29 October 2026. The scenario uses the original committed environmental fields and existing S175 model; no physics thresholds were changed.

All 12 trials completed with a normal offline outcome. Every recorded position stayed within the authoring box (3.5–3.95 E, 39.7–40.15 N). All three seeds have at least one arrival and distinct accumulated response exposure across strategies. The exact definition checksum, source metadata, commands and results are in `validation.json`. Full trajectories remain in `data/runs/menorca-20260909/`.

| Strategy | Outcome across three seeds | Elapsed simulation time |
| --- | --- | --- |
| Direct, 3 m/s target | All reached the 1 km target disk | 8,295 s |
| Reduced, 1.5 m/s target | All exhausted the 3-hour budget; about 9.3 km remained | 10,800 s |
| Initial 90-degree command for 20 minutes, then target steering | All reached | 8,315 s |
| Hold zero speed target for 30 minutes, then escape | All reached | 10,270–10,305 s |

Commands are target heading and target speed, not instantaneous vessel states. The initial detour order does not imply that the slow teaching actuator reaches 90 degrees during that interval. Direct and detour response rankings vary with the metric and seed. Reduced speed is not automatically the lowest total exposure because the route takes longer and encounters a changing sea.

The test suite passes 102 tests, including collection integrity, official XML encoding and timezones, CAP filtering, report revisions, scenario rebuilding and split-run state continuity. A real AEMET public bulletin was collected twice with the same immutable filename. Its normalized record was successfully parsed and retained outside the source package; it is an October bulletin and is excluded from September evidence.

Authenticated Copernicus and historical CAP downloads were not run because local credentials are absent. Remote scheduled workflows were not executed. The historical coastal bulletin for 9 September remains unavailable. Supplemental-source research follows `archive/Data_needs.md`; ERA5, station observations and bathymetry adapters are deferred.

This report verifies a numerical exercise and interpretable exposure differences. Cargo-loss rules, a Director, professional route review, experimental model calibration and real cargo-failure prediction remain outside this delivery.
