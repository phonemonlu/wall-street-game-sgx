from itertools import product

import pytest

from wallstreet.game import RoundRecord
from wallstreet.scoring import SEATS, Card, score_round
from wallstreet.strategies import STRATEGIES, RandomStrategy, SmartBot, Strategy, smart_choices

X, Y = Card.X, Card.Y


def record(round_no: int, cards: str) -> RoundRecord:
    choices = {seat: Card(card) for seat, card in zip(SEATS, cards, strict=True)}
    return RoundRecord(round_no, choices, score_round(choices, round_no), 1)


def test_random_strategy_is_deterministic_with_seed():
    def run(seed: int) -> list[Card]:
        bot = RandomStrategy(seed=seed)
        return [bot.choose("P1", r, []) for r in range(1, 51)]

    assert run(42) == run(42)
    assert run(42) != run(43)
    assert set(run(42)) == {X, Y}


@pytest.mark.parametrize(("p_x", "card"), [(0.0, Y), (1.0, X)])
def test_random_strategy_extremes(p_x, card):
    bot = RandomStrategy(p_x=p_x, seed=1)
    assert {bot.choose("P1", r, []) for r in range(1, 21)} == {card}


@pytest.mark.parametrize("p_x", [-0.1, 1.5])
def test_random_strategy_rejects_bad_probability(p_x):
    with pytest.raises(ValueError):
        RandomStrategy(p_x=p_x)


def test_registry_builds_named_strategies():
    assert set(STRATEGIES) == {"Random", "Smart"}
    for name, factory in STRATEGIES.items():
        bot: Strategy = factory()
        assert bot.name == name
        assert bot.choose("P1", 1, []) in (X, Y)


def cards(text: str) -> dict[str, Card]:
    return {seat: Card(card) for seat, card in zip(SEATS, text, strict=True)}


def smart(text: str, team: str, round_no: int = 1) -> str:
    """``text`` has a card for every seat ("?" for smart seats); returns the smart seats' cards in seat order."""
    choices = {seat: Card(c) for seat, c in zip(SEATS, text, strict=True) if c != "?"}
    picked = smart_choices(choices, [f"P{i}" for i in team], round_no)
    return "".join(str(picked[seat]) for seat in sorted(picked))


@pytest.mark.parametrize(
    ("others", "expected"),
    [
        ("YYY", "Y"),  # all Y: Y keeps the group at +40 (X: +30 for itself, but group 0)
        ("XYY", "X"),  # mixed: the group total is 0 either way, so take the points
        ("YXY", "X"),
        ("XXY", "X"),
        ("XXX", "Y"),  # all X: Y saves the group 40 (group 0 instead of -40)
    ],
)
def test_lone_smart_bot_protects_group_then_maximises_own_score(others: str, expected: str):
    assert smart("?" + others, "1") == expected
    assert smart(others + "?", "4") == expected
    for round_no in (5, 8, 10):  # bonus multipliers scale everything equally
        assert smart("?" + others, "1", round_no) == expected


def test_lone_smart_bot_never_loses_to_a_human_in_its_group():
    """Over every possible table, the smart bot's score is at least that of each human, unless it
    gave up points to protect the group (others unanimous)."""
    for others in product(Card, repeat=3):
        table = dict(zip(SEATS[1:], others, strict=True))
        pick = smart_choices(table, ["P1"], 1)
        payoffs = score_round({**table, **pick}, 1)
        if len(set(others)) > 1:
            assert payoffs["P1"] == max(payoffs.values())


@pytest.mark.parametrize(
    ("table", "team", "expected"),
    [
        ("??YY", "12", "YY"),  # humans cooperate: the team keeps the +40 table
        ("??XY", "12", "XX"),  # group 0 whatever the team does: take 20 + 20
        ("??XX", "12", "XY"),  # one X, one Y keeps the group at 0 (not -40); ties go to the lowest seat
        ("?Y??", "134", "YYY"),
        ("?X??", "134", "XXY"),
        ("????", "1234", "YYYY"),  # an all-smart group cooperates every round
    ],
)
def test_smart_bots_act_as_a_team(table: str, team: str, expected: str):
    assert smart(table, team) == expected


def test_smart_choices_without_smart_seats_is_empty():
    assert smart_choices(cards("XYXY"), [], 1) == {}


def test_smart_bot_placeholder_is_a_valid_card():
    assert SmartBot().choose("P1", 1, []) in (X, Y)


