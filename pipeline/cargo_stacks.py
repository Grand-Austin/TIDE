"""Offline batch evaluation of the runtime stack cargo rule."""

import math

import numpy as np

from game.cargo import CargoModel, EFFECTIVE_HEIGHT_M, STACK_UNIT_INDICES as UNIT_INDICES

STACK_UNIT_INDICES = np.asarray(UNIT_INDICES)


def stack_loss(demand, dt_s, **config):
    """Evaluate a saved demand history with O(samples + stacks) temporary memory."""
    demand = np.asarray(demand, dtype=float)
    if (demand.ndim != 1 or not len(demand) or not np.isfinite(demand).all()
            or (demand < 0).any()):
        raise ValueError("Demand must be a nonempty finite nonnegative history")
    if not math.isfinite(dt_s) or dt_s <= 0:
        raise ValueError("Cargo duration must be finite and positive")
    seed = config.pop("cargo_seed", 101)
    model = CargoModel(config, seed=seed)
    state = model.initial_state()
    required = max(1, math.ceil(model.config["exposure_s"] / dt_s - 1e-12))
    failed = []
    for i, capacity in enumerate(state["capacities_g"]):
        edges = np.diff(np.r_[False, demand >= capacity, False].astype(np.int8))
        starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
        if (ends - starts).max(initial=0) >= required:
            failed.append(state["stack_order"][i])
    failed.sort()
    lost_units = len(failed) * 5
    lost_teu = 5 * sum(1 + stack % 2 for stack in failed)
    return {
        "failed_stacks": len(failed), "failed_stack_ids": failed,
        "lost_units": lost_units, "lost_teu": lost_teu,
        "loss_fraction_units": lost_units / 800,
        "loss_fraction_teu": lost_teu / 1200,
    }
