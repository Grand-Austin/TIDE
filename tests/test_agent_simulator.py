from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import threading
import time

import numpy as np
import pytest
from test_session import PlainRules, make_session
from test_session import vessel as vessel  # noqa: PLC0414 - Shared pytest fixture.

import agent.simulator as simulator
from agent.simulator import SimulatorService

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "scenarios/menorca-20260909/scenario.json"


def factory_for(vessel, rules=None):
    def build(_scenario, directory, restore):
        session = make_session(vessel, directory, restore=restore)
        if rules is not None:
            session.rules = rules
        return session

    return build


def service_for(tmp_path, vessel, rules=None):
    return SimulatorService(
        tmp_path / "workspace", session_factory=factory_for(vessel, rules)
    )


class StopRules(PlainRules):
    def __init__(self, at_s, reason):
        self.at_s, self.reason = at_s, reason

    def evaluate(self, sample, consumed):
        if sample.time_s >= self.at_s:
            return self.reason, [
                {"time_s": sample.time_s, "kind": self.reason, "detail": "planned stop"}
            ]
        return None, []


class ContinueThenStop(PlainRules):
    def __init__(self):
        self.cargo_pause_sent = False

    def evaluate(self, sample, consumed):
        if not self.cargo_pause_sent and sample.time_s >= 1:
            self.cargo_pause_sent = True
            return "cargo_grade_increased", [
                {
                    "time_s": sample.time_s,
                    "kind": "cargo_grade_increased",
                    "detail": "test cargo pause",
                }
            ]
        if sample.time_s >= 3:
            return "escaped", [
                {"time_s": sample.time_s, "kind": "escaped", "detail": "planned stop"}
            ]
        return None, []


def test_step_advances_the_authoritative_session(vessel, tmp_path):
    service = service_for(tmp_path, vessel)
    created = service.create(str(SCENARIO))
    trial_id = created["trial_id"]
    initial = created["view"]
    result = service.step(
        trial_id,
        heading_deg=20,
        speed_knots=2,
        duration_s=1.0,
        expected_revision=initial["revision"],
        operation_id="advance-1",
    )

    assert result["trial_id"] == trial_id
    assert result["view"]["elapsed_s"] == pytest.approx(1.0)
    assert result["view"]["revision"] > initial["revision"]
    assert result["view"]["paused"]
    assert result["view"]["vessel"]["lat"] != initial["vessel"]["lat"]
    trial = service._trials[trial_id]
    assert trial.session.state.tick == 20
    assert service.read(trial_id)["view"]["elapsed_s"] == pytest.approx(1.0)
    service.close()


def test_open_session_releases_only_after_the_handle(vessel, tmp_path, monkeypatch):
    class Environment:
        closed = False

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.closed = True

    class FakeSession:
        fail = False

        def __init__(self, *args, **kwargs):
            if self.fail:
                raise RuntimeError("session failed")

    environment = Environment()

    @contextmanager
    def environment_context():
        try:
            yield environment
        finally:
            environment.closed = True

    scenario = SimpleNamespace(
        definition={"area_bbox": [-1.0, 1.0, -1.0, 1.0]},
        environment=lambda: environment_context(),
        simulation=lambda env: None,
        gameplay=lambda: {},
    )
    fake_guard = SimpleNamespace(
        allowed=lambda lon, lat: True,
        metadata=lambda: {"clearance_nm": 5.0, "fixture": True},
    )
    monkeypatch.setattr(simulator, "load_scenario", lambda *args, **kwargs: scenario)
    monkeypatch.setattr(simulator, "Session", FakeSession)

    handle = simulator.open_session(
        SCENARIO, tmp_path / "kept", coast_guard=fake_guard
    )
    assert not environment.closed
    handle.release()
    assert environment.closed

    failing = Environment()

    @contextmanager
    def failing_context():
        try:
            yield failing
        finally:
            failing.closed = True

    scenario.environment = lambda: failing_context()
    FakeSession.fail = True
    with pytest.raises(RuntimeError, match="session failed"):
        simulator.open_session(
            SCENARIO, tmp_path / "failed", coast_guard=fake_guard
        )
    assert failing.closed


