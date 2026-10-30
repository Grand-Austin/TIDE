"""Replaceable algorithm interfaces; all physics values use SI units."""

from collections.abc import Callable
from typing import Any, Protocol


class MotionBackend(Protocol):
    algorithm_id: str
    environment: Any
    settings: Any
    vessel: Any

    def validate_command(self, command) -> None: ...
    def observe(self, state): ...
    def position(self, state) -> tuple[float, float]: ...
    def advance(
        self,
        state,
        command,
        duration_s: float,
        *,
        should_pause: Callable[[], bool] | None = None,
        on_step: Callable | None = None,
    ): ...


class CargoAlgorithm(Protocol):
    algorithm_id: str

    def initial_state(self) -> dict: ...
    def advance(self, state: dict, sample, dt_s: float) -> list[dict]:
        """Emit cargo_grade_increased whenever the summary grade rises."""
        ...
    def summary(self, state: dict) -> dict: ...
    def lose_all(self, state: dict, time_s: float) -> list[dict]:
        """Lose all remaining deck and hold cargo when the ship is lost."""
        ...
