# Methods

How SeaSense turns Copernicus Marine data into cases, and how far the results can be trusted. Numbers below come from `python -m pipeline.validate_summer` on the summer 2026 data in [`data/copernicus/`](../data/copernicus/README.md) (1 July to 30 September 2026, 92 days). They are a feasibility check, not final case statistics.

## Data

| Layer | Dataset | Use |
|---|---|---|
| Waves | `cmems_mod_med_wav_my_4.2km_PT1H-i` (MED reanalysis, interim) | Significant wave height `VHM0`, mean direction `VMDR` (from), peak period `VTPK` |
| Currents | `cmems_mod_med_phy-cur_my_4.2km_PT1H-m` | Surface `uo`, `vo` (toward) |
| Wind | `cmems_obs-wind_glo_phy_nrt_l4_0.125deg_PT1H` | Evidence layer and case narrative; not used in the simulation |
| Forecast | `cmems_mod_med_wav_anfc_4.2km_PT1H-i`, `cmems_mod_med_phy-cur_anfc_4.2km-2D_PT1H-m` | Archived daily from 26 October 2026 (`.github/workflows/forecast-archive.yml`) |

All times in the data are UTC. The Balearic Islands are UTC+2 in summer; case texts use local time.

## Routes

Three routes from Alcúdia (Mallorca) to Ciutadella (Menorca), defined in `pipeline/routes.py`: A direct, B northern loop, C southern lee. They start and end at harbour exit points in open water, because the 1/24° grid does not resolve the harbours. The harbour approach is not simulated. A test checks that every sample along every route lies on a sea cell of the model grid.

## Simulation

`pipeline/simulate.py` moves a vessel at 6 kn through the water, plus the current component along its heading, in 5-minute steps. At each step it reads the sea state at the vessel's position and time (nearest sea cell, linear in time) and classifies the encounter by the angle between the bow and the incoming waves:

| Relative angle | Category |
|---|---|
| below 60° | head seas |
| 60° to 120° | beam seas |
| above 120° | following seas |

A passage is unsafe if any step has beam seas above `beam_hs`, any wave height above `max_hs`, or arrives after `arrive_by`. The best route for a departure time is the fastest safe one; if none is safe, the right call is not to sail. A **tipping point** is a departure time at which the best route changes.

## Thresholds

The thresholds are teaching values, not safety limits for any real vessel. Real limits depend on the vessel, its loading and its crew.

They are anchored to the WMO sea-state code (Douglas scale), which Spanish marine forecasts for the Balearics also use:

| WMO code | Description | Significant wave height |
|---|---|---|
| 3 | Slight | 0.5–1.25 m |
| 4 | Moderate | 1.25–2.5 m |
| 5 | Rough | 2.5–4 m |

- `max_hs` = 2.5 m: the upper bound of "moderate". A small training vessel does not set out into "rough".
- `beam_hs` = 1.0 to 1.5 m: beam seas are more dangerous than head seas of the same height because of rolling. IMO MSC.1/Circ.1228 (*Revised guidance to the master for avoiding dangerous situations in adverse weather and sea conditions*) describes why heading relative to the waves matters. It is written for ships, not small craft, so it supports the reasoning, not the numbers.

The head/beam boundary of 60° is a modelling choice with no single published value. Because of that, every result below is checked across three threshold settings and five boundaries.

## Results

### Tipping-point days exist

Route-to-route flip days (the best route changes from one route to another during the day), no arrival deadline:

| Setting (`beam_hs`, `max_hs`) | Flip days |
|---|---|
| 1.0 m, 2.0 m | 9 |
| 1.25 m, 2.5 m | 5 |
| 1.5 m, 2.5 m | 3 |

1 July and 9 September flip at all three settings. On flip days the wind is from the north-east (30–44°), the Gregal, not the Tramontana.

### Sensitivity to the head/beam boundary

`python -m pipeline.validate_summer --beam-sector`, flip days per boundary:

| Setting | 50° | 55° | 60° | 65° | 70° |
|---|---|---|---|---|---|
| 1.0 m, 2.0 m | 10 | 13 | 9 | 13 | 6 |
| 1.25 m, 2.5 m | 8 | 7 | 5 | 3 | 5 |
| 1.5 m, 2.5 m | 2 | 2 | 3 | 4 | 4 |

