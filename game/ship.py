"""Total ship loss from extreme motion; thresholds are authored game assumptions."""

import math
import random


class ShipDamageModel:
    algorithm_id = "extreme_motion_loss"
    # Authored per-excursion CDF, not observed real-world casualty frequencies.
    roll_capsize_table = ((35.0, 0.0), (40.0, 0.005), (45.0, 0.05),
                          (50.0, 0.30), (55.0, 0.65), (60.0, 0.90), (65.0, 1.0))
    limits = {
        "roll_start_deg": 20.0,
        "roll_scale_deg": 20.0,
        "roll_exposure_s": 30.0,
        "roll_reset_deg": 20.0,
        "pitch_start_deg": 10.0,
        "pitch_loss_deg": 35.0,
        "vertical_start_g": 0.75,
        "horizontal_start_g": 0.5,
        "structural_exposure_s": 60.0,
        "vertical_loss_g": 3.0,
        "horizontal_loss_g": 2.0,
        "following_exposure_s": 30.0,
        "following_speed_coefficient": 1.8,
        "following_wave_speed_fraction": 0.75,
        "following_height_ratio": 0.04,
        "following_min_wavelength_ratio": 0.6,
        "following_max_wavelength_ratio": 2.3,
        "following_yaw_rate_deg_s": 1.0,
    }

    def __init__(self, vessel, seed=1):
        self.seed = seed
        self.g = vessel.main["g"]
        self.length = vessel.main["Lpp"]
        self.half_length = 0.45 * vessel.main["Lpp"]
        self.half_breadth = 0.4 * vessel.main["B"]
        self.following_speed = self.limits["following_speed_coefficient"] * math.sqrt(self.length) * 1852 / 3600
        self.following_height = self.limits["following_height_ratio"] * self.length
        self.wave_factor = self.g / (2 * math.pi)
        self.following_yaw_rate = math.radians(self.limits["following_yaw_rate_deg_s"])

    def initial_state(self):
        return {"roll_dose": 0.0, "structural_dose": 0.0, "following_dose": 0.0,
                "roll_excursions": 0, "roll_limit_deg": None,
                "lost": False, "cause": None, "time_s": None}

    def _roll_limit(self, excursion):
        draw = random.Random(f"ship-roll:{self.seed}:{excursion}").random()
        for (a, p), (b, q) in zip(self.roll_capsize_table, self.roll_capsize_table[1:]):
            if draw < q:
                return a + (b - a) * (draw - p) / (q - p)

    def _following_load(self, sample):
        relative = (sample.heading_deg - sample.wave_from_deg) % 360
        if sample.hs_m == 0 or not 135 <= relative <= 225:
            return 0.0
        wavelength_ratio = self.wave_factor * sample.tp_s ** 2 / self.length
        if not (self.limits["following_min_wavelength_ratio"] <= wavelength_ratio
                <= self.limits["following_max_wavelength_ratio"]):
            return 0.0
        direction = math.radians(sample.wave_from_deg)
        along_wave = -(sample.velocity_north_through_water_mps * math.cos(direction)
                       + sample.velocity_east_through_water_mps * math.sin(direction))
        critical = max(self.following_speed, self.wave_factor * sample.tp_s
                       * self.limits["following_wave_speed_fraction"])
        speed_load = max(0.0, along_wave / critical - 1)
        sea_load = min(1.0, sample.hs_m / self.following_height)
        response = max(1.0, abs(sample.roll_deg) / 30,
                       abs(sample.angular_velocity_z_rad_s) / self.following_yaw_rate)
        # shortcut: guidance-based exposure replaces nonlinear surf-riding/broaching dynamics.
        return speed_load * sea_load * response

    def advance(self, state, sample, dt_s):
        if state["lost"]:
            return []
        values = (
            dt_s, sample.time_s, sample.roll_deg, sample.pitch_deg,
            sample.longitudinal_acceleration_mps2, sample.lateral_acceleration_mps2,
            sample.vertical_acceleration_mps2, sample.angular_acceleration_x_rad_s2,
            sample.angular_acceleration_y_rad_s2, sample.angular_velocity_x_rad_s,
            sample.angular_velocity_y_rad_s, sample.angular_velocity_z_rad_s,
            sample.heading_deg, sample.wave_from_deg, sample.hs_m, sample.tp_s,
            sample.velocity_north_through_water_mps, sample.velocity_east_through_water_mps,
        )
        if (dt_s <= 0 or sample.hs_m < 0 or sample.tp_s <= 0
                or not all(math.isfinite(value) for value in values)):
            raise ValueError("Ship response and positive duration must be finite")
        p, q, r = (sample.angular_velocity_x_rad_s, sample.angular_velocity_y_rad_s,
                   sample.angular_velocity_z_rad_s)
        # Max |a_z| at four hull points (±0.45 Lpp, ±0.4 B, 0), including rotation.
        vertical_g = (
            abs(sample.vertical_acceleration_mps2)
            + self.half_length * abs(p * r - sample.angular_acceleration_y_rad_s2)
            + self.half_breadth * abs(q * r + sample.angular_acceleration_x_rad_s2)
        ) / self.g
        horizontal_g = math.hypot(sample.longitudinal_acceleration_mps2,
                                  sample.lateral_acceleration_mps2) / self.g
        if not math.isfinite(vertical_g) or not math.isfinite(horizontal_g):
            raise ValueError("Ship demand must be finite")
        roll, pitch = abs(sample.roll_deg), abs(sample.pitch_deg)
        limits = self.limits
        following_load = self._following_load(sample)
        if not math.isfinite(following_load):
            raise ValueError("Following-sea demand must be finite")
        if roll <= limits["roll_reset_deg"]:
            state["roll_limit_deg"] = None
        elif roll >= self.roll_capsize_table[0][0] and state["roll_limit_deg"] is None:
            state["roll_excursions"] += 1
            state["roll_limit_deg"] = self._roll_limit(state["roll_excursions"])
        cause = None
        if state["roll_limit_deg"] is not None and roll >= state["roll_limit_deg"]:
            cause = "roll_capsize"
        elif pitch >= limits["pitch_loss_deg"]:
            cause = "extreme_pitch"
        elif (vertical_g >= limits["vertical_loss_g"]
              or horizontal_g >= limits["horizontal_loss_g"]):
            cause = "extreme_acceleration"
        if cause is None:
            roll_load = max(0.0, (roll - limits["roll_start_deg"]) / limits["roll_scale_deg"])
            structural_load = max(
                0.0, pitch / limits["pitch_start_deg"] - 1,
                vertical_g / limits["vertical_start_g"] - 1,
                horizontal_g / limits["horizontal_start_g"] - 1,
            )
            # shortcut: severe-motion dose replaces flooding/stress; upgrade with GZ/hull data.
            state["roll_dose"] += roll_load ** 2 * dt_s / limits["roll_exposure_s"]
            state["structural_dose"] += structural_load ** 2 * dt_s / limits["structural_exposure_s"]
            state["following_dose"] += following_load ** 2 * dt_s / limits["following_exposure_s"]
            if state["roll_dose"] >= 1 - 1e-12:
                cause = "sustained_rolling"
            elif state["structural_dose"] >= 1 - 1e-12:
                cause = "structural_overload"
            elif state["following_dose"] >= 1 - 1e-12:
                cause = "following_sea_instability"
        if cause is None:
            return []
        state.update(lost=True, cause=cause, time_s=sample.time_s)
        return [{"time_s": sample.time_s, "kind": "ship_lost", "detail": cause,
                 "roll_deg": sample.roll_deg, "pitch_deg": sample.pitch_deg,
                 "hull_vertical_g": vertical_g, "cg_horizontal_g": horizontal_g,
                 "following_load": following_load}]

    def summary(self, state):
        return {key: state[key] for key in ("lost", "cause", "time_s")}
