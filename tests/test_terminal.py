import curses
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_session import make_session
from test_session import vessel as vessel  # noqa: PLC0414 - Re-export the shared pytest fixture.

from game.__main__ import main
from game.console import Controls
from game.engine import GameEngine
from game.terminal import Input, Screen, forecast_lines, instruments


class Window:
    def __init__(self, keys=(), height=24, width=80):
        self.height, self.width = height, width
        self.keys = iter(keys)
        self.erase()

    def getmaxyx(self):
        return self.height, self.width

    def erase(self):
        self.rows = [" " * self.width for _ in range(self.height)]

    def addnstr(self, row, col, text, length, style):
        text = text[:length]
        self.rows[row] = self.rows[row][:col] + text + self.rows[row][col + len(text) :]

    def move(self, row, col):
        assert 0 <= row < self.height and 0 <= col < self.width

    def keypad(self, enabled):
        pass

    def timeout(self, milliseconds):
        pass

    def refresh(self):
        pass

    def clear(self):
        self.erase()

    def get_wch(self):
        return next(self.keys)


@pytest.fixture
def screen(vessel, tmp_path, monkeypatch):
    session = make_session(vessel, tmp_path)
    controls = Controls(session, GameEngine(session))
    monkeypatch.setattr(curses, "curs_set", lambda *args: None)
    monkeypatch.setattr(curses, "set_escdelay", lambda *args: None)
    return Screen(Window(), controls)


def test_instruments_show_units_rates_and_cargo(screen):
    view = screen.controls.session.visible()
    view["vessel"]["hs_m"] = 3.56
    view["vessel"]["speed_through_water_mps"] = 1852 / 3600
    view["vessel"]["speed_over_ground_mps"] = 1852 / 1800
    view["cargo"].update(loss_fraction=0.01, lost_teu=12, grade=2)
    text = "\n".join(instruments(view))
    for expected in (
        "UTC",
        "20x",
        "actual 0.0x",
        "LAT 40.000000",
        "LON 3.500000",
        "water 1.00 kn",
        "ground 2.00 kn",
        "Hs 3.56 m",
        "99.00% TEU retained",
        "grade 2",
        "lost 12/1200 TEU",
    ):
        assert expected in text


def test_forecasts_prioritize_current_sector_and_fit_minimum_screen(screen):
    view = screen.controls.session.visible()
    report = json.loads(Path("scenarios/sole-20261022-21/gameplay.json").read_text())[
        "reports"
    ][0]
    lon, lat = view["vessel"]["lon"], view["vessel"]["lat"]
    remote = dict(report, id="remote", sector_bbox=[lon - 2, lon - 1.5, lat - 1, lat - 0.5])
    local = dict(report, id="local", sector_bbox=[lon - 0.5, lon + 0.5, lat - 0.5, lat + 0.5])
    view["reports"] = [remote, local]
    lines = forecast_lines(view, 77)
    issued = report["issued_at"][11:16]
    valid_to = report["valid_to"][11:16]
    assert "[HERE]" in lines[0]
    assert f"{issued}Z" in lines[0]
    assert "Valid" in lines[1] and f"{valid_to}Z" in lines[1]
    assert "Hs" in lines[2] and "wind" in lines[2]
    assert all(len(line) <= 77 for line in lines)
    screen.draw(view)
    assert "Hs" in screen.window.rows[16]
    assert "FORECASTS" in screen.window.rows[12]
    assert "j slower" in screen.window.rows[23]


def test_prefilled_input_and_cursor_editing():
    field = Input.prefilled("155")
    field.edit("2")
    assert field.text == "2"
    field.edit("0")
    field.edit(curses.KEY_LEFT)
    field.edit("9")
    assert field.text == "290"
    field.edit(curses.KEY_BACKSPACE)
    assert field.text == "20"
    field.edit("\x15")
    assert field.text == "" and field.cursor == 0


def test_full_input_flow_keeps_physics_and_targets_separate(screen, monkeypatch):
    s = screen.controls.session
    initial = s.state.x.copy()

    def resume():
        s.authorize_resume()
        return "Sailing"

    monkeypatch.setattr(screen.controls, "resume", resume)
    screen.window.keys = iter("155\t6\np/quit\n")
    screen.run()
    assert s.order.heading_deg == 155
    assert s.order.speed_mps == pytest.approx(6 * 1852 / 3600)
    assert s.pause_signal.is_set() and screen.controls.closed
    assert (s.state.x == initial).all()


def test_escape_keeps_pause_and_unchanged_targets_keep_full_precision(
    screen, monkeypatch
):
    s = screen.controls.session
    s.set_order(155.123456789, 5.83151234567)
    original = s.order
    screen.open_helm(s.visible())
    screen.handle("\x1b", s.visible())
    assert s.pause_signal.is_set() and screen.mode is None
    screen.handle("p", s.visible())
    monkeypatch.setattr(screen.controls, "resume", lambda: "Sailing")
    screen.apply_helm()
    assert s.order == original


