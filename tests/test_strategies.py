import pytest

from wallstreet.game import RoundRecord
from wallstreet.scoring import SEATS, Card, score_round
from wallstreet.strategies import STRATEGIES, RandomStrategy, Strategy

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
    assert set(STRATEGIES) == {"Random"}  # the only bot; players are the other kind of seat
    for name, factory in STRATEGIES.items():
        bot: Strategy = factory()
        assert bot.name == name
        assert bot.choose("P1", 1, []) in (X, Y)
