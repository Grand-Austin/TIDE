"""Director orchestration with a deterministic, per-step local fallback."""

import threading
import uuid

from game.director.models import ModelError


class GameEngine:
    def __init__(self, session, director=None, *, initialization_error=None):
        self.session = session
        self.director = director
        self.initialization_error = initialization_error
        self.attention = (
            director.create_attention()
            if director and hasattr(director, "create_attention")
            else None
        )
        self.review_worker = None
        self.next_review_s = 0
        self.last_pending = set()
        if self.attention:
            session.publish_fixed_reports()
            session.report_mode = "director"
            session.review_callback = self._schedule_review

    def _schedule_review(self):
        s = self.session
        if self.review_worker and self.review_worker.is_alive():
            return
        context = s.director_context()
        pending = {r["id"] for r in context["pending_reports"]}
        if context["elapsed_s"] < self.next_review_s and pending == self.last_pending:
            return
        self.last_pending = pending
        self.next_review_s = context["elapsed_s"] + 300
        epoch = s.player_epoch
        review_id = f"{s.session_id}:attention:{s.state.tick}:{epoch}"

        def review():
            try:
                self.attention.run(s, context, epoch, review_id)
            except Exception as error:  # noqa: BLE001 - Provider and graph failures must enter the local fallback.
                s.record(
                    "attention_fallback",
                    {"review_id": review_id, "reason": type(error).__name__},
                )
                s.publish_fixed_reports(expected_epoch=epoch)

        self.review_worker = threading.Thread(target=review, daemon=True)
        self.review_worker.start()

    def _local(self, phase):
        s = self.session
        prior_mode, prior_callback = s.report_mode, s.review_callback
        s.report_mode, s.review_callback = "fixed", None
        s.publish_fixed_reports()
        try:
            if (
                phase == "resume"
                and not s.outcome
                and s.resume_authorized
                and not s.pause_signal.is_set()
            ):
                try:
                    s.tool("advance", {}, "local-" + uuid.uuid4().hex, s.revision)
                except ValueError:
                    if not s.pause_signal.is_set():
                        raise
            tool = "finish_mission" if s.outcome else "request_decision"
            result = s.tool(tool, {}, "local-" + uuid.uuid4().hex, s.revision)
            return {
                "engine": "local",
                "reply": "Mission ended" if s.outcome else "Player decision required",
                "outcome": result["outcome"],
                "stopping_reason": result["stopping_reason"],
            }
        finally:
            s.report_mode, s.review_callback = prior_mode, prior_callback

    def run(self, phase="decision"):
        if phase not in ("resume", "decision"):
            raise ValueError("Unknown engine phase")
        s = self.session
        before_tick, epoch = s.state.tick, s.player_epoch
        if self.director is None:
            if self.initialization_error:
                s.record(
                    "director_fallback",
                    {"phase": phase, "reason": self.initialization_error},
                )
            return self._local(phase)
        try:
            return dict(self.director.run(s, phase=phase), engine="director")
        except Exception as error:  # noqa: BLE001 - Provider and graph failures must enter the local fallback.
            reason = (
                str(error) if isinstance(error, ModelError) else type(error).__name__
            )
            s.record(
                "director_fallback",
                {
                    "phase": phase,
                    "reason": reason,
                    "from_tick": before_tick,
                    "current_tick": s.state.tick,
                    "player_epoch": s.player_epoch,
                },
            )
            # Director failure pauses locally. Restore authorization only if no physical step
            # happened and no player cancellation/order superseded this run.
            with s.lock:
                if (
                    phase == "resume"
                    and s.state.tick == before_tick
                    and s.player_epoch == epoch
                    and not s.outcome
                ):
                    s.authorize_resume(expected_epoch=epoch)
            result = self._local(phase)
            return dict(result, fallback=True, fallback_reason=reason)

    def close(self):
        if self.review_worker:
            self.review_worker.join(timeout=1)
        if self.attention and (
            not self.review_worker or not self.review_worker.is_alive()
        ):
            self.attention.close()
        if self.director:
            self.director.close()
