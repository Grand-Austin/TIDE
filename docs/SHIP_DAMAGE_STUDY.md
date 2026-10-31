# Game ship loss: accident evidence and motion-based rules

The live game now models total ship loss. A `ship_lost` event immediately ends the mission in failure and loses
all remaining deck and hold cargo. Motion equations and hydrodynamic data are
unchanged.

The rules combine a probability table for individual severe roll excursions,
accumulated severe-roll exposure, accumulated structural-overload exposure,
and accumulated fast-following-sea instability exposure.
Accident reports establish credible failure mechanisms and warn against low
angle cutoffs. They do not supply statistically calibrated casualty probabilities.
Every probability and exposure threshold below is an authored game assumption.

## Accident evidence

This selection includes 16 container-carrying ship casualties and serious
incidents. Survivors matter: a rule that sinks every ship at 25–40 degrees would
confuse container loss with ship loss. Reported wave heights, significant wave
height (Hs), inclinometer maxima and reconstructed accelerations are different
measurements. Unknown motion values remain unknown.

| Case | Size or cargo context | Reported conditions and response | Ship outcome and implication |
| --- | --- | --- | --- |
| [MSC Carla, 1997][napoli] | About 1,600 containers aboard | North Atlantic hull fracture followed a welded joint at an inserted lengthening section. The MAIB account does not give a usable roll/pitch threshold | Bow sank; stern recovered. Structural weakness can dominate. A universal angle cannot reproduce every hull fracture |
| [MSC Napoli, 2007][napoli] | 4,419 TEU | Up to 9 m waves; about 11 kn; heavy pitching, hull buckling and likely whipping contributions | Catastrophic failure near the engine room, flooding and abandonment; subsequently beached. Supports a structural path alongside roll failure; no measured pitch/acceleration failure threshold is given |
| [MOL Comfort, 2013][comfort] | 316 m; 4,382 containers / 7,041 TEU carried | Reported 6 m waves and 38 kn wind; two large port-bow waves followed by unusual hogging and water ingress | Broke into two sections, both eventually sank. Rigid-body motion is an imperfect proxy for hull-girder failure; actual bending and strength remain essential unknowns |
| [El Faro, 2015][faro] | 241 m; container/Ro-Ro vessel | Hurricane exposure, flooding, a reported 15-degree list and propulsion loss; progressive downflooding | Sank with all aboard. A modest sustained list with flooding differs fundamentally from a brief intact-ship roll. The game does not claim to reconstruct this flooding sequence |
| [Dongedijk, 2000][dongedijk] | 93 m container feeder | Calm weather; overloading, trim and course change; MARIN describes deck water at about 5 degrees, then heel beyond 20 degrees and further flooding | Capsized. Damaged/loading-dependent stability cannot be inferred from a universal intact-ship angle |
| [Deneb, 2011][deneb] | 101.13 m; 509 TEU | During loading, went from upright to resting against the quay at about 45 degrees in about 30 s; later about 50 degrees. Incorrect weights and inadequate loading stability contributed | Constructive total loss. Demonstrates a real 45–50-degree casualty state, not a measured free-rolling capsize threshold for an intact ship at sea |
| [Chicago Express, 2008][chicago] | Container ship in Typhoon Hagupit | BSU reports repeated violent rolls, an uncorrected inclinometer maximum of 44 degrees and an estimated 10 s event | Vessel survived; severe bridge accelerations caused a fatal injury. Short rolls above 40 degrees can be survivable |
| [Svendborg Mærsk, 2014][svendborg] | 347 m; about 8,160 TEU | Two extreme roll events, one reaching 41 degrees; observed regional Hs reached 13.4 m, exceeding forecasts | Survived and reached Málaga; 517 containers lost, 250 damaged. Supports survival space for isolated large rolls |
| [APL England, 2016][apl] | About 277 m; 5,780 TEU | About 6 m seas plus 4–5 m swell; sudden roll about 25 degrees | Survived; 37 containers lost. This is the related 2016 occurrence, not the 2020 voyage |
| [APL England, 2020][apl] | Same vessel | Heavy rolling in reported 6–9 m seas and 4–4.5 m swell; securing deterioration contributed | Survived; 50 containers lost. Cargo securing failure is not evidence of hull failure |
| [YM Efficiency, 2018][ym] | About 269 m; 4,250 TEU | Sudden rolls approaching 30 degrees; engine shutdown followed. Exact dynamic roll/acceleration histories were not recorded | Survived and regained propulsion; 81 containers lost. Propulsion interruption and cargo loss do not automatically establish total ship loss |
| [MSC ZOE, 2019][zoe] | 395.4 m; 19,224 TEU | Beam seas, shallow-water effects, accelerations, green water and wave impacts; Hs about 5–6.5 m | Survived; 342 containers lost. The present deep-water rigid-body model cannot reproduce the complete shallow-water impact mechanism |
| [CMA CGM G. Washington, 2018][washington] | Large container ship | Unexpected rolls about 20 degrees to each side in very heavy seas; stowage and container strength contributed | Survived; 137 containers lost, 85 damaged. No basis for treating a 20-degree roll as ship loss |
| [Maersk Essen, 2021][essen] | 366.45 m; about 13,600 TEU | Two heavy-roll episodes; mechanical inclinometer indicated about 26 degrees, with measurement limitations; hindcast swell Hs about 6 m | Survived; 689 containers lost, 258 damaged. Large cargo loss can coexist with a surviving ship |
| [ZIM Kingston, 2021][zim] | 260.05 m; 4,253 TEU | Parametric rolling reached 36 degrees to both sides. Model tests estimated about 0.9–0.95g transverse acceleration at upper outboard containers | Survived the rolling and subsequent fire; 109 containers lost. Upper-stack accelerations must not be treated as CG or whole-hull structural limits |
| [ONE Apus, 2020][apus] | 146,694 GT | Two heavy-roll episodes about 90 minutes apart, around 20 degrees then 25 degrees or more; about 5–6 m swells | Survived and diverted to port; final JTSB investigation counts 1,841 containers lost and 983 damaged. Severe cargo and hatch-cover damage does not imply total loss |

