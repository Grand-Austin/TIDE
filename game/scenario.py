"""Small, versioned scenario definitions shared by offline tools and the runtime."""

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

import xarray as xr

from game.simulation.environment import CopernicusEnvironment
from game.simulation.runtime import Settings, Simulation
from game.simulation.vessel import load_s175
from game.simulation.wind import WindParameters

ROOT = Path(__file__).resolve().parents[1]


def utc(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Scenario timestamps require a UTC offset")
    return result.astimezone(timezone.utc)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@dataclass(frozen=True)
class Scenario:
    definition: dict
    root: Path

    @contextmanager
    def environment(self):
        with ExitStack() as stack:
            datasets = {}
            for layer, source in self.definition["environment"].items():
                path = self.root / source["path"]
                if digest(path) != source["sha256"]:
                    raise ValueError(f"{layer} checksum mismatch")
                datasets[layer] = stack.enter_context(xr.open_dataset(path))
            yield CopernicusEnvironment(**datasets)

    def simulation(self, environment, seed=None):
        d = self.definition
        model = self.root / d["vessel"]["path"]
        if digest(model / "vessel.npz") != d["vessel"]["sha256"]:
            raise ValueError("Scenario vessel checksum mismatch")
        return Simulation(
            load_s175(model),
            environment,
            seed=d["seed"] if seed is None else seed,
            settings=Settings(**d["settings"]),
            wind_parameters=WindParameters(**d["wind_parameters"]),
        )

    def initial_state(self, simulation):
        d = self.definition
        return simulation.initial_state(utc(d["start"]), **d["initial"])


def load_scenario(path, *, root=ROOT):
    d = json.loads(Path(path).read_text())
    if d["schema_version"] != 1:
        raise ValueError("Unsupported scenario version")
    if set(d["environment"]) != {"waves", "currents", "wind", "temperature"}:
        raise ValueError("Scenario requires all four environmental sources")
    start, end = utc(d["start"]), utc(d["end"])
    if end <= start or d["mission"]["budget_s"] != (end - start).total_seconds():
        raise ValueError("Scenario time budget must match its window")
    Settings(**d["settings"])
    lon, lat = d["mission"]["target_lon_lat"]
    if not -180 <= lon <= 180 or not -85 < lat < 85:
        raise ValueError("Invalid target coordinates")
    if d["mission"]["review_interval_s"] <= 0 or d["mission"]["review_interval_s"] % 5:
        raise ValueError("Review interval must be a positive multiple of five seconds")
    numbers = [
        *d["initial"].values(),
        *d["mission"]["target_lon_lat"],
        d["mission"]["radius_m"],
    ]
    if not all(math.isfinite(v) for v in numbers) or d["mission"]["radius_m"] <= 0:
        raise ValueError("Invalid scenario coordinates or mission geometry")
    if not -180 <= d["initial"]["lon"] <= 180 or not -85 < d["initial"]["lat"] < 85:
        raise ValueError("Invalid initial coordinates")
    return Scenario(d, Path(root))