def test_step_chunks_stay_readable_and_human_order_wins(vessel, tmp_path, monkeypatch):
    service = service_for(tmp_path, vessel)
    trial_id = service.create(str(SCENARIO))["trial_id"]
    session = service._trials[trial_id].session
    original_advance = session.sim.advance

    def slow_advance(*args, **kwargs):
        time.sleep(0.03)
        return original_advance(*args, **kwargs)

    monkeypatch.setattr(session.sim, "advance", slow_advance)
    outcome = {}

    def run():
        try:
            outcome["value"] = service.step(
                trial_id,
                20,
                2,
                20,
                expected_revision=session.revision,
                operation_id="chunked",
            )
        except BaseException as error:  # noqa: BLE001 - Re-raised through the result.
            outcome["error"] = error

    worker = threading.Thread(target=run)
    worker.start()
    time.sleep(0.15)
    started = time.monotonic()
    live = service.read(trial_id)
    read_s = time.monotonic() - started
    assert read_s < 1.0
    assert live["view"]["elapsed_s"] < 20
    assert worker.is_alive()

    session.set_order(123, 1.0)
    worker.join(5)
    assert not worker.is_alive()
    assert "error" not in outcome
    assert outcome["value"]["view"]["elapsed_s"] < 20
    final = service.read(trial_id)["view"]
    assert final["target_heading_deg"] == pytest.approx(123)
    assert final["target_speed_knots"] == pytest.approx(1.0)
    assert final["paused"]
    service.close()


def test_step_rejects_stale_revision_and_invalid_input(vessel, tmp_path):
    service = service_for(tmp_path, vessel)
    trial_id = service.create(str(SCENARIO))["trial_id"]
    initial = service.read(trial_id)["view"]
    first = service.step(
        trial_id,
        10,
        2,
        0.5,
        expected_revision=initial["revision"],
        operation_id="first",
    )
    current = first["view"]

    with pytest.raises(ValueError, match="Stale revision"):
        service.step(
            trial_id,
            20,
            2,
            0.5,
            expected_revision=initial["revision"],
            operation_id="stale",
        )

    invalid = [
        dict(heading_deg=float("nan"), speed_knots=2, duration_s=1),
        dict(heading_deg=360, speed_knots=2, duration_s=1),
        dict(heading_deg=10, speed_knots=-1, duration_s=1),
        dict(heading_deg=10, speed_knots=2, duration_s=float("inf")),
        dict(heading_deg=10, speed_knots=2, duration_s=901),
        dict(heading_deg=10, speed_knots=2, duration_s=0.075),
    ]
    for index, arguments in enumerate(invalid):
        with pytest.raises(ValueError):
            service.step(
                trial_id,
                **arguments,
                expected_revision=current["revision"],
                operation_id=f"invalid-{index}",
            )
    with pytest.raises(ValueError, match="operation_id"):
        service.step(
            trial_id,
            10,
            2,
            1,
            expected_revision=current["revision"],
            operation_id="",
        )
    with pytest.raises(ValueError, match="expected_revision"):
        service.step(
            trial_id,
            10,
            2,
            1,
            expected_revision=True,
            operation_id="bad-revision",
        )

    unchanged = service.read(trial_id)["view"]
    assert unchanged["revision"] == current["revision"]
    assert unchanged["elapsed_s"] == current["elapsed_s"]
    service.close()


def test_step_stops_when_the_player_pauses(vessel, tmp_path):
    service = service_for(tmp_path, vessel)
    trial_id = service.create(str(SCENARIO))["trial_id"]
    session = service._trials[trial_id].session
    outcome = {}

    def run():
        try:
            outcome["value"] = service.step(
                trial_id,
                20,
                2,
                900,
                expected_revision=session.revision,
                operation_id="long-step",
            )
        except BaseException as error:  # noqa: BLE001 - Re-raised in the test thread.
            outcome["error"] = error

    worker = threading.Thread(target=run)
    worker.start()
    time.sleep(0.2)
    session.pause(manual=True)
    worker.join(5)

    assert not worker.is_alive()
    assert "error" not in outcome
    assert outcome["value"]["view"]["elapsed_s"] < 900
    assert outcome["value"]["view"]["paused"]
    service.close()


def test_duplicate_operation_returns_same_commit_and_conflicts_reject(vessel, tmp_path):
    service = service_for(tmp_path, vessel)
    trial_id = service.create(str(SCENARIO))["trial_id"]
    initial = service.read(trial_id)["view"]
    arguments = dict(
        heading_deg=25,
        speed_knots=2.5,
        duration_s=1.0,
    )
    first = service.step(
        trial_id,
        **arguments,
        expected_revision=initial["revision"],
        operation_id="same-operation",
    )
    duplicate = service.step(
        trial_id,
        **arguments,
        expected_revision=initial["revision"],
        operation_id="same-operation",
    )
    assert duplicate == first
    assert service.read(trial_id)["view"]["elapsed_s"] == pytest.approx(1.0)

    with pytest.raises(ValueError, match="different arguments"):
        service.step(
            trial_id,
            heading_deg=30,
            speed_knots=2.5,
            duration_s=1.0,
            expected_revision=initial["revision"],
            operation_id="same-operation",
        )
    assert service.read(trial_id)["view"]["elapsed_s"] == pytest.approx(1.0)
    service.close()


