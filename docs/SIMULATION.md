# Foundational Simulation Toolkit

This toolkit advances one S175 design-waterline vessel in historical marine
fields. It uses Python, NumPy, SciPy and xarray. MATLAB and Octave are not runtime
dependencies.

## Run from the repository root

```bash
uv sync --locked
uv run python -m pipeline.prepare_vessel
uv run python -m game.simulation --seconds 60
uv run python -m pipeline.validate_toolkit --seconds 120
uv run python -m pytest
```

`pipeline.prepare_vessel` downloads three files from the pinned MSS commit,
checks their SHA-256 hashes, and converts the source data. Existing files with
incorrect hashes cause an error. Original data stay in `data/raw/mss`; converted
arrays stay in `data/processed/s175`. These directories are ignored by Git.
Source attribution and conversion assumptions are in `data/vessels/s175`.

The example writes `data/runs/toolkit-demo.json`. It reports the command,
trajectory, response, cumulative exposure, quality flags and stopping reason.
It is a numerical exercise using existing September data, not a curated mission.
The local `tide` project entry point is independent of this module command.

## Python interface

```python
from datetime import datetime, timezone
from game.simulation.environment import CopernicusEnvironment
from game.simulation.runtime import Command, Simulation
from game.simulation.vessel import load_s175

with CopernicusEnvironment.from_directory("data/copernicus") as environment:
    simulation = Simulation(load_s175(), environment, seed=1)
    state = simulation.initial_state(
        datetime(2026, 9, 9, 8, tzinfo=timezone.utc),
        lon=3.5, lat=40.0, heading_deg=90, speed_mps=2,
    )
    result = simulation.advance(state, Command(100, 3), duration_s=60)
    # Inspect stopping_reason before continuing. The input state is unchanged.
    result = simulation.advance(result.state, Command(80, 2), duration_s=60)
```

### Coordinates, units and state

The numerical state is `[u,v,w,p,q,r,N,E,D,roll,pitch,yaw]`. Body axes are
forward-starboard-down (FSD), with forces and moments about the centre of gravity.
The local frame is north-east-down (NED). Internal units are metres, seconds,
radians, newtons and newton-metres. API headings are degrees true, clockwise from
north; commanded speed is forward speed through water in m/s.

Position uses a fixed local spherical-earth tangent approximation. This is for
the small open-water domain, not global or polar navigation. Actual heading,
course over ground, speed through water and speed over ground are separate outputs.
The velocity state already includes current; it must not receive current a second
time during position integration.

The returned state includes controller references/integrals, wave phases, the
fixed-clock tick, held wave force, threshold state and exposure integrals. Callers
must retain the whole state, not reconstruct it from position and heading.
Keep the same Simulation configuration when continuing a state. Independent
trials use copies and the same numerical path as execution.

### Numerical progression

Default integration uses RK4 at 0.05 s. Wave loads update at 0.1 s. Trajectory
records use a 1 s clock, with a final endpoint for shorter segments. Durations
must be positive multiples of the integration step. The integration clock never
restarts at a decision boundary. An uninterrupted run and calls split at valid
ticks therefore preserve the same dynamics, controller and stochastic history.

Heading commands drive a rate-limited reference and bounded yaw moment. Speed
commands drive an acceleration-limited reference, drag feedforward, PI feedback
and bounded longitudinal force. These are teaching actuators, not a model of
S175's actual propeller and rudder. Their limits are explicit in `Settings`.

Wind loads use the MSS Isherwood (1972) empirical model and the S175 wind/current
demo geometry recorded in `data/vessels/s175/wind.json`. Forces enter surge and
sway, with a yaw moment; no direct wind roll moment is assumed. Relative airflow
uses true north/east wind minus vessel **ground** velocity. Calm air therefore
still creates air resistance for a moving vessel. Loads are held over each 0.05 s
integration step and are included in state/trajectory output. Pass
`wind_parameters=None` to `Simulation` for an explicitly wind-free numerical
experiment. Default geometry is only for this S175 example; synthetic or other
vessel models must supply their own parameters or disable wind loads.

`duration_reached` is a normal completion. `paused` responds to an optional
callback between steps. Domain/time boundaries, missing environmental values,
model applicability limits and numerical failure stop progression at the last
valid state. No step teleports the vessel out of invalid water.

## Environmental and wave assumptions

- The sampler uses total `VHM0`, `VTPK`, `VMDR`, horizontal current vectors and
  horizontal wind vectors. Wind now drives direct empirical forces and yaw moment.
  If an optional wind file is absent, the input defaults to still air; this is an
  explicit missing-source assumption, not reconstructed historical wind.
