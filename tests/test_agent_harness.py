import json
import sqlite3

import pytest

from agent.harness import (
    AgentHarness,
    HarnessError,
    HarnessPending,
    Tool,
    ToolDenied,
    ToolRegistry,
    validate_json,
)
from game.director.models import ModelError


PROFILE = {
    "model": "fake-tool-model",
    "temperature": 0.0,
    "top_p": 1.0,
    "max_tokens": 512,
    "tool_round_limit": 8,
}


def call(name, args=None, call_id="c1"):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args or {}),
                },
            }
        ],
    }


def registry(counter=None):
    counter = counter if counter is not None else {"costly": 0, "final": 0}

    def plan(args):
        return {"plan": args["steps"], "status": "planned"}

    def costly(args):
        counter["costly"] += 1
        return {"value": len(args.get("value", ""))}

    def final(args):
        counter["final"] += 1
        return {"status": "ok"}

    return (
        ToolRegistry(
            [
                Tool(
                    "record_plan",
                    "record a plan",
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["steps"],
                        "properties": {
                            "steps": {"type": "array", "items": {"type": "string"}}
                        },
                    },
                    frozenset({"builder"}),
                    plan,
                ),
                Tool(
                    "run_trial",
                    "costly trial",
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["value"],
                        "properties": {"value": {"type": "string"}},
                    },
                    frozenset({"builder"}),
                    costly,
                    costly=True,
                ),
                Tool(
                    "finalize_build",
                    "finish",
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {},
                    },
                    frozenset({"builder"}),
                    final,
                    terminal=True,
                ),
                Tool(
                    "tutor_only",
                    "denied to builder",
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {},
                    },
                    frozenset({"tutor"}),
                    lambda args: {"status": "wrong"},
                ),
            ]
        ),
        counter,
    )


def harness(tmp_path, transport, registry_value, **kwargs):
    return AgentHarness(
        role="builder",
        instructions="Use tools. Never approve.",
        registry=registry_value,
        transport=transport,
        profile=PROFILE,
        checkpoint_path=tmp_path / "graph.sqlite",
        **kwargs,
    )


def test_role_allowlist_denies_tutor_tool():
    tools, _ = registry()
    with pytest.raises(ToolDenied):
        tools.invoke("builder", "tutor_only", {})
    assert [t.name for t in tools.allowed("builder")] == [
        "record_plan",
        "run_trial",
        "finalize_build",
    ]


def test_schema_rejects_extra_and_wrong_types():
    tools, _ = registry()
    with pytest.raises(Exception, match="unsupported fields"):
        tools.invoke("builder", "run_trial", {"value": "x", "heading": 90})
    with pytest.raises(Exception, match="must be array"):
        tools.invoke("builder", "record_plan", {"steps": "not a list"})
    validate_json({"steps": ["a"]}, tools.get("builder", "record_plan").parameters)


def test_malformed_tool_call_is_observed_and_not_executed(tmp_path):
    tools, counter = registry()
    responses = iter(
        [
            call("record_plan", {"steps": ["inspect", "trial"]}),
            call("run_trial", {"value": "x", "heading": 90}),
            call("finalize_build"),
        ]
    )
    seen = []

    def transport(payload):
        seen.append(payload)
        return next(responses)

    agent = harness(tmp_path, transport, tools, max_failures=3)
    result = agent.run({"scenario": "x"})
    agent.close()
    assert result["status"] == "ok"
    assert counter["costly"] == 0
    assert counter["final"] == 1
    observations = [m for m in result.get("plan", [])]
    assert observations == ["inspect", "trial"]
    assert seen[0]["tool_choice"]["function"]["name"] == "record_plan"
    assert all(t["function"]["name"] == "record_plan" for t in seen[0]["tools"])


def test_plan_is_required_before_other_tools(tmp_path):
    tools, counter = registry()
    responses = iter(
        [
            call("run_trial", {"value": "early"}),
            call("record_plan", {"steps": ["plan"]}),
            call("finalize_build"),
        ]
    )

    def transport(_):
        return next(responses)

    agent = harness(tmp_path, transport, tools)
    result = agent.run({"scenario": "x"})
    agent.close()
    assert result["status"] == "ok"
    assert counter["costly"] == 0


def test_tool_call_cap_stops_with_pending(tmp_path):
    tools, counter = registry()
    responses = iter(
        [
            call("record_plan", {"steps": ["plan"]}),
            call("run_trial", {"value": "one"}),
            call("run_trial", {"value": "two"}),
        ]
    )

    def transport(_):
        return next(responses)

    agent = harness(tmp_path, transport, tools, max_tool_calls=2)
    with pytest.raises(HarnessPending, match="tool_call_limit"):
        agent.run({"scenario": "x"})
    agent.close()
    assert counter["costly"] == 1


