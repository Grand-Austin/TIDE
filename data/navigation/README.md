# Coastline data for the TIDE coast guard

`ne_10m_land.zip` is the original Natural Earth **1:10m land** archive, version
**5.1.1**. It is public domain.

| Item | Value |
| --- | --- |
| Dataset | Natural Earth 10m land |
| Version | 5.1.1 |
| Source | <https://naturalearth.s3.amazonaws.com/10m_physical/ne_10m_land.zip> |
| Documentation | <https://www.naturalearthdata.com/downloads/10m-physical-vectors/10m-land/> |
| SHA-256 | `e547d749445eaa0964aba76738090ec88f5e63c4585122170f98c67a7ea922dc` |
| Size | 3,269,070 bytes |
| License | Public domain |

`manifest.json` pins the archive and each required shapefile member. The runtime
reads the tracked zip directly. It never downloads data.

## Safety policy

The original TUI and route search use `game.navigation.CoastGuard`. The default
clearance is **5.0 nautical miles** (9,260 m). The guard rejects land and the
clearance buffer. It also rejects a route segment that crosses either one, even
when both end points are safe. The operator policy is fixed for a scenario.
The player and agent cannot lower it.

`game.navigation.guard_environment` wraps the scenario environment. It raises
`EnvironmentError` with reason `coast_clearance` before an unsafe sample can be
committed. The simulation then stops and rolls back the candidate step. The
original TUI and `--script` runner both use this wrapper. The 3D web client is
not part of this data path.

## Limits

1. Natural Earth 1:10m is a small-scale map product. It is not a hydrographic
   chart and is not calibrated for navigation.
2. The source has mapped coastline resolution limits. It may omit small rocks,
   reefs, harbour walls, and recent shoreline changes.
3. The guard projects the cropped land geometry to a local azimuthal
   equidistant frame. The 5 NM buffer is accurate to the map resolution, not to
   survey tolerance.
4. The guard uses straight route edges. Curved vessel tracks can differ from a
   straight segment between two samples. The physics step guard is authoritative
   for the actual sampled position.
5. The dataset covers land only. It does not model ice, restricted areas,
   traffic separation schemes, or charted depths.

## Refresh

```bash
uv run python -m pipeline.navigation_data
uv run python -m pipeline.navigation_data --verify-only
```

The fetch script checks the pinned SHA-256 before it writes the archive. A
missing, corrupt, or unverifiable archive raises `NavigationDataError`. The
runtime does not silently disable the guard.