- Optional `thetao` sea-surface temperature is sampled spatially and held for
  its UTC day. It is returned as `sst_c` (or `None` without the source). It does
  not change forces, seawater density, damping or exposure thresholds.
- Scalar fields use spatial and temporal interpolation. Wave directions use
  circular interpolation. Required missing cells and out-of-coverage requests
  are errors. There is no nearest-sea-cell replacement or extrapolation.
- Historical wave statistics drive a synthetic JONSWAP total-sea spectrum with
  fixed seed. This does not reconstruct actual individual historical waves.
  Wind sea and swell are not added to the total sea, which would double-count energy.
- Fixed frequency components retain their phases as the sampled sea changes.
  Signed encounter rates support following-sea overtaking. RAOs use complex
  interpolation so phases and direction wrap correctly.
- Forces use the source's zero-speed RAOs. Speed affects encountered wave phase;
  the implementation does not claim validated speed-dependent hydrodynamic loads.
  Deep-water wave dispersion and locally quasi-steady horizontal currents are
  modelling assumptions. Harbour, shallow-water and grounding physics are absent.
- The source frequency range bounds the spectrum. Lost wave energy is reported,
  not silently restored. Quality flags identify truncation, under-resolution and
  spectrum assumptions. A peak outside the available range stops the calculation.
- The six-DOF model includes restoring and damping couplings but uses linear
  hydrostatics and constant, spectrum-reduced added-mass/damping matrices. It is
  not a nonlinear capsize, slamming or container-failure model. The default 30°
  attitude stop is a numerical applicability guard, not a certified safe limit.

## Response and exposure

Outputs include roll, pitch, heave and CG lateral dynamic acceleration. Exposure
contains elapsed seconds, integrals of squared roll/pitch/acceleration, and time
above declared teaching thresholds. The latter default to 10° roll and 0.1 g
lateral acceleration; these are not cited S175 operating limits.

Exposure integrates elapsed physical time on every numerical step. Checkpoint
count and model-call count do not affect it. Threshold entry/exit events are
reported without determining mission outcomes. Event exit uses a 10% hysteresis
band to avoid repeated threshold chatter; duration integrals always use the actual
threshold. Cargo loss belongs to future
game rules.

## Verification and remaining limits

Tests cover interpolation and bounds, matrix/coordinate properties, calm-water
equilibrium, current advection, wave determinism, phase continuity, control
limits, step refinement, rollback at stops and split-run equivalence. A real-data
test runs after vessel preparation. Legacy pipeline tests remain separate.

`tests/reference/s175_reference.m` is an optional headless Octave/MATLAB fixture
generator for the pinned MSS checkout. It exports matrices, a derivative,
fixed-force trajectory and deterministic wave loads. It is not required to use
or test the Python toolkit. No MATLAB/Octave parity is claimed until those
fixtures have actually been generated and compared.

Source-data consistency and numerical checks do not establish experimental
validation of the complete vessel/controller model. Future physical calibration
must use compatible S175 reference measurements, not another vessel's parameters.

## Coarse simulation acceptance

The six environmental groups are wave height, wave direction, wave period,
current, wind (speed/direction) and SST. They are separate from the vessel's six
degrees of freedom. The first five affect dynamics; SST provides context.

`pipeline.validate_toolkit` runs 11 trials from the same historical start and
seed: the original fields, still air, doubled wind, no waves, higher waves,
rotated waves, longer period, no current, warmer water, a heading change and a
speed change. Modified fields are counterfactual numerical tests, not additional
historical reconstructions. The report checks completion, finite states, actual
source availability, expected response to each forcing/control change and exact
motion invariance to SST alone. It records endpoint differences, attitude,
velocity, wind forces, exposure and wave-quality flags in
`data/runs/toolkit-assessment.json`.

Passing this assessment supports coarse functional state/motion simulation in
the tested open-water window. It does not establish loading-specific windage,
accurate manoeuvring performance, gust response, wind-induced heel, severe-sea
fidelity or a validated error percentage against experiments. Wind geometry,
the yaw-moment reference point, damping and teaching controls still require
calibration. The optional Octave generator also exports basic wind-force cases;
cross-runtime comparison remains unexecuted until Octave is available.

In the 120 s check at 2026-09-09 08:00 UTC, 3.5 E / 40 N, all 11 trials
completed. Removing true wind changed the horizontal endpoint by about 0.88 m;
removing current changed it by about 18.39 m. The 3 m/s command reached about
2.86 m/s, while a 1 m/s command reached about 0.98 m/s. A 90° to 0° heading
command reached only about 85.76° in that interval: steering is slow with the
current damping and actuator assumptions. The report includes heading/speed
errors; its pass criterion does not claim calibrated command tracking.
