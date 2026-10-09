"""Payoff rules (pure functions)."""

from collections.abc import Mapping
from enum import StrEnum

from wallstreet.errors import NotReady

SEATS: tuple[str, ...] = ("P1", "P2", "P3", "P4")


class Card(StrEnum):
    X = "X"
    Y = "Y"


# n_x -> (points for each X player, points for each Y player); None = nobody holds that card.
PAYOFF: dict[int, tuple[int | None, int | None]] = {
    0: (None, +10),
    1: (+30, -10),
    2: (+20, -20),
    3: (+10, -30),
    4: (-10, None),
}

BONUS_ROUNDS: dict[int, int] = {5: 3, 8: 5, 10: 10}


def player_label(group_id: int, seat: str) -> str:
    """A seat as players see it, numbered across groups: Group 1 is P1-P4, Group 2 is P5-P8, ...

    Inside a group the rules and bots keep using ``SEATS`` (P1-P4); this is for display only.
    """
    return f"P{(group_id - 1) * len(SEATS) + SEATS.index(seat) + 1}"


def multiplier(round_no: int) -> int:
    return BONUS_ROUNDS.get(round_no, 1)


def score_round(choices: Mapping[str, Card], round_no: int) -> dict[str, int]:
    """Return each seat's points for one round, with the round's multiplier applied.

    Raises NotReady unless ``choices`` has exactly the keys in SEATS.
    """
    if set(choices) != set(SEATS):
        missing = [seat for seat in SEATS if seat not in choices]
        extra = sorted(set(choices) - set(SEATS))
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unknown {', '.join(extra)}")
        raise NotReady(f"Need a card from every seat ({'; '.join(details)}).")

    cards = {seat: Card(choices[seat]) for seat in SEATS}
    n_x = sum(card is Card.X for card in cards.values())
    x_points, y_points = PAYOFF[n_x]
    factor = multiplier(round_no)
    return {
        seat: factor * (x_points if card is Card.X else y_points)  # type: ignore[operator]
        for seat, card in cards.items()
    }
