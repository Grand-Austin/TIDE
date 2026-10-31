import json
import math
from pathlib import Path

from agent.models import Candidate, TrialResult
from agent.solutions import build_solutions


FINGERPRINT = "fingerprint-current"


def definition(*, budget_s=3600, seed=11):
    return {
        "start": "2026-01-01T00:00:00Z",
        "seed": seed,
        "initial": {
            "lon": -10.0,
            "lat": 50.0,
            "heading_deg": 270.0,
            "speed_mps": 5.0,
        },
        "mission": {"budget_s": budget_s},
    }


def make_trial(
    name,
    *,
    orders=None,
    elapsed=900.0,
    lost=0,
    total=1200,
    heading=270.0,
    outcome="escaped",
    qualified=True,
    final_validation=True,
    metrics_valid=True,
    ship_lost=False,
    fingerprint=FINGERPRINT,
    session=None,
    view=None,
    solver="simroute",
    trial_id=None,
):
    if orders is None:
        orders = [{"t": 0.0, "heading_deg": heading, "speed_knots": 5.0}]
    if view is None:
        view = {
            "vessel": {
                "lon": -9.0,
                "lat": 50.2,
                "time_s": elapsed,
                "heading_deg": heading,
                "speed_through_water_mps": 2.5,
            }
        }
    candidate = Candidate(
        index=0,
        name=name,
        solver=solver,
        orders=tuple(dict(order) for order in orders),
    )
    return TrialResult(
        candidate=candidate,
        trial_id=trial_id or f"trial-{name}",
        outcome=outcome,
        elapsed_s=elapsed,
        lost_teu=lost,
        total_teu=total,
        ship_lost=ship_lost,
        session=session,
        view=view,
        qualified=qualified,
        reason="qualified" if qualified else "not_qualified",
        fingerprint=fingerprint,
        orders=tuple(dict(order) for order in orders),
        final_validation=final_validation,
        metrics_valid=metrics_valid,
    )


def write_events(directory: Path, samples):
    directory.mkdir(parents=True, exist_ok=True)
    lines = [
        {
            "revision": 0,
            "time_s": 0.0,
            "kind": "session_started",
            "data": {"seed": 11},
        }
    ]
    for index, (time_s, lon, lat, heading) in enumerate(samples, start=1):
        lines.append(
            {
                "revision": index,
                "time_s": time_s,
                "kind": "advance",
                "data": {
                    "advanced_s": 60.0,
                    "stopping_reason": "duration_reached",
                    "vessel": {
                        "time_s": time_s,
                        "lon": lon,
                        "lat": lat,
                        "heading_deg": heading,
                        "speed_through_water_mps": 2.5,
                        "roll_deg": 1.0,
                    },
                    "cargo": {"lost_teu": 0, "total_teu": 1200},
                    "ship": {"lost": False},
                },
            }
        )
    (directory / "events.jsonl").write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n"
    )


def build(records, *, defn=None, min_solutions=3, project_root=None, reason=None):
    return build_solutions(
        records,
        definition=defn or definition(),
        evidence={"fingerprint": FINGERPRINT},
        min_solutions=min_solutions,
        project_root=project_root or Path("/nonexistent-project-root"),
        reason=reason,
    )


def test_ready_package_has_factual_schema_and_sorted_routes():
    defn = definition()
    result = build(
        [
            make_trial("Bravo", elapsed=1200.0, heading=100.0),
            make_trial("Alpha", elapsed=1200.0, heading=200.0),
            make_trial("Charlie", elapsed=900.0, lost=2, heading=300.0),
        ],
        defn=defn,
    )

    assert result["version"] == 1
    assert result["kind"] == "solution_routes"
    assert result["status"] == "ready"
    assert result["source_fingerprint"] == FINGERPRINT
    assert result["budget_s"] == 3600
    assert result["required_minimum"] == 3
    assert result["solution_count"] == 3
    assert result["reason"] is None
    assert result["initial_state"] == {
        "lon": -10.0,
        "lat": 50.0,
        "heading_deg": 270.0,
        "speed_mps": 5.0,
        "start": "2026-01-01T00:00:00Z",
        "seed": 11,
    }
    assert [item["name"] for item in result["solutions"]] == [
        "Charlie",
        "Alpha",
        "Bravo",
    ]
    assert result["solutions"][0]["summary"].startswith("Charlie by simroute escapes")
    assert result["solutions"][0]["orders"]
    assert result["solutions"][0]["track_status"] == "endpoints_only"
    assert len(result["solutions"][0]["track"]) == 2
    assert "levels" not in result
    assert "hint" not in json.dumps(result).lower()
    assert all(item["id"].startswith("route-") for item in result["solutions"])


