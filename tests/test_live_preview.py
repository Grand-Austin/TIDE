from game.live import Game


def test_preview_runs_the_ship_ahead_without_moving_it():
    game = Game("sole-20261022-21")
    try:
        with game.lock:
            game.session.pause(manual=True)
            before = game.session.last_sample["time_s"]
        heading = (game.session.order.heading_deg + 90) % 360
        p = game.preview(heading, 10.0, horizon_s=60.0)
        assert p["stop"] == "duration_reached"
        assert len(p["track"]) == 6  # one point per 10 s: [lon, lat, t]
        assert p["track"][-1][2] == before + 60
        assert p["track"][0] != p["track"][-1]
        assert game.session.last_sample["time_s"] == before  # the session itself did not advance
    finally:
        game.stopped = True
