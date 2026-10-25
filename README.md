# SeaSense

*Solve real cases at sea. Build the judgment future mariners need.*

SeaSense is a decision-training platform for navigation students, built for the Copernicus Marine Service Hackathon 2026 (theme: advancing ocean research, knowledge, skills and innovation). Each case is a real historical day in the Mallorca–Menorca channel, mined from Copernicus Marine data. Students investigate the evidence, choose a route and departure time, then replay what the sea actually did and find the point where their decision should have changed.

> SeaSense is a training tool. It is not a navigation or safety decision tool.

## Repository layout

```
pipeline/          Python: fetch Copernicus data, simulate routes, mine cases, export case files
tests/             pytest tests for pipeline code
web/prototype/     clickable prototype with illustrative (not real) data
docs/SCHEMA.md     JSON contracts between pipeline, web and api
docs/ARCHITECTURE.md  components, data flow, AI coach design, deployment, known limits
```

Planned: `web/` app, `api/` (AI coach and play-test log), `docs/METHODS.md`.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest
```

Copernicus Marine data needs a free account. Log in once with `copernicusmarine login`; credentials stay on your machine and must never be committed.

## Copernicus Marine products

To be listed with product and dataset IDs once the data pipeline is validated.

## Team

Aoxue Li, Jiahe Su, Qian Sun, Weixuan Ding, Yan Jiang

## License

MIT
