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


@pytest.mark.parametrize("others", ["".join(cards) for cards in product("XY", repeat=3)])
def test_lone_smart_bot_always_takes_the_most_points_for_itself(others: str):
    # X pays its player 20-30 more than Y whatever the others play, even when all three others
    # play Y (X: +30 for the bot, the group drops from +40 to 0) or all X (X: -10 instead of -30).
    assert smart("?" + others, "1") == "X"
    assert smart(others + "?", "4") == "X"
    for round_no in (5, 8, 10):  # bonus multipliers scale everything equally
        assert smart("?" + others, "1", round_no) == "X"


def test_lone_smart_bot_never_scores_below_anyone_in_its_group():
    for others in product(Card, repeat=3):
        table = dict(zip(SEATS[1:], others, strict=True))
        pick = smart_choices(table, ["P1"], 1)
        payoffs = score_round({**table, **pick}, 1)
        assert payoffs["P1"] == max(payoffs.values())


@pytest.mark.parametrize(
    ("table", "team", "expected"),
    [
        ("??YY", "12", "XX"),
        ("??XX", "12", "XX"),
        ("?Y??", "134", "XXX"),
        ("????", "1234", "XXXX"),  # an all-smart group never cooperates: -10 each, every round
    ],
)
def test_several_smart_bots_each_play_for_themselves(table: str, team: str, expected: str):
    assert smart(table, team) == expected


def test_no_smart_bot_could_score_more_by_switching_alone():
    for text in ("".join(cards) for cards in product("XY?", repeat=4)):
        team = [seat for seat, c in zip(SEATS, text, strict=True) if c == "?"]
        table = {seat: Card(c) for seat, c in zip(SEATS, text, strict=True) if c != "?"}
        pick = smart_choices(table, team, 1)
        current = score_round({**table, **pick}, 1)
        for seat in team:
            switched = {**table, **pick, seat: Y if pick[seat] is X else X}
            assert score_round(switched, 1)[seat] <= current[seat]


def test_smart_choices_without_smart_seats_is_empty():
    assert smart_choices(cards("XYXY"), [], 1) == {}


def test_smart_bot_placeholder_is_a_valid_card():
    assert SmartBot().choose("P1", 1, []) in (X, Y)


