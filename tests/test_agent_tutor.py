import json
from copy import deepcopy

from agent.tutor import MAX_SENTENCE_CHARS, Tutor, build_facts, fallback_evaluation
from agent.transport import ModelError

EVIDENCE = {
    "scenario": {"id": "sole-20261022-21", "title": "Escape the Sole storm",
                 "start": "2026-10-22T21:00:00Z", "budget_s": 4500},
    "outcome": {"outcome": "escaped", "escaped": True, "elapsed_s": 1522.4, "remaining_s": 2977.6,
                "ship_lost": False, "lost_teu": 75, "total_teu": 1200, "grade": 2, "stars": 2, "total": 71.3},
    "ship": {"lost": False, "cause": None, "time_s": None},
    "checkpoints": [{"trigger": "roll", "t": 300.0,
                     "results": {"encounter": {"given": "Head seas", "answer": "Beam seas", "correct": False}},
                     "reason": "the sea looked lower ahead"}],
    "orders": [{"t": 0.0, "heading_deg": 279.9, "speed_knots": 9.7},
               {"t": 620.0, "heading_deg": 61.5, "speed_knots": 12.0}],
    "sea": {"peak_roll": {"t": 301.0, "roll_deg": -13.5, "hs_m": 6.8, "heading_deg": 280.0,
                          "wave_from_deg": 290.0, "encounter": "Beam seas"},
            "max_hs": {"t": 900.0, "hs_m": 7.4, "encounter": "Bow-quartering seas", "roll_deg": -6.0},
            "first_loss": {"t": 310.0, "teu": 5, "demand_g": 0.4, "hs_m": 6.9, "encounter": "Beam seas",
                           "heading_deg": 280.0}},
}


def call(name, args=None):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": name, "arguments": json.dumps(args or {})}}]}


class Scripted:
    def __init__(self, *steps):
        self.steps = list(steps)
        self.payloads = []

    def __call__(self, payload):
        self.payloads.append(payload)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def tutor(script):
    return Tutor(models_path=None, keys_path=None,
                 profile={"model": "fake-tool-model", "temperature": 0.0, "top_p": 1.0, "max_tokens": 200},
                 transport_factory=lambda: script)


def finish(decision_fact, decision, improvement_fact, improvement):
    return call("finish_evaluation", {"decision_fact": decision_fact, "decision_sentence": decision,
                                      "improvement_fact": improvement_fact, "improvement_sentence": improvement})


def test_build_facts_names_every_group_and_strips_internal_data():
    facts = build_facts(EVIDENCE)

    assert [fact["id"] for fact in facts] == [
        "outcome", "peak_roll", "max_hs", "first_loss", "checkpoint-1", "order-1", "order-2"]
    assert {fact["id"]: fact["group"] for fact in facts} == {
        "outcome": "outcome", "peak_roll": "sea", "max_hs": "sea", "first_loss": "cargo",
        "checkpoint-1": "checkpoint", "order-1": "order", "order-2": "order"}
    assert all(set(fact) == {"id", "group", "text", "numbers"} for fact in facts)
    assert "5.0" in next(fact for fact in facts if fact["id"] == "checkpoint-1")["numbers"]
    assert "300" in next(fact for fact in facts if fact["id"] == "checkpoint-1")["numbers"]


def test_the_local_debrief_names_the_wrong_answer_and_the_first_loss():
    result = fallback_evaluation(EVIDENCE)

    assert result["status"] == "ready"
    assert result["source"] == "fallback"
    assert result["decision"]["fact"] == "checkpoint-1"
    assert result["improvement"]["fact"] == "first_loss"
    assert result["sentences"] == [result["decision"]["text"], result["improvement"]["text"]]
    assert len(result["sentences"]) == 2
    assert all(sentence.endswith(".") for sentence in result["sentences"])
    assert "Head seas" in result["sentences"][0] and "Beam seas" in result["sentences"][0]
    assert "6.9 m" in result["sentences"][1] and "5 TEU" in result["sentences"][1]


