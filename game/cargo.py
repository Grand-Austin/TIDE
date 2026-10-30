"""Game cargo loss from sustained overload of five-container stacks."""

from bisect import bisect_right
import math

import numpy as np


STACKS = 64
TIERS = 5
TIER_HEIGHTS_M = (5.0, 7.5, 10.0, 12.5, 15.0)
# Equal tier masses: overturning moment divided by mass-weighted height.
EFFECTIVE_HEIGHT_M = sum(h * h for h in TIER_HEIGHTS_M) / sum(TIER_HEIGHTS_M)
STACK_UNIT_INDICES = tuple(
    tuple(10 * (s // 2) + s % 2 + 2 * tier for tier in range(TIERS))
    for s in range(STACKS)
)


def stack_unit_ids(stack):
    return [f"deck-{index + 1:03}" for index in STACK_UNIT_INDICES[stack]]


def _grade(lost_teu):
    fraction = lost_teu / 1200
    return 0 if not lost_teu else 1 + sum(fraction > b for b in (0.005, 0.01, 0.03, 0.1))


class CargoModel:
    algorithm_id = "stack_overload"

    def __init__(self, config=None, seed=1):
        self.config = {"median_g": 0.5, "sigma": 0.35, "exposure_s": 1.0, "strength": 1.0}
        if config and config.keys() - self.config.keys():
            raise ValueError("Unknown cargo configuration")
        self.config.update(config or {})
        if (any(not math.isfinite(self.config[k]) or self.config[k] <= 0
                for k in ("median_g", "exposure_s", "strength"))
                or not math.isfinite(self.config["sigma"]) or self.config["sigma"] < 0):
            raise ValueError("Cargo scales must be finite and positive; sigma must be nonnegative")
        self.seed = seed

    def initial_state(self):
        capacities = self.config["median_g"] * self.config["strength"] * np.exp(
            np.random.default_rng(self.seed).normal(0, self.config["sigma"], STACKS)
        )
        if not np.isfinite(capacities).all() or (capacities <= 0).any():
            raise ValueError("Cargo capacities are outside the finite positive range")
        order = sorted(range(STACKS), key=capacities.__getitem__)
        return {
            "capacities_g": [float(capacities[i]) for i in order],
            "stack_order": order,
            "overload_started_steps": [0] * STACKS,
            "overloaded_count": 0,
            "failed_count": 0,
            "tick": 0,
            "dt_s": None,
            "required_steps": 0,
            "grade": 0,
            "lost_units": 0,
            "lost_teu": 0,
        }

    def advance(self, state, sample, dt_s):
        if state["lost_units"] == 800:
            return []
        values = (sample.time_s, sample.roll_deg, sample.pitch_deg,
                  sample.lateral_acceleration_mps2, sample.angular_acceleration_x_rad_s2,
                  sample.angular_velocity_y_rad_s, sample.angular_velocity_z_rad_s)
        if not math.isfinite(dt_s) or dt_s <= 0:
            raise ValueError("Cargo duration must be finite and positive")
        if not all(math.isfinite(v) for v in values):
            raise ValueError("Cargo response must be finite")
        if state["dt_s"] is not None and state["dt_s"] != dt_s:
            raise ValueError("Cargo requires a fixed integration step")
        gravity_y = 9.81 * math.sin(math.radians(sample.roll_deg)) * math.cos(math.radians(sample.pitch_deg))
        demand = abs(sample.lateral_acceleration_mps2 - gravity_y + EFFECTIVE_HEIGHT_M * (
            sample.angular_acceleration_x_rad_s2
            - sample.angular_velocity_y_rad_s * sample.angular_velocity_z_rad_s
        )) / 9.81
        if not math.isfinite(demand):
            raise ValueError("Cargo demand must be finite")
        if state["dt_s"] is None:
            state["dt_s"] = dt_s
            state["required_steps"] = max(1, math.ceil(self.config["exposure_s"] / dt_s - 1e-12))
        state["tick"] += 1
        tick = state["tick"]
        overloaded = bisect_right(state["capacities_g"], demand)
        failed = state["failed_count"]
        starts = state["overload_started_steps"]
        # Leaving intervals restart on re-entry; only entering stacks need a write.
        for i in range(max(state["overloaded_count"], failed), overloaded):
            starts[i] = tick
        state["overloaded_count"] = overloaded
        end = failed
        # shortcut: one shared load permits a prefix; distinct stack loads need separate intervals.
        while end < overloaded and tick - starts[end] + 1 >= state["required_steps"]:
            end += 1
        if end == failed:
            return []
        stacks = state["stack_order"][failed:end]
        lost = [unit for stack in stacks for unit in stack_unit_ids(stack)]
        state["failed_count"] = end
        state["lost_units"] = end * TIERS
        state["lost_teu"] += TIERS * sum(1 + stack % 2 for stack in stacks)
        before = state["grade"]
        state["grade"] = _grade(state["lost_teu"])
        events = [{"time_s": sample.time_s, "kind": "cargo_lost",
                   "detail": f"{len(lost)} units overboard", "unit_ids": lost, "stack_ids": stacks}]
        if state["grade"] > before:
            events.append({"time_s": sample.time_s, "kind": "cargo_grade_increased",
                           "detail": str(state["grade"])})
        return events

    def lose_all(self, state, time_s):
        if state["lost_units"] == 800:
            return []
        units, teu, grade = state["lost_units"], state["lost_teu"], state["grade"]
        state.update(failed_count=STACKS, lost_units=800, lost_teu=1200, grade=5)
        events = [{"time_s": time_s, "kind": "cargo_total_loss",
                   "detail": "All remaining cargo lost with the ship",
                   "lost_units": 800 - units, "lost_teu": 1200 - teu}]
        if grade < 5:
            events.append({"time_s": time_s, "kind": "cargo_grade_increased", "detail": "5"})
        return events

    def summary(self, state):
        return {
            "total_units": 800, "total_teu": 1200, "deck_units": 320, "hold_units": 480,
            "lost_units": state["lost_units"], "lost_teu": state["lost_teu"],
            "remaining_units": 800 - state["lost_units"],
            "loss_fraction": state["lost_teu"] / 1200, "grade": state["grade"],
            "failed_stacks": state["failed_count"],
        }
