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
| `ocean.js` | JONSWAP-scaled Gerstner sea (24 components), tileable detail map, hull and wake foam, crest spray shaders |
| `sky.js` | Sun position, overcast sky dome |
| `replay.js` | Sampling a replay between its 1 s records |
| `cargo-layout.js` | Deck stowage rule shared with the Blender preview |
| `cargo-fall.js` | Path of a lost unit (slide, fall, float, sink) as a pure function of time; no three.js, reusable |
| `cargo-fall-view.js` | three.js drawing of those paths and the splash spray |
| `panel.js` | Side panel: helm compass, sea, roll trace, cargo bars by grade, loss log (released numbers only) |
| `bow-spray.js` | Spray where the bow drops into the sea; a look only |
| `assets/` | `s175.glb` and `containers.glb`, exported from `assets/blender/s175.blend` |
| `replays/` | Replays written by `pipeline.export_replay` |
| `scenes/` | 34 Biscay storm scenes from `pipeline.export_scene` (`index.json` lists them) |
| `fields/` | each storm's Copernicus Hs and wind grid from `pipeline.export_fields`, drawn on the passage map in replays (not in live play: it would show the hidden escape region) |
| `weather-layer.js` | the passage map's Hs colour field and wind streamlines |
| `classic.html` | Earlier single-file storm page; still hosts live play (`?live`) until it moves into `main.js` |

## Storm scenes

```bash
uv run python -m pipeline.fetch_region biscay              # once: a year of waves, wind, currents (~0.8 GB, git-ignored)
uv run python -m pipeline.storms biscay --subsets          # data/storms/biscay.json and committed per-storm subsets
uv run python -m pipeline.export_scene biscay-20260123-06  # web/game/scenes/<id>.json; uses the subsets, about 7 min
uv run python -m pipeline.export_fields biscay              # web/game/fields/<id>.json for every storm, about 1 min
```

What is data and what is not:
- Sea state, wind and current at the ship: Copernicus Marine (products listed in each scene file).
- Cloud cover and precipitation: ERA5 (Copernicus Climate Change Service) via the Open-Meteo historical
  weather API, hourly at the scene's start point. ERA5 appears about five days after real time.
- Light: computed sun position for the scene time and place. Whitecap cover: wind speed
  (Monahan & O'Muircheartaigh 1980).
- Roll, pitch, heading and cargo loss: TIDE simulation with the game cargo model.
- The rendered sea surface is synthesised from Hs, Tp and direction; individual crests are not data.
- Scene orders are a fixed demonstration plan at 5 m/s, not a player's game.

## Live play

```bash
uv run python -m game.live        # open http://localhost:8000/game/classic.html?live
```

`game/live.py` serves `web/` and wraps `game.session.Session` for one storm scenario
(`pipeline.storm_scenario`): keyboard orders, released game forecasts, judgement checkpoints graded on
what the player could see, and a score in three parts.