Flip days exist at every combination, but which days flip changes. 1 July flips in 14 of 15 combinations, and 9 September in 13 of 15.

On 9 September the main case route A meets the waves at 61–68°, just past the 60° boundary. The best route by departure time (half-hour steps from 06:00 local, `-` = no safe route):

| Boundary | 1.0 m, 2.0 m | 1.5 m, 2.5 m |
|---|---|---|
| 50° | `AAC---…` | `AAAA---…` |
| 55° | `AAC---…` | `AAAA---…` |
| 60° | `AACC---…` | `AAAACCCC---…` |
| 65° | `AACC-C---…` | `AAAACCCC---…` |
| 70° | `AACCCC---…` | `AAAACCCC---…` |

What holds in every combination: the direct route works only for an early departure, and from 10:00 local at the latest no route is safe (from 08:00 with the strictest setting). What depends on the boundary: whether the southern lee route C opens a short window in between. With a narrow head sector (50–55°), C also meets beam seas and the window closes. Case texts should present the "leave early or not at all" decision as the robust finding and the A-to-C switch as setting-dependent.

### Candidate days

`python -m pipeline.mine` writes [`data/candidates.json`](../data/candidates.json). A day is a candidate only if the sea itself changes the decision: flips whose only cause is the arrival deadline are excluded. At the default limits (1.5 m, 2.5 m, 60°, arrival by 19:00 UTC) no day flips because of the deadline alone. Of the 21 candidates, two flip from one route to another at the default limits: 1 July (in 14 of 15 combinations) and 9 September (13 of 15). Five more are go/no-go days, and 14 flip only under other settings.

### Cases

Four candidates are exported as cases (`pipeline/export_cases.py`). Titles, goals and key evidence are chosen by hand after reading the data; every number in the case texts comes from the case file. Times below are local (UTC+2).

| Case | Day | Kind | What the data shows | Goal |
|---|---|---|---|---|
| c01 The quiet swell | 1 July | route | Wind under 14 kn, but a north-north-east swell of 1.4 to 1.7 m (period about 7 s) arrives from late morning; wind sea stays under 0.4 m (heights from the model's wind-sea and swell partitions). Wind under 14 kn is the channel mean; during the day single cells reach about 19 kn. A until 09:00, C 09:30 to 13:00, B 13:30 to 14:00 | `wind_vs_swell` |
| c04 First light only | 26 July | go/no-go | Steady 16 to 18 kn north-east wind, waves growing from 1.1 m to almost 2 m. Only a 06:00 or 06:30 departure stays within the limits | `time_window` |
| c07 The Gregal morning | 9 September | route | Wind swings from SSE to NE between 09:00 and 11:00. A until 07:30, C 08:00 to 09:30, no safe route from 10:00 | `time_window` |
| c08 After the Gregal | 10 September | go/no-go | Wind eases from 24 kn to calm, but a 1.5 to 2 m swell keeps coming from the NNE. A and C have more than 2 h of beam seas at every departure; only B, which meets the swell head-on and then from astern, is safe from 12:00 to 14:00 | `beam_seas` |

1 July and 9 September are the most robust days. 2, 3 July and 17 September are left out because they repeat the c08 pattern (no safe route until the swell drops, then only B) or flip mostly because of the deadline.

No summer candidate has a flip caused by current (reason `slower`): averaged over the channel, surface currents never exceed 0.8 kn in summer 2026 (99 % of grid values are under 0.9 kn), too little to change which route is faster. The `current_time` goal has no case until autumn and winter data are mined.

### Other numbers

With `beam_hs` 1.5 m, `max_hs` 2.5 m and arrival by 19:00 UTC:

- The shortest route A is not the best choice at an 08:00 UTC departure on 7 of 92 days.
- Days with any flip, including flips caused by the arrival deadline: 7 of 92.

## Known limits

- **Forecast versus analysis.** Past forecasts are not archived by Copernicus Marine. Cases before 26 October 2026 show the analysis up to departure time; the prototype's forecast for 9 September is illustrative and labelled as such.
- **One season.** The results cover a calm summer. Autumn and winter data will change the counts.
- **Vessel model.** Constant speed through the water, no wave-induced speed loss, no sails.
- **Model resolution.** The 1/24° grid (about 4 km) smooths coastal shelter; harbour approaches are not simulated.
