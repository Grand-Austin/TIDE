"""Focused boundary checks for scoped user retention of final solutions."""

import math
from pathlib import Path

import pytest

from agent.solutions import build_solutions
from test_agent_solutions import FINGERPRINT, definition, make_trial


def receipt(names=("High loss",), *, approved_by="user", fingerprint=FINGERPRINT):
    return {
        "approved_by": approved_by,
        "source_fingerprint": fingerprint,
        "candidate_names": list(names),
    }


def build(records, approval=None, *, min_solutions=3):
    return build_solutions(
        records,
        definition=definition(),
        evidence={"fingerprint": FINGERPRINT},
        min_solutions=min_solutions,
        project_root=Path("/nonexistent-project-root"),
        manual_approval=approval,
    )


def test_default_strict_rule_rejects_cargo_loss_above_one_percent():
    result = build([make_trial("High loss", lost=20)])

    assert result["status"] == "pending"
    assert result["solution_count"] == 0
    assert result["reason"] == "no_qualified_final_solution"


def test_user_approval_retains_only_the_selected_high_loss_route():
    records = [
        make_trial("High loss", lost=20, elapsed=777.0, qualified=False, heading=100.0),
        make_trial("Other high loss", lost=30, elapsed=666.0, qualified=False, heading=160.0),
    ]
    approval = receipt()

    result = build(records, approval, min_solutions=1)
    solution = result["solutions"][0]

    assert result["status"] == "ready"
    assert result["solution_count"] == 1
    assert solution["name"] == "High loss"
    assert solution["automatic_qualified"] is False
    assert solution["selection_basis"] == "user_approved"
    assert (solution["lost_teu"], solution["total_teu"]) == (20, 1200)
    assert solution["loss_fraction"] == pytest.approx(20 / 1200, abs=1e-6)
    assert solution["elapsed_s"] == 777.0
    assert result["manual_approval"] == approval
    assert result["automatic_qualification_rule"] != result["governing_rule"]


@pytest.mark.parametrize(
    "approval",
    [
        receipt(approved_by="model"),
        receipt(fingerprint="fingerprint-old"),
        receipt(names=("Different route",)),
    ],
    ids=["model-cannot-approve", "fingerprint-must-match", "name-must-match"],
)
def test_receipt_is_scoped_to_human_name_and_fingerprint(approval):
    records = [
        make_trial("High loss", lost=20, qualified=False, heading=100.0),
        make_trial("Other high loss", lost=30, qualified=False, heading=160.0),
    ]

    result = build(records, approval, min_solutions=1)

    assert result["solution_count"] == 0
    assert result["reason"] == "no_qualified_final_solution"


@pytest.mark.parametrize(
    "overrides",
    [
        {"final_validation": False},
        {"outcome": "timeout"},
        {"ship_lost": True},
        {"elapsed": 4000.0},
        {"elapsed": math.inf},
        {"metrics_valid": False},
    ],
    ids=["not-final", "not-escaped", "ship-lost", "over-budget", "not-finite", "bad-metrics"],
)
def test_approval_does_not_waive_final_escape_or_budget_checks(overrides):
    record = make_trial("High loss", lost=20, qualified=False, **overrides)

    result = build([record], receipt(), min_solutions=1)

    assert result["solution_count"] == 0
    assert result["status"] == "pending"


@pytest.mark.parametrize(
    "minimum, expected_status",
    [(2, "ready"), (3, "insufficient")],
    ids=["meets-minimum", "below-minimum"],
)
def test_mixed_automatic_and_approved_routes_count_toward_the_minimum(
    minimum, expected_status
):
    records = [
        make_trial("Low loss", lost=0, elapsed=1000.0, heading=100.0),
        make_trial("High loss", lost=20, elapsed=900.0, qualified=False, heading=160.0),
    ]

    result = build(records, receipt(), min_solutions=minimum)
    by_name = {item["name"]: item for item in result["solutions"]}

    assert result["solution_count"] == 2
    assert result["status"] == expected_status
    assert by_name["Low loss"]["automatic_qualified"] is True
    assert by_name["Low loss"]["selection_basis"] == "automatic"
    assert by_name["High loss"]["automatic_qualified"] is False
    assert by_name["High loss"]["selection_basis"] == "user_approved"


@pytest.mark.parametrize(
    "approval",
    [
        None,
        {},
        receipt(fingerprint="fingerprint-old"),
        receipt(approved_by="model"),
    ],
    ids=["none", "empty", "stale", "model"],
)
def test_empty_or_mismatched_receipt_does_not_weaken_automatic_rule(approval):
    records = [
        make_trial("Low loss", lost=0, elapsed=1000.0, heading=100.0),
        make_trial("High loss", lost=20, elapsed=900.0, qualified=False, heading=160.0),
    ]

    result = build(records, approval, min_solutions=1)
    solution = result["solutions"][0]

    assert [item["name"] for item in result["solutions"]] == ["Low loss"]
    assert solution["automatic_qualified"] is True
    assert solution["selection_basis"] == "automatic"
    assert result["manual_approval"] is None
    assert result["governing_rule"] == result["automatic_qualification_rule"]
