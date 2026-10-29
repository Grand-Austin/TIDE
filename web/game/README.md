# Game Frontend

`index.html` is a 3D storm scene (three.js 0.160 from jsDelivr, no build step). It replays a recorded S175 run
through one catalogued storm: Copernicus sea state at the ship, simulated roll, pitch and cargo loss, one box
per deck cargo unit. Pick a storm in the top-left list; exported storms are selectable.

```bash
uv run python -m pipeline.fetch_region biscay          # once: a year of waves, wind, currents (~0.8 GB, git-ignored)
uv run python -m pipeline.storms biscay                # data/storms/biscay.json
uv run python -m pipeline.export_scene biscay-20260123-06   # web/game/scenes/<id>.json, about 7 min each
python -m http.server -d web 8000                      # open http://localhost:8000/game/
```

Controls: drag to orbit, wheel to zoom, Space play/pause, 1–4 playback rate, C chase/free camera,
←/→ jump 10 s, click the timeline to seek.

What is data and what is not:
- Sea state, wind and current at the ship: Copernicus Marine (products listed in each scene file).
- Roll, pitch, heading and cargo loss: TIDE simulation with the game cargo model.
- The rendered sea surface is synthesised from Hs, Tp and direction; individual crests are not data. The ship's
  vertical position follows that surface.
- Orders are a fixed demonstration plan at 5 m/s, not a player's game. Below about 5 m/s the ship can barely turn in
  10–12 m seas, because rudder force grows with speed (see STATUS.md).

Live play (sending orders to `game/session.py`) is not connected yet.
