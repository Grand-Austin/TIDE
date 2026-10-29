"""Bounded background judgment over issued reports and current observations."""

import json
import sqlite3
import time
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from .models import ModelError


class AttentionState(TypedDict):
    context: dict
    decision: dict


class AttentionDirector:
    def __init__(self, profile, transport, checkpoint_path):
        self.profile, self.transport = profile, transport
        self.connection = sqlite3.connect(str(checkpoint_path), check_same_thread=False)
        self.checkpointer = SqliteSaver(self.connection)
        graph = StateGraph(AttentionState)
        graph.add_node("choose_delivery_and_attention", self._choose)
        graph.add_edge(START, "choose_delivery_and_attention")
        graph.add_edge("choose_delivery_and_attention", END)
        self.graph = graph.compile(checkpointer=self.checkpointer)

    def _choose(self, state):
        root = Path(__file__).with_name("harness")
        registry = json.loads((root / "tools.json").read_text())
        tool = next(
            t for t in registry["tools"] if t["function"]["name"] == "manage_attention"
        )
        tool = {k: v for k, v in tool.items() if k != "phases"}
        messages = [
            {
                "role": "system",
                "content": (root / "SKILL.md").read_text()
                + "\n"
                + (root / "ATTENTION.md").read_text(),
            },
            {
                "role": "user",
                "content": json.dumps(state["context"], ensure_ascii=False),
            },
        ]
        for _ in range(self.profile["tool_round_limit"]):
            payload = {
                k: self.profile[k]
                for k in ("model", "temperature", "top_p", "max_tokens")
            }
            payload.update(self.profile.get("request_options", {}))
            payload.update(
                messages=messages,
                tools=[tool],
                parallel_tool_calls=False,
                tool_choice={
                    "type": "function",
                    "function": {"name": "manage_attention"},
                },
            )
            result = self.transport(payload)
            calls = result.get("tool_calls") or []
            if (
                len(calls) == 1
                and calls[0].get("function", {}).get("name") == "manage_attention"
            ):
                try:
                    decision = json.loads(calls[0]["function"]["arguments"])
                    if isinstance(decision, dict):
                        return {"decision": decision}
                except (KeyError, TypeError, ValueError):
                    pass
            messages.append(
                {
                    "role": "user",
                    "content": "Return one native manage_attention call with all required fields. No prose.",
                }
            )
        raise ModelError("Attention Director exhausted its structured decision budget")

    def run(self, session, context, epoch, review_id):
        start = time.monotonic()
        session.record(
            "attention_model_start",
            {
                "review_id": review_id,
                "observed_s": context["elapsed_s"],
                "model": self.profile["model"],
            },
        )
        result = self.graph.invoke(
            {"context": context, "decision": {}},
            config={"configurable": {"thread_id": review_id}},
        )
        decision = result["decision"]
        outcome = session.apply_attention(
            decision,
            review_id=review_id,
            expected_epoch=epoch,
            observed_s=context["elapsed_s"],
        )
        session.record(
            "attention_model_end",
            {
                "review_id": review_id,
                "duration_s": time.monotonic() - start,
                "decision": decision,
                "result": outcome,
            },
        )
        return outcome

    def close(self):
        self.connection.close()
