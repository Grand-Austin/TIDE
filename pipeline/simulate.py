"""Route simulation: where a vessel is along a route over time, and what sea state it meets.

Times are hours on the case day (e.g. 7.5 = 07:30). Sea state comes from a callable so the
same code runs on the prototype's synthetic fields and on Copernicus Marine grids.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, NamedTuple, Sequence

Point = tuple[float, float]  # (lon, lat), degrees


class SeaState(NamedTuple):
    hs: float  # significant wave height, m
    wave_from: float  # direction the waves come from, degrees true
    period: float  # wave period, s
    current_kn: float = 0.0
    current_to: float = 0.0  # direction the current flows toward, degrees true


SeaStateAt = Callable[[float, float, float], SeaState]  # (t, lon, lat) -> SeaState


@dataclass(frozen=True)
class Leg:
    start: Point
    end: Point
    nm: float
    heading: float  # degrees true


@dataclass(frozen=True)
class Limits:
    beam_hs: float  # max wave height accepted in beam seas, m
    max_hs: float  # max wave height accepted on any heading, m
    arrive_by: float  # latest arrival, hours on the case day
    beam_from: float = 60.0  # relative angle where head seas become beam seas; beam ends at 180 - beam_from


@dataclass(frozen=True)
class Sample:
    t: float
    lon: float
    lat: float
    hs: float
    rel: float  # angle between heading and wave direction, 0 = head seas
    category: str  # "head" | "beam" | "following"
    beam_exceeded: bool
    over_max: bool
    speed_kn: float


@dataclass(frozen=True)
class Result:
    departure: float
    arrival: float
    beam_hours: float
    over_hours: float
    max_hs: float
    late: bool
    samples: tuple[Sample, ...]

    @property
    def eta_hours(self) -> float:
        return self.arrival - self.departure

    @property
    def safe(self) -> bool:
        return self.beam_hours == 0 and self.over_hours == 0 and not self.late


def legs_of(points: Sequence[Point]) -> list[Leg]:
    legs = []
    for (lon1, lat1), (lon2, lat2) in zip(points, points[1:]):
        mid = math.radians((lat1 + lat2) / 2)
        dx = (lon2 - lon1) * 60 * math.cos(mid)
        dy = (lat2 - lat1) * 60
        heading = math.degrees(math.atan2(dx, dy)) % 360
        legs.append(Leg((lon1, lat1), (lon2, lat2), math.hypot(dx, dy), heading))
    return legs


def encounter(heading: float, wave_from: float, beam_from: float = 60.0) -> tuple[float, str]:
    """Relative angle between the bow and the incoming waves, and its category."""
    rel = abs((heading - wave_from) % 360)
    rel = 360 - rel if rel > 180 else rel
    if rel < beam_from:
        return rel, "head"
    if rel <= 180 - beam_from:
        return rel, "beam"
    return rel, "following"


def simulate(
    points: Sequence[Point],
    departure: float,
    sea_at: SeaStateAt,
    limits: Limits,
    speed_kn: float = 6.0,
    dt_hours: float = 1 / 12,
) -> Result:
    t = departure
    samples: list[Sample] = []
    for leg in legs_of(points):
        done = 0.0
        while done < leg.nm:
            frac = done / leg.nm
            lon = leg.start[0] + (leg.end[0] - leg.start[0]) * frac
            lat = leg.start[1] + (leg.end[1] - leg.start[1]) * frac
            sea = sea_at(t, lon, lat)
            rel, category = encounter(leg.heading, sea.wave_from, limits.beam_from)
            speed = speed_kn + sea.current_kn * math.cos(math.radians(leg.heading - sea.current_to))
            samples.append(
                Sample(
                    t=t,
                    lon=lon,
                    lat=lat,
                    hs=sea.hs,
                    rel=rel,
                    category=category,
                    beam_exceeded=category == "beam" and sea.hs > limits.beam_hs,
                    over_max=sea.hs > limits.max_hs,
                    speed_kn=speed,
                )
            )
            done += speed * dt_hours
            t += dt_hours
    return Result(
        departure=departure,
        arrival=t,
        beam_hours=sum(s.beam_exceeded for s in samples) * dt_hours,
        over_hours=sum(s.over_max for s in samples) * dt_hours,
        max_hs=max((s.hs for s in samples), default=0.0),
        late=t > limits.arrive_by,
        samples=tuple(samples),
    )


def best_route(results: dict[str, Result]) -> str | None:
    """Fastest safe route, or None when no route is safe (the right call is not to sail)."""
    safe = [(r.eta_hours, name) for name, r in results.items() if r.safe]
    return min(safe)[1] if safe else None


def tipping_points(best_by_departure: Sequence[tuple[float, str | None]]) -> list[tuple[float, str | None, str | None]]:
    """(departure, from, to) wherever the best route changes between consecutive departures."""
    return [
        (dep, prev, cur)
        for (_, prev), (dep, cur) in zip(best_by_departure, best_by_departure[1:])
        if cur != prev
    ]
