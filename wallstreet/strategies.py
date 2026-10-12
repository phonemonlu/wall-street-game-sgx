"""Bot strategies."""

import random
from collections.abc import Callable, Collection, Mapping, Sequence
from itertools import product
from typing import Protocol

from wallstreet.game import RoundRecord
from wallstreet.scoring import SEATS, Card, score_round


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


class SmartBot:
    """Sees every other seat's final card and plays the card best for itself (see ``smart_choices``).

    ``choose`` only fills the seat when a round opens, so the seat counts as submitted and never
    blocks a reveal. The room replaces that placeholder with ``smart_choices`` at reveal time.
    """

    name = "Smart"

    def choose(self, seat: str, round_no: int, history: Sequence[RoundRecord]) -> Card:
        return Card.Y


def smart_choices(choices: Mapping[str, Card], smart_seats: Collection[str], round_no: int) -> dict[str, Card]:
    """Selfish cards for ``smart_seats`` given everyone else's final cards in ``choices``.

    Each smart bot plays for its own score only, never for the group's: the result is a card
    combination in which no smart bot could score more by switching its own card (other smart bots'
    cards included). Ties go to the first such combination in seat order, X before Y.

    With the PAYOFF table, X always pays its player 20-30 more than Y, whatever the others do, so
    every smart bot plays X every round. (Should a payoff table leave no such combination, the bots
    play the first combination: X.)
    """
    team = [seat for seat in SEATS if seat in smart_seats]
    others = {seat: Card(card) for seat, card in choices.items() if seat not in smart_seats}

    def nobody_gains_by_switching(pick: dict[str, Card]) -> bool:
        current = score_round({**others, **pick}, round_no)
        for seat in team:
            switched = {**others, **pick, seat: Card.Y if pick[seat] is Card.X else Card.X}
            if score_round(switched, round_no)[seat] > current[seat]:
                return False
        return True

    combos = [dict(zip(team, cards, strict=True)) for cards in product(Card, repeat=len(team))]
    return next((pick for pick in combos if nobody_gains_by_switching(pick)), combos[0])


STRATEGIES: dict[str, Callable[[], Strategy]] = {cls.name: cls for cls in (RandomStrategy, SmartBot)}
