import json
import threading

import pytest

from game.director.attention import AttentionDirector
from game.director.models import load_profile, ModelError
from game.engine import GameEngine
from test_session import make_session, vessel


class ForecastRules:
    budget_s = 1000

    def evaluate(self, sample, consumed):
        return None, []

    def visible_reports(self, t, *position):
        return (
            [
                {
                    "id": "r1",
                    "issued_s": 0.1,
                    "valid_to_s": 1000,
                    "label": "game_simulated_forecast",
                }
            ]
            if t >= 0.1
            else []
        )


def setup(vessel, path):
    s = make_session(vessel, path)
    s.rules = ForecastRules()
    s.report_mode = "director"
    s.authorize_resume()
    s.advance_segment(0.1)
    return s


def decision(publish=True, pause=False):
    return {
        "report_ids": ["r1"] if publish else [],
        "pause": pause,
        "reason": "New weather evidence",
        "evidence_ids": ["r1"] if pause else [],
    }


def response(d):
    return {
        "tool_calls": [
            {"function": {"name": "manage_attention", "arguments": json.dumps(d)}}
        ]
    }


def test_graph_can_publish_without_pausing(vessel, tmp_path):
    s = setup(vessel, tmp_path / "session")
    p, _ = load_profile("models.json")
    seen = []

    def transport(payload):
        seen.append(payload)
        return response(decision())

    d = AttentionDirector(p, transport, tmp_path / "attention.sqlite")
    result = d.run(s, s.director_context(), s.player_epoch, "review-1")
    assert result == {"published": ["r1"], "paused": False}
    assert not s.pause_signal.is_set()
    assert s.visible()["reports"][0]["id"] == "r1"
    assert seen[0]["tool_choice"]["function"]["name"] == "manage_attention"
    assert "pending_reports" in seen[0]["messages"][1]["content"]
    d.close()


def test_attention_can_defer_or_request_pause_and_is_idempotent(vessel, tmp_path):
    s = setup(vessel, tmp_path)
    epoch = s.player_epoch
    s.apply_attention(
        decision(False), review_id="a", expected_epoch=epoch, observed_s=0.1
    )
    assert s.visible()["reports"] == []
    result = s.apply_attention(
        decision(True, True), review_id="b", expected_epoch=epoch, observed_s=0.1
    )
    assert result["paused"] and s.pause_signal.is_set()
    assert s.reason == "director_attention"
    assert s.apply_attention(
        decision(), review_id="b", expected_epoch=epoch, observed_s=0.1
    ) == {"duplicate": True}


def test_unknown_future_or_cancelled_evidence_is_rejected(vessel, tmp_path):
    s = setup(vessel, tmp_path)
    epoch = s.player_epoch
    invalid = dict(decision(), report_ids=["future-report"])
    with pytest.raises(ValueError, match="available"):
        s.apply_attention(invalid, review_id="a", expected_epoch=epoch, observed_s=0.1)
    s.pause(manual=True)
    s.authorize_resume()
    with pytest.raises(ValueError, match="superseded"):
        s.apply_attention(
            decision(), review_id="b", expected_epoch=epoch, observed_s=0.1
        )


def test_background_review_does_not_stop_physics_or_spawn_duplicates(vessel, tmp_path):
    s = setup(vessel, tmp_path / "s")
    entered, release = threading.Event(), threading.Event()

    class Attention:
        calls = 0

        def run(self, session, context, epoch, review_id):
            self.calls += 1
            entered.set()
            release.wait(2)
            session.apply_attention(
                decision(False),
                review_id=review_id,
                expected_epoch=epoch,
                observed_s=context["elapsed_s"],
            )

        def close(self):
            pass

    attention = Attention()

    class Director:
        def create_attention(self):
            return attention

        def close(self):
            pass

    e = GameEngine(s, Director())
    e._schedule_review()
    assert entered.wait(1)
    e._schedule_review()
    s.advance_segment(0.2)
    assert s.state.tick == 6 and not s.pause_signal.is_set() and attention.calls == 1
    release.set()
    e.review_worker.join(2)
    e.close()


def test_attention_failure_falls_back_to_fixed_delivery_without_pause(vessel, tmp_path):
    s = setup(vessel, tmp_path / "s")

    class Attention:
        def run(self, *args):
            raise ModelError("offline")

        def close(self):
            pass

    class Director:
        def create_attention(self):
            return Attention()

        def close(self):
            pass

    e = GameEngine(s, Director())
    s.delivered_reports.clear()
    e._schedule_review()
    e.review_worker.join(2)
    assert s.visible()["reports"][0]["id"] == "r1"
    assert not s.pause_signal.is_set()
    assert "attention_fallback" in s.log_path.read_text()
    e.close()


def test_important_issue_is_delivery_not_automatic_weather_pause(vessel, tmp_path):
    from game.rules.mission import MissionRules
    from copy import deepcopy

    gameplay = {
        "version": 1,
        "regions": [{"time_s": 0, "polygon": [[3, 39], [4, 39], [4, 41], [3, 41]]}],
        "reports": [
            {
                "id": "new",
                "sector": [1, 1],
                "issued_s": 0.1,
                "valid_to_s": 100,
                "important": True,
            }
        ],
    }
    for mode in ("fixed", "director"):
        s = make_session(vessel, tmp_path / mode)
        s.rules = MissionRules(deepcopy(gameplay), 100, [3, 4, 39, 41])
        s.report_mode = mode
        s.authorize_resume()
        s.advance_segment(0.2)
        assert not s.pause_signal.is_set()
        assert s.state.tick == 4
        assert bool(s.visible()["reports"]) == (mode == "fixed")
        if mode == "fixed":
            assert s.notifications.get()["reports"][0]["id"] == "new"
        else:
            assert s.director_context()["pending_reports"][0]["id"] == "new"
