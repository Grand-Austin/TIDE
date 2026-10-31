import json

import pytest

from game.live import Game

SCENARIO = "sole-20261022-21"  # the only package that carries solutions.json


class FakeCopilot:
    def __init__(self):
        self.calls = []

    def advise(self, mode, context, solutions):
        self.calls.append({"mode": mode, "context": context, "solutions": solutions})
        return {"status": "ready", "mode": mode, "source": "agent", "text": "Fake insight.",
                "route": None, "reason": None, "at_s": context["time"]["elapsed_s"]}


class FakeTutor:
    def __init__(self, result):
        self.result = result
        self.seen = []

    def evaluate(self, evidence):
        self.seen.append(evidence)
        return self.result


@pytest.fixture
def game():
    created = []

    def make(**kwargs):
        instance = Game(SCENARIO, **kwargs)
        created.append(instance)
        return instance

    yield make
    for instance in created:
        instance.stopped = True


def finish(game):
    with game.lock:
        game.stopped = True
        game.session.outcome = "escaped"
        game.session.reason = "escaped"
        game._finish()


def test_without_agent_settings_the_copilot_is_absent(game):
    instance = game()

    assert instance.briefing()["copilot"] == {"l1": False, "l2": False}
    assert instance.solutions is None and instance.solutions_reason == "agent_disabled"
    advice = instance.copilot_advice("l1")
    assert advice == {"status": "unavailable", "mode": "l1", "source": "local",
                      "text": "Copilot is not enabled on this server.", "route": None,
                      "reason": "agent_disabled", "at_s": None}
    with pytest.raises(ValueError):
        instance.copilot_advice("l3")


def test_a_copilot_receives_the_visible_state_reports_and_the_package(game):
    fake = FakeCopilot()
    instance = game(copilot=fake)

    assert instance.briefing()["copilot"] == {"l1": True, "l2": True}
    result = instance.copilot_advice("l2")

    assert result["text"] == "Fake insight."
    assert result["mode"] == "l2"
    call = fake.calls[0]
    assert isinstance(call["context"]["reports"], list)
    assert call["solutions"] is instance.solutions
    assert instance.solutions is not None and len(instance.solutions["solutions"]) == 8
    assert isinstance(call["context"]["time"]["elapsed_s"], float)
    assert "sector" in call["context"] and "encounter" in call["context"]


def test_the_copilot_stops_advising_after_the_result(game):
    instance = game(copilot=FakeCopilot())
    with instance.lock:
        instance.score = {"outcome": "escaped"}

    assert instance.copilot_advice("l1")["reason"] == "mission_ended"


def test_the_local_tutor_debriefs_the_finished_mission(game):
    instance = game()
    finish(instance)

    tutor = instance.state(-1.0)["tutor"]
    assert tutor["status"] == "ready"
    assert tutor["source"] == "fallback"
    assert len(tutor["sentences"]) == 2
    evidence = json.dumps(instance.tutor_evidence)
    assert '"regions"' not in evidence and "solutions" not in evidence


def test_a_ready_tutor_result_replaces_the_local_one(game):
    instance = game()
    finish(instance)
    ready = {"status": "ready", "source": "agent", "decision": {"fact": "order-1", "text": "One."},
             "improvement": {"fact": "max_hs", "text": "Two."}, "sentences": ["One.", "Two."], "reason": None}
    fake = FakeTutor(ready)
    instance.tutor_agent = fake

    instance._evaluate_tutor(instance.tutor_evidence)

    assert instance.state(-1.0)["tutor"] == ready
    assert fake.seen == [instance.tutor_evidence]


def test_a_failing_tutor_falls_back_without_touching_the_score(game):
    instance = game()

    class Broken:
        def evaluate(self, evidence):
            raise RuntimeError("model exploded")

    finish(instance)
    score = instance.score
    instance.tutor_agent = Broken()
    instance._evaluate_tutor(instance.tutor_evidence)

    tutor = instance.state(-1.0)["tutor"]
    assert tutor["source"] == "fallback"
    assert tutor["status"] == "ready"
    assert instance.score == score


def test_the_order_log_keeps_one_entry_per_change(game):
    instance = game()
    instance.order(90.0, 8.0)
    instance.order(90.0, 8.0)

    assert [entry["heading_deg"] for entry in instance.orders] == [279.9, 90.0]
    assert instance.orders[0]["speed_knots"] == pytest.approx(9.7192, abs=1e-4)
