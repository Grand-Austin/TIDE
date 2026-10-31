# S175 design-waterline reference

The source is MSS commit `ce0b40ed101411fc4eebd064b35c5a96916106ad`.
`manifest.json` records source URLs, SHA-256 values, loading condition and
coordinate conventions. The source is distributed under the included MIT licence.

Run `uv run python -m pipeline.prepare_vessel` from the repository root. Original
files remain in `data/raw/mss`; the numerical archive and conversion provenance
are generated under `data/processed/s175`. Neither data directory is committed.
The runtime reads the prepared archive without downloading anything.

Geometry, mass, inertia, hydrostatics and force RAOs all come from the same S175
data file. Body axes are forward-starboard-down (FSD), about the centre of gravity.
Positions and attitudes use local north-east-down (NED). Units are SI.

The Python conversion uses the source's zero-speed hydrodynamic slice and MSS's
power-based equivalent matrices at a fixed 1 rad/s spectral peak. These constant
coefficients are a model reduction, not frequency-dependent radiation memory.
The source speed range is metadata, not a validated operating envelope.

The source has no `powerBased` damping preset. Teaching damping is therefore
recorded separately: the surge/sway/yaw decay times are
`min(120, 0.95*Mii/Beqii)` seconds, with restored-mode damping increments
`[0, 0.1, 0]` and zero quadratic roll/pitch/yaw damping multipliers.
An arbitrary 120-second total decay time would require negative additional
damping for this dataset. The derived times preserve positive added damping.
These assumptions require calibration before any claim of measured S175 fidelity.

`provenance.json` records the actual derived values, source and prepared checksums,
source parameters, and matrix symmetry diagnostics. Loading checks the prepared
checksum and rejects invalid matrices. Arrays load with pickle disabled.
