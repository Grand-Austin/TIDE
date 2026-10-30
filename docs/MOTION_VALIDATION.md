# S175 motion repair and MSS comparison — 29 October 2026

## Subsequent manoeuvring upgrade — 30 October 2026

The four Biscay missions now select `s175_maneuvering`, separating native
Son–Nomoto manoeuvring from the original S175 six-DOF wave response.
See [TURNING_MODEL.md](TURNING_MODEL.md). The actuator-only integration and
damping restoration below remain historical comparison results, not validation
of the new combined model or its mission feasibility.

## Son–Nomoto actuator integration — 30 October 2026

The first integration selected the physical propeller/rudder mode described
in [CONTAINER_ACTUATOR.md](CONTAINER_ACTUATOR.md). It retains the prepared hull
matrices and has an independently reproduced original `container.m` reference.
The fixed force limits below describe the retained `teaching` mode. The new
mode uses source rudder/RPM constraints and remains an uncalibrated combination;
calm-water checks still show slow turning. No new mission pass is claimed.

## Steering restoration — 30 October 2026

The storm branch's later yaw-damping override has been removed. Simulation uses the prepared vessel's full
`D` matrix unchanged. The yaw actuator again has the original fixed 30 MN m limit, without a speed-squared
multiplier. The PID heading integral, anti-windup and quicker storm reference settings remain in place.

The removed override changed `D66` from 4.2165e10 to 2.6308e9, reducing it to 6.24% of its prepared value,
while retaining the off-diagonal coupling. The resulting full damping matrix had a negative eigenvalue
(-2.7435e8). The pinned MSS `computeManeuveringModel` rejects a 30 s yaw time constant for these equivalent
matrices: it would require negative added viscous damping, whereas the admissible upper time constant is
about 1.97 s. These bounds apply to this data and equivalent-matrix construction, not every possible S175 model.

A no-input check at zero displacement, without wind, waves or current, uses roll rate 0.001 rad/s and yaw rate
0.0004347365205377971 rad/s. Including full Coriolis and cross-flow drag, the prepared vessel loses 7181.55 W
of kinetic energy at that instant; the former override gains 290.29 W. Python and the pinned MSS executed in
Octave agree on these values. A regression test checks that every committed storm configuration preserves the
prepared damping and dissipates energy for this state. Other tests check the fixed yaw bound in both turn
directions at multiple speeds, and that the PID integral still operates near the ordered heading.

The fixed 30 MN m limit is the original TIDE teaching actuator bound, not an experimentally calibrated MSS
rudder model. Restoring it preserves the model boundary; it does not establish real-ship turning accuracy.
Scenario validation must be rerun after these settings change. Existing scene JSON records remain historical
outputs of their recorded motion configurations until they are regenerated.

## Original motion repair

The runtime retains the full pinned MSS Coriolis coupling. The horizontal-only
replacement from `4880170` has been removed. No mass, restoring, damping,
actuator limit was increased to obtain completion. The TIDE-only 30-degree
roll/pitch cutoff and approximately 20-knot instantaneous/command speed ceilings
were removed at the user's request. The original MSS example has no such stops.
The speed-table endpoint describes data coverage, not a maximum motion speed. Genuine numerical and Euler
singularity checks remain active.

## Diagnosis

The restored Python derivatives and matched-force trajectories agree with the
original `hydroVessel` executed in GNU Octave 11.3.0. This includes a replay of
the 400–430.55 s interval immediately before a synthetic 12 m head-seas TIDE speed
stop. The excessive acceleration is therefore reproducible in the combined
first-order-wave / constant-matrix / bounded-control model; it is not explained
by a Python kinetics-port error in that test.

Before the force repair, an 8.2 m head-seas run spent 97% of its accepted steps
at the 300 kN braking limit. The source S175 MAT file also contains a
longitudinal mean wave-drift table which the first-order-only runtime did not use.
MSS `HYDRO/utils/readdata/read_veres_WD.m` documents its units as
`F = drift_coefficient * wave_amplitude^2`.

The runtime now sums the diagonal mean surge contributions from this table over
the same wave components. The first-order oscillating force remains separate.
The table's real frequency, direction and five-speed axes are used; the five
extra padded speed slots are excluded. Direction is periodic. Speed lookup uses
quasi-steady forward horizontal water speed; unsupported speeds use the nearest
table endpoint and emit `mean_drift_speed_clipped`. This is not an astern model.

The speed quality flag does not stop or clip the vessel state. Abnormal motion
remains available for future ship-damage and cargo-damage rules.

This added load is an extension of the original first-order MSS example, not a
claim that the example already applied it. The underlying MSS kinetics remain
unchanged. There is no full QTF, difference-frequency load, invented lateral/yaw
drift, propeller model or experimental strong-sea calibration.

## Spectrum repair

Fixed logarithmic frequency cells replace fixed uniform cells. Component
identities and phases still persist when the sea changes. With the source S175
range and 128 cells, the largest cell carries about 8.3% of energy at Tp 11.5 s,
instead of 13.4%. The underresolution threshold remains 10%; it was not relaxed.

For Tp 5, 8.39, 11.5, 15 and 19 s, spectral-energy quadrature agrees with dense
integration within 0.1%, and all source force-RAO RMS integrals within 3%.
These are quadrature checks, not percentages of real-ship accuracy. Deliberately
coarse grids still emit `spectrum_underresolved`; frequency-range and retained
energy checks remain active.

## Executed reference results