@pytest.mark.parametrize(
    "heading,speed",
    [("360", "6"), ("nan", "6"), ("155", "-1"), ("155", "inf"), ("", "6")],
)
def test_invalid_helm_cannot_resume_or_partially_change_order(screen, heading, speed):
    s = screen.controls.session
    original = s.order
    screen.open_helm(s.visible())
    screen.fields[0].text, screen.fields[1].text = heading, speed
    with pytest.raises(ValueError):
        screen.apply_helm()
    assert s.pause_signal.is_set()
    assert s.order == original and screen.mode == "helm"


def test_command_input_does_not_pause_and_hotkeys_are_text_while_typing(screen):
    s = screen.controls.session
    s.authorize_resume()
    screen.handle("/", s.visible())
    for key in "jkp":
        screen.handle(key, s.visible())
    assert screen.command_input.text == "jkp"
    assert not s.pause_signal.is_set() and s.rate == 20
    screen.handle("\x1b", s.visible())
    screen.handle("j", s.visible())
    assert s.rate == 5
    screen.handle("k", s.visible())
    assert s.rate == 20
    screen.handle("p", s.visible())
    assert s.pause_signal.is_set() and screen.mode == "helm"


def test_rate_boundaries_and_invalid_values(screen):
    controls = screen.controls
    controls.set_rate(1)
    controls.change_rate(-1)
    assert controls.session.rate == 1
    controls.set_rate(0)
    controls.change_rate(1)
    assert controls.session.rate == 0
    controls.change_rate(-1)
    assert controls.session.rate == 60
    for command in ("rate 2", "rate nan", "rate inf"):
        with pytest.raises(ValueError):
            controls.command(command)


def test_pending_resume_waits_for_old_engine_and_player_pause_cancels_it(screen):
    controls, s = screen.controls, screen.controls.session
    release = threading.Event()
    calls = []

    class SlowEngine:
        def run(self, phase):
            calls.append(phase)
            assert release.wait(2)
            s.pause()
            return {}

    controls.engine = SlowEngine()
    try:
        controls.resume()
        controls.pause()
        controls.resume()
        assert controls.resume_pending and s.pause_signal.is_set()
        controls.pause()
        release.set()
        controls.worker.join(2)
        controls.poll()
        assert calls == ["resume"]
        assert s.pause_signal.is_set() and not controls.resume_pending
    finally:
        release.set()
        controls.worker.join(2)


def test_small_terminal_and_resize_keep_controls_available(screen):
    screen.window.height, screen.window.width = 10, 40
    screen.open_helm(screen.controls.session.visible())
    screen.draw(screen.controls.session.visible())
    assert "resize" in screen.window.rows[2]
    screen.handle("/", screen.controls.session.visible())
    for key in "quit\n":
        screen.handle(key, screen.controls.session.visible())
    assert screen.controls.closed and screen.controls.session.pause_signal.is_set()


def test_pending_resume_that_immediately_pauses_reopens_helm(screen, monkeypatch):
    controls = screen.controls
    controls.resume_pending = True
    alive = iter((True, False))
    controls.worker = SimpleNamespace(is_alive=lambda: next(alive))

    def resume():
        controls.resume_pending = False
        controls.session.pause()
        return "Paused again"

    monkeypatch.setattr(controls, "resume", resume)
    reads = 0

    def key():
        nonlocal reads
        reads += 1
        if reads == 1:
            assert controls.resume_pending and screen.mode is None
            raise curses.error()
        assert not controls.resume_pending and screen.mode == "helm"
        return "\x03"

    monkeypatch.setattr(screen.window, "get_wch", key)
    screen.run()
    assert reads == 2


def test_shrinking_terminal_pauses_and_prevents_blind_resumption(screen):
    s = screen.controls.session
    s.authorize_resume()
    screen.window.height, screen.window.width = 10, 40
    screen.window.keys = iter("/resume\np/quit\n")
    screen.run()
    assert s.pause_signal.is_set() and screen.controls.worker is None


def test_line_json_mode_is_removed(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["game", "--plain"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "unrecognized arguments: --plain" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments,message",
    [
        (["--engine", "director"], "invalid choice: 'director'"),
        (["--models", "models.json"], "unrecognized arguments: --models"),
        (["--keys", "keys.json"], "unrecognized arguments: --keys"),
    ],
)
def test_director_options_are_disabled(monkeypatch, capsys, arguments, message):
    monkeypatch.setattr(sys, "argv", ["game", *arguments])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert message in capsys.readouterr().err


def test_interactive_play_requires_a_terminal(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["game"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "requires a terminal" in capsys.readouterr().err


def test_quit_saves_and_never_starts_a_model(screen):
    controls = screen.controls
    controls.command("/quit")
    controls.close()
    assert controls.worker is None
    assert controls.session.pause_signal.is_set()
    assert controls.session.snapshot_path.exists()
