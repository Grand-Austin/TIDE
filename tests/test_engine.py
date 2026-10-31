from game.engine import GameEngine
from game.director.models import ModelError
from test_session import make_session, vessel


class StopRules:
    budget_s = 100

    def evaluate(self, sample, consumed):
        return ("director_attention", []) if sample.time_s >= 0.2 else (None, [])

    def visible_reports(self, *args):
        return []


class FailingDirector:
    def __init__(self, after_advance=False, manual=False):
        self.after_advance, self.manual = after_advance, manual

    def run(self, session, phase):
        if self.after_advance:
            session.advance_segment(0.2)
        session.pause(manual=self.manual)
        raise ModelError("invalid tool response")


def test_local_mode_runs_without_director(vessel, tmp_path):
    s = make_session(vessel, tmp_path)
    s.rules = StopRules()
    s.authorize_resume()
    result = GameEngine(s).run("resume")
    assert result["engine"] == "local"
    assert s.state.tick == 4 and s.pause_signal.is_set()


def test_failure_before_advance_falls_back_to_current_step(vessel, tmp_path):
    s = make_session(vessel, tmp_path)
    s.rules = StopRules()
    s.authorize_resume()
    result = GameEngine(s, FailingDirector()).run("resume")
    assert result["fallback"]
    assert s.state.tick == 4
    assert "director_fallback" in s.log_path.read_text()


def test_failure_after_committed_advance_does_not_repeat_it(vessel, tmp_path):
    s = make_session(vessel, tmp_path)
    s.rules = StopRules()
    s.authorize_resume()
    result = GameEngine(s, FailingDirector(after_advance=True)).run("resume")
    assert result["fallback"] and s.state.tick == 4


def test_player_pause_prevents_fallback_resuming(vessel, tmp_path):
    s = make_session(vessel, tmp_path)
    s.rules = StopRules()
    s.authorize_resume()
    GameEngine(s, FailingDirector(manual=True)).run("resume")
    assert s.state.tick == 0 and s.pause_signal.is_set()


def test_decision_fallback_never_advances(vessel, tmp_path):
    s = make_session(vessel, tmp_path)
    GameEngine(s, FailingDirector()).run("decision")
    assert s.state.tick == 0


def test_revoked_generation_cannot_be_reauthorized(vessel, tmp_path):
    s = make_session(vessel, tmp_path)
    epoch = s.player_epoch
    s.pause(manual=True)
    assert not s.authorize_resume(expected_epoch=epoch)
    assert s.pause_signal.is_set()
