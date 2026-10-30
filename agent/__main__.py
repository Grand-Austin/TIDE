"""Command line entry point for the TIDE agents.

    python -m agent build --scenario PATH --out DIR --mode local

Local mode is deterministic and never reads model configuration or API keys.
AI mode uses the ``builder`` role from models.json and keeps local tools
authoritative.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent.builder import Builder


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m agent",
        description="Bounded TIDE Builder. It writes a review package; it never publishes.",
    )
    sub = p.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="search, trial and package one scenario")
    build.add_argument("--scenario", type=Path, required=True)
    build.add_argument(
        "--out",
        type=Path,
        required=True,
        help="run directory; the package is written below <out>/package",
    )
    build.add_argument("--mode", choices=("local", "ai"), default="local")
    build.add_argument("--models", type=Path, default=Path("models.json"))
    build.add_argument(
        "--keys", type=Path, default=Path("config/api_keys.local.json")
    )
    build.add_argument("--max-trials", type=int, default=64)
    build.add_argument("--max-candidates", type=int, default=12)
    build.add_argument("--expert-limit", type=int, default=3)
    build.add_argument("--max-revisions", type=int, default=2)
    build.add_argument("--min-solutions", type=int, default=3)
    build.add_argument(
        "--resume",
        action="store_true",
        help="resume an AI run from its SQLite checkpoint",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.mode == "local":
        print(
            "LOCAL DETERMINISTIC MODE: no model call and no API credential is used.",
            file=sys.stderr,
        )
    try:
        builder = Builder(
            args.scenario,
            args.out,
            max_trials=args.max_trials,
            max_candidates=args.max_candidates,
            expert_limit=args.expert_limit,
            max_revisions=args.max_revisions,
            min_solutions=args.min_solutions,
            models_path=args.models,
            keys_path=args.keys,
        )
        result = builder.build(args.mode, resume=args.resume)
    except Exception as error:
        print(json.dumps({"status": "error", "error": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] == "review_ready":
        print(
            "REVIEW ONLY: no approval or publication was granted.",
            file=sys.stderr,
        )
        return 0
    print(
        "PENDING: human review is required. No impossibility claim was made.",
        file=sys.stderr,
    )
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
