# Game cargo damage study: accident scale, S175 response and algorithm comparison

This study compares the archived category rule, the initial individual-box
candidate and the stack rule now used by live sessions. The selected median
capacity is C50=0.5g; 0.4g and 0.6g remain comparison settings.

## 1. Real accidents and relative losses

A container count measures physical boxes. TEU measures capacity. These units
must not be mixed. Loss fractions below use
`containers lost overboard / containers actually carried on the voyage`.
Nominal vessel capacity is listed separately. Containers damaged but retained
aboard are excluded from overboard counts; they still represent cargo damage.

| Accident | Vessel size | Conditions and response | Lost / carried | Box loss fraction | Damaged but retained aboard |
| --- | --- | --- | --- | --- | --- |
| APL England, 18 August 2016, Great Australian Bight [A] | About 277 m LOA; 5,780 TEU | 17.9 kn; 6 m seas and 4–5 m swell; sudden roll of about 25°. The report identifies high GM, top-tier weights and base-socket failure | 37 / unknown | Cannot calculate | Not specified in the cited passage |
| APL England, 24 May 2020, off Sydney [A] | Same vessel; 40 m breadth | Recorded 6–9 m seas and about 4–4.5 m swell; heavy rolling. Corroded fixed securing devices and high-cube stowage contributed to failure | 50 / 3,161 | **1.58%** | 63 |
| YM Efficiency, 1 June 2018, off Newcastle [B] | About 269 m LOA; 32.2 m breadth; 4,250 TEU | About 3–4 kn; recorded 6 m seas and 5 m swell; sudden rolls approaching 30° to either side. Losses were confined to two bays, with stowage forces exceeding limits | 81 / 2,249 | **3.60%** | 62 |
| MSC ZOE, 1–2 January 2019, north of the Wadden Islands [C] | 395.4 m LOA; 59 m breadth; 19,224 TEU | Beam seas; Hs increased from about 5 to 6.5 m; Tp about 11.8–12.5 s. Shallow-water conditions involved motion accelerations, green water and wave impacts | 342 / 8,062 | **4.24%** | No consolidated count used here |
| MAERSK ESSEN, 16 January 2021, north of Hawaii [D] | 366.45 m LOA; about 13,600 TEU | Hindcast swell Hs about 6 m, period 15 s; two short episodes of heavy rolling. A mechanical inclinometer indicated 26° to either side, subject to measurement limitations | **689 / 6,643** | **10.37%** | 258 |
| ZIM Kingston, 21 October 2021, off Vancouver Island [E] | 260.05 m LOA; 4,253 TEU | Observed waves about 5–6 m; drifting in near-head seas; rolls reached 36° to either side during about 1.5 minutes. The investigation identified parametric rolling | 109 / 1,965 | **5.55%** | Damage occurred; not quantified here |

The 37-container APL loss was the **2016 related occurrence**, not the 2020
accident investigated by the linked report. The 2020 manifest of 3,161 containers
cannot serve as the 2016 denominator. Assuming only 20/40 ft boxes, the 37 lost
containers represent 0.64–1.28% of nominal TEU capacity. That range is not a
fraction of the cargo actually carried.

Early MAERSK ESSEN reports estimated about 750 lost containers. The final
investigation confirmed 689. Its introductory vessel description gives about
13,600 TEU, while the loading section uses 13,566 TEU; neither capacity is used
as the box-count denominator here. YM Efficiency's 2,249 cargo containers come
from the final investigation's voyage bay plans and should not be mixed with
counts in other briefings.

ZIM Kingston model tests reproduced severe rolling at conditions including
Hs 5.6 m and Tp 13.5 s. They estimated transverse accelerations of about
0.9–0.95g at upper outboard container positions. These are model-test estimates,
not onboard measurements, and are not directly equivalent to this tool's
gravity-corrected demand. [E]

Mapping the known **whole-vessel box loss fractions** to TIDE's 800 containers
gives approximately 13, 29, 34, 83 and 44 lost boxes. These numbers indicate
scale; they are not target answers for S175 in the same wave heights. Real
vessels differ in deck-cargo fraction, GM, dimensions, stack height and securing
condition. ZIM carried 1,273 containers on deck, giving a deck-box loss fraction
of 8.56%. TIDE places only 40% of its boxes on deck.

### Primary sources