def test_fork_preserves_complete_state_then_isolates(vessel, tmp_path):
    service = service_for(tmp_path, vessel)
    trial_id = service.create(str(SCENARIO))["trial_id"]
    first = service.step(
        trial_id,
        15,
        2,
        1.5,
        expected_revision=0,
        operation_id="source",
    )
    source = service._trials[trial_id].session
    fork_id = service.fork(trial_id)["trial_id"]
    copy = service._trials[fork_id].session

    assert fork_id != trial_id
    assert copy.directory != source.directory
    assert copy.seed == source.seed
    assert copy.revision == source.revision == first["view"]["revision"]
    assert copy.cargo == source.cargo
    assert copy.ship == source.ship
    assert copy.consumed == source.consumed
    assert copy.order == source.order
    assert copy.state.control == source.state.control
    assert copy.state.exposure == source.state.exposure
    np.testing.assert_array_equal(copy.state.x, source.state.x)
    np.testing.assert_array_equal(copy.state.wave.phase, source.state.wave.phase)
    np.testing.assert_array_equal(
        copy.state.wave.previous_rate, source.state.wave.previous_rate
    )

    expected = first["view"]["revision"]
    second_source = service.step(
        trial_id,
        35,
        2.5,
        1.0,
        expected_revision=expected,
        operation_id="continue",
    )
    second_copy = service.step(
        fork_id,
        35,
        2.5,
        1.0,
        expected_revision=expected,
        operation_id="continue",
    )
    assert second_source["view"] == second_copy["view"]
    np.testing.assert_array_equal(source.state.x, copy.state.x)
    np.testing.assert_array_equal(source.state.wave.phase, copy.state.wave.phase)

    copy_before = service.read(fork_id)["view"]
    service.step(
        trial_id,
        90,
        1.5,
        0.5,
        expected_revision=second_source["view"]["revision"],
        operation_id="only-source",
    )
    assert service.read(fork_id)["view"] == copy_before
    source_before = service.read(trial_id)["view"]
    service.step(
        fork_id,
        270,
        1.0,
        0.5,
        expected_revision=second_copy["view"]["revision"],
        operation_id="only-fork",
    )
    assert service.read(trial_id)["view"] == source_before
    service.close()


def test_step_chunks_preserve_mandatory_event_stops(vessel, tmp_path):
    service = service_for(tmp_path, vessel, StopRules(1.2, "escaped"))
    created = service.create(str(SCENARIO))
    result = service.step(
        created["trial_id"],
        20,
        2,
        20,
        expected_revision=created["view"]["revision"],
        operation_id="stop-on-event",
    )

    assert result["view"]["outcome"] == "escaped"
    assert result["view"]["elapsed_s"] == pytest.approx(1.2)
    assert result["view"]["paused"]
    service.close()


@pytest.mark.parametrize(
    "reason", ["escaped", "ship_lost", "timeout", "domain_boundary", "model_limit"]
)
def test_run_plan_stops_at_terminal_and_validity_stops(vessel, tmp_path, reason):
    service = service_for(tmp_path, vessel, StopRules(1.2, reason))
    result = service.run_plan(
        str(SCENARIO), [{"t": 0, "heading_deg": 20, "speed_knots": 2}]
    )

    assert result["outcome"] == reason
    assert result["elapsed_s"] == pytest.approx(1.2)
    assert result["orders"] == [{"t": 0.0, "heading_deg": 20.0, "speed_knots": 2.0}]
    assert Path(result["session"]).is_dir()
    assert result["view"]["stopping_reason"] == reason
    assert result["trial_id"] in service._trials
    service.close()


def test_run_plan_records_forecast_continuation(vessel, tmp_path):
    service = service_for(tmp_path, vessel, ContinueThenStop())
    result = service.run_plan(
        str(SCENARIO), [{"t": 0, "heading_deg": 20, "speed_knots": 2}]
    )

    assert result["outcome"] == "escaped"
    assert result["elapsed_s"] == pytest.approx(3.0)
    log = Path(result["session"]).joinpath("events.jsonl").read_text()
    assert '"kind": "plan_continuation"' in log
    assert '"reason": "cargo_grade_increased"' in log
    service.close()


def test_run_plan_rejects_invalid_orders_without_starting_a_trial(vessel, tmp_path):
    service = service_for(tmp_path, vessel)
    for orders in (
        [{"t": -1, "heading_deg": 0, "speed_knots": 1}],
        [
            {"t": 2, "heading_deg": 0, "speed_knots": 1},
            {"t": 1, "heading_deg": 0, "speed_knots": 1},
        ],
        [{"t": 0, "heading_deg": float("nan"), "speed_knots": 1}],
        [{"t": 0, "heading_deg": 0, "speed_knots": 1, "extra": 1}],
    ):
        with pytest.raises(ValueError):
            service.run_plan(str(SCENARIO), orders)
    assert not service._trials
    service.close()


def test_scenario_reference_must_stay_inside_project(vessel, tmp_path):
    service = service_for(tmp_path, vessel)
    with pytest.raises(ValueError, match="inside"):
        service.create(tmp_path / "outside.json")
    service.close()
