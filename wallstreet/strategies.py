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
    """Sees every other seat's final card and plays the best reply (see ``smart_choices``).

    ``choose`` only fills the seat when a round opens, so the seat counts as submitted and never
    blocks a reveal. The room replaces that placeholder with ``smart_choices`` at reveal time.
    """

    name = "Smart"

    def choose(self, seat: str, round_no: int, history: Sequence[RoundRecord]) -> Card:
        return Card.Y


def smart_choices(
    choices: Mapping[str, Card],
    smart_seats: Collection[str],
    round_no: int,
    totals: Mapping[str, int] | None = None,
) -> dict[str, Card]:
    """Best cards for ``smart_seats`` given everyone else's cards in ``choices``.

    The smart seats act as one team: highest group total first, then the highest combined score
    of the smart seats. With the PAYOFF table a lone smart bot therefore plays X, unless the other
    three all chose the same card, then Y (it gives up 20 points to save the group 40).

    When the team must split its cards, the worse card goes to the bot that would otherwise end
    up richest (``totals`` are the scores before this round), so the bots take turns. Remaining
    ties go to the first combination in seat order, X before Y, so the result is deterministic.
    """
    team = [seat for seat in SEATS if seat in smart_seats]
    others = {seat: Card(card) for seat, card in choices.items() if seat not in smart_seats}
    before = totals or {}

    def rank(pick: dict[str, Card]) -> tuple[int, int, int]:
        payoffs = score_round({**others, **pick}, round_no)
        poorest = min((before.get(seat, 0) + payoffs[seat] for seat in team), default=0)
        return sum(payoffs.values()), sum(payoffs[seat] for seat in team), poorest

    combos = (dict(zip(team, cards, strict=True)) for cards in product(Card, repeat=len(team)))
    return max(combos, key=rank)


STRATEGIES: dict[str, Callable[[], Strategy]] = {cls.name: cls for cls in (RandomStrategy, SmartBot)}
