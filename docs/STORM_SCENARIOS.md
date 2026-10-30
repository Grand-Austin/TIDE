# Storm scenarios: build rules and validation report

`pipeline/storm_scenario.py` turns a catalogued storm (`data/storms/<region>.json`, data subsets in
`data/storms/<region>/<id>/`) into a playable escape scenario in `scenarios/<id>/`. A scenario is published only
if its validation report shows at least one way out.

## Build rules

- **Start time.** The 20-minute step within 12 h of the storm peak with the highest Hs at the start point while the
  sun stays above 10° (`pipeline.export_scene.daylight_start`). If those daylight seas are below 60 % of the
  storm's peak Hs, the scenario starts 20 minutes before the peak, at night.
- **Hidden region.** `pipeline.gameplay`'s rule: the convex envelope of cells with Hs at or above `region_hs_m`,
  frozen per minute. `region_hs_m` is the start's Hs minus 1 m, rounded down to 0.5 m (the storm core).
- **Start position.** The storm's peak cell, moved towards the nearest region edge until it is 15 km inside if it
  was deeper than 25 km. Edges along the data box and edges with land just beyond them are not exits.
- **Mission.** Two hours, 5 m/s initial speed, head seas initially. Storm settings: dt 0.1 s, wave forces every
  0.2 s, 1.5 MN thrust.
- **Weather.** Hourly ERA5 cloud cover and precipitation at the start point (Open-Meteo) in `weather.json`.

## Validation report: `scenarios/<id>/validation.json`

Written by `python -m pipeline.storm_scenario <id> --check`. Solvers, including a future Builder Agent pathfinder,
add strategies in the same shape.

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
- `reference` is the passing strategy with the least cargo lost, then the shortest time. Debriefs and hints may
  draw on it.
- A changed `scenario.json` or `gameplay.json` invalidates the report (checksums).

## For the Builder Agent and hints (planned, Xuan)

- A pathfinding solver (for example the isochrone method used in weather routing) proposes order sequences from
  the scenario's fields, then verifies each with a full session and appends it to `strategies`.
- Solvers see the hidden region and future fields; players do not. Hints drawn from a solution are extra
  information beyond the released reports, so they should be graded and cost judgement points.
