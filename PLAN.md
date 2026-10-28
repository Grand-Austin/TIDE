# TIDE Prototype Plan

Build the core capabilities first. Use scripts or a command-line interface until the game works, then add a frontend demo. Prototype has one vessel, one loading condition, and one historical scenario.

## Foundations

- [x] Define the product direction in [Product.md](docs/Product.md).
- [x] Define module responsibilities in [ARCHITECTURE.md](docs/ARCHITECTURE.md).
- [x] Establish the workspace template and module ownership in [README.md](README.md).


## 1. Foudational Simulation Toolkit

- [ ] Construct environmental simulation capability using Copercius data and other data sources.
- [ ] Implement vessel movements: heading and speed commands, continuous movement, and environmental sampling.
- [ ] Integrate vessel response and accumulated exposure through a shared advance function, according to the sea environmental sampling

## 2. Scenario and Data Build

- [ ] According to the data, select operating area, and historical time window.(Harsh, Challenging Weather Condition), Boudaries can be examined from this start
- [ ] Prepare environmental inputs and consistent vessel parameters
- [ ] Assemble one scenario package manually with an initial state and mission settings.


## 3. Game Rules and Events

- [ ] Implement the escape objective, time budget, and explicit game cargo-loss rules.
- [ ] Return updated state, metrics, events, and stopping reasons from each advance.
- [ ] Complete a scripted mission with repeated decisions and interpretable consequences, without a frontend or Agent.

## 4. Director Game Engine

- [ ] Connect the Director to scenario loading, validation, simulation, and game-rule tools.
- [ ] Run the action → simulation → event → next-action loop and record the results.
- [ ] Complete the mission through the Director using command-line input. Keep local execution functions minimal.

## 5. Scenario Construction Automation

- [ ] Turn the working manual preparation process into a local pipeline.
- [ ] Add Builder Agent assistance for candidate review, mission settings, and briefing material.
- [ ] Generate and validate a second playable scenario through the pipeline.

## 6. Frontend Demo and Copilot

- [ ] Display the map, vessel state, mission metrics, and checkpoints; connect direct controls and pause/resume.
- [ ] Connect Copilot text/voice input to the same heading and speed action format.
- [ ] Complete an end-to-end playthrough and show its recorded results.
