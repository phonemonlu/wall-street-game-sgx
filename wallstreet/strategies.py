"""Bot strategies."""

import random
from collections.abc import Callable, Sequence
from typing import Protocol

from wallstreet.game import RoundRecord
from wallstreet.scoring import Card


class Strategy(Protocol):
    name: str

    def choose(self, seat: str, round_no: int, history: Sequence[RoundRecord]) -> Card: ...


class RandomStrategy:
    """X with probability ``p_x``; reproducible when ``seed`` is given."""

    name = "Random"

    def __init__(self, p_x: float = 0.5, seed: int | None = None) -> None:
        if not 0.0 <= p_x <= 1.0:
            raise ValueError("p_x must be between 0 and 1.")
        self.p_x = p_x
        self._rng = random.Random(seed)

    def choose(self, seat: str, round_no: int, history: Sequence[RoundRecord]) -> Card:
        return Card.X if self._rng.random() < self.p_x else Card.Y


STRATEGIES: dict[str, Callable[[], Strategy]] = {RandomStrategy.name: RandomStrategy}