def test_a_lost_ship_leads_the_debrief():
    evidence = deepcopy(EVIDENCE)
    evidence["ship"] = {"lost": True, "cause": "roll_capsize", "time_s": 720.0}
    evidence["sea"]["peak_roll"]["roll_deg"] = -34.0

    result = fallback_evaluation(evidence)

    assert result["decision"]["fact"] == "ship_loss"
    assert result["decision"]["text"] == "The ship was lost to roll_capsize at 12.0 min."
    assert result["improvement"]["fact"] == "first_loss"


def test_no_evidence_gives_an_honest_unavailable_answer():
    assert fallback_evaluation({}) == {"status": "unavailable", "source": "fallback", "reason": "no_evidence",
                                       "decision": None, "improvement": None, "sentences": []}


def test_a_valid_agent_debrief_keeps_its_sentences():
    fallback = fallback_evaluation(EVIDENCE)
    script = Scripted(call("record_plan", {"steps": ["read", "answer"]}), call("read_evidence"),
                      finish("checkpoint-1", fallback["sentences"][0], "first_loss", fallback["sentences"][1]))
    result = tutor(script).evaluate(EVIDENCE)

    assert result["status"] == "ready"
    assert result["source"] == "agent"
    assert result["sentences"] == fallback["sentences"]
    assert result["decision"] == {"fact": "checkpoint-1", "text": fallback["sentences"][0]}
    evidence_prompt = script.payloads[2]["messages"][-1]["content"]
    assert "facts" in evidence_prompt and "Do not change the outcome" in evidence_prompt


def test_an_invented_cargo_loss_is_rejected():
    script = Scripted(call("record_plan", {"steps": ["read"]}), call("read_evidence"),
                      finish("checkpoint-1", "You lost 999 TEU at 5.0 min.",
                             "first_loss", "Next time, slow down before the 6.9 m seas at 280°."))
    result = tutor(script).evaluate(EVIDENCE)

    assert result["source"] == "fallback"
    assert result["reason"] == "invalid_model_output"
    assert "999" not in " ".join(result["sentences"])


def test_three_sentences_and_long_sentences_are_rejected():
    long_sentence = "You answered wrongly. " * 14
    for decision in ("First. Second. Third.", long_sentence):
        script = Scripted(call("record_plan", {"steps": ["read"]}), call("read_evidence"),
                          finish("checkpoint-1", decision, "first_loss",
                                 "Next time, change heading before the 6.9 m seas at 280°."))
        result = tutor(script).evaluate(EVIDENCE)
        assert result["source"] == "fallback"
        assert result["reason"] == "invalid_model_output"
    assert len(long_sentence) > MAX_SENTENCE_CHARS


def test_fact_ids_and_groups_are_checked():
    cases = [
        ("checkpoint-9", "At 5.0 min you answered 'Head seas'.", "first_loss", "See the loss."),  # no such fact
        ("outcome", "The mission ended with escaped after 25.4 min.", "first_loss", "See the loss."),  # bad group
        ("checkpoint-1", "At 5.0 min you answered 'Head seas'.", "outcome", "See the outcome."),  # bad group
    ]
    for decision_fact, decision, improvement_fact, improvement in cases:
        script = Scripted(call("record_plan", {"steps": ["read"]}), call("read_evidence"),
                          finish(decision_fact, decision, improvement_fact, improvement))
        assert tutor(script).evaluate(EVIDENCE)["source"] == "fallback"


def test_evidence_must_be_read_before_the_debrief():
    fallback = fallback_evaluation(EVIDENCE)
    script = Scripted(call("record_plan", {"steps": ["answer"]}),
                      finish("checkpoint-1", fallback["sentences"][0], "first_loss", fallback["sentences"][1]))
    result = tutor(script).evaluate(EVIDENCE)

    assert result["source"] == "fallback"
    assert result["reason"] == "evidence_not_read"


def test_a_failing_transport_still_debriefs_from_the_log():
    result = tutor(Scripted(ModelError("provider down"))).evaluate(EVIDENCE)

    assert result["status"] == "ready"
    assert result["source"] == "fallback"
    assert result["reason"] == "model_failed"
    assert len(result["sentences"]) == 2
