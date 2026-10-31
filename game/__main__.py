"""Text-only TIDE prototype. Run python -m game --help."""

import argparse
import json
import sys
import uuid
from pathlib import Path

from game.scenario import load_scenario
from game.session import Session, atomic_json


def show(session):
    print(json.dumps(session.visible(), ensure_ascii=False, indent=2), flush=True)


def scripted(session, actions):
    for action in actions:
        session.set_order(action["heading_deg"], action["speed_knots"])
        session.authorize_resume()
        until = min(action["until_s"], session.rules.budget_s)
        while session.state.exposure.elapsed_s < until and not session.outcome:
            remaining = until - session.state.exposure.elapsed_s
            session.advance_segment(min(1, remaining))
            if session.pause_signal.is_set() and not session.outcome:
                session.record("script_decision", {"action": "keep_current_order"})
                session.authorize_resume()
        session.pause()
        if session.outcome:
            break
    return session.visible()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        type=Path,
        default=Path("scenarios/sole-20261022-21/scenario.json"),
    )
    parser.add_argument("--session", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--seed", type=int)
    parser.add_argument(
        "--script", type=Path, help="Deterministic stage-3 runner; no model calls"
    )
    parser.add_argument("--engine", choices=("local",), default="local")
    args = parser.parse_args()
    if not args.script and not (sys.stdin.isatty() and sys.stdout.isatty()):
        parser.error(
            "Interactive play requires a terminal; use --script for automated runs"
        )
    scenario = load_scenario(args.scenario)
    source = scenario.definition.get("gameplay")
    if not source:
        parser.error(
            "Build the gameplay artifact and attach its checksum to the scenario first"
        )
    gameplay = scenario.gameplay()
    directory = args.session or Path("data/runs/sessions") / uuid.uuid4().hex
    if args.resume and args.session is None:
        parser.error("--resume requires --session")
    if args.resume and args.seed is None:
        args.seed = json.loads((directory / "state.json").read_text())["seed"]
    with scenario.environment() as environment:
        from game.navigation import guard_environment

        guarded = guard_environment(
            environment, scenario.definition["area_bbox"]
        )
        sim = scenario.simulation(guarded, args.seed)
        session = Session(
            scenario, sim, gameplay, directory, seed=args.seed, restore=args.resume
        )
        if args.script:
            result = scripted(session, json.loads(args.script.read_text()))
            atomic_json(directory / "result.json", result)
            show(session)
            return
        from game.engine import GameEngine

        engine = GameEngine(session)
        from game.console import Controls

        controls = Controls(session, engine)
        try:
            from game.terminal import run_screen

            run_screen(controls)
        except KeyboardInterrupt:
            pass
        finally:
            controls.close()
        print(f"Session saved: {directory}")


if __name__ == "__main__":
    main()
