"""Bounded LangGraph tool harness shared by active TIDE agents.

The harness is deliberately small.  It supplies:
- instructions loaded from a role directory,
- a typed tool registry with an explicit role allowlist,
- a finite plan -> tool -> observation -> revision -> finalize loop,
- SQLite checkpoints and resume,
- an audit stream that never stores credentials.

The harness does not own game state and does not approve scenarios.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from agent.models import canonical, digest_file, stable_digest

from game.director.models import load_profile


class HarnessError(RuntimeError):
    """Base harness error."""


class HarnessPending(HarnessError):
    """The model, a tool or a finite budget stopped the run for human review."""

    def __init__(self, reason: str, *, state: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.state = state or {}


class ToolError(HarnessError):
    """A tool call is malformed, unknown or failed locally."""


class ToolDenied(ToolError):
    """The current role is not allowed to call this tool."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    roles: frozenset[str]
    handler: Callable[[dict], dict]
    costly: bool = False
    terminal: bool = False

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """A reusable registry with role filtering and strict schema validation."""

    def __init__(self, tools: list[Tool]):
        self._tools = {tool.name: tool for tool in tools}
        if len(self._tools) != len(tools):
            raise ValueError("Tool names must be unique")

    def add(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def allowed(self, role: str) -> list[Tool]:
        return [tool for tool in self._tools.values() if role in tool.roles]

    def schemas(self, role: str) -> list[dict]:
        return [tool.schema() for tool in self.allowed(role)]

    def get(self, role: str, name: str) -> Tool:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolDenied("Unknown or unauthorized tool")
        if role not in tool.roles:
            raise ToolDenied("Tool is not allowed for this Agent role")
        return tool

    def invoke(self, role: str, name: str, arguments: dict) -> dict:
        tool = self.get(role, name)
        validate_json(arguments, tool.parameters, path="arguments")
        result = tool.handler(arguments)
        if not isinstance(result, dict):
            raise ToolError("Tool handler must return an object")
        try:
            json.dumps(result, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ToolError(f"Tool result is not JSON-ready: {error}") from None
        return result

    @classmethod
    def from_specs(
        cls, specs: list[dict], handlers: dict[str, Callable[[dict], dict]]
    ) -> "ToolRegistry":
        tools = []
        for spec in specs:
            name = spec["name"]
            if name not in handlers:
                raise ValueError(f"No handler bound for tool {name}")
            tools.append(
                Tool(
                    name=name,
                    description=spec["description"],
                    parameters=spec["parameters"],
                    roles=frozenset(spec["roles"]),
                    handler=handlers[name],
                    costly=bool(spec.get("costly", False)),
                    terminal=bool(spec.get("terminal", False)),
                )
            )
        return cls(tools)


def load_tool_specs(path: str | Path) -> list[dict]:
    value = json.loads(Path(path).read_text())
    if value.get("version") != 1 or not isinstance(value.get("tools"), list):
        raise ValueError("Unsupported tool registry")
    return value["tools"]


def _type_ok(value: Any, kind: str) -> bool:
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "null":
        return value is None
    raise ValueError(f"Unsupported schema type: {kind}")


def validate_json(value: Any, schema: dict, *, path: str = "value") -> None:
    """Validate the small JSON Schema subset used by the tool registry."""
    if "type" in schema and not _type_ok(value, schema["type"]):
        raise ToolError(f"{path} must be {schema['type']}")
    if "enum" in schema and value not in schema["enum"]:
        raise ToolError(f"{path} must be one of {schema['enum']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ToolError(f"{path} is below the minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ToolError(f"{path} exceeds the maximum")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise ToolError(f"{path} is missing required fields: {', '.join(missing)}")
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(properties)
            if extra:
                raise ToolError(f"{path} has unsupported fields: {', '.join(sorted(extra))}")
        for key, item in value.items():
            if key in properties:
                validate_json(item, properties[key], path=f"{path}.{key}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise ToolError(f"{path} has too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ToolError(f"{path} has too many items")
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                validate_json(item, item_schema, path=f"{path}[{index}]")


class HarnessState(TypedDict):
    messages: list
    context: dict
    context_hash: str
    plan: list
    rounds: int
    tool_calls: int
    failures: int
    done: bool
    result: dict
    phase: str
    pending: str


class AgentHarness:
    """A bounded graph with one model node and one local tool node."""

    def __init__(
        self,
        *,
        role: str,
        instructions: str,
        registry: ToolRegistry,
        transport: Callable[[dict], dict],
        profile: dict,
        checkpoint_path: str | Path,
        thread_id: str | None = None,
        max_rounds: int | None = None,
        max_tool_calls: int | None = None,
        max_failures: int = 4,
        audit_path: str | Path | None = None,
    ):
        if not instructions.strip():
            raise ValueError("Agent instructions must not be empty")
        self.role = role
        self.instructions = instructions
        self.registry = registry
        self.transport = transport
        self.profile = profile
        self.max_rounds = int(max_rounds or profile.get("tool_round_limit", 4))
        self.max_tool_calls = int(max_tool_calls or self.max_rounds)
        self.max_failures = int(max_failures)
        if not 1 <= self.max_rounds <= 64:
            raise ValueError("Model round budget must be between 1 and 64")
        if not 1 <= self.max_tool_calls <= 64:
            raise ValueError("Tool-call budget must be between 1 and 64")
        self.thread_id = thread_id or uuid.uuid4().hex
        self.audit_path = Path(audit_path) if audit_path else None
        self._events: list[dict] = []
        self.checkpoint_path = Path(checkpoint_path)
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(
            str(self.checkpoint_path), check_same_thread=False
        )
        self.saver = SqliteSaver(self.connection)
        builder = StateGraph(HarnessState)
        builder.add_node("model", self._model)
        builder.add_node("tools", self._tools)
        builder.add_edge(START, "model")
        builder.add_conditional_edges(
            "model", self._after_model, {"tools": "tools", "model": "model"}
        )
        builder.add_conditional_edges(
            "tools", lambda state: END if state["done"] else "model"
        )
        self.graph = builder.compile(checkpointer=self.saver)
        self.config = {
            "configurable": {"thread_id": self.thread_id},
            "recursion_limit": max(8, self.max_rounds * 3 + 4),
        }

    # -------------------------------- audit --------------------------------
    def _audit(self, kind: str, data: dict) -> None:
        event = {
            "timestamp": time.time(),
            "thread_id": self.thread_id,
            "role": self.role,
            "kind": kind,
            "data": data,
        }
        # Only harness-owned facts are accepted.  Transport headers and keys
        # never enter this structure.
        self._events.append(event)
        if self.audit_path is not None:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a") as stream:
                stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")

    @property
    def events(self) -> list[dict]:
        return list(self._events)

    # -------------------------------- graph --------------------------------
    def _model(self, state: HarnessState) -> dict:
        if state["done"]:
            return state
        if state["rounds"] >= self.max_rounds:
            raise HarnessPending("model_round_limit", state=dict(state))
        tools = self.registry.schemas(self.role)
        if not tools:
            raise HarnessPending("no_authorized_tools", state=dict(state))
        first = not state["plan"]
        if first:
            forced = next(
                (tool for tool in tools if tool["function"]["name"] == "record_plan"),
                None,
            )
            if forced is None:
                raise HarnessPending("plan_tool_unavailable", state=dict(state))
            tools = [forced]
        payload = {
            key: self.profile[key]
            for key in ("model", "temperature", "top_p", "max_tokens")
            if key in self.profile
        }
        options = self.profile.get("request_options", {})
        if set(options) & {"messages", "tools", "tool_choice", "model"}:
            raise ValueError("Request options cannot override harness authority")
        payload.update(options)
        payload.update(
            messages=state["messages"],
            tools=tools,
            tool_choice=(
                {"type": "function", "function": {"name": "record_plan"}}
                if first
                else "auto"
            ),
            parallel_tool_calls=False,
        )
        try:
            message = self.transport(payload)
        except Exception as error:
            raise HarnessPending(f"model_failure: {error}", state=dict(state)) from None
        if not isinstance(message, dict):
            raise HarnessPending("malformed_model_response", state=dict(state))
        standard = {
            key: message[key]
            for key in ("role", "content", "tool_calls")
            if key in message
        }
        standard["role"] = "assistant"
        calls = standard.get("tool_calls", [])
        self._audit(
            "model_response",
            {
                "round": state["rounds"] + 1,
                "message": standard,
                "tool_choice": "record_plan" if first else "auto",
            },
        )
        messages = state["messages"] + [standard]
        if not isinstance(calls, list) or len(calls) != 1:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Validation error: return exactly one native function call. "
                        "No action was executed."
                    ),
                }
            )
            failures = state["failures"] + 1
            if failures >= self.max_failures:
                raise HarnessPending("model_validation_limit", state=dict(state))
            return {
                "messages": messages,
                "rounds": state["rounds"] + 1,
                "failures": failures,
                "phase": "revision",
            }
        return {
            "messages": messages,
            "rounds": state["rounds"] + 1,
            "phase": "tool",
        }

    def _after_model(self, state: HarnessState) -> str:
        last = state["messages"][-1]
        if last.get("role") == "assistant" and last.get("tool_calls"):
            return "tools"
        return "model"

    def _tools(self, state: HarnessState) -> dict:
        if state["tool_calls"] >= self.max_tool_calls:
            raise HarnessPending("tool_call_limit", state=dict(state))
        call = state["messages"][-1]["tool_calls"][0]
        function = call.get("function", {}) if isinstance(call, dict) else {}
        name = function.get("name")
        messages = list(state["messages"])
        plan = list(state["plan"])
        done = False
        result = dict(state["result"])
        failures = state["failures"]
        args = None
        try:
            if not isinstance(name, str) or not name:
                raise ToolError("Tool call has no name")
            if not state["plan"] and name != "record_plan":
                raise ToolError("Call record_plan before any other tool")
            raw = function.get("arguments", "{}")
            try:
                args = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                raise ToolError("Tool arguments are not valid JSON") from None
            if not isinstance(args, dict):
                raise ToolError("Tool arguments must be an object")
            tool = self.registry.get(self.role, name)
            value = self.registry.invoke(self.role, name, args)
            if name == "record_plan":
                steps = value.get("plan")
                if not isinstance(steps, list) or not steps:
                    raise ToolError("Plan must be a non-empty list")
                plan = steps
            done = tool.terminal
            if done:
                result = value
            failures = 0
        except (ToolError, ValueError, KeyError, TypeError) as error:
            value = {"error": str(error)}
            failures += 1
            if failures >= self.max_failures:
                self._audit(
                    "tool_failure",
                    {"name": name, "error": str(error), "failures": failures},
                )
                raise HarnessPending(
                    f"tool_failure_limit: {error}", state=dict(state)
                ) from None
        except Exception as error:
            self._audit(
                "tool_failure",
                {"name": name, "error": str(error), "unexpected": True},
            )
            raise HarnessPending(
                f"tool_failure: {error}", state=dict(state)
            ) from None
        self._audit(
            "tool_call",
            {
                "name": name,
                "arguments": args,
                "result": value,
                "tool_calls": state["tool_calls"] + 1,
            },
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call.get("id", "invalid"),
                "content": json.dumps(value, ensure_ascii=False, allow_nan=False),
            }
        )
        return {
            "messages": messages,
            "tool_calls": state["tool_calls"] + 1,
            "failures": failures,
            "plan": plan,
            "done": done,
            "result": result,
        }

    # --------------------------------- run ---------------------------------
    def _initial(self, context: dict) -> HarnessState:
        context = json.loads(json.dumps(context, ensure_ascii=False, allow_nan=False))
        return {
            "messages": [
                {"role": "system", "content": self.instructions},
                {
                    "role": "user",
                    "content": json.dumps(context, ensure_ascii=False, allow_nan=False),
                },
            ],
            "context": context,
            "context_hash": stable_digest(context),
            "plan": [],
            "rounds": 0,
            "tool_calls": 0,
            "failures": 0,
            "done": False,
            "result": {},
            "phase": "plan",
            "pending": "",
        }

    def run(self, context: dict, *, resume: bool = False) -> dict:
        existing = self.graph.get_state(self.config).values
        if resume:
            if not existing:
                raise HarnessError("No checkpoint exists for this thread")
            if existing.get("context_hash") != stable_digest(context):
                raise HarnessError("Resume context does not match the checkpoint")
            if existing.get("done"):
                terminal = dict(existing.get("result") or {})
                return {
                    "status": terminal.get("status", "finalized"),
                    "rounds": existing.get("rounds", 0),
                    "tool_calls": existing.get("tool_calls", 0),
                    "plan": existing.get("plan", []),
                    "result": terminal,
                }
            self._audit("resume", {"rounds": existing.get("rounds", 0)})
            try:
                state = self.graph.invoke(None, config=self.config)
            except HarnessPending as error:
                self._audit("pending", {"reason": error.reason})
                raise
        else:
            if existing and not existing.get("done"):
                raise HarnessError(
                    "A checkpoint already exists; use resume=True or a new thread"
                )
            self._audit("start", {"context_hash": stable_digest(context)})
            try:
                state = self.graph.invoke(self._initial(context), config=self.config)
            except HarnessPending as error:
                self._audit("pending", {"reason": error.reason})
                raise
        self._audit(
            "finish",
            {
                "done": state.get("done"),
                "rounds": state.get("rounds"),
                "tool_calls": state.get("tool_calls"),
                "status": (state.get("result") or {}).get("status"),
            },
        )
        return {
            "status": (state.get("result") or {}).get("status", "finalized"),
            "rounds": state.get("rounds", 0),
            "tool_calls": state.get("tool_calls", 0),
            "plan": state.get("plan", []),
            "result": state.get("result", {}),
        }

    def close(self) -> None:
        close = getattr(self.transport, "close", None)
        if close:
            close()
        self.connection.close()


def load_builder_profile(models_path: str | Path, role: str = "builder"):
    return load_profile(models_path, role)


def instructions_from(directory: str | Path, names: tuple[str, ...] = ("INSTRUCTIONS.md",)) -> str:
    base = Path(directory)
    parts = []
    for name in names:
        path = base / name
        if not path.is_file():
            raise ValueError(f"Missing instructions: {path}")
        parts.append(path.read_text())
    return "\n\n".join(parts)


def instruction_digest(directory: str | Path, names: tuple[str, ...] = ("INSTRUCTIONS.md",)) -> str:
    base = Path(directory)
    return stable_digest(
        {name: digest_file(base / name) for name in names if (base / name).is_file()}
    )


__all__ = [
    "AgentHarness",
    "HarnessError",
    "HarnessPending",
    "Tool",
    "ToolDenied",
    "ToolError",
    "ToolRegistry",
    "canonical",
    "instruction_digest",
    "instructions_from",
    "load_builder_profile",
    "load_tool_specs",
    "validate_json",
]
