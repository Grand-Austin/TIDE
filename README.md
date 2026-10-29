# TIDE

*Observe sea conditions, command a vessel, and learn from the consequences.*

TIDE is a maritime training-game prototype. Players command a vessel in historical marine conditions through repeated heading and speed decisions. The target architecture uses a Director Agent to orchestrate local simulation tools and game rules. A Copilot translates natural-language input into actions.

The Python simulation toolkit implements continuous S175 six-degree-of-freedom motion, environmental sampling and exposure. See [SIMULATION.md](docs/SIMULATION.md) for setup, Python calls and modelling limits. Game rules and Agents remain to be implemented. The scenario pipeline builds and validates a curated historical escape exercise; see [DATA_PIPELINE.md](docs/DATA_PIPELINE.md). Earlier route-selection analysis and export tools remain in `pipeline/` for reference and reuse; existing browser prototypes remain available.

> TIDE is a training prototype, not a navigation or safety decision tool.

## Repository layout

```text
pipeline/             Current data collection, preparation and scenario construction
game/                 Python runtime core
  simulation/         Environment sampling, vessel dynamics, exposure, and events
  rules/              Mission objectives, time limits, and game cargo loss
  director/           Agent-orchestrated game loop and local execution tools
  copilot/            Natural-language intent to heading and speed actions
scenarios/            Curated scenario definitions, briefings, and data references
data/
  copernicus/         Existing committed environmental subsets
  vessels/            Vessel metadata, model references, and source documentation
  raw/                Additional downloads and external model files (ignored)
  processed/          Generated environmental and vessel-model products (ignored)
  runs/               Local trajectories, event logs, and trial results (ignored)
web/
  game/               Reserved for the new frontend demo
  prototype/          Existing route-selection prototype
  map-spike/          Existing map experiment
  public/             Existing case and map exports
tests/                Tests for pipeline and game capabilities
docs/                 Current product and architecture; legacy methods and schema
archive/              Historical discussion and reference material
PLAN.md               Implementation checklist
```

### Ownership and implementation order

- **Offline preparation:** `pipeline/` reads source data and produces scenario inputs. Scenario Builder assistance belongs here.
- **Runtime capabilities:** `game/simulation/` and `game/rules/` contain reusable local calculations. Both the pipeline and Director can call them; runtime code must not depend on the offline pipeline.
- **Game flow:** `game/director/` receives actions and invokes tools. Local validation, state updates, and pause handling do not require a separate Session Controller service.
- **Player input:** `game/copilot/` produces the same heading and speed actions as direct controls. `web/game/` presents the game; it does not duplicate simulation logic.
- **Verification:** `tests/` follows the implementation boundaries. Tests cover current capabilities and the retained earlier route-selection pipeline.

Follow [PLAN.md](PLAN.md): build and verify the toolkit, prepare one manual scenario, add rules, connect the Director, automate scenario construction, then build the frontend and Copilot. Shared action and state definitions can live directly in `game/` when implemented. No API framework or frontend framework is selected by this template.

## Setup

```bash
uv sync --locked
uv run python -m pipeline.prepare_vessel
uv run python -m game.simulation --seconds 60
uv run python -m pytest
```

Copernicus Marine data needs a free account. Log in once with `copernicusmarine login`; credentials stay on your machine and must never be committed.

## Copernicus Marine products

Committed subsets for the Mallorca–Menorca channel, 1 July to 30 September 2026. Details, variables and caveats are in [`data/copernicus/README.md`](data/copernicus/README.md).

| Layer | Product | Dataset |
|---|---|---|
| Waves | `MEDSEA_MULTIYEAR_WAV_006_012` | `cmems_mod_med_wav_my_4.2km_PT1H-i_202511` |
| Currents | `MEDSEA_MULTIYEAR_PHY_006_004` | `cmems_mod_med_phy-cur_my_4.2km_PT1H-m_202511` |
| Sea temperature | `MEDSEA_MULTIYEAR_PHY_006_004` | `cmems_mod_med_phy-temp_my_4.2km_P1D-m_202511` |
| Wind | `WIND_GLO_PHY_L4_NRT_012_004` | `cmems_obs-wind_glo_phy_nrt_l4_0.125deg_PT1H_202207` |

## Team

Aoxue Li, Jiahe Su, Qian Sun, Weixuan Ding, Yan Jiang

## License

MIT
