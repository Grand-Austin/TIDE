# Game Runtime

This package reserves the implementation boundaries for the new prototype. It currently contains package markers only.

| Package | Responsibility |
| --- | --- |
| `simulation/` | Sample prepared environmental fields, advance vessel motion and response, accumulate exposure, and return events. |
| `rules/` | Evaluate escape progress, time limits, and game cargo loss from calculated results. |
| `director/` | Load scenarios, accept actions, invoke tools, inspect results, and request further input. Local execution functions maintain state and enforce immediate stops. |
| `copilot/` | Translate text or voice transcripts into heading and speed actions, with clarification when needed. |

Keep shared action and state definitions directly in this package when they become necessary. The simulation and rules must work from scripts before Agents or the frontend are connected. The Director calls these local functions; it does not replace their numerical calculations.

The offline pipeline can reuse this package for scenario trials. This package must not import the offline pipeline or browser code. Extract useful calculations from existing pipeline modules when implementing the new capabilities; do not move old code merely to populate the template.

See [ARCHITECTURE.md](../docs/ARCHITECTURE.md) for conceptual inputs and outputs. No runtime entry point or Agent SDK is implemented yet.
