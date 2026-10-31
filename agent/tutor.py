"""Tutor: a two-sentence, evidence-backed debrief for the live game.

The Tutor never re-simulates and never re-scores. It reads a fixed list of facts that the game logged, names
one decision fact and one improvement fact, and writes one sentence about each. Every number in a sentence
must appear in that evidence, so an invented loss, time or outcome is rejected and the local summary is used.

The local summary is a real result, not a placeholder: a mission debrief always ends with two sentences,
whether or not a model answered.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Callable

from agent.harness import AgentHarness, ToolRegistry, instructions_from, load_tool_specs
from agent.transport import ChatClient, load_profile

MAX_SENTENCE_CHARS = 220
PROFILE_ROLE = "tutor"
HARNESS_DIR = Path(__file__).resolve().parent / "tutor_harness"
AUDIT_FILE = "tutor.jsonl"
NOTE = "Call read_evidence once, then finish_evaluation with one decision fact and one improvement fact."
DECISION_GROUPS = ("checkpoint", "order", "ship_loss")
IMPROVEMENT_GROUPS = ("checkpoint", "order", "sea", "cargo")
UNAVAILABLE = {"status": "unavailable", "source": "fallback", "reason": "no_evidence",
               "decision": None, "improvement": None, "sentences": []}


def _finite(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int(value, default: int = 0) -> int:
    number = _finite(value)
    return default if number is None else int(number)


def _num(value, default: float = 0.0) -> float:
    number = _finite(value)
    return default if number is None else number


def _record(fact_id: str, group: str, text: str, data: dict, extra: tuple = ()) -> dict:
    """One citable fact: the words the model reads and every number it may quote."""
    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    for value in extra:
        number = _finite(value)
        if number is None:
            continue
        numbers.append(f"{number:g}")
        numbers.append(f"{abs(number):g}")
    return {"id": fact_id, "group": group, "text": text, "numbers": numbers, "data": data}


def _fact_records(evidence: dict) -> list[dict]:
    outcome, ship = evidence.get("outcome") or {}, evidence.get("ship") or {}
    sea = evidence.get("sea") or {}
    records = []
    records.append(_record(
        "outcome", "outcome",
        f"Mission outcome: {outcome.get('outcome')} after {_num(outcome.get('elapsed_s')) / 60:.1f} min, "
        f"{_int(outcome.get('lost_teu'))} of {_int(outcome.get('total_teu'))} TEU lost, "
        f"grade {_int(outcome.get('grade'))}.",
        {"outcome": outcome.get("outcome"), "elapsed_s": outcome.get("elapsed_s")},
        extra=(outcome.get("elapsed_s"), outcome.get("lost_teu"), outcome.get("total_teu"),
               outcome.get("grade"), outcome.get("stars"), outcome.get("total")),
    ))
    if ship.get("lost"):
        records.append(_record(
            "ship_loss", "ship_loss",
            f"The ship was lost to {ship.get('cause') or 'the sea'} at {_num(ship.get('time_s')) / 60:.1f} min.",
            {"cause": ship.get("cause"), "t": ship.get("time_s")},
            extra=(ship.get("time_s"),),
        ))
    peak = sea.get("peak_roll") or {}
    if peak:
        records.append(_record(
            "peak_roll", "sea",
            f"Peak roll {abs(_num(peak.get('roll_deg'))):.0f}° at {_num(peak.get('t')) / 60:.1f} min in "
            f"{peak.get('encounter')} with Hs {_num(peak.get('hs_m')):.1f} m while steering "
            f"{_num(peak.get('heading_deg')):.0f}°.",
            {"roll_deg": peak.get("roll_deg"), "hs_m": peak.get("hs_m"), "t": peak.get("t"),
             "heading_deg": peak.get("heading_deg")},
            extra=(peak.get("roll_deg"), peak.get("hs_m"), peak.get("t"), peak.get("heading_deg")),
        ))
    highest = sea.get("max_hs") or {}
    if highest:
        records.append(_record(
            "max_hs", "sea",
            f"Highest sea {_num(highest.get('hs_m')):.1f} m at {_num(highest.get('t')) / 60:.1f} min in "
            f"{highest.get('encounter')} with roll {abs(_num(highest.get('roll_deg'))):.0f}°.",
            {"hs_m": highest.get("hs_m"), "roll_deg": highest.get("roll_deg"), "t": highest.get("t")},
            extra=(highest.get("hs_m"), highest.get("t"), highest.get("roll_deg")),
        ))
    loss = sea.get("first_loss") or None
    if loss:
        records.append(_record(
            "first_loss", "cargo",
            f"First cargo loss {_int(loss.get('teu'))} TEU at {_num(loss.get('t')) / 60:.1f} min in Hs "
            f"{_num(loss.get('hs_m')):.1f} m, {loss.get('encounter')}, heading "
            f"{_num(loss.get('heading_deg')):.0f}°.",
            {"teu": loss.get("teu"), "t": loss.get("t"), "hs_m": loss.get("hs_m"),
             "heading_deg": loss.get("heading_deg"), "demand_g": loss.get("demand_g")},
            extra=(loss.get("teu"), loss.get("t"), loss.get("hs_m"), loss.get("heading_deg"),
                   loss.get("demand_g")),
        ))
    for index, checkpoint in enumerate(evidence.get("checkpoints") or [], start=1):
        results = checkpoint.get("results") or {}
        answers = "; ".join(
            f"{question} answered '{result.get('given')}' (expected '{result.get('answer')}', "
            f"{'correct' if result.get('correct') else 'incorrect'})"
            for question, result in results.items())
        reason = checkpoint.get("reason")
        records.append(_record(
            f"checkpoint-{index}", "checkpoint",
            f"Decision at {_num(checkpoint.get('t')) / 60:.1f} min triggered by {checkpoint.get('trigger')}. "
            f"{answers}." + (f" Stated reason: {reason}." if reason else ""),
            {"trigger": checkpoint.get("trigger"), "t": checkpoint.get("t"), "results": results},
            extra=(checkpoint.get("t"),),
        ))
    for index, order in enumerate(evidence.get("orders") or [], start=1):
        records.append(_record(
            f"order-{index}", "order",
            f"At {_num(order.get('t')) / 60:.1f} min ordered {_num(order.get('heading_deg')):.0f}° at "
            f"{_num(order.get('speed_knots')):.1f} kn.",
            {"t": order.get("t"), "heading_deg": order.get("heading_deg"),
             "speed_knots": order.get("speed_knots")},
            extra=(order.get("t"), order.get("heading_deg"), order.get("speed_knots")),
        ))
    return records


def build_facts(evidence: dict) -> list[dict]:
    """The facts the model may cite, in a stable order."""
    return [{key: record[key] for key in ("id", "group", "text", "numbers")}
            for record in _fact_records(evidence)]


def _incorrect(record: dict) -> bool:
    return any(result.get("correct") is False for result in (record["data"].get("results") or {}).values())


def _decision_record(records: list[dict]) -> dict | None:
    by_id = {record["id"]: record for record in records}
    if "ship_loss" in by_id:
        return by_id["ship_loss"]
    checkpoints = [record for record in records if record["group"] == "checkpoint"]
    wrong = next((record for record in checkpoints if _incorrect(record)), None)
    if wrong is not None:
        return wrong
    if checkpoints:
        return checkpoints[0]
    orders = [record for record in records if record["group"] == "order"]
    if orders:
        return orders[0]
    return by_id.get("outcome")


def _improvement_record(records: list[dict]) -> dict | None:
    by_id = {record["id"]: record for record in records}
    if "first_loss" in by_id:
        return by_id["first_loss"]
    peak = by_id.get("peak_roll")
    if peak is not None and abs(_num(peak["data"].get("roll_deg"))) >= 10:
        return peak
    checkpoints = [record for record in records if record["group"] == "checkpoint"]
    wrong = next((record for record in checkpoints if _incorrect(record)), None)
    if wrong is not None:
        return wrong
    if "max_hs" in by_id:
        return by_id["max_hs"]
    orders = [record for record in records if record["group"] == "order"]
    if orders:
        return orders[-1]
    return None


def _decision_sentence(record: dict) -> str:
    data = record["data"]
    minutes = _num(data.get("t")) / 60.0
    if record["group"] == "ship_loss":
        return f"The ship was lost to {data.get('cause') or 'the sea'} at {minutes:.1f} min."
    if record["group"] == "checkpoint":
        results = data.get("results") or {}
        chosen = next(((name, result) for name, result in results.items()
                       if result.get("correct") is False), None)
        if chosen is None and results:
            chosen = next(iter(results.items()))
        if chosen is None:
            return f"At {minutes:.1f} min the {data.get('trigger')} decision point closed."
        question, result = chosen
        return (f"At {minutes:.1f} min you answered '{result.get('given')}' to the {data.get('trigger')} "
                f"question; the expected answer was '{result.get('answer')}'.")
    if record["group"] == "order":
        return (f"At {minutes:.1f} min you ordered {_num(data.get('heading_deg')):.0f}° at "
                f"{_num(data.get('speed_knots')):.1f} kn.")
    return f"The mission ended with {data.get('outcome')} after {_num(data.get('elapsed_s')) / 60:.1f} min."


def _improvement_sentence(record: dict) -> str:
    data = record["data"]
    if record["group"] == "cargo":
        return (f"Next time, change heading or speed before the {_num(data.get('hs_m')):.1f} m seas at "
                f"{_num(data.get('heading_deg')):.0f}° put {_int(data.get('teu'))} TEU overboard.")
    if record["group"] == "sea":
        if record["id"] == "peak_roll":
            return (f"Next time, bring the {_num(data.get('hs_m')):.1f} m seas off the beam before roll "
                    f"reaches {abs(_num(data.get('roll_deg'))):.0f}°.")
        return f"Next time, compare the released forecast areas before the sea reaches {_num(data.get('hs_m')):.1f} m."
    if record["group"] == "checkpoint":
        return (f"Next time, use the released forecast to check the {data.get('trigger')} answer before you "
                f"commit the order.")
    return (f"Next time, compare the released forecasts before ordering {_num(data.get('heading_deg')):.0f}° at "
            f"{_num(data.get('speed_knots')):.1f} kn.")


def fallback_evaluation(evidence: dict) -> dict:
    """Two sentences from the log alone, chosen by a fixed order: the same answer every run."""
    records = _fact_records(evidence)
    if not records:
        return dict(UNAVAILABLE, sentences=[])
    decision, improvement = _decision_record(records), _improvement_record(records)
    if decision is None or improvement is None:
        return dict(UNAVAILABLE, sentences=[])
    sentences = [_decision_sentence(decision), _improvement_sentence(improvement)]
    return {"status": "ready", "source": "fallback",
            "decision": {"fact": decision["id"], "text": sentences[0]},
            "improvement": {"fact": improvement["id"], "text": sentences[1]},
            "sentences": sentences, "reason": None}


def _number_allowed(token: str, numbers: list[str]) -> bool:
    value = float(token)
    for raw in numbers:
        number = _finite(raw)
        if number is None:
            continue
        if abs(value - number) <= 0.05 or value == round(number) or abs(value - number) <= 0.05 * abs(number):
            return True
    return False


def valid_sentence(sentence, numbers: list[str]) -> bool:
    """One sentence, one terminator, and no number the evidence does not carry."""
    if not isinstance(sentence, str):
        return False
    text = sentence.strip()
    if not text or len(text) > MAX_SENTENCE_CHARS or "\n" in text or "\r" in text:
        return False
    if text[-1] not in ".!?":
        return False
    if len(re.findall(r"[.!?]", re.sub(r"\d+\.\d+", "", text))) != 1:
        return False
    return all(_number_allowed(token, numbers)
               for token in re.findall(r"\d+(?:\.\d+)?", text))


def _close(transport) -> None:
    close = getattr(transport, "close", None)
    if close:
        try:
            close()
        except Exception:
            pass


class Tutor:
    """The Tutor role: one bounded harness run per mission, no checkpoint file, no owned game state."""

    def __init__(self, *, models_path: Path, keys_path: Path, audit_dir: Path | None = None,
                 profile: dict | None = None, provider: dict | None = None,
                 transport_factory: Callable[[], Callable] | None = None):
        self.audit_dir = Path(audit_dir) if audit_dir is not None else None
        self._factory = transport_factory
        self._profile = profile
        self._provider = provider
        self._keys_path = keys_path
        if transport_factory is None:
            self._profile, self._provider = load_profile(models_path, PROFILE_ROLE)
            _close(ChatClient(self._profile, self._provider, keys_path))  # fail now, not at the first debrief
        elif not isinstance(profile, dict):
            raise ValueError("A scripted Tutor transport needs an explicit profile")

    def close(self) -> None:
        """Release what this instance still owns: every client goes to a harness, which closes it."""

    def _transport(self):
        return self._factory() if self._factory is not None else ChatClient(
            self._profile, self._provider, self._keys_path)

    def evaluate(self, evidence: dict) -> dict:
        records = _fact_records(evidence)
        if not records:
            return fallback_evaluation(evidence)
        facts = build_facts(evidence)
        used = {"evidence": False}

        def read(args: dict) -> dict:
            used["evidence"] = True
            return {"status": "ok", "facts": facts,
                    "note": "Use only these facts. Do not change the outcome, score or cargo."}

        def finish(args: dict) -> dict:
            return {"status": "ok", **args}

        outcome, reason = self._run(evidence, facts, read, finish)
        if outcome is None:
            return self._fallback(evidence, "model_failed")
        if not used["evidence"]:
            return self._fallback(evidence, "evidence_not_read")
        result = outcome.get("result") or {}
        if not self._valid(result, records):
            return self._fallback(evidence, "invalid_model_output")
        return {"status": "ready", "source": "agent",
                "decision": {"fact": result["decision_fact"], "text": result["decision_sentence"].strip()},
                "improvement": {"fact": result["improvement_fact"],
                                "text": result["improvement_sentence"].strip()},
                "sentences": [result["decision_sentence"].strip(), result["improvement_sentence"].strip()],
                "reason": None}

    def _run(self, evidence: dict, facts: list[dict], read, finish) -> tuple[dict | None, str | None]:
        transport = self._transport()
        harness = None
        context = {"scenario": evidence.get("scenario"), "outcome": evidence.get("outcome"), "note": NOTE}
        try:
            harness = AgentHarness(
                role=PROFILE_ROLE,
                instructions=instructions_from(HARNESS_DIR),
                registry=ToolRegistry.from_specs(
                    load_tool_specs(HARNESS_DIR / "tools.json"),
                    {"record_plan": _record_plan, "read_evidence": read, "finish_evaluation": finish}),
                transport=transport,
                profile=self._profile,
                checkpoint_path=":memory:",
                max_rounds=4,
                max_tool_calls=4,
                max_failures=2,
                audit_path=self.audit_dir / AUDIT_FILE if self.audit_dir is not None else None,
            )
            return harness.run(context), None
        except Exception as error:
            return None, str(error)
        finally:
            if harness is not None:
                harness.close()
            else:
                _close(transport)

    def _valid(self, result: dict, records: list[dict]) -> bool:
        by_id = {record["id"]: record for record in records}
        decision, improvement = by_id.get(result.get("decision_fact")), by_id.get(result.get("improvement_fact"))
        if decision is None or improvement is None:
            return False
        if decision["group"] not in DECISION_GROUPS or improvement["group"] not in IMPROVEMENT_GROUPS:
            return False
        numbers = [number for record in records for number in record["numbers"]]
        return (valid_sentence(result.get("decision_sentence"), numbers)
                and valid_sentence(result.get("improvement_sentence"), numbers))

    def _fallback(self, evidence: dict, reason: str) -> dict:
        result = fallback_evaluation(evidence)
        return {**result, "reason": reason}


def _record_plan(args: dict) -> dict:
    return {"plan": args["steps"], "status": "planned"}
