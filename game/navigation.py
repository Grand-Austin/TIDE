"""Authoritative coast-clearance guard for the original TUI and route search.

The guard answers one operational question: may the ship occupy this position, or
move along this segment, while staying at least ``DEFAULT_CLEARANCE_NM`` nautical
miles from the Natural Earth 10m land polygon? It does not change vessel physics,
weather, cargo rules, or the authored initial state.

Natural Earth is a small-scale public-domain map product. It is not a
hydrographic chart and it is not calibrated for navigation. The guard therefore
adds a fixed operator policy on top of the mapped coastline; it does not claim to
resolve every rock, reef, or harbour wall.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import io
import json
import math
from pathlib import Path
import zipfile

import numpy as np
import shapefile
import shapely
from pyproj import CRS, Transformer
from shapely import STRtree, make_valid, transform as shapely_transform
from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from game.simulation.environment import EnvironmentError

DEFAULT_CLEARANCE_NM = 5.0
METRES_PER_NM = 1852.0
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "navigation"
ARCHIVE_NAME = "ne_10m_land.zip"
MANIFEST_NAME = "manifest.json"
REQUIRED_MEMBERS = (
    "ne_10m_land.shp",
    "ne_10m_land.shx",
    "ne_10m_land.dbf",
    "ne_10m_land.prj",
)
MAX_MEMBER_BYTES = 64 * 1024 * 1024


class NavigationDataError(RuntimeError):
    """The pinned coastline dataset is missing, corrupt, or unsupported."""


@dataclass(frozen=True)
class _Source:
    version: str
    source: str
    checksum: str
    license: str
    rings: tuple[np.ndarray, ...]
    limitations: str


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest_path(data_path: Path | str | None) -> Path:
    path = Path(data_path) if data_path is not None else DEFAULT_DATA_DIR
    return path / MANIFEST_NAME if path.is_dir() else path


def _archive_path(manifest_path: Path, manifest: dict) -> Path:
    name = manifest.get("archive", {}).get("path")
    if not isinstance(name, str) or Path(name).name != name:
        raise NavigationDataError("Coastline manifest has an unsafe archive path")
    return manifest_path.parent / name


def _read_members(data: bytes, manifest: dict) -> dict[str, bytes]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise NavigationDataError("Coastline archive is not a valid zip file") from error
    with archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise NavigationDataError("Coastline archive has duplicate member names")
        for name in names:
            member = Path(name)
            if member.is_absolute() or ".." in member.parts or len(member.parts) != 1:
                raise NavigationDataError(f"Coastline archive has an unsafe member: {name!r}")
        missing = set(REQUIRED_MEMBERS) - set(names)
        if missing:
            raise NavigationDataError(
                f"Coastline archive is missing required members: {sorted(missing)}"
            )
        expected = {
            row["name"]: row["sha256"]
            for row in manifest.get("members", [])
            if isinstance(row, dict) and "name" in row and "sha256" in row
        }
        members = {}
        for name in REQUIRED_MEMBERS:
            info = archive.getinfo(name)
            if info.file_size > MAX_MEMBER_BYTES:
                raise NavigationDataError(f"Coastline member is too large: {name!r}")
            payload = archive.read(name)
            if name in expected and _sha256(payload) != expected[name]:
                raise NavigationDataError(f"Coastline member checksum mismatch: {name!r}")
            members[name] = payload
    return members


def _parse_rings(members: dict[str, bytes]) -> tuple[np.ndarray, ...]:
    reader = None
    try:
        reader = shapefile.Reader(
            shp=io.BytesIO(members["ne_10m_land.shp"]),
            shx=io.BytesIO(members["ne_10m_land.shx"]),
            dbf=io.BytesIO(members["ne_10m_land.dbf"]),
            encoding="utf-8",
            encodingErrors="strict",
        )
        rings = []
        for shape in reader.iterShapes():
            if shape.shapeType == 0:
                continue
            if shape.shapeType not in (5, 15, 25):
                raise NavigationDataError("Coastline archive contains a non-polygon shape")
            for index, start in enumerate(shape.parts):
                end = shape.parts[index + 1] if index + 1 < len(shape.parts) else len(shape.points)
                coordinates = np.asarray(shape.points[start:end], dtype=float)
                if len(coordinates) < 4:
                    continue
                if not np.all(np.isfinite(coordinates)):
                    raise NavigationDataError("Coastline contains non-finite coordinates")
                lon, lat = coordinates[:, 0], coordinates[:, 1]
                if np.any(lon < -180) or np.any(lon > 180) or np.any(lat < -90) or np.any(lat > 90):
                    raise NavigationDataError("Coastline coordinates are outside WGS84 lon/lat")
                rings.append(coordinates)
    except NavigationDataError:
        raise
    except Exception as error:
        raise NavigationDataError("Coastline shapefile could not be decoded") from error
    finally:
        if reader is not None:
            reader.close()
    if not rings:
        raise NavigationDataError("Coastline archive contains no polygon rings")
    return tuple(rings)


@lru_cache(maxsize=4)
def _load_source(
    manifest_path_text: str,
    archive_path_text: str,
    size: int,
    mtime_ns: int,
) -> _Source:
    manifest_path = Path(manifest_path_text)
    archive_path = Path(archive_path_text)
    try:
        manifest = json.loads(manifest_path.read_text())
        data = archive_path.read_bytes()
    except OSError as error:
        raise NavigationDataError("Coastline dataset or manifest is missing") from error
    if manifest.get("schema_version") != 1:
        raise NavigationDataError("Unsupported coastline manifest version")
    if not {"dataset", "version", "source", "archive", "members"} <= set(manifest):
        raise NavigationDataError("Coastline manifest is incomplete")
    if len(data) != size or int(manifest["archive"].get("bytes", -1)) != size:
        raise NavigationDataError("Coastline archive size does not match its manifest")
    checksum = manifest["archive"].get("sha256")
    if not isinstance(checksum, str) or _sha256(data) != checksum:
        raise NavigationDataError("Coastline archive checksum does not match its manifest")
    source = manifest.get("source", {})
    source_url = source.get("url") if isinstance(source, dict) else None
    if not isinstance(source_url, str):
        raise NavigationDataError("Coastline manifest has no source URL")
    members = _read_members(data, manifest)
    return _Source(
        version=str(manifest["version"]),
        source=source_url,
        checksum=checksum,
        license=str(manifest.get("license", "")),
        rings=_parse_rings(members),
        limitations=(
            "Natural Earth 1:10m land polygons are a small-scale map product, "
            "not a hydrographic chart or a calibrated navigation surface."
        ),
    )


def _tracked_source(data_path: Path | str | None) -> _Source:
    manifest_path = _manifest_path(data_path)
    try:
        manifest = json.loads(manifest_path.read_text())
        archive_path = _archive_path(manifest_path, manifest)
        stat = archive_path.stat()
    except (OSError, json.JSONDecodeError, NavigationDataError) as error:
        raise NavigationDataError(
            f"Coastline dataset is unavailable at {manifest_path.parent}"
        ) from error
    return _load_source(
        str(manifest_path), str(archive_path), stat.st_size, stat.st_mtime_ns
    )


def _polygonal(geometry: BaseGeometry) -> BaseGeometry:
    if geometry.is_empty or geometry.geom_type in ("Polygon", "MultiPolygon"):
        return geometry
    parts = [part for part in shapely.get_parts(geometry)
             if part.geom_type in ("Polygon", "MultiPolygon")]
    return unary_union(parts) if parts else Polygon()


def _crop_rings(rings: tuple[np.ndarray, ...], crop: BaseGeometry) -> BaseGeometry:
    """Classify rings into shells and holes, then crop in lon/lat."""
    min_lon, min_lat, max_lon, max_lat = crop.bounds
    selected = []
    for coordinates in rings:
        lon, lat = coordinates[:, 0], coordinates[:, 1]
        if (lon.max() < min_lon or lon.min() > max_lon or
                lat.max() < min_lat or lat.min() > max_lat):
            continue
        selected.append(coordinates)
    if not selected:
        return Polygon()
    polygons = [Polygon(coordinates) for coordinates in selected]
    tree = STRtree(polygons)
    depths, containers = [], []
    for index, polygon in enumerate(polygons):
        probe = polygon.representative_point()
        containing = [
            int(other) for other in tree.query(probe)
            if other != index and polygons[int(other)].contains(probe)
        ]
        containers.append(containing)
        depths.append(len(containing))
    children: dict[int, list[np.ndarray]] = {index: [] for index, depth in enumerate(depths)
                                            if depth % 2 == 0}
    for index, depth in enumerate(depths):
        if depth % 2 == 0:
            continue
        parents = [parent for parent in containers[index] if depths[parent] % 2 == 0]
        if not parents:
            raise NavigationDataError("Coastline hole has no enclosing land polygon")
        parent = min(parents, key=lambda candidate: polygons[candidate].area)
        children[parent].append(selected[index])
    land_parts = []
    for index, depth in enumerate(depths):
        if depth % 2:
            continue
        try:
            part = Polygon(selected[index], children[index])
        except Exception as error:
            raise NavigationDataError("Coastline polygon ring is invalid") from error
        land_parts.append(part if part.is_valid else make_valid(part))
    land = unary_union(land_parts) if land_parts else Polygon()
    if not land.is_valid:
        land = make_valid(land)
    land = _polygonal(land.intersection(crop))
    return land if land.is_valid else _polygonal(make_valid(land))


class CoastGuard:
    """Fixed-clearance land and near-coast policy for one scenario bounding box."""

    def __init__(
        self,
        bbox,
        *,
        clearance_nm: float = DEFAULT_CLEARANCE_NM,
        land: BaseGeometry | None = None,
        data_path: Path | str | None = None,
    ):
        try:
            west, east, south, north = (float(value) for value in bbox)
        except (TypeError, ValueError) as error:
            raise ValueError("Coast guard bbox must contain four numbers") from error
        if not all(math.isfinite(value) for value in (west, east, south, north)):
            raise ValueError("Coast guard bbox must be finite")
        if not -180 <= west < east <= 180 or not -90 < south < north <= 90:
            raise ValueError("Coast guard bbox must be a valid WGS84 lon/lat range")
        if not math.isfinite(clearance_nm) or clearance_nm <= 0:
            raise ValueError("Coast clearance must be finite and positive")
        if land is not None and data_path is not None:
            raise ValueError("Provide either fixture land or a coastline dataset, not both")
        if land is not None and not isinstance(land, BaseGeometry):
            raise TypeError("Fixture land must be a shapely geometry")
        if (land is not None and not land.is_empty and
                land.geom_type not in ("Polygon", "MultiPolygon", "GeometryCollection")):
            raise ValueError("Fixture land must contain polygonal geometry")

        self.bbox = (west, east, south, north)
        self.clearance_nm = float(clearance_nm)
        self.clearance_m = self.clearance_nm * METRES_PER_NM
        centre_lon, centre_lat = (west + east) / 2, (south + north) / 2
        self._transformer = Transformer.from_crs(
            CRS.from_epsg(4326),
            CRS.from_proj4(
                f"+proj=aeqd +lat_0={centre_lat:.12f} +lon_0={centre_lon:.12f} "
                "+datum=WGS84 +units=m +no_defs"
            ),
            always_xy=True,
        )

        if land is None:
            source = _tracked_source(data_path)
            self._fixture = False
            self._version = source.version
            self._source = source.source
            self._checksum = source.checksum
            self._license = source.license
            self._limitations = source.limitations
            rings = source.rings
        else:
            self._fixture = True
            self._version = "synthetic-fixture"
            self._source = "injected fixture geometry"
            self._checksum = "fixture"
            self._license = "test fixture"
            self._limitations = "Injected geometry is for tests, not an operational chart."
            rings = None

        # Keep a margin so the projected clearance buffer is not clipped by the
        # lon/lat crop, including at the colder side of a wide bounding box.
        pad = 1.25 * self.clearance_m / 111_320.0
        pad_lon = pad / max(math.cos(math.radians(centre_lat)), 1e-6)
        expanded = box(
            max(-180.0, west - pad_lon),
            max(-90.0, south - pad),
            min(180.0, east + pad_lon),
            min(90.0, north + pad),
        )
        if land is None:
            land_lonlat = _crop_rings(rings, expanded)
        else:
            land_lonlat = land
            if not land_lonlat.is_valid:
                land_lonlat = make_valid(land_lonlat)
            land_lonlat = _polygonal(land_lonlat.intersection(expanded))

        def project(coordinates):
            x, y = self._transformer.transform(coordinates[:, 0], coordinates[:, 1])
            return np.column_stack((x, y))

        land_projected = _polygonal(shapely_transform(land_lonlat, project))
        unsafe = land_projected.buffer(self.clearance_m, quad_segs=16)
        self._unsafe = _polygonal(unsafe) if not unsafe.is_valid else unsafe
        if not self._unsafe.is_valid:
            self._unsafe = _polygonal(make_valid(self._unsafe))
        self._bounds = None if self._unsafe.is_empty else self._unsafe.bounds
        shapely.prepare(self._unsafe)

    def _point(self, lon, lat):
        return self._transformer.transform(float(lon), float(lat))

    @staticmethod
    def _valid_lonlat(lon, lat) -> bool:
        return (math.isfinite(lon) and math.isfinite(lat)
                and -180 <= lon <= 180 and -90 <= lat <= 90)

    def _outside(self, x, y) -> bool:
        if self._bounds is None:
            return True
        min_x, min_y, max_x, max_y = self._bounds
        return x < min_x or x > max_x or y < min_y or y > max_y

    def allowed(self, lon: float, lat: float) -> bool:
        """Return True only when the point is outside land and its clearance buffer."""
        try:
            lon, lat = float(lon), float(lat)
        except (TypeError, ValueError):
            return False
        if not self._valid_lonlat(lon, lat):
            return False
        x, y = self._point(lon, lat)
        if self._outside(x, y):
            return True
        return not self._unsafe.intersects(Point(x, y))

    def segment_allowed(self, start_lon_lat, end_lon_lat) -> bool:
        """Return True only when the whole straight segment misses land and clearance."""
        try:
            start_lon, start_lat = (float(value) for value in start_lon_lat)
            end_lon, end_lat = (float(value) for value in end_lon_lat)
        except (TypeError, ValueError):
            return False
        if not (self._valid_lonlat(start_lon, start_lat) and
                self._valid_lonlat(end_lon, end_lat)):
            return False
        start_x, start_y = self._point(start_lon, start_lat)
        end_x, end_y = self._point(end_lon, end_lat)
        if self._bounds is None:
            return True
        min_x, max_x = sorted((start_x, end_x))
        min_y, max_y = sorted((start_y, end_y))
        bounds = (min_x, min_y, max_x, max_y)
        if (bounds[2] < self._bounds[0] or bounds[0] > self._bounds[2] or
                bounds[3] < self._bounds[1] or bounds[1] > self._bounds[3]):
            return True
        line = LineString(((start_x, start_y), (end_x, end_y)))
        return not self._unsafe.intersects(line)

    def metadata(self) -> dict:
        """Provenance and fixed-policy values suitable for a session fingerprint."""
        return {
            "dataset": "Natural Earth 10m land" if not self._fixture else "synthetic fixture",
            "version": self._version,
            "source": self._source,
            "checksum": self._checksum,
            "license": self._license,
            "clearance": {"nm": self.clearance_nm, "m": self.clearance_m},
            "clearance_nm": self.clearance_nm,
            "clearance_m": self.clearance_m,
            "units": {
                "coordinates": "WGS84 longitude and latitude in decimal degrees",
                "projection": "local azimuthal equidistant metres",
                "clearance": "nautical miles; geometry buffer in metres",
            },
            "bbox": list(self.bbox),
            "fixture": self._fixture,
            "resolution": "1:10m Natural Earth map resolution",
            "limitations": self._limitations,
        }


class CoastalEnvironment:
    """Environment proxy that rejects unsafe positions before source sampling."""

    def __init__(self, source, guard: CoastGuard):
        self.source = source
        self.guard = guard

    def sample(self, time, lon, lat):
        if not self.guard.allowed(lon, lat):
            raise EnvironmentError(
                "coast_clearance",
                "Position is on land or inside the fixed coast-clearance buffer",
            )
        return self.source.sample(time, lon, lat)

    def close(self):
        """Do not close the source; its owning ExitStack or caller remains responsible."""
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def guard_environment(
    environment,
    bbox,
    *,
    clearance_nm: float = DEFAULT_CLEARANCE_NM,
    coast_guard: CoastGuard | None = None,
) -> CoastalEnvironment:
    """Wrap an environment with the authoritative coast policy.

    The returned wrapper does not own ``environment``. Closing the wrapper is a
    no-op so an outer ``ExitStack`` can close the source exactly once.
    """
    guard = coast_guard or CoastGuard(bbox, clearance_nm=clearance_nm)
    return CoastalEnvironment(environment, guard)