def test_all_six_routes_are_retained_without_a_storage_cap():
    records = [
        make_trial(f"Route {index}", elapsed=600.0 + index * 100, heading=index * 20)
        for index in range(6)
    ]
    result = build(records, min_solutions=3)
    assert result["status"] == "ready"
    assert result["solution_count"] == 6
    assert [item["elapsed_s"] for item in result["solutions"]] == [
        600.0,
        700.0,
        800.0,
        900.0,
        1000.0,
        1100.0,
    ]


def test_identical_applied_orders_are_rejected_in_favour_of_the_best():
    records = [
        make_trial("Bravo copy", elapsed=800.0, heading=100.0),
        make_trial("Alpha copy", elapsed=600.0, heading=100.0),
    ]
    result = build(records, min_solutions=1)
    assert result["solution_count"] == 1
    assert result["solutions"][0]["name"] == "Alpha copy"
    assert result["solutions"][0]["orders"] == [
        {"t": 0.0, "heading_deg": 100.0, "speed_knots": 5.0}
    ]


def test_distinct_routes_may_share_a_name_but_keep_distinct_ids():
    records = [
        make_trial("Repeated name", heading=100.0),
        make_trial("Repeated name", heading=160.0),
    ]
    result = build(records, min_solutions=2)
    assert result["solution_count"] == 2
    assert [item["name"] for item in result["solutions"]] == [
        "Repeated name",
        "Repeated name",
    ]
    assert result["solutions"][0]["id"] != result["solutions"][1]["id"]


def test_orders_after_escape_are_trimmed_before_deduplication():
    orders = [
        {"t": 0.0, "heading_deg": 100.0, "speed_knots": 5.0},
        {"t": 100.0, "heading_deg": 120.0, "speed_knots": 4.0},
        {"t": 250.0, "heading_deg": 140.0, "speed_knots": 3.0},
        {"t": 500.0, "heading_deg": 160.0, "speed_knots": 2.0},
    ]
    records = [
        make_trial("Before trim", orders=orders, elapsed=200.0),
        make_trial("After trim", orders=orders, elapsed=200.0),
    ]
    result = build(records, min_solutions=1)
    assert result["solution_count"] == 1
    assert result["solutions"][0]["orders"] == [
        {"t": 0.0, "heading_deg": 100.0, "speed_knots": 5.0},
        {"t": 100.0, "heading_deg": 120.0, "speed_knots": 4.0},
    ]


def test_bad_stale_and_unverified_records_are_rejected():
    records = [
        make_trial("Valid", elapsed=900.0),
        make_trial("Not qualified", qualified=False),
        make_trial("Not final", final_validation=False),
        make_trial("Bad metrics", metrics_valid=False),
        make_trial("Stale", fingerprint="fingerprint-old"),
        make_trial("Timeout", outcome="timeout"),
        make_trial("Ship lost", ship_lost=True),
        make_trial("Too much loss", lost=13, total=1200),
        make_trial("Too slow", elapsed=5000.0),
        make_trial("Not finite", elapsed=math.inf),
        make_trial("Zero total", lost=0, total=0),
    ]
    result = build(records)
    assert result["status"] == "insufficient"
    assert result["solution_count"] == 1
    assert [item["name"] for item in result["solutions"]] == ["Valid"]
    assert result["reason"] == "insufficient_qualified_solutions"


