"""Bot strategies."""

import random
from collections.abc import Callable, Sequence
from typing import Protocol

from wallstreet.game import RoundRecord
from wallstreet.scoring import BONUS_ROUNDS, Card


class Strategy(Protocol):
    name: str

    def choose(self, seat: str, round_no: int, history: Sequence[RoundRecord]) -> Card: ...


def _others_played_x(seat: str, record: RoundRecord) -> bool:
    return any(card is Card.X for other, card in record.choices.items() if other != seat)


class AlwaysY:
    name = "Always Y"

    def choose(self, seat: str, round_no: int, history: Sequence[RoundRecord]) -> Card:
        return Card.Y


class AlwaysX:
    name = "Always X"

    def choose(self, seat: str, round_no: int, history: Sequence[RoundRecord]) -> Card:
        return Card.X


class TitForTat:
    """Y in round 1; afterwards X iff any other seat played X in the last revealed round."""

    name = "Tit for Tat"

    def choose(self, seat: str, round_no: int, history: Sequence[RoundRecord]) -> Card:
        if history and _others_played_x(seat, history[-1]):
            return Card.X
        return Card.Y


class GrimTrigger:
    """Y until any other seat ever plays X, then X forever."""

    name = "Grim Trigger"

    def choose(self, seat: str, round_no: int, history: Sequence[RoundRecord]) -> Card:
        if any(_others_played_x(seat, record) for record in history):
            return Card.X
        return Card.Y


class BonusDefector:
    """X in bonus rounds, Y otherwise."""

    name = "Bonus Defector"

    def choose(self, seat: str, round_no: int, history: Sequence[RoundRecord]) -> Card:
        return Card.X if round_no in BONUS_ROUNDS else Card.Y


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


STRATEGIES: dict[str, Callable[[], Strategy]] = {
    cls.name: cls
    for cls in (AlwaysY, AlwaysX, TitForTat, GrimTrigger, BonusDefector, RandomStrategy)
}
