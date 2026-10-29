"""Replaceable game cargo damage; not a calibrated container-securing solver."""

import math
import random


class CargoModel:
    algorithm_id = "response_damage_v1"

    def __init__(self, config=None, seed=1):
        self.config = {
            "threshold_g": 0.30,
            "damage_time_s": 60.0,
            "multipliers": [1.0, 1.15, 1.3],
        }
        self.config.update(config or {})
        self.seed = seed
        if any(
            not math.isfinite(self.config[k]) or self.config[k] <= 0
            for k in ("threshold_g", "damage_time_s")
        ):
            raise ValueError("Cargo thresholds must be finite and positive")
        if len(self.config["multipliers"]) != 3 or any(
            not math.isfinite(v) or v <= 0 for v in self.config["multipliers"]
        ):
            raise ValueError("Cargo requires three positive exposure multipliers")

    def initial_state(self):
        rng = random.Random(self.seed)
        units = []
        for location, count in (("deck", 320), ("hold", 480)):
            for i in range(count):
                units.append(
                    {
                        "id": f"{location}-{i + 1:03}",
                        "teu": 1 if i % 2 == 0 else 2,
                        "category": i % 3 if location == "deck" else None,
                        "threshold": rng.uniform(0.8, 1.2),
                        "overboard": False,
                    }
                )
        return {
            "units": units,
            "damage": [0.0, 0.0, 0.0],
            "grade": 0,
            "lost_units": 0,
            "lost_teu": 0,
            "next_threshold": [
                min(u["threshold"] for u in units[:320] if u["category"] == c)
                for c in range(3)
            ],
        }

    def advance(self, state, sample, dt_s):
        if not math.isfinite(dt_s) or dt_s <= 0:
            raise ValueError("Damage duration must be positive")
        values = (sample.roll_deg, sample.pitch_deg, sample.lateral_acceleration_mps2)
        if not all(math.isfinite(v) for v in values):
            raise ValueError("Cargo response must be finite")
        gravity_y = (
            9.81
            * math.sin(math.radians(sample.roll_deg))
            * math.cos(math.radians(sample.pitch_deg))
        )
        demand = abs(sample.lateral_acceleration_mps2 - gravity_y) / 9.81
        lost = []
        for category, multiplier in enumerate(self.config["multipliers"]):
            state["damage"][category] += (
                max(0.0, demand * multiplier / self.config["threshold_g"] - 1) ** 2
                * dt_s
                / self.config["damage_time_s"]
            )
            if state["damage"][category] < state["next_threshold"][category]:
                continue
            pending = []
            for unit in state["units"][:320]:
                if unit["category"] == category and not unit["overboard"]:
                    if state["damage"][category] >= unit["threshold"]:
                        unit["overboard"] = True
                        lost.append(unit["id"])
                        state["lost_units"] += 1
                        state["lost_teu"] += unit["teu"]
                    else:
                        pending.append(unit["threshold"])
            # All possible thresholds are <=1.2; this finite sentinel serializes safely.
            state["next_threshold"][category] = min(pending, default=1e300)
        fraction = state["lost_teu"] / 1200
        old = state["grade"]
        state["grade"] = (
            0
            if fraction == 0
            else 1 + sum(fraction > b for b in (0.005, 0.01, 0.03, 0.1))
        )
        events = []
        if lost:
            events.append(
                {
                    "time_s": sample.time_s,
                    "kind": "cargo_lost",
                    "detail": f"{len(lost)} units overboard",
                    "unit_ids": lost,
                }
            )
        if state["grade"] > old:
            events.append(
                {
                    "time_s": sample.time_s,
                    "kind": "cargo_grade_increased",
                    "detail": str(state["grade"]),
                }
            )
        return events

    def summary(self, state):
        return {
            "total_units": 800,
            "total_teu": 1200,
            "deck_units": 320,
            "hold_units": 480,
            "lost_units": state["lost_units"],
            "lost_teu": state["lost_teu"],
            "remaining_units": 800 - state["lost_units"],
            "loss_fraction": state["lost_teu"] / 1200,
            "grade": state["grade"],
        }
