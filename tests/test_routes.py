import numpy as np
import pytest

from pipeline.routes import ROUTES, WAVES, land_hits, load_mask, samples

# 3 x 3 cells, 0.1 degree apart; the centre cell is land.
LON = np.array([3.0, 3.1, 3.2])
LAT = np.array([40.0, 40.1, 40.2])
SEA = np.ones((3, 3), dtype=bool)
SEA[1, 1] = False


def test_samples_keep_both_ends_and_spacing():
    pts = samples([(3.0, 40.0), (3.0, 40.1), (3.1, 40.1)], step_nm=1.0)  # 6 nm north, then east
    assert pts[0] == (3.0, 40.0) and pts[-1] == pytest.approx((3.1, 40.1))
    assert (3.0, 40.1) in [pytest.approx(p) for p in pts]
    gaps = [abs(b[1] - a[1]) * 60 + abs(b[0] - a[0]) * 60 * 0.77 for a, b in zip(pts, pts[1:])]
    assert max(gaps) <= 1.0


def test_route_through_land_cell_is_caught():
    assert land_hits([(3.0, 40.1), (3.2, 40.1)], SEA, LON, LAT)
    assert not land_hits([(3.0, 40.0), (3.2, 40.0)], SEA, LON, LAT)


@pytest.mark.skipif(not WAVES.exists(), reason="needs data/copernicus wave file")
@pytest.mark.xfail(strict=True, reason="prototype routes cross land; remove this mark once the routes are redrawn")
@pytest.mark.parametrize("name", sorted(ROUTES))
def test_case_routes_stay_on_sea_cells(name):
    sea, lon, lat = load_mask()
    assert land_hits(ROUTES[name], sea, lon, lat) == []