def test_model_failure_is_pending_not_success(tmp_path):
    tools, _ = registry()

    def fail(_):
        raise ModelError("offline")

    agent = harness(tmp_path, fail, tools)
    with pytest.raises(HarnessPending, match="model_failure"):
        agent.run({"scenario": "x"})
    agent.close()


def test_resume_does_not_repeat_a_completed_costly_tool(tmp_path):
    tools, counter = registry()
    first = iter(
        [
            call("record_plan", {"steps": ["plan", "trial"]}),
            call("run_trial", {"value": "expensive"}),
        ]
    )

    def first_transport(_):
        try:
            return next(first)
        except StopIteration:
            raise ModelError("cut after the costly tool")

    agent = harness(
        tmp_path, first_transport, tools, thread_id="stable-thread"
    )
    with pytest.raises(HarnessPending, match="model_failure"):
        agent.run({"scenario": "same"})
    agent.close()
    assert counter["costly"] == 1

    def resumed_transport(_):
        return call("finalize_build")

    second = harness(
        tmp_path, resumed_transport, tools, thread_id="stable-thread"
    )
    result = second.run({"scenario": "same"}, resume=True)
    second.close()
    assert result["status"] == "ok"
    assert counter["costly"] == 1
    assert counter["final"] == 1
    with sqlite3.connect(tmp_path / "graph.sqlite") as connection:
        assert connection.execute("select count(*) from checkpoints").fetchone()[0] > 0


def test_resume_context_mismatch_is_rejected(tmp_path):
    tools, _ = registry()
    first = iter([call("record_plan", {"steps": ["plan"]}), call("finalize_build")])

    def transport(_):
        return next(first)

    agent = harness(tmp_path, transport, tools, thread_id="thread")
    agent.run({"scenario": "first"})
    agent.close()
    second = harness(tmp_path, lambda _: call("finalize_build"), tools, thread_id="thread")
    with pytest.raises(HarnessError, match="context"):
        second.run({"scenario": "other"}, resume=True)
    second.close()


def test_audit_records_tools_without_credentials(tmp_path):
    tools, _ = registry()
    responses = iter(
        [call("record_plan", {"steps": ["plan"]}), call("finalize_build")]
    )

    def transport(_):
        return next(responses)

    agent = harness(tmp_path, transport, tools, audit_path=tmp_path / "audit.jsonl")
    agent.run({"scenario": "x"})
    agent.close()
    text = (tmp_path / "audit.jsonl").read_text()
    assert '"kind": "tool_call"' in text
    assert "Authorization" not in text
    assert "api_key" not in text.lower()


def test_unexpected_tool_runtime_error_becomes_pending(tmp_path):
    def boom(_args):
        raise RuntimeError("tool backend crashed")

    tools = ToolRegistry(
        [
            Tool(
                "record_plan",
                "plan",
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["steps"],
                    "properties": {
                        "steps": {"type": "array", "items": {"type": "string"}}
                    },
                },
                frozenset({"builder"}),
                lambda args: {"plan": args["steps"]},
            ),
            Tool(
                "run_trial",
                "fails",
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {},
                },
                frozenset({"builder"}),
                boom,
            ),
        ]
    )
    responses = iter(
        [
            call("record_plan", {"steps": ["plan"]}),
            call("run_trial", {}),
        ]
    )
    agent = harness(
        tmp_path,
        lambda _: next(responses),
        tools,
        max_failures=1,
    )
    with pytest.raises(HarnessPending, match="tool_failure"):
        agent.run({"scenario": "x"})
    agent.close()


def test_resume_after_done_returns_the_initial_wrapper_shape(tmp_path):
    tools, _ = registry()
    responses = iter(
        [call("record_plan", {"steps": ["plan"]}), call("finalize_build")]
    )

    def transport(_):
        return next(responses)

    first = harness(tmp_path, transport, tools, thread_id="done-thread")
    result = first.run({"scenario": "x"})
    first.close()
    assert set(result) == {"status", "rounds", "tool_calls", "plan", "result"}

    def must_not_call(_):
        raise AssertionError("completed checkpoint must not call the model")

    second = harness(tmp_path, must_not_call, tools, thread_id="done-thread")
    resumed = second.run({"scenario": "x"}, resume=True)
    second.close()
    assert resumed == result
