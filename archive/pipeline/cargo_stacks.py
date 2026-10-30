"""Experimental stack failure on a fixed clock; not a securing-system solver."""

import math

import numpy as np


TIERS = 5
STACKS = 64
TIER_HEIGHTS_M = np.array([5.0, 7.5, 10.0, 12.5, 15.0])
# Equal box masses within a stack: overturning moment / (mass * mean height).
EFFECTIVE_HEIGHT_M = float(TIER_HEIGHTS_M @ TIER_HEIGHTS_M / TIER_HEIGHTS_M.sum())
STACK_UNIT_INDICES = np.array([
    [10 * (s // 2) + s % 2 + 2 * tier for tier in range(TIERS)]
    for s in range(STACKS)
])


class StackCargo:
    """One capacity and consecutive-overload counter per five-box stack.

    Stack failure removes its five boxes together. Hold cargo stays protected.
    A sub-capacity sample resets the counter; this is a short-duration rupture
    rule, not fatigue accumulation across separate wave encounters.
    """

    algorithm_id = "stack_overload_experiment_v2"

    def __init__(self, dt_s, *, median_g=0.5, sigma=0.35, exposure_s=1.0,
                 strength=1.0, cargo_seed=101):
        scales = (dt_s, median_g, exposure_s, strength)
        if (not all(math.isfinite(v) and v > 0 for v in scales)
                or not math.isfinite(sigma) or sigma < 0):
            raise ValueError("Positive finite scales and nonnegative sigma required")
        self.dt_s = dt_s
        self.required_steps = max(1, math.ceil(exposure_s / dt_s - 1e-12))
        self.capacities_g = median_g * strength * np.exp(
            np.random.default_rng(cargo_seed).normal(0, sigma, STACKS)
        )
        self.overload_steps = np.zeros(STACKS, dtype=np.int64)
        self.failed = np.zeros(STACKS, dtype=bool)

    def advance(self, demand_g):
        """Accept one fixed-clock demand; return newly failed stack indices."""
        if not math.isfinite(demand_g) or demand_g < 0:
            raise ValueError("Stack demand must be finite and nonnegative")
        self.overload_steps += 1
        self.overload_steps[demand_g < self.capacities_g] = 0
        newly_failed = np.flatnonzero(
            (self.overload_steps >= self.required_steps) & ~self.failed
        )
        self.failed[newly_failed] = True
        return newly_failed

    def summary(self):
        failed = np.flatnonzero(self.failed)
        lost_units = len(failed) * TIERS
        lost_teu = int((1 + failed % 2).sum()) * TIERS
        return {
            "failed_stacks": len(failed), "failed_stack_ids": failed.tolist(),
            "lost_units": lost_units, "lost_teu": lost_teu,
            "loss_fraction_units": lost_units / 800,
            "loss_fraction_teu": lost_teu / 1200,
        }


def stack_loss(demand, dt_s, **config):
    """Exact batch counterpart of advance, using O(samples + stacks) memory."""
    demand = np.asarray(demand, dtype=float)
    if (demand.ndim != 1 or not len(demand) or not np.isfinite(demand).all()
            or (demand < 0).any()):
        raise ValueError("Demand must be a nonempty finite nonnegative history")
    model = StackCargo(dt_s, **config)
    for i, capacity in enumerate(model.capacities_g):
        edges = np.diff(np.r_[False, demand >= capacity, False].astype(np.int8))
        starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
        model.failed[i] = (ends - starts).max(initial=0) >= model.required_steps
    return model.summary()
