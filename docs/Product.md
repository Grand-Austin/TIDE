# TIDE: An Agent-Orchestrated Maritime Training Game

## 1. Product Description

### 1.1 Core experience

TIDE is a scenario-based maritime training game. The learner acts as the captain of a specified vessel in a historical marine environment. The learner observes available evidence, adjusts heading and speed, monitors the consequences, and revises the plan as conditions change.

The first mission is **to leave an adverse sea-state region while controlling journey time and game cargo loss**. A continuous vessel state connects successive decisions. The player's actions determine the subsequent trajectory and exposure. The system therefore calculates consequences during play instead of selecting a prewritten branch or marking a single route answer.

The intended experience follows the scenario structure of tactical simulation games: a mission briefing, a bounded operating area, a modelled platform, changing information, meaningful decisions, and a mission outcome. TIDE adapts that structure to maritime observation and decision-making.

### 1.2 Intended learners and educational purpose

The initial audience is deck-officer cadets and nautical-science students who have completed basic navigation, marine meteorology, and introductory ship-stability instruction. 

The exercise targets the application of knowledge:

- Relate wave height, direction, period, and wave systems to the specified vessel and its operating state.
- Distinguish heading, course over ground, speed through water, and speed over ground.
- Identify which information was available at a decision time and whether it needs updating.
- Compare alternative actions and explain their consequences and limitations.
- Monitor the developing situation and revise an earlier decision when appropriate.

