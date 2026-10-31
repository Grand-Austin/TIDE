# Game Runtime

The simulation package implements the foundational Python toolkit. Other packages
reserve boundaries for later prototype stages. See [SIMULATION.md](../docs/SIMULATION.md)
for runnable examples, coordinate conventions, numerical checks and model limits.

| Package | Responsibility |
| --- | --- |
| `simulation/` | Sample prepared environmental fields, advance vessel motion and response, accumulate exposure, and return events. |
| `cargo.py` | Apply seeded stack capacities and consecutive overload to accepted motion samples; emit irreversible loss and grade events. |
| `ship.py` | Detect total loss from short roll excursions and accumulated extreme motion; Session enforces mission failure and total cargo loss. |
| `rules/` | Evaluate escape progress, time limits and scheduled report releases. |
| `director/` | Load scenarios, accept actions, invoke tools, inspect results, and request further input. Local execution functions maintain state and enforce immediate stops. |
| `copilot/` | Translate text or voice transcripts into heading and speed actions, with clarification when needed. |

Keep shared action and state definitions directly in this package when they become necessary. The simulation and rules must work from scripts before Agents or the frontend are connected. The Director calls these local functions; it does not replace their numerical calculations.

The offline pipeline can reuse this package for scenario trials. This package must not import the offline pipeline or browser code. Extract useful calculations from existing pipeline modules when implementing the new capabilities; do not move old code merely to populate the template.

Run `uv run python -m game.simulation` from the repository root after preparing
the S175 data. See [ARCHITECTURE.md](../docs/ARCHITECTURE.md) for conceptual inputs
and outputs. An Agent SDK is not required for the simulation toolkit.
