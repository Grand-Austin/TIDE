"""A restricted Director graph. Simulation and authoritative state stay local."""

import json
import sqlite3
import uuid
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from .models import ChatClient, ModelError, load_profile


class GraphState(TypedDict):
    messages: list
    rounds: int
    done: bool
    phase: str
    advanced: bool
    operation_id: str
    reply: str
    retry: bool


class Director:
    def __init__(
        self,
        model_config="models.json",
        keys_path="config/api_keys.local.json",
        checkpoint_path="data/runs/director.sqlite",
        transport=None,
    ):
        self.checkpoint_path = Path(checkpoint_path)
        self.profile, provider = load_profile(model_config)
        self.transport = transport or ChatClient(self.profile, provider, keys_path)
        Path(checkpoint_path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(checkpoint_path), check_same_thread=False)
        self.saver = SqliteSaver(self.connection)
        builder = StateGraph(GraphState)
        builder.add_node("model", self._model)
        builder.add_node("tools", self._tools)
        builder.add_edge(START, "model")
        builder.add_conditional_edges(
            "model", lambda s: "model" if s["retry"] else END if s["done"] else "tools"
        )
        builder.add_conditional_edges("tools", lambda s: END if s["done"] else "model")
        self.graph = builder.compile(checkpointer=self.saver)

    def _schema(self, phase):
        registry = json.loads(
            (Path(__file__).with_name("harness") / "tools.json").read_text()
        )
        return [
            {k: v for k, v in tool.items() if k != "phases"}
            for tool in registry["tools"]
            if phase in tool["phases"]
        ]

    def _model(self, state):
        if state["rounds"] >= self.profile["tool_round_limit"]:
            raise ModelError("Director exceeded tool-round limit")
        payload = {
            k: self.profile[k] for k in ("model", "temperature", "top_p", "max_tokens")
        }
        options = self.profile.get("request_options", {})
        if set(options) & {"messages", "tools", "tool_choice", "model"}:
            raise ValueError("Request options cannot override Director authority")
        payload.update(options)
        required = (
            "advance"
            if state["phase"] == "resume" and not state["advanced"]
            else "finish_mission"
            if self.session.visible().get("outcome")
            else "request_decision"
        )
        messages = state["messages"] + [
            {
                "role": "user",
                "content": f"The only required next action is a native call to {required} with empty arguments {{}}. Do not return prose.",
            }
        ]
        payload.update(
            messages=messages,
            tools=[
                t
                for t in self._schema(state["phase"])
                if t["function"]["name"] == required
            ],
            tool_choice={"type": "function", "function": {"name": required}},
            parallel_tool_calls=False,
        )
        message = self.transport(payload)
        self.session.record(
            "model_response",
            {
                "round": state["rounds"] + 1,
                "message": {
                    k: message[k]
                    for k in ("role", "content", "tool_calls")
                    if k in message
                },
            },
        )
        calls = message.get("tool_calls", [])
        if not isinstance(calls, list) or len(calls) != 1:
            return {
                "messages": state["messages"]
                + [
                    {
                        "role": "user",
                        "content": "Validation error: return exactly one native function call, with empty arguments. No action was executed.",
                    }
                ],
                "rounds": state["rounds"] + 1,
                "retry": True,
            }
        # Persist only standard message fields, never transport headers or reasoning internals.
        message = {
            k: message[k] for k in ("role", "content", "tool_calls") if k in message
        }
        message["role"] = "assistant"
        return {
            "messages": state["messages"] + [message],
            "rounds": state["rounds"] + 1,
            "retry": False,
        }

    def _tools(self, state):
        call = state["messages"][-1]["tool_calls"][0]
        name = call.get("function", {}).get("name")
        allowed = {t["function"]["name"] for t in self._schema(state["phase"])}
        messages = list(state["messages"])
        advanced = state["advanced"]
        done = False
        try:
            if name not in allowed:
                raise ValueError("Unknown or unauthorized tool")
            args = json.loads(call["function"].get("arguments", "{}"))
            if args != {}:
                raise ValueError("Tool accepts no model-selected arguments")
            if name == "advance" and advanced:
                raise ValueError("Advance already completed for this player resume")
            if (
                name == "request_decision"
                and state["phase"] == "resume"
                and not advanced
            ):
                raise ValueError(
                    "Call advance for the pending authorized player resume first"
                )
            # The operation key is fixed for this graph run, independent of generated call IDs.
            result = self.session.tool(
                name, args, state["operation_id"], self.expected_revision
            )
            if name == "advance":
                advanced = True
            if name in ("request_decision", "finish_mission"):
                done = True
        except (ValueError, KeyError, TypeError) as error:
            result = {"error": str(error)}
        self.session.record(
            "director_tool",
            {
                "name": name,
                "operation_id": state["operation_id"],
                "revision": self.session.revision,
                "result": result,
            },
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call.get("id", "invalid"),
                "content": json.dumps(result, ensure_ascii=False),
            }
        )
        return {
            "messages": messages,
            "advanced": advanced,
            "done": done,
            "reply": (
                "Mission ended"
                if name == "finish_mission"
                else "Player decision required"
            )
            if done
            else "",
        }

    def run(self, session, phase="decision"):
        if phase not in ("decision", "resume"):
            raise ValueError("Unknown Director phase")
        self.session = session
        self.expected_revision = session.revision
        view = session.visible()
        harness = Path(__file__).with_name("harness")
        system = "\n\n".join(
            (harness / name).read_text() for name in ("SKILL.md", "WORKFLOWS.md")
        )

        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": json.dumps(
                    {"phase": phase, "visible": view}, ensure_ascii=False
                ),
            },
        ]
        session.record(
            "director_start",
            {"profile": self.profile, "phase": phase, "revision": session.revision},
        )
        try:
            result = self.graph.invoke(
                {
                    "messages": messages,
                    "rounds": 0,
                    "done": False,
                    "phase": phase,
                    "advanced": False,
                    "operation_id": uuid.uuid4().hex,
                    "reply": "",
                    "retry": False,
                },
                config={
                    "configurable": {"thread_id": session.session_id},
                    "recursion_limit": 24,
                },
            )
            return {
                "reply": result["reply"],
                "rounds": result["rounds"],
                "model": self.profile["model"],
            }
        except Exception:
            session.pause()
            raise
        finally:
            session.record(
                "director_end",
                {"model": self.profile["model"], "revision": session.revision},
            )

    def create_attention(self):
        from .attention import AttentionDirector

        return AttentionDirector(
            self.profile,
            self.transport,
            self.checkpoint_path.with_name("attention.sqlite"),
        )

    def close(self):
        close = getattr(self.transport, "close", None)
        if close:
            close()
        self.connection.close()
