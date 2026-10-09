import pytest

from wallstreet.game import RoundRecord
from wallstreet.scoring import SEATS, Card, score_round
from wallstreet.strategies import (
    STRATEGIES,
    AlwaysX,
    AlwaysY,
    BonusDefector,
    GrimTrigger,
    RandomStrategy,
    Strategy,
    TitForTat,
)

X, Y = Card.X, Card.Y


def record(round_no: int, cards: str) -> RoundRecord:
    choices = {seat: Card(card) for seat, card in zip(SEATS, cards, strict=True)}
    return RoundRecord(round_no, choices, score_round(choices, round_no), 1)


def test_always_y_and_always_x():
    history = [record(1, "XXXX")]
    for round_no in (1, 2, 5, 10):
        assert AlwaysY().choose("P1", round_no, history) is Y
        assert AlwaysX().choose("P1", round_no, history) is X


def test_tit_for_tat_opens_with_y():
    assert TitForTat().choose("P1", 1, []) is Y


def test_tit_for_tat_reacts_to_others_in_last_round_only():
    bot = TitForTat()
    assert bot.choose("P1", 2, [record(1, "YXYY")]) is X
    assert bot.choose("P1", 2, [record(1, "XYYY")]) is Y  # its own X does not count
    assert bot.choose("P1", 3, [record(1, "YXYY"), record(2, "YYYY")]) is Y
    assert bot.choose("P1", 3, [record(1, "YYYY"), record(2, "YYYX")]) is X


def test_grim_trigger_never_forgives():
    bot = GrimTrigger()
    assert bot.choose("P2", 1, []) is Y
    assert bot.choose("P2", 2, [record(1, "YXYY")]) is Y  # own X does not trigger
    history = [record(1, "YYYY"), record(2, "YYXY"), record(3, "YYYY"), record(4, "YYYY")]
    assert bot.choose("P2", 5, history) is X
    assert bot.choose("P2", 5, history[:1]) is Y


def test_bonus_defector():
    bot = BonusDefector()
    assert [bot.choose("P1", r, []) for r in range(1, 11)] == [Y, Y, Y, Y, X, Y, Y, X, Y, X]


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
    assert set(STRATEGIES) == {"Always Y", "Always X", "Tit for Tat", "Grim Trigger", "Bonus Defector", "Random"}
    for name, factory in STRATEGIES.items():
        bot: Strategy = factory()
        assert bot.name == name
        assert bot.choose("P1", 1, []) in (X, Y)