[napoli]: https://assets.publishing.service.gov.uk/media/547c703ced915d4c0d000087/NapoliReport.pdf
[comfort]: https://www.bahamasmaritime.com/wp-content/uploads/2020/10/BMA-Investigation-Report-Loss-of-the-MOL-Comfort.pdf
[faro]: https://www.ntsb.gov/investigations/AccidentReports/Reports/MAR1701.pdf
[dongedijk]: https://www.marin.nl/api/marin/downloads/get-download/497/0/514/block_files_uL7YdZ8wQzAm0/-7yTpo4sv4U27TVouhg1LK50Esg%3D/R80_p4_Dongedijk.pdf
[deneb]: https://maritimesafetyinnovationlab.org/wp-content/uploads/2019/03/CIAIM-Deneb-Capsize-during-cargo-operations-June-2011.pdf
[chicago]: https://www.atsb.gov.au/sites/default/files/media/3446314/bsu%20investigation%20report%20510_08%20-%20cmv%20chicago%20express.pdf
[svendborg]: https://dmaib.com/media/8621/svendborg-maersk-heavy-weather-damage-on-14-february-2014.pdf
[apl]: https://www.atsb.gov.au/investigations/351-mo-2020-002
[ym]: https://www.atsb.gov.au/investigations/344-mo-2018-008
[zoe]: https://onderzoeksraad.nl/wp-content/uploads/2023/11/internationale_toedrachtsrapport_msc_zoe.pdf
[washington]: https://www.gov.uk/maib-reports/loss-of-cargo-containers-overboard-from-container-ship-cma-cgm-g-washington
[essen]: https://dmaib.dk/media/7853/maersk-essen-loss-of-cargo-on-16-january-2021.pdf
[zim]: https://publications.gc.ca/collections/collection_2025/bst-tsb/TU3-12-21-0297-1-eng.pdf
[apus]: https://jtsb.mlit.go.jp/eng-mar_report/2024/2022tk0001e.pdf

The reports support a broad **40–60-degree transition region for game design**,
not a universal physical capsize angle. Intact survivors exceed 40 degrees;
loading/flooding casualties can fail far below it. Deneb was supported by a quay
and had deficient loading stability. Its observed angle cannot calibrate an
intact S175 probability. The current source supplies small-angle restoring
coefficients, not the loading-specific nonlinear GZ curve or downflooding angles
needed for a physical threshold.

