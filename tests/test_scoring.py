import pytest

from wallstreet.errors import NotReady
from wallstreet.scoring import BONUS_ROUNDS, PAYOFF, SEATS, Card, multiplier, score_round

X, Y = Card.X, Card.Y


def choices(*cards: Card) -> dict[str, Card]:
    return dict(zip(SEATS, cards, strict=True))


@pytest.mark.parametrize(
    ("cards", "expected"),
    [
        ((Y, Y, Y, Y), (10, 10, 10, 10)),
        ((X, Y, Y, Y), (30, -10, -10, -10)),
        ((X, X, Y, Y), (20, 20, -20, -20)),
        ((X, X, X, Y), (10, 10, 10, -30)),
        ((X, X, X, X), (-10, -10, -10, -10)),
    ],
    ids=["0x", "1x", "2x", "3x", "4x"],
)
def test_every_n_x_case(cards, expected):
    assert score_round(choices(*cards), 1) == dict(zip(SEATS, expected, strict=True))


def test_payoff_table_is_explicit_and_complete():
    assert PAYOFF == {
        0: (None, 10),
        1: (30, -10),
        2: (20, -20),
        3: (10, -30),
        4: (-10, None),
    }


@pytest.mark.parametrize(("round_no", "factor"), [(1, 1), (4, 1), (5, 3), (6, 1), (8, 5), (9, 1), (10, 10)])
def test_multiplier(round_no, factor):
    assert multiplier(round_no) == factor


def test_bonus_rounds():
    assert BONUS_ROUNDS == {5: 3, 8: 5, 10: 10}


@pytest.mark.parametrize(("round_no", "factor"), [(5, 3), (8, 5), (10, 10)])
def test_multiplier_applied_to_payoffs(round_no, factor):
    result = score_round(choices(X, Y, Y, Y), round_no)
    assert result == {"P1": 30 * factor, "P2": -10 * factor, "P3": -10 * factor, "P4": -10 * factor}


def test_seat_order_does_not_matter():
    shuffled = {"P4": Y, "P2": X, "P1": Y, "P3": Y}
    assert score_round(shuffled, 1) == {"P1": -10, "P2": 30, "P3": -10, "P4": -10}


@pytest.mark.parametrize(
    "bad",
    [
        {"P1": X, "P2": Y, "P3": Y},
        {"P1": X, "P2": Y, "P3": Y, "P4": Y, "P5": X},
        {"P1": X, "P2": Y, "P3": Y, "P5": X},
        {},
    ],
    ids=["missing", "extra", "wrong", "empty"],
)
def test_requires_exactly_the_seats(bad):
    with pytest.raises(NotReady):
        score_round(bad, 1)
