"""Turn a recorded game session into a compact replay file for the 3D demo (web/game).

Reads only what the session logged after each advance: the vessel sample, the cargo summary and the
events, including cargo_lost with its unit ids. Nothing is simulated or invented here.

    uv run python -m game --script actions.json --session data/runs/my-run --seed 1
    uv run python -m pipeline.export_replay data/runs/my-run web/game/replays/my-run.json
"""

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

# replay column <- vessel sample field, decimals
COLUMNS = {
    "lon": ("lon", 6), "lat": ("lat", 6), "heading": ("heading_deg", 2), "sog": ("speed_over_ground_mps", 3),
    "roll": ("roll_deg", 3), "pitch": ("pitch_deg", 3), "heave": ("heave_m", 3),
    "hs": ("hs_m", 3), "tp": ("tp_s", 2), "wave_from": ("wave_from_deg", 1),
    "wind_e": ("wind_east_mps", 2), "wind_n": ("wind_north_mps", 2),
}
EVENT_KINDS = ("cargo_lost", "cargo_grade_increased")


def export(session_dir):
    rows = [json.loads(line) for line in (session_dir / "events.jsonl").read_text().splitlines() if line]
    result = json.loads((session_dir / "result.json").read_text())
    start = datetime.fromisoformat(result["utc"]) - timedelta(seconds=result["elapsed_s"])
    advances = [r for r in rows if r["kind"] == "advance" and r["data"].get("vessel")]
    if not advances:
        raise ValueError(f"{session_dir} has no advance records")
    replay = {
        "start_utc": start.isoformat().replace("+00:00", "Z"),
        "session": session_dir.name,
        "seed": next((r["data"].get("seed") for r in rows if r["kind"] == "session_started"), None),
        "outcome": result["outcome"],
        "t": [round(r["data"]["vessel"]["time_s"], 2) for r in advances],
        **{col: [round(r["data"]["vessel"][field], nd) for r in advances] for col, (field, nd) in COLUMNS.items()},
        "orders": [
            {"t": r["time_s"], "heading_deg": r["data"]["heading_deg"], "speed_knots": round(r["data"]["speed_mps"] * 3600 / 1852, 3)}
            for r in rows if r["kind"] == "player_order"
        ],
        "events": [],
        "cargo": [],
    }
    last = None
    for r in advances:
        for e in r["data"]["events"]:
            if e["kind"] in EVENT_KINDS:
                replay["events"].append({k: e[k] for k in ("time_s", "kind", "detail", "unit_ids") if k in e})
        summary = r["data"]["cargo"]
        if summary != last:
            replay["cargo"].append({"t": r["time_s"], **{k: summary[k] for k in ("lost_units", "lost_teu", "grade", "total_teu")}})
            last = summary
    return replay


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("session", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    replay = export(args.session)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(replay, separators=(",", ":"), allow_nan=False))
    print(f"{args.out}: {len(replay['t'])} samples, {len(replay['events'])} events, outcome {replay['outcome']}")


if __name__ == "__main__":
    main()
