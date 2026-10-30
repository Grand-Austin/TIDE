# Reproducible reference baselines

`reproduce.py` runs routing baselines **outside** the TIDE source tree. It downloads pinned public artifacts, checks SHA-256 digests, prepares an isolated runtime, and records the measured result with provenance and limitations.

Run it from the repository root:

```bash
uv run python -m agent.references.reproduce visir2 --cache /private/tmp/tide-reference-cache
uv run python -m agent.references.reproduce simroute --cache /private/tmp/tide-reference-cache
```

The first run needs network access. Later runs can use `--offline`. The default cache is `/private/tmp/tide-reference-cache`, outside this repository. The runner does not import reference code from the repository. An offline run re-verifies the pinned archive digests, extracts the original source again, and reuses the verified downloads and prepared runtime. The stored `visir2-run.json` is the record of the successful run below; do not rerun the heavy job only to restate that evidence.

## VISIR-2 official analytic current

This executes the original VISIR-2 Python source. The source is GPL-3.0-or-later and is extracted and run only under the cache directory. No upstream source is copied into TIDE.

| Item | Value |
| --- | --- |
| Source archive | Zenodo record 10960842, `VISIR-2--gmd_2023.zip` |
| Source SHA-256 | `62a3a79bb668e38459e35ca44114d07cdde12802df14ba0ca44bca75b5ed4a74` |
| Analytic input | Zenodo record 8322460, `current_short.zip` |
| Analytic input SHA-256 | `01cd73822cef63e1f740848624b352f5b3931eddc4c34d7f3fcc75be7c3f9784` |
| Official case | Techy Example 3, dynamic analytic current |
| Official criterion | `abs(deltaL) <= 3%` and `abs(deltaT*) <= 3%` |
| Reference row | `L/L0 = 1.00480754`, `T/T0 = 1.03038814` |
| Measured row | `L/L0 = 1.017587668235335`, `T/T0 = 1.0568394582308234` |
| Measured deltas | `deltaL = 1.271898%`, `deltaT* = 2.567122%` |
| Result | **PASSED** (both deltas within 3%) |
| Repository status | `official_analytic_current_reproduced` |
| Alboran full numerical case | **NOT RUN** (`not_run`) |

The captured result is in `agent/references/visir2-run.json`. The official console also reports `Curr analytic PASSED`. Source, analytic input and reference-CSV digests are verified before execution. Stage log digests are recorded in the result.

Offline rerun with the existing cache:

```bash
uv run python -m agent.references.reproduce visir2 --cache /private/tmp/tide-reference-cache --offline
```

The runner uses three original entry points:

```text
from _a_Grafi.MAIN_Grafi import Main; Main('crt_analytic')
from _b_Campi.MAIN_Campi import MAIN; MAIN('techy')
from _d_Tracce.tracce_analitiche import MAIN_Analitiche; MAIN_Analitiche('Techy_curr')
```

The isolated Python 3.9 runtime pins compatible versions of NumPy, SciPy, pandas, networkx and the other original dependencies. The exact package versions and command durations are recorded in `visir2-run.json`.

### Limitations

1. This is the official small analytic-current benchmark, not the full Alboran numerical case. The Alboran case is not run.
2. The upstream conda file names Python 3.7.3; this runner uses Python 3.9 with compatible pinned packages.
3. The result depends on the official code and the official analytic namelists only. No large environmental netCDF products are used.
4. The original code remains external and GPL-3.0-or-later. No upstream source is redistributed in TIDE.
5. The TIDE candidate solvers are adaptations. They do not execute VISIR-2 and do not reproduce its full graph, performance-model or environmental pipeline.

## SIMROUTE Palma-Barna

This runs an independent NumPy implementation of the published Palma de Mallorca to Barcelona case. It reads the pinned wave archive and the published report, and it does not copy upstream SIMROUTE code.

| Item | Value |
| --- | --- |
| Snapshot commit | `7d9a3381b8078013f63c3007c10f60dcf4f19f93` |
| Waves SHA-256 | `10ad9eb9b445462b83e34772ee3f48d2c969fd5780f2929b36ff4eebf7270027` |
| Published report SHA-256 | `5c3b619c419e826e6360b74af89970efb5ab57cc3d067271d41695205b0eb25a` |
| Without waves | 9.402637 h / 151.382453 nm (published 9.40 / 151.38) |
| With waves | 14.973726 h / 161.557640 nm (published 14.97 / 161.56) |
| Minimum distance with waves | 16.928160 h / 151.382453 nm (published 16.93 / 151.38) |
| Tolerance | 0.005 h and 0.005 nm (half a unit in the published last digit) |

The result is written to `results/simroute-run.json` in the cache. The same independent result is pinned in `benchmarks.json`.

Offline rerun with the existing cache:

```bash
uv run python -m agent.references.reproduce simroute --cache /private/tmp/tide-reference-cache --offline
```

## Tests

The unit tests use fixtures only. They do not use the network or run the reference model.

```bash
uv run python -m pytest tests/test_routing_reference_runner.py -q
```

They cover manifest pins, checksum replacement, offline cache behavior, ZIP path traversal including directory entries, source-tree re-extraction, bounded subprocess timeouts, nonzero exits, reference CSV parsing, tolerance evaluation, SIMROUTE report parsing, and consistency of the committed result and benchmark metadata.

## Provenance policy

1. `benchmarks.json` remains the inspected-source metadata record. This runner reads pinned values but does not edit peer-owned metadata.
2. The runner stores source, inputs, runtime and logs under the cache directory only.
3. A measured result is written only after the original command succeeds and the declared comparison passes.
4. No credentials, secrets or unrelated private files are used.
