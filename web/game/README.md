# Game Frontend

3D replay of a recorded TIDE session: the S175 and its 320 deck containers on a sea built from the session's
Copernicus sea state. Physical calculations and mission rules remain in `game/`; this page only draws what the
session logged.

## Run

```bash
uv run python -m game --script actions.json --session data/runs/my-run --seed 1
uv run python -m pipeline.export_replay data/runs/my-run web/game/replays/my-run.json
uv run python -m http.server 8000 --bind 127.0.0.1 --directory web/game
```

Open http://127.0.0.1:8000/ (or `?replay=./replays/my-run.json`). `?hs=8&tp=12&from=270` freezes a test sea and
labels it DEV OVERRIDE; it is not scenario data. three.js 0.170 loads from jsDelivr, so the page needs network access.

`bun test` in this directory checks the wave spectrum, height queries, replay interpolation, sun position and deck
stowage. CI does not run it yet.

## Files

| Path | Contents |
|---|---|
| `main.js` | Scene, lighting, playback loop and HUD |
| `ocean.js` | JONSWAP-scaled Gerstner sea and its shaders |
| `sky.js` | Sun position, overcast sky dome |
| `replay.js` | Sampling a replay between its 1 s records |
| `cargo-layout.js` | Deck stowage rule shared with the Blender preview |
| `cargo-fall.js` | Path of a lost unit (slide, fall, float, sink) as a pure function of time; no three.js, reusable |
| `cargo-fall-view.js` | three.js drawing of those paths and the splash spray |
| `assets/` | `s175.glb` and `containers.glb`, exported from `assets/blender/s175.blend` |
| `replays/` | Replays written by `pipeline.export_replay` |

The existing `web/prototype/`, `web/map-spike/`, and `web/public/` remain available as reference experiments and exports.