The reference shares matrices, state, ocean current, control/external force
history and the 0.05 s RK4 clock. Conversion from the raw MAT file and wave loads
are checked separately. The mean-surge integration is independently calculated
in Octave from the original drift table.

| Check | Maximum absolute difference |
| --- | --- |
| Four state derivatives | 5.6e-17 |
| 60 s fixed-force trajectory | 1.5e-14 |
| First-order wave force | 1.91e-6 N or Nm, relative norm 1.2e-15 |
| Mean longitudinal wave force | 5.9e-11 N |
| Matrices | Relative norm below 6e-16 |

A separate repaired 600–660 s storm-force replay also matches the original
kinetics. Small executed fixtures are stored under `tests/fixtures/mss/`.
Reference source files are pinned by `data/vessels/s175/reference-manifest.json`.
Detailed local inputs, outputs and logs are under `data/runs/motion-repair/`.
The compact numerical summary is stored at
`data/vessels/s175/motion-validation.json`.

## Synthetic pressure tests

All conditions are controlled synthetic tests, not reconstructed Ushant weather.
The default seed-1 matrix runs calm water; Hs 8.2 m at head, bow-quarter, beam,
stern-quarter and following headings; head-seas initial/target speeds of 1.5,
3 and 5 m/s; Hs 12 m / Tp 15 s and Hs 16 m / Tp 19 s head seas; and 16 m beam
seas. Head-fast, 12 m head and following cases are repeated for seeds 2 and 3.

| Case, seed 1 | Before mean-force repair | After repair |
| --- | --- | --- |
| 8.2 m head, 3 m/s | Peak 9.55 m/s; braking saturation | Completes 1,200 s; peak 4.21 m/s |
| 8.2 m head, 5 m/s | Speed stop at 686.75 s | Completes 1,200 s; peak 6.05 m/s |
| 12 m head, 3 m/s | Speed stop at 430.55 s | Completes 1,200 s |
| 16 m head, 3 m/s | Speed stop at 1,000.30 s | Completes 1,200 s |
| 16 m beam, 3 m/s | Earlier TIDE-only cutoff at 61.05 s | Completes 1,200 s without the cutoff; peak roll 35.89 degrees |

All 17 cases complete 20 minutes. The formerly blocked 16 m beam-seas case was
rerun after removing the cutoff. The other 16 cases stayed below the old cutoff,
so removing that unused condition does not change their recorded equations or
states. Their completed results were retained rather than rerun.
No case stops for `spectrum_underresolved`, which is a quality flag rather than
a stopping condition. Slow head-seas cases can temporarily reverse their
horizontal forward velocity and report the drift-table endpoint assumption.

The current Menorca direct strategy, seed 1, also completed a full game mission:
escape at 9,139.25 simulated seconds with zero cargo loss. This checks current
playability; it does not replace the complete three-seed/four-strategy matrix.

## Expanded 12–16-knot tests

Initial and target speeds are 12, 14 and 16 knots. Each speed uses nine
conditions: calm; Hs 8.2 m / Tp 11.5 s at head, bow-quarter, beam, stern-quarter
and following headings; Hs 12 m / Tp 15 s head seas; and Hs 16 m / Tp 19 s
at head and beam headings. Seed 1, no wind/current, unchanged teaching control
limits, 0.05 s integration, 128 wave components and 1,200 s per case.

All 27 cases complete. All seven cases previously blocked by the TIDE speed
ceiling were rerun after its removal, plus one completed control case. The other
19 completed cases never activated that ceiling; their results remain unchanged.

| Initial/target speed | Completed | Peak sampled water speed | Peak sampled roll |
| --- | --- | --- | --- |
| 12 kn | 9/9 | 49.13 kn | 30.74 degrees |
| 14 kn | 9/9 | 46.10 kn | 28.98 degrees |
| 16 kn | 9/9 | 49.71 kn | 37.43 degrees |

These maxima use 1-second records. Subsecond peaks can be higher. The high
following-sea speeds are abnormal outputs of this combined wave/controller
model, not evidence of realistic S175 performance. No physical state was clipped
or stopped to hide them. Future ship/cargo damage rules will use these outputs.
The source drift table continues to use endpoint lookup outside its speed axis
and reports the nonblocking `mean_drift_speed_clipped` quality flag.

The 600–660 s applied-force replay of the 12-knot following-sea run also agrees
with original MSS in Octave: maximum state difference 1.14e-13. This verifies
the kinetics under identical applied forces; it does not validate the combined
forcing/control assumptions. A separate above-30-degree replay agrees to
1.59e-15. The Python suite passes 196 tests.

## Reproduce

```bash
uv run python -m pipeline.prepare_vessel
uv run python -m pipeline.validate_mss
uv run python -m pipeline.validate_storm
uv run python -m pipeline.validate_storm --speed-knots 12 14 16 --cases calm head bow beam stern-quarter following head-12m head-16m beam-16m
uv run python -m pipeline.validate_storm --seed 2 --cases head-fast head-12m following
uv run python -m pipeline.validate_mss --replay data/runs/storm-validation/head-fast-seed1-n128.npz --start-s 600
uv run python -m pytest -q
```

Octave is needed only for live reference execution, not gameplay or the default
test suite. A stale prepared vessel must be regenerated to include drift data.
The exact reported historical 867 s failure cannot be replayed without its
original inputs. Cargo calibration and the complete historical strategy matrix
must be reassessed after the changed motion/forcing; previous mission reports
remain labelled historical evidence.
