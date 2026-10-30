from game.live import CHECKPOINT_GAP_S, Game


def test_cards_after_a_decision_wait_but_the_briefing_holds_none_back():
    game = Game("biscay-20251218-03")  # one game: session folders are named to the second
    try:
        with game.lock:
            game.session.pause(manual=True)
        game.session.reason = "cargo_grade_increased"
        view = game.session.visible()

        game.pending, game.checkpoints = None, [{"trigger": "briefing", "t": 0.0}]
        view["vessel"]["time_s"] = 30.0
        game._triggers(view)
        assert game.pending["trigger"] == "cargo"  # the briefing does not delay the first card

        game.pending, game.checkpoints = None, [{"trigger": "briefing", "t": 0.0}, {"trigger": "cargo", "t": 100.0}]
        view["vessel"]["time_s"] = 100.0 + CHECKPOINT_GAP_S - 1
        game._triggers(view)
        assert game.pending is None  # a burst of losses does not stack a second card

        view["vessel"]["time_s"] = 100.0 + CHECKPOINT_GAP_S
        game._triggers(view)
        assert game.pending["trigger"] == "cargo"
    finally:
        game.stopped = True