[IMO's dynamic stability framework](https://www.imo.org/en/ourwork/safety/pages/shipdesignandstability-default.aspx)
distinguishes dead-ship behaviour, excessive acceleration, pure loss of stability,
parametric rolling and surf-riding/broaching. This game detects severe responses;
it does not implement or certify those five assessment methods. In particular,
constant restoring coefficients do not generate genuine wave-dependent loss of
stability or validated parametric resonance.

## Short roll excursions

| Absolute roll angle | Authored probability of loss during one excursion |
| --- | --- |
| At or below 35 degrees | 0% |
| 40 degrees | 0.5% |
| 45 degrees | 5% |
| 50 degrees | 30% |
| 55 degrees | 65% |
| 60 degrees | 90% |
| At or above 65 degrees | 100% |

Linear interpolation connects the rows. These values deliberately replace the
initial discussion's unsupported 2% at 50 degrees. They are **game balance
parameters**, not percentages measured in accident investigations.

One excursion starts when absolute roll first reaches 35 degrees. It ends when
absolute roll returns to 20 degrees or less. Each side-to-side half-roll can
therefore produce one excursion. On entry, a seed and excursion counter select
one critical angle from the table's inverse cumulative distribution. Increasing
roll is checked against that same angle. The model does not redraw every frame
or every second. Holding a fixed angle does not repeatedly apply its probability.
A higher peak always carries at least as much risk for the same draw.

The counter and sampled critical angle are saved. Cargo randomness uses its own
stream. Reading status, changing playback rate, pausing and restoring do not
provide fresh draws. The sampled critical angle stays hidden from the player
and Director.

With independent excursions and no other failure path, ten excursions reaching
50 degrees have a combined game probability of `1 - 0.7^10`, about 97.2%.
This is a property of the authored rule, not an observed maritime casualty rate.

## Accumulated severe motion

At every accepted physics step of duration `dt`, the model adds:

```
roll_load = max((abs(roll_deg) - 20) / 20, 0)
roll_dose += roll_load**2 * dt / 30

structural_load = max(
    abs(pitch_deg) / 10 - 1,
    hull_vertical_g / 0.75 - 1,
    cg_horizontal_g / 0.5 - 1,
    0,
)
structural_dose += structural_load**2 * dt / 60
```

Any exposure dose reaching 1 causes total loss. Taking the maximum structural load
avoids counting the same pitching motion once as angle and again as acceleration.
Roll and structural channels remain separate. Ordinary response below the
onsets adds nothing. Severe exposure is retained through quiet intervals and
save/restore; there is no automatic repair during one mission. These are
irreversible game exposure budgets, not measured metal-fatigue damage or a
simulation of water volume inside the hull.

For a roll excursion that survives its initial random threshold:

| Constant response | Exposure to deterministic loss, starting with zero dose |
| --- | --- |
| 30-degree absolute roll | 120 s |
| 40-degree absolute roll | 30 s |
| 45-degree absolute roll | 19.20 s at the 0.05 s clock |
| 50-degree absolute roll | 13.35 s at the 0.05 s clock |
| 60-degree absolute roll | 7.50 s at the 0.05 s clock |
| 20-degree absolute pitch | 60 s |
| 1.5g hull-point vertical acceleration | 60 s |
| 1g CG horizontal acceleration | 60 s |

Oscillating motion accumulates less than a constant peak held for the same
period. Quoting only a roll maximum cannot establish the exposure dose.

A pitch angle of at least 35 degrees, a hull-point inertial vertical acceleration
of at least 3g, or a CG horizontal inertial acceleration of at least 2g also
causes immediate total loss. These conservative gameplay boundaries cover
responses grossly outside this model's useful physical regime. They are not
class-approved structural limits or experimentally measured S175 failure values.

## High-speed following and quartering seas

This path covers dangerous fast following seas even when the resolved roll and
CG accelerations remain small. It adds one saved scalar, `following_dose`.
It does not clip speed or alter motion equations.

The hull heading must be within 45 degrees of the wave travel direction
(relative wave-from bearing 135–225 degrees). The deep-water peak wavelength
`lambda = g * Tp**2 / (2*pi)` must fall within `0.6–2.3 Lpp`. This applicability
window comes from the wave-crest stability discussion in
[IMO MSC.1/Circ.1228, sections 3.1.2 and 4.2.1](https://wwwcdn.imo.org/localresources/en/OurWork/Safety/Documents/Stability/MSC.1-CIRC.1228.pdf).
The combination below is an authored gameplay proxy, not an IMO failure test.

```
along_wave = dot(actual_horizontal_water_velocity, wave_travel_unit_vector)
c = g * Tp / (2*pi)
critical_speed = max(1.8 * sqrt(Lpp) * 1852/3600, 0.75*c)
sea_load = min(1, Hs / (0.04*Lpp))
response = max(1, abs(roll_deg)/30, abs(body_yaw_rate)/radians(1))
following_load = max(along_wave/critical_speed - 1, 0) * sea_load * response
following_dose += following_load**2 * dt / 30
```

Outside the heading/wavelength window, or at Hs=0, the new load is zero.
Actual north/east water velocity excludes current; ground speed and the player's
speed order do not substitute for it. The 1.8 coefficient uses knots as in the
IMO guidance. The additional 75% wave-speed gate is informed by
[USCG surf-riding background](https://www.dco.uscg.mil/Portals/9/DCO%20Documents/5p/5ps/Design%20and%20Engineering%20Standards/Naval%20Architecture%20Division/dtmb-2ndgen-is-rpt2011.pdf).
For S175 with Tp=11.5 s, the combined onset is about 26.2 kn along the waves.
Roughness weights the exposure; roll beyond 30 degrees or yaw rate beyond
1 degree/s further amplifies it. The 30 s budget is an authored game parameter.
Reaching a dangerous speed once does not cause instant loss. A normalized load
of 1 maintained for 30 s exhausts the new budget. Quiet intervals, pause and
restore retain accumulated exposure.

## Current ship-loss indicator categories

| Category | Indicators and terminal condition |
| --- | --- |
| Single severe roll excursion | Seeded probability table, 35–65 degrees; 50 degrees gives 30%, 55 gives 65%, 65 gives 100%. One draw per excursion |
| Accumulated severe roll/heel | Above 20 degrees; `((abs(roll)-20)/20)^2 * dt/30`; loss at dose 1 |
| Extreme pitch | Absolute pitch at least 35 degrees; immediate loss |
| Extreme inertial acceleration | Hull-point vertical at least 3g or CG horizontal at least 2g; immediate loss |
| Accumulated structural overload | Pitch above 10 degrees, hull-point vertical above 0.75g, or CG horizontal above 0.5g; maximum normalized exceedance squared, 60 s budget; loss at dose 1 |
| Fast following/quartering-sea instability | Matching heading/wavelength, actual along-wave overspeed, roughness and amplified roll/yaw response; 30 s normalized budget; loss at dose 1 |

## Response signals and covered situations

Accepted endpoint samples expose body rates `p, q, r`, angular accelerations
`p_dot, q_dot`, and inertial CG accelerations `ax, ay, az` expressed in body axes.
The translational acceleration includes `nu_dot + omega cross nu`; body velocity
derivatives alone would create false loads during rotating motion. No new
physics derivative evaluations are required.

Four representative hull points sit at `x = ±0.45 Lpp`, `y = ±0.4 B`, `z = 0`
relative to CG. Rigid-body kinematics give the exact maximum absolute vertical
acceleration over these four authored points:

```
hull_vertical_g = (
    abs(az)
    + 0.45 * Lpp * abs(p*r - q_dot)
    + 0.4 * B * abs(q*r + p_dot)
) / g
cg_horizontal_g = hypot(ax, ay) / g
```

This includes heave, pitch/roll angular acceleration and coupled body rotation.
It does not calculate local slamming pressure or whipping/bending stress.
Gravity is excluded from these inertial acceleration metrics; a vessel at rest
does not acquire a constant 1g damage load.

| Motion situation | Detection path | Limit |
| --- | --- | --- |
| Isolated violent roll, including a roll after a broach | Excursion probability and the 65-degree terminal boundary | No nonlinear restoring-energy calculation |
| Repeated large rolls or prolonged extreme heel | Roll exposure dose | No explicit flooding, free-surface or propulsion-failure model |
| Heavy head-sea pitching and repeated violent heave/pitch | Pitch and hull-point acceleration in structural dose | Proxy for structural overload; no hull-girder stress or slam detection |
| Severe sway, surge acceleration or coupled yaw/roll/pitch | Horizontal CG acceleration and coupled hull-point terms | A large yaw rate alone does not imply loss |
| Unusually high speed with weak acceleration in following/quartering waves | Following-sea instability dose | Guidance-based game proxy; the earlier 46–50 kn outputs remain model-quality concerns |
| Large static heave offset, high Hs, RAO speed clipping or numerical failure | No direct ship-loss trigger | These do not establish loss of buoyancy or a physical casualty |

Collision, grounding, fire, cargo-induced loading changes and internal flooding
are outside this motion-driven module. It will miss a weakened hull that fails
without a sufficiently severe resolved response. The model can also produce
unphysical severe responses; their terminal treatment is a game consequence,
not proof that a real ship would behave that way.

## Live integration, cost and verification

`game/ship.py` owns ship-loss rules. `Session` advances its small JSON state
before normal cargo and mission rules. A casualty at the same accepted endpoint
wins over escape, timeout and a cargo-grade pause. `CargoModel.lose_all` counts
only remaining cargo, including the protected hold cargo, and is idempotent.
The resulting cargo total is 800 units / 1,200 TEU lost, grade 5.

The terminal shows mission failure. Local and Director engines preserve the
terminal outcome. Further orders and resume are rejected. Saves made without
this ship algorithm are rejected by the session fingerprint rather than silently
starting their accumulated exposure at zero.

Initial numeric evidence is local under `data/runs/ship-damage/`; the revised
rule evidence is under `data/runs/ship-damage-improved/`. The document keeps
result tables; no numerical evidence is placed under `data/vessels/` or
`docs/evidence/`.

| Check | Result |
| --- | --- |
| Initial-rule baseline, before the roll/following-sea improvements | 44 tapes replayed; no ship loss |
| Peak roll across those full-resolution state tapes | 37.457 degrees |
| Peak pitch | 9.586 degrees |
| Estimated peak hull-point vertical acceleration | 0.854g |
| Estimated peak CG horizontal acceleration | 0.177g |
| Maximum final roll / structural dose | 0.00269 / 0.00020 |
| Default frozen scenario through actual scripted Session | 10 s; no casualty or cargo loss |
| Same live S175 Session with a prescribed initial 70-degree roll | Stops at the first accepted step, 0.05 s; `ship_lost`; all 800 units lost; terminal save restored exactly |
| Calm ship-rule cost, five repetitions of 100,000 calls | Median 1.71 microseconds per call on this machine; excludes physics, sample construction and persistence |
| Initial ship state serialized as JSON | 134 bytes |

The tape replay reuses previously generated motion rather than rerunning seas
or mission strategies. Translation/angular derivatives are estimated from the
0.05 s states by central differences. Force-update discontinuities can change
exact endpoint peaks, so this is a threshold sanity check. Live endpoint signal
checks use the actual derivative and nonzero coupled body rates in tests.

Deterministic fixtures cover both signs of extreme motion, repeated roll,
repeated pitch and translational overload, exact exposure boundaries at 0.05 and
0.025 s, one draw per excursion, distribution/monotonicity, JSON continuity,
first-step terminal priority, complete cargo accounting and prevention of resume.
A 5,000-draw distribution check compares the implemented table at its knots;
it tests the random rule, not real maritime casualty rates.

The complete active suite passes 270 tests. No full historical mission matrix
was rerun. The live catastrophe check prescribes an initial extreme roll; it
verifies integration and settlement, not wave-driven capsize prediction.

## Results after the roll and following-sea improvements

The same 44 existing motion tapes were replayed with the revised rule. No seas
or historical mission strategies were rerun. Three fast following-sea cases
now fail through `following_sea_instability`; the other 41 do not trigger loss.
These are game classification results, not established real-world outcomes.

| Original initial/target speed | Original full-tape peak | New loss time | Speed at new loss |
| --- | --- | --- | --- |
| 12 kn | 49.13 kn | 1,062.80 s | 47.63 kn |
| 14 kn | 46.10 kn | 1,038.70 s | 38.26 kn |
| 16 kn | 49.73 kn | 852.60 s | 47.37 kn |

The original full-tape maxima can occur after the new stopping time; they are
not maxima reached in a live game that has already ended. Reconstructed
accelerations use central differences, as in the baseline replay.

For 14 s sinusoidal rolls, the deterministic exposure-only timing below uses
cycle-average dose rates. It excludes the separate excursion probability table
and the starting phase of the oscillation.

| Roll amplitude | Previous cumulative-only estimate | Revised estimate |
| --- | --- | --- |
| ±30 degrees | No accumulation | 428 s |
| ±35 degrees | 10,525 s | 167 s |
| ±40 degrees | 1,981 s | 87 s |
| ±45 degrees | 760 s | 53 s |
| ±50 degrees | 389 s | 35 s |

A constant 30-degree heel for 30 s now uses 25% of the roll budget.
That establishes significant accumulated danger without asserting that every
ship rolling around 30 degrees for a few tens of seconds must sink.

On this machine the revised ship rule took median 2.28 microseconds per calm
call and 3.12 microseconds per eligible following-sea call (three repetitions
of 100,000 calls; excludes physics, sample construction and persistence).
Initial ship state is 157 JSON bytes. New north/east water-velocity sample
fields reuse already calculated motion values; no extra derivative is evaluated.
Tests cover both integration steps, heading wrap and quartering boundaries,
current-excluding water velocity, calm/head/beam/slow-motion exclusions,
wave-length eligibility, roll/yaw amplification, save/restore, and first-step
terminal cargo settlement.
