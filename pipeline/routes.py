"""Case routes and their check against the model land mask.

The simulation only reads sea cells, so every route must stay on sea cells of the 1/24 degree MED grid.
Harbours are not resolved at that resolution: routes start and end at a harbour exit point in open
water, and the harbour approach is not simulated (docs/ARCHITECTURE.md, known limits).

    python -m pipeline.routes     # map of the land mask with the routes, and land samples per route
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from pipeline.simulate import Point, legs_of

WAVES = (
    Path(__file__).resolve().parent.parent
    / "data" / "copernicus" / "cmems_mod_med_wav_my_4.2km_PT1H-i_20260701-20260930.nc"
)

# Prototype routes (web/prototype/index.html), Alcúdia -> Ciutadella. Not yet checked against the land mask.
ROUTES: dict[str, list[Point]] = {
    "A": [(3.14, 39.84), (3.25, 39.88), (3.83, 40.00)],
    "B": [(3.14, 39.84), (3.55, 40.18), (3.83, 40.00)],
    "C": [(3.14, 39.84), (3.48, 39.78), (3.78, 39.92), (3.83, 40.00)],
}


def samples(points: list[Point], step_nm: float = 0.5) -> list[Point]:
    """Points along the route about `step_nm` apart, including both ends."""
    out = [points[0]]
    for leg in legs_of(points):
        n = max(1, int(np.ceil(leg.nm / step_nm)))
        out += [
            (leg.start[0] + (leg.end[0] - leg.start[0]) * f, leg.start[1] + (leg.end[1] - leg.start[1]) * f)
            for f in np.linspace(0, 1, n + 1)[1:]
        ]
    return out


def land_hits(points: list[Point], sea: np.ndarray, lon: np.ndarray, lat: np.ndarray) -> list[Point]:
    """Samples of the route that fall in a land cell. `sea[i, j]` is True for sea at (lat[i], lon[j])."""
    return [
        (x, y) for x, y in samples(points)
        if not sea[int(np.abs(lat - y).argmin()), int(np.abs(lon - x).argmin())]
    ]


def land_share(points: list[Point], sea: np.ndarray, lon: np.ndarray, lat: np.ndarray) -> float:
    return len(land_hits(points, sea, lon, lat)) / len(samples(points))


def load_mask(path: Path = WAVES) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Sea mask, longitudes and latitudes of the wave grid."""
    import xarray as xr

    with xr.open_dataset(path) as waves:
        return ~np.isnan(waves.VHM0.isel(time=0).values), waves.longitude.values, waves.latitude.values


def draw(sea: np.ndarray, lon: np.ndarray, lat: np.ndarray, routes: dict[str, list[Point]]) -> str:
    """Text map, north up: '.' sea, '#' land, route letter on sea, '!' route on land."""
    rows = [["." if s else "#" for s in row] for row in sea]
    for name, points in routes.items():
        for x, y in samples(points):
            i, j = int(np.abs(lat - y).argmin()), int(np.abs(lon - x).argmin())
            rows[i][j] = name if sea[i, j] else "!"
    lines = [f"{lat[i]:6.3f}N " + "".join(rows[i]) for i in range(len(lat) - 1, -1, -1)]
    return "\n".join(lines + [f"        {lon[0]:.3f}E to {lon[-1]:.3f}E, one character per cell"])


def main() -> None:
    sea, lon, lat = load_mask()
    print(draw(sea, lon, lat, ROUTES))
    print()
    for name, points in ROUTES.items():
        hits = land_hits(points, sea, lon, lat)
        print(f"{name}: {sum(l.nm for l in legs_of(points)):5.1f} nm, {len(hits)} land samples")
        for x, y in hits:
            print(f"     {x:.3f}E {y:.3f}N")


if __name__ == "__main__":
    main()
