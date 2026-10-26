"""Find candidate case days: days on which the best decision changes with departure time because of the sea.

For every day, departure and route, simulate the passage and label each tipping point with the reason the
previous best route stopped being best. Flips caused only by the arrival deadline (reason `late`) do not
count: a day is a candidate only if the sea itself changes the decision. Each candidate is re-checked at
every threshold setting and head/beam boundary, and candidates are ranked by how many of those
combinations still show a sea-state flip.

    python -m pipeline.mine     # writes data/candidates.json (format in docs/SCHEMA.md)
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from pipeline.grid import CURRENTS, KN, WAVES, WIND, Grid
from pipeline.routes import ROUTES
from pipeline.simulate import Limits, Result, SeaStateAt, best_route, simulate, tipping_points
from pipeline.validate_summer import BEAM_FROM, DAYS, DEPARTURES, SETTINGS

OUT = Path(__file__).resolve().parent.parent / "data" / "candidates.json"
ARRIVE_BY = 19.0  # hours UTC, 21:00 local summer time; illustrative
DEFAULT = Limits(beam_hs=1.5, max_hs=2.5, arrive_by=ARRIVE_BY)  # illustrative, docs/METHODS.md
COMBINATIONS = [
    Limits(beam_hs, max_hs, ARRIVE_BY, beam_from) for beam_hs, max_hs in SETTINGS for beam_from in BEAM_FROM
]
GOAL_BY_REASON = {"beam_hours": "beam_seas", "over_hours": "time_window", "slower": "current_time", None: "time_window"}


@dataclass(frozen=True)
class Flip:
    at: float  # departure, hours UTC
    from_: str | None
    to: str | None
    reason: str | None  # why `from_` stopped being best; None when nothing was safe before

    @property
    def sea_state(self) -> bool:
        return self.reason != "late"

    @property
    def route_to_route(self) -> bool:
        return self.from_ is not None and self.to is not None


def reason(result: Result) -> str:
    """The first rule that makes `result` worse than the new best route, in the order of docs/SCHEMA.md."""
    if result.beam_hours > 0:
        return "beam_hours"
    if result.over_hours > 0:
        return "over_hours"
    if result.late:
        return "late"
    return "slower"


def day_flips(at: SeaStateAt, limits: Limits) -> list[Flip]:
    results = {float(dep): {k: simulate(p, float(dep), at, limits) for k, p in ROUTES.items()} for dep in DEPARTURES}
    best = [(dep, best_route(r)) for dep, r in results.items()]
    return [
        Flip(dep, a, b, reason(results[dep][a]) if a else None)
        for dep, a, b in tipping_points(best)
    ]


def iso(day: pd.Timestamp, hours: float) -> str:
    return (day + pd.Timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> None:
    waves, currents, wind = (xr.open_dataset(p) for p in (WAVES, CURRENTS, WIND))
    grid = Grid(waves, currents)
    w = wind.interp(longitude=waves.longitude, latitude=waves.latitude)
    u, v = w.eastward_wind.mean(["latitude", "longitude"]), w.northward_wind.mean(["latitude", "longitude"])
    speed = (np.hypot(w.eastward_wind, w.northward_wind) * KN).mean(["latitude", "longitude"])
    daytime = slice(int(DEPARTURES[0]), int(ARRIVE_BY))

    candidates, deadline_only = [], []
    for day in DAYS:
        at = grid.sea_at(day)
        flips = day_flips(at, DEFAULT)
        sea = [f for f in flips if f.sea_state]
        if flips and not sea:
            deadline_only.append(f"{day:%Y-%m-%d}")
        by_combination = [[f for f in day_flips(at, c) if f.sea_state] for c in COMBINATIONS]
        robust_any = sum(bool(fs) for fs in by_combination)
        if not robust_any:
            continue
        robust_route = sum(any(f.route_to_route for f in fs) for fs in by_combination)
        first = next((f for f in sea if f.route_to_route), sea[0] if sea else None)
        s = speed.sel(time=str(day.date())).isel(time=daytime)
        t = s.time.values[int(s.argmax(dim="time"))]
        hs = grid.hs[grid.times.get_loc(day) + daytime.start : grid.times.get_loc(day) + daytime.stop]
        candidates.append({
            "date": f"{day:%Y-%m-%d}",
            "kind": "route" if any(f.route_to_route for f in sea) else "go_no_go" if sea else "other_settings",
            "robust_route": robust_route,
            "robust_any": robust_any,
            "flips": [{"at": iso(day, f.at), "from": f.from_, "to": f.to, "reason": f.reason} for f in flips],
            "suggested_goal": GOAL_BY_REASON[first.reason] if first else None,
            "max_hs": round(float(np.nanmax(hs)), 2),
            "peak_wind": {
                "kn": round(float(s.max()), 1),
                "from": round(math.degrees(math.atan2(-float(u.sel(time=t)), -float(v.sel(time=t)))) % 360),
                "at": pd.Timestamp(t).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        })

    order = {"route": 0, "go_no_go": 1, "other_settings": 2}
    candidates.sort(key=lambda c: (order[c["kind"]], -c["robust_route"], -c["robust_any"], c["date"]))
    OUT.write_text(json.dumps({
        "generated_by": "pipeline/mine.py",
        "datasets": [p.stem.rsplit("_", 1)[0] for p in (WAVES, CURRENTS, WIND)],
        "default_limits": asdict(DEFAULT),
        "combinations": len(COMBINATIONS),
        "candidates": candidates,
        "deadline_only": deadline_only,
    }, indent=2) + "\n")

    print(f"{len(candidates)} candidate days, written to {OUT.relative_to(OUT.parent.parent)}")
    print(f"{'date':<11} {'kind':<15} route/any of {len(COMBINATIONS)}  goal          max Hs  peak wind")
    for c in candidates:
        wind_ = c["peak_wind"]
        print(f"{c['date']:<11} {c['kind']:<15} {c['robust_route']:>5}/{c['robust_any']:<10} "
              f"{str(c['suggested_goal']):<13} {c['max_hs']:>5.2f}  {wind_['kn']:>4.1f} kn from {wind_['from']:>3}")
    print(f"deadline-only flip days at the default setting (excluded): {len(deadline_only)} {deadline_only}")


if __name__ == "__main__":
    main()
