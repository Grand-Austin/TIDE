import json

from pipeline.export_replay import COLUMNS, export


def vessel(t, roll):
    sample = {field: 0.0 for field, _ in COLUMNS.values()}
    return {**sample, "time_s": t, "lon": 3.65, "lat": 40.05, "roll_deg": roll}


def cargo(lost):
    return {"lost_units": lost, "lost_teu": lost * 2, "grade": int(lost > 0), "total_teu": 1200, "remaining_units": 800 - lost}


def test_replay_keeps_logged_state_and_cargo_events(tmp_path):
    rows = [
        {"time_s": 0, "kind": "session_started", "data": {"seed": 3}},
        {"time_s": 0, "kind": "player_order", "data": {"heading_deg": 100, "speed_mps": 3.0}},
        {"time_s": 1, "kind": "advance", "data": {"vessel": vessel(1, 1.23456), "cargo": cargo(0), "events": [
            {"time_s": 0.5, "kind": "report_released", "detail": "forecast-0-0-1"}]}},
        {"time_s": 2, "kind": "advance", "data": {"vessel": vessel(2, -4.5), "cargo": cargo(2), "events": [
            {"time_s": 1.7, "kind": "cargo_lost", "detail": "2 units overboard", "unit_ids": ["deck-005", "deck-008"]}]}},
    ]
    (tmp_path / "events.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    (tmp_path / "result.json").write_text(json.dumps({"utc": "2026-09-09T15:00:02+00:00", "elapsed_s": 2.0, "outcome": None}))

    replay = export(tmp_path)

    assert replay["start_utc"] == "2026-09-09T15:00:00Z"
    assert replay["seed"] == 3
    assert replay["t"] == [1, 2] and replay["roll"] == [1.235, -4.5]
    assert replay["orders"] == [{"t": 0, "heading_deg": 100, "speed_knots": 5.832}]
    assert replay["events"] == [{"time_s": 1.7, "kind": "cargo_lost", "detail": "2 units overboard", "unit_ids": ["deck-005", "deck-008"]}]
    assert [c["lost_units"] for c in replay["cargo"]] == [0, 2]
