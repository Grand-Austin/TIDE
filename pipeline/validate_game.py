"""Verify complete game missions against frozen rules, independently of an Agent."""

import argparse
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from game.scenario import digest, load_scenario
from game.session import Session, atomic_json
from pipeline.scenario import displacement


def trial(source, output, seed, strategy):
    scenario = load_scenario(source)
    gameplay = json.loads(
        (scenario.root / scenario.definition["gameplay"]["path"]).read_text()
    )
    with scenario.environment() as env:
        s = Session(
            scenario,
            scenario.simulation(env, seed),
            gameplay,
            Path(output) / f"{strategy}-{seed}",
            seed=seed,
        )
        d = scenario.definition
        target = d["mission"]["target_lon_lat"]
        budget = d["mission"]["budget_s"]
        next_order = 0
        while not s.outcome and s.state.exposure.elapsed_s < budget:
            t = s.state.exposure.elapsed_s
            if t >= next_order:
                _, heading = displacement(s.sim.position(s.state), target)
                speed = 1.5 if strategy == "reduced" else 3.0
                if strategy == "detour" and t < 1200:
                    heading = 90
                if strategy == "delayed" and t < 1800:
                    speed = 0
                s.set_order(heading, speed * 3600 / 1852)
                s.authorize_resume()
                next_order = t + 300
            s.advance_segment(min(5, budget - t))
            if s.pause_signal.is_set() and not s.outcome:
                if s.reason not in (
                    "weather_report",
                    "cargo_grade_increased",
                    "paused",
                ):
                    break
                s.record("script_decision", {"action": "keep_order"})
                s.authorize_resume()
        result = s.visible()
        atomic_json(s.directory / "result.json", result)
        return {
            "seed": seed,
            "strategy": strategy,
            "elapsed_s": result["elapsed_s"],
            "outcome": s.outcome,
            "reason": s.reason,
            "cargo": result["cargo"],
        }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scenario", default="scenarios/menorca-20260909/scenario.json")
    p.add_argument("--out", type=Path, default=Path("data/runs/game-verification"))
    p.add_argument("--workers", type=int, default=3)
    a = p.parse_args()
    results = []
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        pending = [
            pool.submit(trial, a.scenario, str(a.out), seed, strategy)
            for seed in (1, 2, 3)
            for strategy in ("direct", "reduced", "detour", "delayed")
        ]
        for job in as_completed(pending):
            r = job.result()
            results.append(r)
            print(json.dumps(r), flush=True)
            atomic_json(a.out / "progress.json", results)
    good = all(
        any(r["seed"] == seed and r["outcome"] == "escaped" for r in results)
        for seed in (1, 2, 3)
    )
    atomic_json(
        a.out / "validation.json",
        {"passed": good, "definition_sha256": digest(a.scenario), "trials": results},
    )
    if not good:
        raise SystemExit("No completed escape for at least one seed")


if __name__ == "__main__":
    main()
