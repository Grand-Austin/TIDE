"""Stateful, fixed-clock six-DOF advancement. No mission rules or global state."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import math
from typing import Callable

import numpy as np

from .dynamics import derivative, rotation, surge_drag
from .environment import EnvironmentError, SeaState
from .vessel import Vessel
from .waves import WaveModel, WaveState
from .wind import WindParameters, wind_load

EARTH_RADIUS_M = 6_371_000.0


@dataclass(frozen=True)
class Command:
    heading_deg: float
    speed_mps: float


@dataclass(frozen=True)
class Settings:
    """Numerical and teaching-control choices, not measured S175 limits."""

    dt_s: float = 0.05
    wave_period_s: float = 0.1
    record_period_s: float = 1.0
    max_thrust_n: float = 300_000.0
    max_yaw_moment_nm: float = 30_000_000.0
    max_reference_yaw_rate: float = math.radians(0.5)
    max_reference_accel: float = 0.02
    speed_time_constant_s: float = 60.0
    yaw_natural_frequency: float = 0.04
    roll_exposure_deg: float = 10.0
    lateral_exposure_mps2: float = 0.1 * 9.81
    max_attitude_deg: float = 30.0

    def __post_init__(self):
        for name, value in vars(self).items():
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for period in (self.wave_period_s, self.record_period_s):
            if period < self.dt_s or not math.isclose(period / self.dt_s, round(period / self.dt_s)):
                raise ValueError("Wave and recording periods must be multiples of dt_s")
        if self.max_attitude_deg >= 80:
            raise ValueError("The small-angle vessel model cannot use this attitude limit")


@dataclass
class ControlState:
    heading_ref: float
    speed_ref: float
    speed_integral: float = 0.0


@dataclass
class Exposure:
    elapsed_s: float = 0.0
    roll_squared_rad2_s: float = 0.0
    pitch_squared_rad2_s: float = 0.0
    lateral_squared_m2_s3: float = 0.0
    roll_exceeded_s: float = 0.0
    lateral_exceeded_s: float = 0.0


@dataclass
class State:
    start_time: datetime
    origin_lon: float
    origin_lat: float
    x: np.ndarray  # [u,v,w,p,q,r,N,E,D,roll,pitch,yaw], SI, FSD/NED at CG
    control: ControlState
    wave: WaveState
    tick: int = 0
    exposure: Exposure = field(default_factory=Exposure)
    wave_force: np.ndarray = field(default_factory=lambda: np.zeros(6))
    wind_force: np.ndarray = field(default_factory=lambda: np.zeros(6))
    wave_elevation: float = 0.0
    wave_flags: tuple[str, ...] = ()
    retained_wave_energy: float = 1.0
    active_risks: tuple[str, ...] = ()
    command: Command | None = None


@dataclass(frozen=True)
class Event:
    time_s: float
    kind: str
    detail: str


@dataclass(frozen=True)
class Sample:
    time_s: float
    lon: float
    lat: float
    heading_deg: float
    speed_through_water_mps: float
    speed_over_ground_mps: float
    course_over_ground_deg: float | None
    roll_deg: float
    pitch_deg: float
    heave_m: float
    lateral_acceleration_mps2: float
    hs_m: float
    tp_s: float
    wave_from_deg: float
    current_east_mps: float
    current_north_mps: float
    wind_east_mps: float
    wind_north_mps: float
    wind_force_x_n: float
    wind_force_y_n: float
    wind_moment_z_nm: float
    sst_c: float | None


@dataclass(frozen=True)
class AdvanceResult:
    state: State
    trajectory: tuple[Sample, ...]
    events: tuple[Event, ...]
    stopping_reason: str
    advanced_s: float


def _wrap(angle: float) -> float:
    return (angle + math.pi) % (2 * math.pi) - math.pi


def _exceed_fraction(a: float, b: float, threshold: float) -> float:
    """Fraction with |value| > threshold, including a sign change in a step."""
    if a == b:
        return float(abs(a) > threshold)
    lo, hi = sorted((a, b))
    return (max(0, min(hi, -threshold) - lo) + max(0, hi - max(lo, threshold))) / (hi - lo)


class Simulation:
    """One vessel/environment context; each State is an independent realization.

    All accepted calls end on the fixed integration clock. This makes a split
    run identical to one uninterrupted run, including wave and controller state.
    """

    def __init__(self, vessel: Vessel, environment, *, seed: int = 1, settings: Settings | None = None,
                 wind_parameters: WindParameters | None = WindParameters()):
        self.vessel = vessel
        self.environment = environment
        self.settings = settings or Settings()
        self.waves = WaveModel(vessel, seed=seed)
        self.wind_parameters = wind_parameters

    def initial_state(self, time: datetime, lon: float, lat: float, *, heading_deg: float = 0,
                      speed_mps: float = 0) -> State:
        self._validate_command(Command(heading_deg, speed_mps))
        if time.tzinfo is None or time.utcoffset() is None:
            raise ValueError("Initial time must have a UTC offset")
        if not all(map(math.isfinite, (lon, lat))) or not -180 <= lon <= 180 or not -85 < lat < 85:
            raise ValueError("Invalid local-coordinate origin")
        time = time.astimezone(timezone.utc)
        sea = self.environment.sample(time, lon, lat)
        psi = math.radians(heading_deg % 360)
        x = np.zeros(12)
        x[11] = psi
        current_body = rotation(x[9:12]).T @ np.array([sea.current_north_mps, sea.current_east_mps, 0.0])
        x[:3] = current_body + [speed_mps, 0.0, 0.0]
        return State(time, lon, lat, x, ControlState(psi, speed_mps), self.waves.initial_state())

    def position(self, state: State) -> tuple[float, float]:
        lat = state.origin_lat + math.degrees(state.x[6] / EARTH_RADIUS_M)
        lon = state.origin_lon + math.degrees(state.x[7] / (EARTH_RADIUS_M * math.cos(math.radians(state.origin_lat))))
        return lon, lat

    def _validate_command(self, command: Command):
        if not math.isfinite(command.heading_deg) or not math.isfinite(command.speed_mps):
            raise ValueError("Commands must be finite")
        if not 0 <= command.speed_mps <= self.vessel.max_speed_mps:
            raise ValueError(f"Speed must be between 0 and {self.vessel.max_speed_mps:g} m/s")

    def _sea(self, state: State) -> SeaState:
        return self.environment.sample(state.start_time + timedelta(seconds=state.tick * self.settings.dt_s),
                                       *self.position(state))

    @staticmethod
    def _water_velocity(x: np.ndarray, sea: SeaState) -> np.ndarray:
        ground = rotation(x[9:12]) @ x[:3]
        return ground - np.array([sea.current_north_mps, sea.current_east_mps, 0.0])

    def _control(self, state: State, command: Command, sea: SeaState) -> np.ndarray:
        cfg, v, c = self.settings, self.vessel, state.control
        c.heading_ref += float(np.clip(_wrap(math.radians(command.heading_deg) - c.heading_ref),
                                      -cfg.max_reference_yaw_rate * cfg.dt_s, cfg.max_reference_yaw_rate * cfg.dt_s))
        c.speed_ref += float(np.clip(command.speed_mps - c.speed_ref,
                                    -cfg.max_reference_accel * cfg.dt_s, cfg.max_reference_accel * cfg.dt_s))
        water_body = rotation(state.x[9:12]).T @ self._water_velocity(state.x, sea)
        error = c.speed_ref - water_body[0]
        kp = v.M[0, 0] / cfg.speed_time_constant_s
        ki = kp / (4 * cfg.speed_time_constant_s)
        thrust = surge_drag(c.speed_ref, v) + kp * error + ki * c.speed_integral
        if abs(thrust) < cfg.max_thrust_n or thrust * error < 0:
            c.speed_integral += error * cfg.dt_s
        yaw = v.M[5, 5] * (cfg.yaw_natural_frequency**2 * _wrap(c.heading_ref - state.x[11])
                            - 2 * cfg.yaw_natural_frequency * state.x[5])
        return np.array([np.clip(thrust, -cfg.max_thrust_n, cfg.max_thrust_n), 0, 0, 0, 0,
                         np.clip(yaw, -cfg.max_yaw_moment_nm, cfg.max_yaw_moment_nm)], dtype=float)

    def _sample(self, state: State, sea: SeaState, acceleration: float) -> Sample:
        x = state.x
        ground = rotation(x[9:12]) @ x[:3]
        sog = float(np.linalg.norm(ground[:2]))
        water = self._water_velocity(x, sea)
        return Sample(state.tick * self.settings.dt_s, *self.position(state), math.degrees(x[11]) % 360,
                      float(np.linalg.norm(water[:2])), sog,
                      math.degrees(math.atan2(ground[1], ground[0])) % 360 if sog > 1e-9 else None,
                      math.degrees(x[9]), math.degrees(x[10]), -float(x[8]), acceleration, sea.hs, sea.tp,
                      sea.wave_from_deg, sea.current_east_mps, sea.current_north_mps,
                      sea.wind_east_mps, sea.wind_north_mps, float(state.wind_force[0]),
                      float(state.wind_force[1]), float(state.wind_force[5]), sea.sst_c)

    def advance(self, state: State, command: Command, duration_s: float, *,
                should_pause: Callable[[], bool] | None = None) -> AdvanceResult:
        """Advance a copy; return the last valid state at any mandatory stop.

        duration_s must be a positive multiple of dt_s. Threshold events report
        teaching exposure only; game cargo loss and mission outcomes live elsewhere.
        """
        self._validate_command(command)
        cfg = self.settings
        if not math.isfinite(duration_s) or duration_s <= 0:
            raise ValueError("duration_s must be finite and positive")
        steps = round(duration_s / cfg.dt_s)
        if steps < 1 or not math.isclose(steps * cfg.dt_s, duration_s, abs_tol=1e-9):
            raise ValueError("duration_s must be a multiple of dt_s")
        if state.x.shape != (12,) or not np.all(np.isfinite(state.x)) or state.tick < 0:
            raise ValueError("Invalid simulation state")
        out = deepcopy(state)
        out.command = command
        trajectory, events = [], []
        last_sample = None
        reason = "duration_reached"
        wave_ticks = round(cfg.wave_period_s / cfg.dt_s)
        record_ticks = round(cfg.record_period_s / cfg.dt_s)
        for _ in range(steps):
            if should_pause is not None and should_pause():
                reason = "paused"
                break
            candidate = deepcopy(out)
            try:
                sea = self._sea(out)
                tau = self._control(candidate, command, sea)
                if out.tick % wave_ticks == 0:
                    wave = self.waves.evaluate(out.wave, out.tick * cfg.dt_s, sea, out.x[11],
                                               self._water_velocity(out.x, sea)[:2])
                    candidate.wave = wave.state
                    candidate.wave_force = wave.force
                    candidate.wave_elevation = wave.elevation
                    candidate.wave_flags = wave.flags
                    candidate.retained_wave_energy = wave.retained_energy
                candidate.wind_force = (wind_load(out.x, sea, self.wind_parameters)
                                        if self.wind_parameters is not None else np.zeros(6))
                total_force = tau + candidate.wave_force + candidate.wind_force
                current = (sea.current_north_mps, sea.current_east_mps)
                f = lambda x: derivative(x, total_force, self.vessel, current)
                h, x = cfg.dt_s, out.x
                k1 = f(x)
                k2 = f(x + h * k1 / 2)
                k3 = f(x + h * k2 / 2)
                k4 = f(x + h * k3)
                candidate.x = x + h * (k1 + 2*k2 + 2*k3 + k4) / 6
                candidate.tick += 1
                if not np.all(np.isfinite(candidate.x)):
                    reason = "numerical_failure"
                    break
                if np.max(np.abs(candidate.x[9:11])) > math.radians(cfg.max_attitude_deg):
                    reason = "model_limit"
                    break
                endpoint_sea = self._sea(candidate)
                if np.linalg.norm(self._water_velocity(candidate.x, endpoint_sea)[:2]) > self.vessel.max_speed_mps:
                    reason = "model_limit"
                    break
                end_derivative = derivative(candidate.x, total_force, self.vessel,
                                            (endpoint_sea.current_north_mps, endpoint_sea.current_east_mps))
            except EnvironmentError as error:
                reason = error.reason
                events.append(Event(out.tick * cfg.dt_s, reason, str(error)))
                break
            except (FloatingPointError, np.linalg.LinAlgError) as error:
                reason = "numerical_failure"
                events.append(Event(out.tick * cfg.dt_s, reason, str(error)))
                break
            except ValueError as error:
                reason = "model_limit"
                events.append(Event(out.tick * cfg.dt_s, reason, str(error)))
                break
            accel0 = float((k1[:3] + np.cross(x[3:6], x[:3]))[1])
            accel1 = float((end_derivative[:3] + np.cross(candidate.x[3:6], candidate.x[:3]))[1])
            e = candidate.exposure
            e.elapsed_s = candidate.tick * h
            e.roll_squared_rad2_s += h * (x[9]**2 + candidate.x[9]**2) / 2
            e.pitch_squared_rad2_s += h * (x[10]**2 + candidate.x[10]**2) / 2
            e.lateral_squared_m2_s3 += h * (accel0**2 + accel1**2) / 2
            e.roll_exceeded_s += h * _exceed_fraction(x[9], candidate.x[9], math.radians(cfg.roll_exposure_deg))
            e.lateral_exceeded_s += h * _exceed_fraction(accel0, accel1, cfg.lateral_exposure_mps2)
            risks = tuple(name for name, exceeded in (
                ("roll_exposure", abs(candidate.x[9]) > math.radians(cfg.roll_exposure_deg) *
                 (0.9 if "roll_exposure" in out.active_risks else 1)),
                ("lateral_exposure", abs(accel1) > cfg.lateral_exposure_mps2 *
                 (0.9 if "lateral_exposure" in out.active_risks else 1))) if exceeded)
            for risk in risks:
                if risk not in out.active_risks:
                    events.append(Event(candidate.tick * h, "threshold_entered", risk))
            for risk in out.active_risks:
                if risk not in risks:
                    events.append(Event(candidate.tick * h, "threshold_left", risk))
            candidate.active_risks = risks
            for flag in candidate.wave_flags:
                if flag not in out.wave_flags:
                    events.append(Event(out.tick * h, "wave_quality", flag))
            out = candidate
            last_sample = self._sample(out, endpoint_sea, accel1)
            if out.tick % record_ticks == 0:
                trajectory.append(last_sample)
        if out.tick > state.tick and (not trajectory or trajectory[-1].time_s != out.tick * cfg.dt_s):
            trajectory.append(last_sample)
        if reason != "duration_reached" and not any(e.kind == reason for e in events):
            events.append(Event(out.tick * cfg.dt_s, reason, "Stopped at the last valid integration state"))
        return AdvanceResult(out, tuple(trajectory), tuple(events), reason, (out.tick - state.tick) * cfg.dt_s)