- [A — ATSB MO-2020-002 final investigation, including the 2016 related occurrence](https://www.atsb.gov.au/investigations/351-mo-2020-002). See occurrence, related occurrences, findings and ship details.
- [B — ATSB MO-2018-008 final investigation](https://www.atsb.gov.au/investigations/344-mo-2018-008). See occurrence, container loss, stowage and ship details.
- [C — MSC ZOE international investigation report](https://onderzoeksraad.nl/wp-content/uploads/2023/11/internationale_toedrachtsrapport_msc_zoe.pdf). See the cargo table, weather and appendix A; see also the [Dutch Safety Board explanation](https://onderzoeksraad.nl/en/onderzoek/safe-container-transport-north-of-the-wadden-islands-lessons-learned/).
- [D — DMAIB final report, published 28 February 2022](https://dmaib.dk/media/7853/maersk-essen-loss-of-cargo-on-16-january-2021.pdf). See printed pages 5, 13, 20–24, 43–45 and 51.
- [E — Canadian TSB M21P0297 final report](https://www.tsb.gc.ca/eng/rapports-reports/marine/2021/m21p0297/m21p0297.html). See sections 1.1, 1.3, 1.13.2, 1.18.4 and 2.1; an [official PDF](https://www.tsb.gc.ca/sites/default/files/2024-09/M21P0297-ENG-AAR_0.pdf) is also available.

These are accident samples. They lack a complete denominator of voyages with
no loss under comparable conditions. They can constrain the scale of severe
events, but cannot establish a per-voyage probability of container loss.

## 2. Problems with the archived 0.14g algorithm

The original category rule is preserved in `archive/game/cargo.py`:

\[
d=|a_{CG,y}-g\sin\phi\cos\theta|/g,
\quad D_c=\int [\max(m_c d/0.14-1,0)]^2\,dt/60.
\]

The multipliers are `m = [1, 1.15, 1.3]`. Each category contains about 106–107
deck boxes that share one `D_c`. Each box receives a failure-dose threshold
within the narrow range 0.8–1.2. Sustained exposure can therefore push a whole
category through all its thresholds in a short interval.

The 0.14g setting is gameplay tuning from the previous motion model, not a
measured breaking strength for container securing systems. The former Menorca
scenario used 0.30g. Both values remain historical comparison baselines.

For cargo seed 1 and constant demand, the 0.14g version gives these theoretical
failure times:

| Constant effective transverse demand | First box lost | All 320 deck boxes lost |
| --- | --- | --- |
| 0.20g | 65.6 s | 390.5 s |
| 0.30g | 15.1 s | 54.9 s |
| 0.42g | 5.7 s | 17.9 s |

Constant demand differs from sinusoidal rolling. This table only demonstrates
the dose formula's time scale. The existing gravity-projection sign is correct:
in the FSD convention, the y component of `R.T @ [0,0,g]` is
`+g sin(roll) cos(pitch)`. A new test verifies this sign. The motion equations
remain unchanged.

## 3. Algorithms compared

| Algorithm | Failure unit | Demand | Failure rule |
| --- | --- | --- | --- |
| Legacy `response_damage_v1` | Box thresholds sharing three category doses | CG demand with multipliers 1/1.15/1.3 | Quadratic cumulative overload above 0.14g, with a 60 s dose scale |
| Initial individual-box candidate | 320 independent deck boxes | Cargo-point demand at 5/10/15 m above CG | Peak above capacity; its separate cumulative term adds no baseline losses |
| Live `stack_overload` | 64 stacks of five boxes | Equivalent stack overturning-moment demand | Demand at or above capacity for 1 continuous second loses the whole stack |

Both candidates draw capacities once as
`C_i = C50 * strength * exp(sigma * Z_i)`, with standard-normal `Z_i`.
The initial candidate used C50=0.8g. The comparison scans
**C50=0.4/0.5/0.6g**. Live sessions and new scenario builds select
**0.5g, sigma=0.35, exposure_s=1, strength=1**.
These are authored gameplay settings. The capacity describes a box in the first
candidate and an equivalent stack in the second, not measured lashing strength.

Both calculate cargo-location acceleration as
`a_P = a_CG + alpha cross r + omega cross (omega cross r)` and subtract body-axis
gravity. For equal box masses at heights 5/7.5/10/12.5/15 m above CG, the stack's
moment-weighted effective height is `sum(h²)/sum(h) = 11.25 m`:

\[
d_s=\frac{|\sum_i m h_i(a_{P_i,y}-g_y)|}{g\sum_i m h_i}
   =\frac{|a_{CG,y}-g_y+11.25(\dot p-qr)|}{g}.
\]

Thirty-two stacks contain 20 ft boxes and 32 contain 40 ft boxes. Their mapping
partitions the original 320 deck IDs without mixing sizes within a stack.
The 480 hold boxes remain protected; maximum loss is 320 boxes / 480 TEU.
Each stack stores one capacity, one consecutive-overload counter and one failed
flag. A sub-capacity sample resets the counter. A failed stack cannot fail again.
The one-second gate rejects short spikes; it is not fatigue across separate waves.
Its duration rounds up to the fixed integration clock, independently of display rate.
Live demand uses the already computed endpoint body angular acceleration, not
differences between output samples. Save/restore retains unfinished intervals.

## 4. Numerical comparison

The comparison reuses 33 S175 motion traces and four half-step traces. Each sea
case lasts 1,200 s, with wave seeds 1/2/3 and cargo seed 101. Baseline integration
is 0.05 s; wind/current are zero. Wave updates remain 0.1 s, with 128 frequency
cells and 12 direction cells. The motion equations and forcing are unchanged.
Six additional 20-minute beam-sea runs at Hs 8.2/16 m record endpoint angular
acceleration directly. Their live-kernel results match the stack counts below.
All entries count lost boxes, in wave-seed order where applicable.

| Sea | Legacy 0.14g | Independent C50=0.8g | Independent C50=0.5g | Stack C50=0.5g |
| --- | --- | --- | --- | --- |
| Calm | 0 | 0 | 0 | 0 |
| Hs 3 m, Tp 8 s, beam, target 3 m/s | 0 | 0 | 0 | 0 |
| Hs 6 m, Tp 11.5 s, beam, target 3 m/s | 0 | 1 / 1 / 1 | 11 / 17 / 23 | **5 / 15 / 15** |
| Hs 8.2 m, Tp 11.5 s, beam, target 3 m/s | 0 / 179 / 0 | 5 / 14 / 3 | 70 / 117 / 67 | **65 / 90 / 65** |
| Hs 16 m, Tp 19 s, beam, target 3 m/s | 320 / 320 / 320 | 152 / 153 / 70 | 290 / 290 / 221 | **265 / 265 / 185** |

Higher losses relative to the initial candidate come primarily from capacity
retuning. Grouping changes correlation and loss increments, not necessarily the
expected loss count. At unchanged stack C50=0.8g, losses are 0 / 5 / 0 in 8.2 m
beam seas and 125 / 125 / 65 in 16 m beam seas. The independent 0.5g column
controls for retuning; load geometry and failure timing still differ.

Stack sensitivity at sigma=0.35 and one-second continuous overload:

| Input | C50=0.4g | C50=0.5g | C50=0.6g |
| --- | --- | --- | --- |
| Hs 6 m beam sea, three seeds | 45 / 60 / 65 | 5 / 15 / 15 | 0 / 0 / 0 |
| Hs 8.2 m beam sea, three seeds | 125 / 145 / 100 | 65 / 90 / 65 | 30 / 65 / 25 |
| Hs 16 m beam sea, three seeds | **310 / 310 / 260** | **265 / 265 / 185** | **215 / 215 / 130** |
| Prescribed 8° roll, 15 s period, 60 s | 0 | 0 | 0 |
| Prescribed 15° roll, 15 s period, 60 s | 85 | 35 | 5 |
| Prescribed 25° roll, 15 s period, 60 s | 215 | 140 | 90 |
| Prescribed 36° roll, 15 s period, 60 s | 300 | 255 | 185 |

The 24 prescribed-roll cases fix CG, with no sway or pitch. They are response
tests, not accident reconstructions. At C50=0.5g, 25° rolling with 10/15/20 s
periods loses 190/140/125 boxes in 60 s. Repeating the unchanged 15 s signal for
300 s adds no losses. The gate does not model long-term deterioration.

The 16 m stack results at C50=0.5g represent 57.8–82.8% of deck boxes; at 0.4g,
81.3–96.9%. These severe gameplay settings can exceed the real accident loss
fractions in section 1. They do not constitute an accident fit.
The full grid also scans sigma=0.2/0.35, exposure_s=0.5/1/2, three cargo seeds
and securing-strength multipliers; all results remain in the evidence file.
Halving the integration step to 0.025 s leaves stack losses unchanged in the
four checked cases: 65/90 boxes for 8.2 m beam seas and 265/265 for 16 m beam
seas, wave seeds 1/2. This is a limited numerical check, not physical validation.

## 5. Computation cost

Offline batch comparison evaluates several cargo rules on one saved motion
trace without integrating the ship again. Streaming advancement updates cargo
once per motion step. Their final stack summaries agree in tests and benchmark
cases. Only streaming advancement reports newly failed stacks at each step.

The runtime sorts capacities once. A binary search finds the overloaded prefix;
only entering stacks get new start ticks, and only newly failed stacks generate
box IDs and events. Lower-capacity stacks cannot have shorter consecutive
exposure than higher-capacity stacks under this shared load. This avoids a
64-stack array update and temporary NumPy arrays on every step. Work per step
is O(log(stacks) + entering stacks + failures). Distinct per-stack loads would
require different interval tracking.

Local medians use three repetitions, 24,000 samples per case, Darwin arm64,
Python 3.12.14 and NumPy 2.5.4. Runtime calls include point-demand calculation
and event generation, but exclude motion integration, I/O and sample construction.
The archived array prototype accepts precomputed demand:

| Case, seed 1 | Legacy rule, microseconds/step | Array prototype, microseconds/step | Live stack rule, microseconds/step |
| --- | --- | --- | --- |
| Calm | 2.60 | 7.58 | **1.98** |
| Hs 8.2 m beam | 2.47 | 7.67 | **2.07** |
| Hs 16 m beam | 3.08 | 7.95 | **1.99** |

The paired benchmark gives about a 3.7–4.0x reduction relative to the array
prototype, even with live demand and event processing included. The cargo state
serializes to about 1.9–2.1 KB, versus about 80 KB for the original per-box state;
this also reduces session-copy and snapshot work. This comparison concerns the
cargo portion, not the complete session or complete game CPU cost.
Cargo-only `deepcopy` medians fall from about 3.0 ms to 59 microseconds in a
separate three-by-100-call check (`data/runs/cargo-runtime/state-copy.json`).

The batch evaluator uses runtime capacities and tests the longest continuous
overload interval for each capacity. It processes one history at a time, using
O(samples + stacks) temporary memory. On the recorded inputs, runtime, batch
and archived array-reference final losses agree. Full measurements remain in the
`data/runs/cargo-runtime/results.json`. Timings depend on background load;
observed live calls range from about 1.1 to 2.5 microseconds/step across local runs.

## 6. Reproduction and scope

```bash
uv run python -m pipeline.cargo_study --replay data/runs/cargo-study --output data/runs/cargo-runtime --benchmark
uv run python -m pipeline.cargo_study --replay data/runs/cargo-study-half-step --output data/runs/cargo-runtime-half-step
uv run python -m pytest -q
```

Generate motion tapes with
`uv run python -m pipeline.cargo_study --output data/runs/cargo-study --workers 3`.
Generated output stays under ignored `data/runs/` directories. Results include
source/trace checksums and parameter grids. The independent candidate remains
a baseline. Numerical artifacts are not committed alongside the document.

Validation: **220 active Python tests passed**. Cargo checks cover joint loss,
ID/TEU mapping, protected holds, exact overload boundaries, reset and duplicate
prevention, runtime/batch/reference agreement, independent states, JSON restore
mid-overload, mandatory grade pauses and old-save rejection. Endpoint angular
fields and split advancement are tested. The local CLI also completed a
120-second run with the updated pinned scenario. A live 20-minute Hs 8.2 m
beam-sea Session lost 65 boxes / 95 TEU, paused at grade changes and restored
its saved cargo state exactly (`data/runs/cargo-live-storm/verification.json`).

The live implementation is `game/cargo.py`; comparison helpers remain under
`pipeline/`. Previous implementations and tests retain their directory structure
under `archive/game/`, `archive/pipeline/` and `archive/tests/`. Production code
does not import them. The default test suite excludes archived tests.
No dependencies were added. Old cargo snapshots are rejected by the existing
algorithm fingerprint; they are not converted to the new state format.

Replay remains valid because loss does not alter vessel mass or hydrodynamics.
Whole-stack overboard loss, equal tier masses and centreline positions remain
approximations. Neighbouring-stack collapse, green-water impacts, vertical
unloading and hull flexibility are excluded. S175's fixed restoring matrix does
not model wave-induced periodic stability changes. These are gameplay settings,
not measured securing limits. Historical mission verification is reported in
[GAME_VERIFICATION.md](GAME_VERIFICATION.md).
