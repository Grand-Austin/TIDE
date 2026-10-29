# Contributing to TIDE

TIDE is a maritime training game: a deck-officer cadet commands an S175 container ship through a real
storm reconstructed from Copernicus Marine data, revising heading and speed while cargo is lost to
the vessel's calculated motion. Built for the Copernicus Marine Service Hackathon 2026.

Product and architecture: [docs/Product.md](docs/Product.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Plan: [PLAN.md](PLAN.md). Current state and decisions: [STATUS.md](STATUS.md).

## Non-negotiable rules

1. Never invent environmental data, forecasts, counts or results. Game-authored forecasts are labelled
   `game_simulated_forecast`; every number shown to a player comes from data or the simulation.
2. Runtime code in `game/` does not import `pipeline/`.
3. Physics, cargo and mission rules live in `game/`. Frontends and agents call them; they do not
   reimplement them.
4. The player interface and the Copilot receive released information only: no escape-region
   boundaries, cargo vulnerability thresholds or unreleased future fields.
5. No credentials in the repository, scenarios or run logs. Keys live in `config/api_keys.local.json`
   (ignored); Copernicus logins stay in each user's `copernicusmarine login`.
6. Large downloads stay under `data/raw/` (ignored). A committed data file is small and pinned by
   SHA-256 in the scenario that uses it.
7. Anything that shows Copernicus data credits Copernicus Marine Service and names the products used.
8. Repository content is in English. Commits and PRs carry no AI co-author or "generated with" lines.

## Workflow

- One pull request per complete, working feature; reviewed by a teammate before merge.
- Run `uv run python -m pytest` before every commit. CI runs the README setup on a fresh clone.
- Record decisions in `STATUS.md` (date, decision, reason, who decided). Update it after a merge.
- When the plan or a spec is ambiguous, ask the team; list assumptions in the PR.
- Times: data and code use UTC; the event schedule is CET; the team works in Finland (CET+1).

## Commands

```bash
uv sync --locked
uv run python -m pipeline.prepare_vessel   # once per clone
uv run python -m pytest
uv run python -m game                      # play; --plain for line commands
```
