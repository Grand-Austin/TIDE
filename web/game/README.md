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

`node --test game.test.js` (or `bun test`) checks the wave spectrum, height queries, replay interpolation, sun
position, deck stowage, the ocean track and the 200 nm port rule. CI does not run it yet.

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
| `geo.js` | spherical helpers: distances in nm, great-circle tracks, 200 nm rings, bounds |
| `ocean-route.js` | the map's one ocean track, the Le Havre–Halifax sea lane (Eurostat SeaRoute), and its end labels |
| `port-guide.js` | which global container ports the map draws: the nearest six within 200 nm, plus the range ring |
| `deepwater-ports.js` | the global port list behind that layer: name, country, longitude, latitude |
| `classic.html` | Earlier single-file storm page; still hosts live play (`?live`) until it moves into `main.js` |

## Map guidance: the ocean track and the ports

Two layers give the map a wider reference than the storm area.

- **Ocean track.** The sea lane from Le Havre (France) to Halifax (Canada), about 2592 nm: down the English Channel
  south of the Lizard and the Scilly Isles, then across the Atlantic. It is the Eurostat SeaRoute (MARNET) network,
  computed once with the searoute package and stored in `ocean-route.js`; the game does not depend on searoute. It
  passes about 1 nm north of the 2026-01-08 scenario start. This is a reference crossing for the North Atlantic, not a
  scenario's own route. A Portugal–Halifax crossing was the other candidate, but its great circle passes
  about 380 nm south of the Biscay operating area.
- **Ports.** The ship's 200 nm range is the rule. **Ports** shows or hides the ring and the markers. The map draws
  at most the six nearest ports from the global list. A click gives the name, country, straight-line distance and
  bearing.

The list in `deepwater-ports.js` holds 223 container ports worldwide. Coordinates come from the NGA World Port
Index (Pub. 150, CSV downloaded 2026-10-30) where that source has the port; the rest are placed by hand. A port
stays on the list when it has a container terminal that ocean-going ships call at. Around the Bay of Biscay the
list keeps smaller regional ports on purpose, because those are the realistic ports to put in at. Depth is not a
filter: the World Port Index records it unevenly. The list and the 200 nm range are game guidance. They are not a
survey, not a route and not for navigation.

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
