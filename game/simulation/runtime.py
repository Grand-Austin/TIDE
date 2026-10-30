"""Stateful, fixed-clock six-DOF advancement. No mission rules or global state."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import math
from typing import Callable

import numpy as np

from .dynamics import derivative, rotation, surge_drag
from .environment import EnvironmentError, SeaState
from .vessel import Vessel
from .waves import WaveModel, WaveState
from .wind import WindParameters, wind_load
from . import actuators, maneuvering

EARTH_RADIUS_M = 6_371_000.0
MANEUVERING_MODEL = "s175_maneuvering"
MANEUVERING_ALGORITHM = "s175_two_timescale_mmg_seakeeping_v1"
# Source SIMcontainer PD baseline: delta_c = -Kp*(ssa(psi-psi_ref) + Td*r).
MANEUVERING_HEADING_KP = 1.0
MANEUVERING_HEADING_TD_S = 10.0
MANEUVERING_HEADING_TI_S = 300.0


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
    actuator_model: str = "teaching"

    def __post_init__(self):
        if self.actuator_model not in ("teaching", "son_nomoto", MANEUVERING_MODEL):
            raise ValueError("Unknown actuator_model")
        for name, value in vars(self).items():
            if name == "actuator_model":
                continue
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for period in (self.wave_period_s, self.record_period_s):
            if period < self.dt_s or not math.isclose(period / self.dt_s, round(period / self.dt_s)):
                raise ValueError("Wave and recording periods must be multiples of dt_s")


@dataclass
class ControlState:
    heading_ref: float
    speed_ref: float
    speed_integral: float = 0.0
    heading_integral: float = 0.0
    rudder_rad: float = 0.0
    shaft_rpm: float = 0.0
    rudder_order_rad: float = 0.0
    shaft_order_rpm: float = 0.0


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
    wave_mean_surge_n: float = 0.0
    wind_force: np.ndarray = field(default_factory=lambda: np.zeros(6))
    wave_elevation: float = 0.0
    wave_flags: tuple[str, ...] = ()
    retained_wave_energy: float = 1.0
    active_risks: tuple[str, ...] = ()
    command: Command | None = None
    # New two-time-scale mode only: LF native four-DOF and WF perturbation arrays.
    # x above stays the derived legacy-shaped cache for the existing API.
    lf: np.ndarray | None = None  # [u,v,p,r,N,E,phi,psi]
    wf: np.ndarray | None = None  # [uW,vW,wW,pW,qW,rW,xW,yW,zW,phiW,thetaW,psiW]


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
    wave_mean_surge_n: float = 0.0
    # Body-frame terms for damage loads, not Euler-angle derivatives.
    angular_acceleration_x_rad_s2: float = 0.0
    angular_velocity_y_rad_s: float = 0.0
    angular_velocity_z_rad_s: float = 0.0
    # Inertial CG acceleration in body axes; gravity is excluded.
    longitudinal_acceleration_mps2: float = 0.0
    vertical_acceleration_mps2: float = 0.0
    angular_velocity_x_rad_s: float = 0.0
    angular_acceleration_y_rad_s2: float = 0.0
    velocity_north_through_water_mps: float = 0.0
    velocity_east_through_water_mps: float = 0.0
    rudder_angle_deg: float | None = None
    shaft_rpm: float | None = None
    ordered_rudder_deg: float | None = None
    ordered_shaft_rpm: float | None = None


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

    algorithm_id = 's175_fixed_clock_v1'

    def observe(self, state: State) -> Sample:
        if self.settings.actuator_model == MANEUVERING_MODEL:
            lf, wf = self._lf_arrays(state)
            zeros = (np.zeros(8), np.zeros(12))
            positioned, _ = self._compose_arrays(lf, wf, *zeros, current_ne=(0.0, 0.0))
            sea = self._sea(replace(state, x=positioned))
            total_x, _ = self._compose_arrays(
                lf, wf, *zeros, current_ne=(sea.current_north_mps, sea.current_east_mps))
            return self._sample(replace(state, x=total_x), sea, 0.)
        return self._sample(state, self._sea(state), 0.)

    def __init__(self, vessel: Vessel, environment, *, seed: int = 1, settings: Settings | None = None,
                 wind_parameters: WindParameters | None = WindParameters()):
        self.settings = settings or Settings()
        self.vessel = vessel
        self._kernel = None
        if self.settings.actuator_model == "son_nomoto":
            if not math.isclose(vessel.main["Lpp"], 175) or not math.isclose(vessel.main["B"], 25.4):
                raise ValueError("Son–Nomoto actuator geometry is specific to the 175 m container ship")
            self.algorithm_id = "s175_son_nomoto_actuator_v1"
        elif self.settings.actuator_model == MANEUVERING_MODEL:
            from . import two_timescale
            self._kernel = two_timescale
            self.algorithm_id = MANEUVERING_ALGORITHM
        self.environment = environment
        self.waves = WaveModel(vessel, seed=seed)
        self.wind_parameters = wind_parameters

    def initial_state(self, time: datetime, lon: float, lat: float, *, heading_deg: float = 0,
                      speed_mps: float = 0) -> State:
        self.validate_command(Command(heading_deg, speed_mps))
        if time.tzinfo is None or time.utcoffset() is None:
            raise ValueError("Initial time must have a UTC offset")
        if not all(map(math.isfinite, (lon, lat))) or not -180 <= lon <= 180 or not -85 < lat < 85:
            raise ValueError("Invalid local-coordinate origin")
        time = time.astimezone(timezone.utc)
        sea = self.environment.sample(time, lon, lat)
        psi = math.radians(heading_deg % 360)
        if self.settings.actuator_model == MANEUVERING_MODEL:
            lf = np.array([speed_mps, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, psi], dtype=float)
            wf = np.zeros(12, dtype=float)
            control = ControlState(psi, speed_mps)
            straight = self._source_straight_resistance(speed_mps)
            control.shaft_rpm = control.shaft_order_rpm = min(
                maneuvering.RPM_LIMIT,
                actuators.rpm_for_thrust(straight, [speed_mps, 0, 0, 0, 0, 0],
                                                 rho=self.vessel.main["rho"]))
            state = State(time, lon, lat, np.zeros(12), control, self.waves.initial_state(),
                          lf=lf, wf=wf)
            state.x, _ = self._compose_state(
                state, current_ne=(sea.current_north_mps, sea.current_east_mps))
            return state
        x = np.zeros(12)
        x[11] = psi
        current_body = rotation(x[9:12]).T @ np.array([sea.current_north_mps, sea.current_east_mps, 0.0])
        x[:3] = current_body + [speed_mps, 0.0, 0.0]
        control = ControlState(psi, speed_mps)
        if self.settings.actuator_model == "son_nomoto":
            control.shaft_rpm = min(actuators.RPM_LIMIT, actuators.rpm_for_thrust(
                surge_drag(speed_mps, self.vessel), [speed_mps, 0, 0, 0, 0, 0], rho=self.vessel.main["rho"]))
            control.shaft_order_rpm = control.shaft_rpm
        return State(time, lon, lat, x, control, self.waves.initial_state())

    def position(self, state: State) -> tuple[float, float]:
        x = state.x
        if self.settings.actuator_model == MANEUVERING_MODEL and state.lf is not None:
            lf, wf = self._lf_arrays(state)
            x, _ = self._compose_arrays(lf, wf, np.zeros(8), np.zeros(12), current_ne=(0.0, 0.0))
        lat = state.origin_lat + math.degrees(x[6] / EARTH_RADIUS_M)
        lon = state.origin_lon + math.degrees(x[7] / (EARTH_RADIUS_M * math.cos(math.radians(state.origin_lat))))
        return lon, lat

    def _lf_arrays(self, state: State) -> tuple[np.ndarray, np.ndarray]:
        if state.lf is None or state.wf is None:
            raise ValueError("The maneuvering mode requires both LF and WF state arrays")
        lf = np.asarray(state.lf, dtype=float)
        wf = np.asarray(state.wf, dtype=float)
        if lf.shape != (8,) or wf.shape != (12,):
            raise ValueError("Invalid LF/WF state shape")
        if not np.all(np.isfinite(lf)) or not np.all(np.isfinite(wf)):
            raise ValueError("LF/WF state must be finite")
        return lf, wf

    def _compose_arrays(self, lf, wf, lf_dot, wf_dot, *, current_ne):
        total_x, total_dot = self._kernel.compose(
            lf, wf, lf_dot, wf_dot, current_ne=current_ne)
        total_x = np.asarray(total_x, dtype=float)
        total_dot = np.asarray(total_dot, dtype=float)
        if total_x.shape != (12,) or total_dot.shape != (12,):
            raise ValueError("Composed state and derivative must be twelve-vectors")
        if not np.all(np.isfinite(total_x)) or not np.all(np.isfinite(total_dot)):
            raise ValueError("Composed state and derivative must be finite")
        return total_x, total_dot

    def _compose_state(self, state: State, *, lf_dot=None, wf_dot=None,
                       current_ne=(0.0, 0.0)):
        lf, wf = self._lf_arrays(state)
        return self._compose_arrays(
            lf, wf,
            np.zeros(8) if lf_dot is None else lf_dot,
            np.zeros(12) if wf_dot is None else wf_dot,
            current_ne=current_ne)

    @staticmethod
    def _lf_water_velocity_ne(lf: np.ndarray) -> np.ndarray:
        u, v = lf[0], lf[1]
        phi, psi = lf[6], lf[7]
        return np.array([math.cos(psi)*u - math.sin(psi)*v*math.cos(phi),
                         math.sin(psi)*u + math.cos(psi)*v*math.cos(phi)])

    @staticmethod
    def _lf_wind_state(lf: np.ndarray, sea: SeaState) -> np.ndarray:
        """Legacy-shaped LF mean ground state for the horizontal wind model."""
        u, v = lf[0], lf[1]
        phi, psi = lf[6], lf[7]
        current_body = np.array([
            math.cos(psi)*sea.current_north_mps + math.sin(psi)*sea.current_east_mps,
            -math.sin(psi)*sea.current_north_mps + math.cos(psi)*sea.current_east_mps,
        ])
        x = np.zeros(12)
        x[:3] = [u, v*math.cos(phi), 0.0] + np.r_[current_body, 0.0]
        x[11] = psi
        return x

    def _source_straight_resistance(self, speed_mps: float) -> float:
        """Required ahead thrust from the source straight-hull X term."""
        force = maneuvering.hull_force(
            np.array([max(0.0, float(speed_mps)), 0, 0, 0, 0, 0], dtype=float),
            0.0, self.vessel.main)
        return max(0.0, -float(force[0]))

    def validate_command(self, command: Command):
        if not math.isfinite(command.heading_deg) or not math.isfinite(command.speed_mps):
            raise ValueError("Commands must be finite")
        if command.speed_mps < 0:
            raise ValueError("Speed must be nonnegative")

    def _sea(self, state: State) -> SeaState:
        return self.environment.sample(state.start_time + timedelta(seconds=state.tick * self.settings.dt_s),
                                       *self.position(state))

    @staticmethod
    def _water_velocity(x: np.ndarray, sea: SeaState) -> np.ndarray:
        ground = rotation(x[9:12]) @ x[:3]
        return ground - np.array([sea.current_north_mps, sea.current_east_mps, 0.0])

    @staticmethod
    def _actuator_velocity(x, sea):
        # Use the same yaw-only current convention as the pinned hull kinetics.
        psi = x[11]
        north, east = sea.current_north_mps, sea.current_east_mps
        current = np.array([north*math.cos(psi)+east*math.sin(psi),
                            -north*math.sin(psi)+east*math.cos(psi), 0.0])
        return np.r_[x[:3]-current, x[3:6]]

    def _control(self, state: State, command: Command, sea: SeaState) -> np.ndarray | None:
        """Update orders; legacy modes also return their beginning-of-step force."""
        cfg, v, c = self.settings, self.vessel, state.control
        if cfg.actuator_model == MANEUVERING_MODEL:
            return self._maneuvering_control(state, command, sea)
        c.heading_ref += float(np.clip(_wrap(math.radians(command.heading_deg) - c.heading_ref),
                                      -cfg.max_reference_yaw_rate * cfg.dt_s, cfg.max_reference_yaw_rate * cfg.dt_s))
        c.speed_ref += float(np.clip(command.speed_mps - c.speed_ref,
                                    -cfg.max_reference_accel * cfg.dt_s, cfg.max_reference_accel * cfg.dt_s))
        water_body = rotation(state.x[9:12]).T @ self._water_velocity(state.x, sea)
        if cfg.actuator_model == "son_nomoto":
            water_body = self._actuator_velocity(state.x, sea)[:3]
        error = c.speed_ref - water_body[0]
        kp = v.M[0, 0] / cfg.speed_time_constant_s
        ki = kp / (4 * cfg.speed_time_constant_s)
        thrust = surge_drag(c.speed_ref, v) + kp * error + ki * c.speed_integral
        if cfg.actuator_model == "son_nomoto":
            return self._actuator_control(state, command, water_body, thrust, error)
        if abs(thrust) < cfg.max_thrust_n or thrust * error < 0:
            c.speed_integral += error * cfg.dt_s
        # PID autopilot. The integral removes the steady heading error that a constant wind and wave
        # yaw moment leaves under PD control. It accumulates only once the reference has reached the
        # ordered heading, the ship is within 10° of it and the rudder is not saturated, and it can ask
        # for at most the rudder limit.
        heading_error = _wrap(c.heading_ref - state.x[11])
        w = cfg.yaw_natural_frequency
        ki = v.M[5, 5] * w**3 / 5
        yaw = v.M[5, 5] * (w**2 * heading_error - 2 * w * state.x[5]) + ki * c.heading_integral
        yaw_limit = cfg.max_yaw_moment_nm
        # integrate only near the ordered heading: during a long turn the error is large for minutes and
        # would wind the integral up to the rudder limit, which then carries the ship 15–40° past the order
        settled = abs(_wrap(math.radians(command.heading_deg) - c.heading_ref)) < 1e-9 and abs(heading_error) < math.radians(10)
        if settled and (abs(yaw) < yaw_limit or yaw * heading_error < 0):
            c.heading_integral = float(np.clip(c.heading_integral + heading_error * cfg.dt_s,
                                               -yaw_limit / ki, yaw_limit / ki))
        return np.array([np.clip(thrust, -cfg.max_thrust_n, cfg.max_thrust_n), 0, 0, 0, 0,
                         np.clip(yaw, -yaw_limit, yaw_limit)], dtype=float)

    def _maneuvering_control(self, state: State, command: Command, sea: SeaState) -> None:
        """Source-style PD heading and force-to-RPM speed PI for the LF mean ship.

        The heading loop sees the native LF heading and cos(phi)*r. It only
        writes a bounded rudder order; it never applies a yaw moment or rate.
        """
        cfg, v, c = self.settings, self.vessel, state.control
        lf, _ = self._lf_arrays(state)
        c.speed_ref += float(np.clip(command.speed_mps - c.speed_ref,
                                    -cfg.max_reference_accel * cfg.dt_s, cfg.max_reference_accel * cfg.dt_s))
        heading_error = _wrap(math.radians(command.heading_deg) - lf[7])
        c.heading_ref = lf[7] + heading_error
        native_rate = math.cos(lf[6]) * lf[3]
        rudder_limit = maneuvering.RUDDER_LIMIT_RAD
        integral_term = MANEUVERING_HEADING_KP * c.heading_integral / MANEUVERING_HEADING_TI_S
        rudder = MANEUVERING_HEADING_KP * (
            heading_error - MANEUVERING_HEADING_TD_S * native_rate) + integral_term
        settled = abs(heading_error) < math.radians(10.0)
        if settled and (abs(rudder) < rudder_limit or rudder * heading_error < 0):
            limit = rudder_limit * MANEUVERING_HEADING_TI_S / MANEUVERING_HEADING_KP
            c.heading_integral = float(np.clip(c.heading_integral + heading_error * cfg.dt_s,
                                               -limit, limit))
        c.rudder_order_rad = float(np.clip(rudder, -rudder_limit, rudder_limit))

        u = lf[0]
        velocity = np.array([lf[0], lf[1], 0.0, lf[2], 0.0, lf[3]], dtype=float)
        speed_error = c.speed_ref - u
        kp = v.M[0, 0] / cfg.speed_time_constant_s
        ki = kp / (4.0 * cfg.speed_time_constant_s)
        thrust = self._source_straight_resistance(c.speed_ref) + kp * speed_error + ki * c.speed_integral
        rpm = actuators.rpm_for_thrust(max(thrust, 0.0), velocity, rho=v.main["rho"])
        c.shaft_order_rpm = float(np.clip(rpm, 0.0, maneuvering.RPM_LIMIT))
        if (0.0 < rpm < maneuvering.RPM_LIMIT or
                rpm >= maneuvering.RPM_LIMIT and speed_error < 0 or
                rpm <= 0.0 and speed_error > 0):
            c.speed_integral += speed_error * cfg.dt_s

    def _actuator_control(self, state, command, water_body, thrust, speed_error):
        """Map the PID force requests to bounded physical actuator orders."""
        cfg, v, c = self.settings, self.vessel, state.control
        velocity = np.r_[water_body, state.x[3:6]]
        rpm = actuators.rpm_for_thrust(thrust, velocity, rho=v.main["rho"])
        c.shaft_order_rpm = float(np.clip(rpm, 0, actuators.RPM_LIMIT))
        if (0 < rpm < actuators.RPM_LIMIT or
                rpm >= actuators.RPM_LIMIT and speed_error < 0 or rpm <= 0 and speed_error > 0):
            c.speed_integral += speed_error * cfg.dt_s
        error = _wrap(c.heading_ref - state.x[11])
        w = cfg.yaw_natural_frequency
        ki = v.M[5, 5] * w**3 / 5
        moment = v.M[5, 5] * (w*w*error - 2*w*state.x[5]) + ki*c.heading_integral
        neutral = actuators.loads(velocity, 0, c.shaft_rpm, rho=v.main["rho"])[5]
        slope = (actuators.loads(velocity, 0.01, c.shaft_rpm, rho=v.main["rho"])[5] -
                 actuators.loads(velocity, -0.01, c.shaft_rpm, rho=v.main["rho"])[5]) / 0.02
        # No flow gives no steering authority. Freeze the integral until flow returns.
        rudder = ((moment-neutral)/slope if slope > 1e-9 else
                  (0.0 if abs(moment) < 1e-9 else math.copysign(actuators.RUDDER_LIMIT_RAD, moment)))
        c.rudder_order_rad = float(np.clip(rudder, -actuators.RUDDER_LIMIT_RAD, actuators.RUDDER_LIMIT_RAD))
        settled = abs(_wrap(math.radians(command.heading_deg)-c.heading_ref)) < 1e-9 and abs(error) < math.radians(10)
        if slope > 1e-9 and settled and (abs(rudder) < actuators.RUDDER_LIMIT_RAD or rudder*error < 0):
            integral_limit = slope*actuators.RUDDER_LIMIT_RAD/ki
            c.heading_integral = float(np.clip(c.heading_integral+error*cfg.dt_s, -integral_limit, integral_limit))
        return actuators.loads(velocity, c.rudder_rad, c.shaft_rpm, rho=v.main["rho"])

    def _sample(self, state: State, sea: SeaState, acceleration: float,
                response_derivative=None) -> Sample:
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
                      float(state.wind_force[1]), float(state.wind_force[5]), sea.sst_c,
                      state.wave_mean_surge_n,
                      float(response_derivative[3]) if response_derivative is not None else 0.0,
                      float(x[4]), float(x[5]),
                      float(response_derivative[0] + x[4]*x[2] - x[5]*x[1])
                      if response_derivative is not None else 0.0,
                      float(response_derivative[2] + x[3]*x[1] - x[4]*x[0])
                      if response_derivative is not None else 0.0,
                      float(x[3]),
                      float(response_derivative[4]) if response_derivative is not None else 0.0,
                      float(water[0]), float(water[1]),
                      math.degrees(state.control.rudder_rad) if self.settings.actuator_model in ("son_nomoto", MANEUVERING_MODEL) else None,
                      state.control.shaft_rpm if self.settings.actuator_model in ("son_nomoto", MANEUVERING_MODEL) else None,
                      math.degrees(state.control.rudder_order_rad) if self.settings.actuator_model in ("son_nomoto", MANEUVERING_MODEL) else None,
                      state.control.shaft_order_rpm if self.settings.actuator_model in ("son_nomoto", MANEUVERING_MODEL) else None)

    def _advance_maneuvering(self, state: State, command: Command, duration_s: float, *,
                             should_pause: Callable[[], bool] | None = None,
                             on_step: Callable[[Sample, float], str | None] | None = None) -> AdvanceResult:
        """Coupled fixed-clock RK4 for LF 8, WF 12 and two servo states."""
        cfg = self.settings
        if not math.isfinite(duration_s) or duration_s <= 0:
            raise ValueError("duration_s must be finite and positive")
        steps = round(duration_s / cfg.dt_s)
        if steps < 1 or not math.isclose(steps * cfg.dt_s, duration_s, abs_tol=1e-9):
            raise ValueError("duration_s must be a multiple of dt_s")
        lf, wf = self._lf_arrays(state)
        if state.tick < 0:
            raise ValueError("Invalid simulation state")
        out = deepcopy(state)
        out.lf, out.wf = lf.copy(), wf.copy()
        out.command = command
        trajectory, events = [], []
        last_sample = None
        reason = "duration_reached"
        wave_ticks = round(cfg.wave_period_s / cfg.dt_s)
        record_ticks = round(cfg.record_period_s / cfg.dt_s)
        rho = self.vessel.main["rho"]
        for _ in range(steps):
            if should_pause is not None and should_pause():
                reason = "paused"
                break
            candidate = deepcopy(out)
            try:
                sea = self._sea(out)
                self._control(candidate, command, sea)
                if out.tick % wave_ticks == 0:
                    wave = self.waves.evaluate(
                        out.wave, out.tick * cfg.dt_s, sea, out.lf[7],
                        self._lf_water_velocity_ne(out.lf))
                    candidate.wave = wave.state
                    candidate.wave_force = wave.force
                    candidate.wave_mean_surge_n = wave.mean_surge_n
                    candidate.wave_elevation = wave.elevation
                    candidate.wave_flags = wave.flags
                    candidate.retained_wave_energy = wave.retained_energy
                candidate.wind_force = (
                    wind_load(self._lf_wind_state(out.lf, sea), sea, self.wind_parameters)
                    if self.wind_parameters is not None else np.zeros(6))
                current = (sea.current_north_mps, sea.current_east_mps)
                wave_force = np.array(candidate.wave_force, dtype=float, copy=True)
                mean_surge = float(candidate.wave_mean_surge_n)
                wind_force = np.array(candidate.wind_force, dtype=float, copy=True)
                h = cfg.dt_s
                z = np.r_[out.lf, out.wf, out.control.rudder_rad, out.control.shaft_rpm]

                def f(z):
                    lf_z, wf_z = z[:8], z[8:20]
                    rudder, rpm = float(z[20]), float(z[21])
                    velocity = np.array([lf_z[0], lf_z[1], 0.0, lf_z[2], 0.0, lf_z[3]])
                    external = actuators.loads(velocity, rudder, rpm, rho=rho) + wind_force
                    external[0] += mean_surge
                    lf_dot = self._kernel.lf_derivative(
                        lf_z, external, self.vessel, current_ne=current)
                    wf_dot = derivative(wf_z, wave_force, self.vessel, (0, 0))
                    servo = maneuvering.servo_rates(
                        rudder, rpm, candidate.control.rudder_order_rad,
                        candidate.control.shaft_order_rpm)
                    return np.r_[lf_dot, wf_dot, servo]

                k1 = f(z)
                k2 = f(z + h * k1 / 2)
                k3 = f(z + h * k2 / 2)
                k4 = f(z + h * k3)
                endpoint = z + h * (k1 + 2*k2 + 2*k3 + k4) / 6
                candidate.lf = np.array(endpoint[:8], dtype=float, copy=True)
                candidate.wf = np.array(endpoint[8:20], dtype=float, copy=True)
                candidate.control.rudder_rad = float(endpoint[20])
                candidate.control.shaft_rpm = float(endpoint[21])
                candidate.tick += 1
                if not np.all(np.isfinite(endpoint)):
                    reason = "numerical_failure"
                    break
                kend = f(endpoint)
                endpoint_sea = self._sea(candidate)
                total_x_begin, total_dot_begin = self._compose_arrays(
                    out.lf, out.wf, k1[:8], k1[8:20], current_ne=current)
                total_x_end, total_dot_end = self._compose_arrays(
                    candidate.lf, candidate.wf, kend[:8], kend[8:20],
                    current_ne=(endpoint_sea.current_north_mps, endpoint_sea.current_east_mps))
                candidate.x = total_x_end
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
            accel0 = float((total_dot_begin[:3] + np.cross(
                total_x_begin[3:6], total_x_begin[:3]))[1])
            accel1 = float((total_dot_end[:3] + np.cross(
                total_x_end[3:6], total_x_end[:3]))[1])
            roll0, roll1 = float(total_x_begin[9]), float(total_x_end[9])
            pitch0, pitch1 = float(total_x_begin[10]), float(total_x_end[10])
            e = candidate.exposure
            e.elapsed_s = candidate.tick * h
            e.roll_squared_rad2_s += h * (roll0**2 + roll1**2) / 2
            e.pitch_squared_rad2_s += h * (pitch0**2 + pitch1**2) / 2
            e.lateral_squared_m2_s3 += h * (accel0**2 + accel1**2) / 2
            e.roll_exceeded_s += h * _exceed_fraction(
                roll0, roll1, math.radians(cfg.roll_exposure_deg))
            e.lateral_exceeded_s += h * _exceed_fraction(
                accel0, accel1, cfg.lateral_exposure_mps2)
            risks = tuple(name for name, exceeded in (
                ("roll_exposure", abs(roll1) > math.radians(cfg.roll_exposure_deg) *
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
            last_sample = self._sample(out, endpoint_sea, accel1, total_dot_end)
            if out.tick % record_ticks == 0:
                trajectory.append(last_sample)
            if on_step is not None:
                stop = on_step(last_sample, h)
                if stop:
                    reason = stop
                    break
        if out.tick > state.tick and (not trajectory or trajectory[-1].time_s != out.tick * cfg.dt_s):
            trajectory.append(last_sample)
        if reason != "duration_reached" and not any(e.kind == reason for e in events):
            events.append(Event(out.tick * cfg.dt_s, reason, "Stopped at the last valid integration state"))
        return AdvanceResult(out, tuple(trajectory), tuple(events), reason,
                             (out.tick - state.tick) * cfg.dt_s)

    def advance(self, state: State, command: Command, duration_s: float, *,
                should_pause: Callable[[], bool] | None = None,
                on_step: Callable[[Sample, float], str | None] | None = None) -> AdvanceResult:
        """Advance a copy; return the last valid state at any mandatory stop.

        duration_s must be a positive multiple of dt_s. Threshold events report
        teaching exposure only; game cargo loss and mission outcomes live elsewhere.
        """
        self.validate_command(command)
        if self.settings.actuator_model == MANEUVERING_MODEL:
            return self._advance_maneuvering(
                state, command, duration_s, should_pause=should_pause, on_step=on_step)
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
                    candidate.wave_mean_surge_n = wave.mean_surge_n
                    candidate.wave_elevation = wave.elevation
                    candidate.wave_flags = wave.flags
                    candidate.retained_wave_energy = wave.retained_energy
                candidate.wind_force = (wind_load(out.x, sea, self.wind_parameters)
                                        if self.wind_parameters is not None else np.zeros(6))
                total_force = tau + candidate.wave_force + candidate.wind_force
                total_force[0] += candidate.wave_mean_surge_n
                current = (sea.current_north_mps, sea.current_east_mps)
                h, x = cfg.dt_s, out.x
                if cfg.actuator_model == "son_nomoto":
                    external = candidate.wave_force + candidate.wind_force
                    external[0] += candidate.wave_mean_surge_n

                    def f(z):
                        force = actuators.loads(self._actuator_velocity(z[:12], sea), z[12], z[13],
                                                rho=self.vessel.main["rho"])
                        servo = actuators.rates(z[12], z[13], candidate.control.rudder_order_rad,
                                               candidate.control.shaft_order_rpm)
                        return np.r_[derivative(z[:12], external+force, self.vessel, current), servo]

                    z = np.r_[x, out.control.rudder_rad, out.control.shaft_rpm]
                else:
                    f = lambda z: derivative(z, total_force, self.vessel, current)
                    z = x
                k1 = f(z)
                k2 = f(z + h * k1 / 2)
                k3 = f(z + h * k2 / 2)
                k4 = f(z + h * k3)
                endpoint = z + h * (k1 + 2*k2 + 2*k3 + k4) / 6
                candidate.x = endpoint[:12]
                if cfg.actuator_model == "son_nomoto":
                    candidate.control.rudder_rad, candidate.control.shaft_rpm = map(float, endpoint[12:])
                candidate.tick += 1
                if not np.all(np.isfinite(endpoint)):
                    reason = "numerical_failure"
                    break
                endpoint_sea = self._sea(candidate)
                if cfg.actuator_model == "son_nomoto":
                    total_force = external + actuators.loads(self._actuator_velocity(candidate.x, endpoint_sea),
                        candidate.control.rudder_rad, candidate.control.shaft_rpm, rho=self.vessel.main["rho"])
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
            last_sample = self._sample(out, endpoint_sea, accel1, end_derivative)
            if out.tick % record_ticks == 0:
                trajectory.append(last_sample)
            if on_step is not None:
                stop = on_step(last_sample, h)
                if stop:
                    reason = stop
                    break
        if out.tick > state.tick and (not trajectory or trajectory[-1].time_s != out.tick * cfg.dt_s):
            trajectory.append(last_sample)
        if reason != "duration_reached" and not any(e.kind == reason for e in events):
            events.append(Event(out.tick * cfg.dt_s, reason, "Stopped at the last valid integration state"))
        return AdvanceResult(out, tuple(trajectory), tuple(events), reason, (out.tick - state.tick) * cfg.dt_s)