def test_relative_session_log_extracts_actual_samples_and_boundaries(tmp_path):
    session = "sessions/route-a"
    samples = [
        (time_s, -10.0 + time_s / 1000.0, 50.0 + time_s / 5000.0, 90.0)
        for time_s in range(60, 901, 60)
    ]
    write_events(tmp_path / session, samples)
    orders = [
        {"t": 0.0, "heading_deg": 90.0, "speed_knots": 5.0},
        {"t": 120.0, "heading_deg": 100.0, "speed_knots": 5.0},
        {"t": 600.0, "heading_deg": 110.0, "speed_knots": 4.0},
    ]
    record = make_trial(
        "Sampled",
        orders=orders,
        elapsed=900.0,
        session=session,
        view={
            "vessel": {
                "lon": -8.5,
                "lat": 51.0,
                "time_s": 900.0,
                "heading_deg": 110.0,
                "speed_through_water_mps": 2.0,
            }
        },
    )

    result = build([record], min_solutions=1, project_root=tmp_path)
    solution = result["solutions"][0]
    track = solution["track"]
    assert result["status"] == "ready"
    assert solution["track_status"] == "sampled"
    assert track[0] == {
        "t": 0.0,
        "lon": -10.0,
        "lat": 50.0,
        "heading_deg": 270.0,
        "speed_knots": 9.719,
    }
    assert track[-1]["t"] == 900.0
    assert track[-1]["lon"] == -8.5
    assert track[-1]["lat"] == 51.0
    times = [point["t"] for point in track]
    assert 120.0 in times
    assert 600.0 in times
    assert max(after - before for before, after in zip(times, times[1:])) <= 300.0

    actual_positions = {
        (round(lon, 6), round(lat, 6)) for _, lon, lat, _ in samples
    }
    actual_positions.add((-8.5, 51.0))
    for point in track[1:]:
        assert (point["lon"], point["lat"]) in actual_positions


def test_track_density_is_capped_but_keeps_the_endpoints(tmp_path):
    session = "sessions/long-run"
    samples = [
        (time_s, -10.0 + time_s / 10000.0, 50.0, 90.0)
        for time_s in range(60, 60001, 60)
    ]
    write_events(tmp_path / session, samples)
    record = make_trial(
        "Long run",
        elapsed=60000.0,
        session=session,
        view={
            "vessel": {
                "lon": -4.0,
                "lat": 51.0,
                "time_s": 60000.0,
                "heading_deg": 90.0,
                "speed_through_water_mps": 2.0,
            }
        },
    )
    result = build_solutions(
        [record],
        definition=definition(budget_s=70000),
        evidence={"fingerprint": FINGERPRINT},
        min_solutions=1,
        project_root=tmp_path,
    )
    track = result["solutions"][0]["track"]
    assert result["solutions"][0]["track_status"] == "sampled"
    assert len(track) <= 120
    assert track[0]["t"] == 0.0
    assert track[-1]["t"] == 60000.0
    assert track[-1]["lon"] == -4.0


def test_missing_log_uses_only_actual_endpoints():
    record = make_trial(
        "No log",
        elapsed=700.0,
        view={
            "vessel": {
                "lon": -9.1,
                "lat": 50.3,
                "time_s": 700.0,
                "heading_deg": 80.0,
                "speed_through_water_mps": 2.0,
            }
        },
    )
    result = build([record], min_solutions=1)
    solution = result["solutions"][0]
    assert solution["track_status"] == "endpoints_only"
    assert [point["t"] for point in solution["track"]] == [0.0, 700.0]
    assert solution["track"][0]["lon"] == -10.0
    assert solution["track"][-1]["lon"] == -9.1


def test_log_without_intermediate_samples_is_endpoints_only(tmp_path):
    session = "sessions/final-only"
    write_events(tmp_path / session, [(700.0, -9.1, 50.3, 80.0)])
    record = make_trial(
        "Final only",
        elapsed=700.0,
        session=session,
        view={
            "vessel": {
                "lon": -9.1,
                "lat": 50.3,
                "time_s": 700.0,
                "heading_deg": 80.0,
                "speed_through_water_mps": 2.0,
            }
        },
    )
    result = build([record], min_solutions=1, project_root=tmp_path)
    solution = result["solutions"][0]
    assert solution["track_status"] == "endpoints_only"
    assert [point["t"] for point in solution["track"]] == [0.0, 700.0]


def test_missing_optional_vessel_fields_are_not_invented():
    record = make_trial(
        "Sparse endpoint",
        elapsed=500.0,
        view={"vessel": {"lon": -9.3, "lat": 50.4}},
    )
    result = build([record], min_solutions=1)
    final = result["solutions"][0]["track"][-1]
    assert final == {"t": 500.0, "lon": -9.3, "lat": 50.4}
    assert "heading_deg" not in final
    assert "speed_knots" not in final


def test_pending_reason_preserves_an_empty_route_list():
    result = build(
        [],
        reason="final_revalidation_failed: simulated failure",
    )
    assert result["status"] == "pending"
    assert result["solution_count"] == 0
    assert result["solutions"] == []
    assert result["reason"] == "final_revalidation_failed: simulated failure"
    assert "No verified escape route is available." in result["summary"]
    assert result["limitations"]
    assert "levels" not in result
