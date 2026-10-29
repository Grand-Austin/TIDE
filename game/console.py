"""Shared player commands and background engine execution for terminal frontends."""

import json
import queue
import threading

RATES = (1.0, 5.0, 20.0, 60.0, 0.0)


class Controls:
    def __init__(self, session, engine):
        self.session, self.engine = session, engine
        self.worker = None
        self.messages = queue.SimpleQueue()
        self.resume_pending = False
        self.closed = False

    def pause(self):
        self.resume_pending = False
        self.session.pause(manual=True)

    def resume(self):
        if self.session.outcome:
            raise ValueError("Mission has ended; use quit to save and exit")
        if self.worker and self.worker.is_alive():
            self.resume_pending = self.session.pause_signal.is_set()
            return "Waiting for the previous engine step to finish"
        self.resume_pending = False
        self.session.authorize_resume()
        self._start("resume")
        return "Sailing under the current target heading and speed"

    def _start(self, phase):
        def run():
            try:
                result = self.engine.run(phase=phase)
                self.session.record("director_result", result)
                if result.get("fallback"):
                    self.messages.put("Director step handled by local fallback")
            except Exception as error:  # noqa: BLE001 - Keep the UI available after an engine failure.
                self.session.pause()
                self.session.record("engine_failure", {"type": type(error).__name__})
                self.messages.put(
                    f"Engine failed ({type(error).__name__}); paused. Use retry or quit."
                )
            finally:
                self.session.save()

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def poll(self):
        if self.resume_pending and not self.closed and not self.worker.is_alive():
            self.resume_pending = False
            if not self.session.outcome:
                self.resume()
        messages = []
        while not self.messages.empty():
            messages.append(self.messages.get())
        while not self.session.notifications.empty():
            notice = self.session.notifications.get()
            reports = notice.get("reports", [])
            if reports:
                messages.append(f"New forecast: {len(reports)} sector report(s)")
            if notice.get("reason"):
                messages.append(notice["reason"])
            if notice.get("paused"):
                messages.append("Game paused for your decision")
        return messages

    def set_rate(self, value):
        if value not in RATES:
            raise ValueError("Rate must be 1, 5, 20, 60 or max")
        self.session.rate = value
        self.session.record("player_rate", {"rate": value})

    def change_rate(self, direction):
        index = RATES.index(self.session.rate)
        self.set_rate(RATES[max(0, min(len(RATES) - 1, index + direction))])

    def command(self, line):
        parts = line.strip().removeprefix("/").split()
        if not parts:
            return ""
        name, *args = parts
        if name == "quit" and not args:
            self.closed = True
            self.pause()
            return "Saved and exiting"
        if name == "pause" and not args:
            self.pause()
            return "Paused; edit the target heading and speed"
        if name in ("heading", "speed") and len(args) == 1:
            self.resume_pending = False
            self.session.set_order(
                **{("heading" if name == "heading" else "speed_knots"): float(args[0])}
            )
            return "Target updated; paused"
        if name == "rate" and len(args) == 1:
            self.set_rate(0.0 if args[0] == "max" else float(args[0]))
            return (
                f"Requested rate: {args[0]}x"
                if args[0] != "max"
                else "Requested rate: max"
            )
        if name == "resume" and not args:
            return self.resume()
        if name == "retry" and not args:
            if self.worker and self.worker.is_alive():
                return "Engine is still processing; pause remains available"
            self._start("decision")
            return "Retrying engine decision while paused"
        if name == "save" and not args:
            self.session.save()
            return "Session saved"
        if name in ("status", "reports") and not args:
            view = self.session.visible()
            return json.dumps(
                view if name == "status" else view["reports"],
                ensure_ascii=False,
                indent=2,
            )
        if name == "help" and not args:
            return "heading DEG | speed KNOTS | pause | resume | rate 1/5/20/60/max | status | reports | save | retry | quit"
        raise ValueError("Unknown command or arguments; use help")

    def close(self):
        self.closed = True
        self.pause()
        if self.worker:
            self.worker.join(timeout=2)
        self.session.save()
        if not self.worker or not self.worker.is_alive():
            self.engine.close()


def run_plain(controls):
    """Keep line-oriented operation available for pipes and non-curses terminals."""
    done = threading.Event()

    def updates():
        while not done.wait(0.1):
            for message in controls.poll():
                print(f"\n[Game update] {message}", flush=True)

    worker = threading.Thread(target=updates, daemon=True)
    worker.start()
    print(f"TIDE session: {controls.session.directory}")
    print(controls.command("help"))
    print(controls.command("status"))
    try:
        while not controls.closed:
            try:
                line = input("tide> ")
            except (EOFError, KeyboardInterrupt):
                break
            try:
                print(controls.command(line), flush=True)
            except ValueError as error:
                print(str(error), flush=True)
    finally:
        done.set()
        worker.join(timeout=0.5)