[IMO A.893(21)](https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/AssemblyDocuments/A.893%2821%29.pdf) establishes appraisal, planning, execution, and monitoring as connected voyage-planning activities. TIDE concentrates on execution and monitoring, with a briefing and initial plan supplying the earlier context. [IMO MSC.1/Circ.1228](https://wwwcdn.imo.org/localresources/en/OurWork/Safety/Documents/Stability/MSC.1-CIRC.1228.pdf) connects adverse-weather decisions to vessel characteristics, speed, and wave conditions.

### 1.3 Initial scope and success conditions

The initial implementation needs one vessel with a coherent performance and response model. A container ship is the preferred starting point because it fits the cargo-loss mission and has a promising public modelling reference (S175). A Ro-Ro vessel remains possible if an equally suitable model becomes available.

The first release should provide:

1. One  historical adverse-weather window.
2. One fixed vessel loading condition and a documented operating envelope.
3. Continuous movement with heading and speed commands.
4. Manual pauses and event-triggered decision checkpoints.
5. A Copilot that retrieves evidence and compares executable plans.
6. A replay and debrief that trace consequences back to decisions and evidence.

The scenario defines an escape objective, a time budget, and an interpretable cargo-loss mapping. The objective must reward progress as well as exposure reduction. Waiting indefinitely or always using maximum speed must not become a trivial winning strategy.

Precise weights, pass criteria, and the treatment of reasonable decisions with unlucky outcomes remain design questions. They should be settled through a complete worked scenario with instructors, rather than hidden inside a single aggregate score.

## 2. Data Sources and Scenario Construction

### 2.1 Three kinds of scenario content

| Content | Meaning | Who may change it? |
| --- | --- | --- |
| Historical environmental reconstruction | Versioned ocean and weather fields for the selected place and period. | Data preparation, before the scenario is published. |
| Authored mission content | Objectives, briefing, instructional prompts, and game rules. | Scenario authors and constrained Agents, within the published rules. |

The weather timeline remains fixed during a session. The player's route through that timeline does not. This distinction provides replayability.

### 2.2 Environmental inputs

| Source or input | Role in the game |   |
| --- | --- | --- |
| Copernicus wave fields | Supply significant wave height, direction, period, and available wind-sea and swell components. |  |
| Copernicus surface-current vectors | Affect position, course over ground, and passage time. |  |
| Appropriate meteorological forecasts | Supply wind and, when needed, gusts, pressure, visibility, or other mission inputs. |  |
| Official marine bulletins, such as AEMET | Provide professional forecast language, warnings, and regional context. |   |

The approximately 4 km regional ocean grid supports bounded open-water exercises. 

### 2.3 Vessel reference package

The leading candidate is the **S175 container-ship dataset in the Marine Systems Simulator (MSS)**. Its [ShipX input report](https://github.com/cybergalactic/MSS/blob/master/HYDRO/vessels_shipx/s175/input.hyd) specifies a 175 m length between perpendiculars, 25.4 m breadth, 9.5 m draft, and a design-waterline loading condition. The [dataset directory](https://github.com/cybergalactic/MSS/tree/master/HYDRO/vessels_shipx/s175) includes hydrodynamic outputs and transfer-function data. MSS provides a [six-degree-of-freedom vessel model](https://github.com/cybergalactic/MSS/blob/master/CRAFT/hydroVessel.m) and a [simulation example](https://github.com/cybergalactic/MSS/blob/master/CRAFT/SIMhydroVessel.m). This is a candidate foundation for one consistent teaching vessel.


### 2.5 Scenario-building workflow

1. **Search historical fields.** Identify candidate windows with relevant changes in wave systems, direction, period, current, or forecast information. Find available scenarios.
2. **Assemble the mission.** Select an initial state, scenario area, ...
3. **Run numerical trials.** Test representative heading and speed sequences with the same model used during play. Confirm that supported actions produce interpretable tradeoffs. Record at least one model-feasible completion.
4. **Publish a versioned package.** Create Mission Briefing, etc.

A Scenario Builder Agent assists the local scenario-building pipeline by interpreting candidate results, proposing mission settings, invoking validators, reviewing package completeness, and preparing briefings and supporting material. Local code performs data collection, historical scans, numerical trials, and package generation.

## 3. Underlying Physics and Simulation Toolkit

### 3.1 Purpose and module boundaries

The toolkit converts commands and environmental inputs into reproducible vessel consequences. It must capture the effects needed for the exercise with documented accuracy and limitations. TIDE does not need a new CFD solver.

| Module | Responsibility | Main outputs |
| --- | --- | --- |
| Environment sampler | Read fields at the vessel position and time;  | Environmental inputs with provenance and quality flags. |
| Motion and control | Apply heading and speed orders with finite turning and acceleration;  | Position, heading, velocities, and progress. |
| Vessel performance and response | Estimate supported speed loss, motions, or accelerations for the fixed vessel condition. | Vessel-specific response indicators and applicability status. |
| Hazard and exposure | Apply supported criteria and integrate exposure over elapsed simulation time. | Risk flags, accumulated exposure, and explanatory quantities. |
| Numerical integration and event detection | Advance the state and locate relevant changes between decisions. | Updated state, event records, and stopping reasons. |

Geographic validity, missing data, model applicability, and numerical validity apply across these modules. 

### 3.2 Practical modelling level

The minimum useful model needs stateful movement, plausible control response, current-driven drift, and vessel-specific consequences that depend on wave conditions, heading, speed, and exposure duration. A constant-speed point following fixed lines is insufficient for this product. Integrate the S175/MSS time-domain dynamics where the data, control model, numerical stability, and runtime cost are demonstrated to be suitable.

A six-degree-of-freedom model can represent surge, sway, heave, roll, pitch, and yaw. The inspected MSS example uses first-order wave excitation and a restoring matrix. 

Nevertheless, the simulation of ships within the scene remains relatively coarse-grained.

### 3.3 Reusable algorithm 

| Reference | Useful contribution |  |
| --- | --- | --- |
| [MSS S175 and vessel simulation](https://github.com/cybergalactic/MSS/tree/master/HYDRO/vessels_shipx/s175) | A coherent vessel-data candidate and reusable dynamics components. |  |
| [SIMROUTE](https://doi.org/10.1016/j.oceaneng.2022.111427) and its [performance code](https://github.com/ManelGrifoll/SIMROUTE/blob/master/func_simroute.py) | Examples of translating wave fields into empirical speed loss and passage-time calculations. |  |
| [VISIR-2](https://gmd.copernicus.org/articles/17/4355/2024/) | Combines environmental fields, vessel performance, vector currents, and routing objectives. |  |
| [MARIN TopTier](https://www.marin.nl/en/jips/toptier) and its public roll-risk resources | A candidate basis for studying container-ship roll-risk screening. | 


### 3.4 Cargo loss as a game metric

The consequence chain is:

**Sea conditions + vessel state + control history → supported response or risk indicators → accumulated exposure → declared game cargo loss.**

Cargo loss provides visible feedback and a mission objective. It does not represent a validated prediction of real container failures. Actual failures depend on stowage, securing, loading, structural response, and other information outside the initial model.

The mapping should satisfy four requirements:

1. Heading and speed changes affect outcomes through the underlying response or risk calculation.
2. Wave period and separate wave systems affect outcomes when the selected method supports them.
3. Loss accumulates with exposure duration, not with the number of checkpoints or LLM calls.
4. Repeating the same inputs and seed reproduces the result.

## 4. Agent-Orchestrated Game Engine

### 4.1 Control architecture

The Orchestrator (Director) Agent is the core game engine: it loads the scenario, receives commands, invokes tools, inspects results, and decides whether to continue or wait for player input. Local execution functions validate commands, maintain authoritative state, and enforce numerical stopping conditions. A separate Session Controller is unnecessary; any controller retained should be a thin execution layer without duplicating the Director's orchestration.

```mermaid
flowchart TB
    CASE[Versioned scenario package] --> DIRECTOR[Orchestrator / Director Agent: core game engine]
    PLAYER[Player interface: controls, text, voice] -->|Direct commands| DIRECTOR
    PLAYER -->|Manual pause| EXEC[Local execution functions: validation and state updates]
    PLAYER <--> COPILOT[Copilot Agent]
    COPILOT -->|Commands and trial requests| DIRECTOR
    DIRECTOR -->|Tool results| COPILOT
    DIRECTOR -->|Validate and advance, or run trial| EXEC
    EXEC --> PHYSICS[Physics toolkit and game rules]
    EXEC --> TRIAL[Isolated trial simulation]
    TRIAL -->|Trial results| DIRECTOR
    PHYSICS --> STATE[Authoritative vessel and mission state]
    STATE --> INFO[Information-release policy]
    INFO --> PLAYER
    INFO --> COPILOT
    STATE --> EVENTS[Deterministic event detection]
    EVENTS -->|State and event results| DIRECTOR
    DIRECTOR -->|Checkpoint, results, or request for input| PLAYER
    PHYSICS -. Same calculation modules .-> TRIAL
    DIRECTOR --> LOG[Decision and evidence log]
    STATE --> LOG
    LOG --> TUTOR[Tutor and debrief]
    TUTOR --> PLAYER
```

These boxes are responsibilities, not a requirement for separate services or separate language models. One backend can implement them with explicit tool and information boundaries.

### 4.2 Agent responsibilities

| Role | Meaningful actions | Limits |
| --- | --- | --- |
| Scenario Builder | Assist the local pipeline by interpreting candidates, proposing mission settings, invoking checks, and preparing scenario-package material. | Cannot manufacture historical fields or approve professional validity by itself. |
| Orchestrator / Director (Core Game Engine) | Run the game loop, receive commands, select and invoke tools, inspect results, and continue or pause for player input. | Cannot change physical results, force an accident, or execute an unapproved player manoeuvre. |
| Copilot |  interpret requests and apply instructions, assist according to the game settings          | Cannot access hidden future fields or silently take command. |
| Personalized Tutor | Examine evidence use, decisions, stated reasons, revisions, and assistance; generate specific debriefs and propose follow-up exercises. |  /|

The Director provides dynamic, extensible orchestration with the aim of reducing custom game-flow and branch logic. Physics, event detection, and state updates remain local tools. The Director calls them for each game-advance segment, rather than at every numerical integration step, to limit model-call overhead.

### 4.3 Event-driven progression

1. The Director loads the frozen scenario and presents the briefing and initial information.
2. The Director receives a command from the player or through the Copilot.
3. The Director calls local tools to validate the command against vessel capability, geographic bounds, and scenario permissions.
4. The Director invokes the simulation to advance until a relevant event, player-defined review condition, manual pause, or mission end.
5. The tools return the updated state and event results. The Director inspects them, presents available information, and request input.
6. The player can consult the Copilot, revise the command to continue.
7. The Director repeats the tool-driven loop from the current state.

Checkpoint triggers can include a new forecast issue, a supported risk-criterion crossing, a completed manoeuvre, an information gap, an approaching domain boundary, or the completion of a player-defined condition, etc. Mandatory stop conditions belong to program logic. The Director may add contextual review requests using documented triggers and tool evidence.

Trigger persistence, hysteresis, and review intervals should prevent repeated interruptions around the same threshold. A maximum advance interval can preserve observability when no event occurs. 

### 4.4 Plans and branching

The game does not require authors to enumerate every future branch. A command changes state. A conditional plan changes which command should become active when a supported condition holds. Subsequent environmental sampling follows the new trajectory.

The interpreter can translate a request into a bounded plan containing heading or waypoint targets, speed targets, duration or completion conditions, and pause conditions. Deterministic checks then establish whether the plan is executable. 

[WhatELSE, CHI 2025](https://arxiv.org/abs/2502.18641) provides a design reference for author-controlled narrative spaces and runtime generation of executable events. TIDE adapts that idea to an instructional decision space. This is a proposed transfer, not evidence that WhatELSE validates maritime physics or learning outcomes.

[LLM-Modulo](https://arxiv.org/abs/2402.01817) provides a complementary architectural reference: generated proposals interact with external model-based checks. In TIDE, an Agent can propose and revise a plan, while tools establish its supported consequences and constraints.

## 5. Game Interaction with a Copilot Agent

### 5.1 Player workspace

The main workspace combines:

- A map with the vessel, recent track, intended path, operating boundaries, and selectable environmental layers.
- Instruments showing time, heading, course, speeds, relevant sea-state components, and supported response indicators.
- A mission panel showing the time budget, and game cargo status.
- Direct heading and speed controls, a pause/resume control, and voice interaction for voice command (with copilot)

The interface should connect environmental values to the vessel's location and expected arrival times. Encounter diagrams, response plots, and exposure timelines are more useful than an undifferentiated set of raw map layers. Model outputs, forecasts, observations, and game indicators need distinct labels.

An evidence-credit budget remains an optional game experiment. It is not part of the core training requirement. Any later use must explain what resource constraint it represents and how it affects the learning objective.

### 5.2 Direct commands and natural-language commands

The player can issue precise orders through controls or ask the Copilot to construct a command from a broader intention.

The Copilot retrieves the applicable evidence and vessel constraints, proposes executable commands, and calls the Director to run trial simulations and continue game process. 

Copilots ability can be extended (configured) according to the game settings (difficulty), for example, it can support giving recommendations (so the Copilot should have basic marine knowledge)

## 6. Extensibility and Proposed Contributions

### 6.1 Model and scenario extensions

Institutions can improve physical fidelity without redesigning the whole interaction loop if modules expose clear inputs, outputs, units, state, supported events, and applicability limits. 

### 6.4 Proposed contributions and required evidence

The contribution is the integrated training loop: historical marine information becomes vessel-specific consequences, actionable choices, and traceable learning evidence. 

| Proposed contribution | Concrete system capability |   |
| --- | --- | --- |
| Data accessibility through action | Learners turn sourced fields and forecasts into vessel-specific comparisons and decisions. |  |
| Agent-assisted scenario construction | A Builder assists local search, validation, and packaging code by interpreting results and preparing mission settings, briefings, and supporting material. |  |
| Agent-orchestrated game engine | A Director runs the game through reusable local tools, reducing custom flow and branch logic while supporting new scenarios and tool capabilities. |  |
| Open interaction with verifiable consequences | Text or voice plans become supported command sequences  |  |
| Reusable institutional infrastructure | Versioned scenarios, model modules, and teaching strategies can be reviewed and adapted. |  
