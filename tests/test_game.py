from collections.abc import Mapping

import pytest

from wallstreet.errors import GameError, InvalidTransition, NotReady, UnknownSeat
from wallstreet.game import Game, Phase, RoundRecord
from wallstreet.scoring import SEATS, Card

X, Y = Card.X, Card.Y

# A real game, one string per round with the cards of P1..P4.
REAL_GAME = ["XYXX", "YXYY", "YYXX", "XXXY", "XYYY", "YXXY", "XYYY", "YXXX", "XYYY", "XYYY"]
REAL_PAYOFFS = [
    (10, -30, 10, 10),
    (-10, 30, -10, -10),
    (-20, -20, 20, 20),
    (10, 10, 10, -30),
    (90, -30, -30, -30),  # x3
    (-20, 20, 20, -20),
    (30, -10, -10, -10),
    (-150, 50, 50, 50),  # x5
    (30, -10, -10, -10),
    (300, -100, -100, -100),  # x10
]


def play_round(game: Game, cards: str) -> RoundRecord:
    for seat, card in zip(SEATS, cards, strict=True):
        game.submit(seat, Card(card))
    return game.reveal()


def started(rounds: int = 10) -> Game:
    game = Game(rounds)
    game.start()
    return game


def test_replay_real_game():
    game = started()
    for round_no, (cards, payoffs) in enumerate(zip(REAL_GAME, REAL_PAYOFFS, strict=True), start=1):
        assert game.round_no == round_no
        record = play_round(game, cards)
        assert record.round_no == round_no
        assert dict(record.payoffs) == dict(zip(SEATS, payoffs, strict=True))
        assert record.group_total == 0
        if round_no < 10:
            game.next_round()

    assert game.phase is Phase.OVER
    assert game.totals() == {"P1": 270, "P2": -90, "P3": -50, "P4": -130}
    assert game.group_total() == 0
    assert [r.multiplier for r in game.history] == [1, 1, 1, 1, 3, 1, 1, 5, 1, 10]


def test_initial_state():
    game = Game()
    assert (game.rounds, game.phase, game.round_no) == (10, Phase.LOBBY, 0)
    assert game.history == ()
    assert game.totals() == dict.fromkeys(SEATS, 0)
    assert game.group_total() == 0


def test_rounds_must_be_positive():
    with pytest.raises(ValueError):
        Game(0)


def test_start_opens_round_one():
    game = started()
    assert (game.phase, game.round_no) == (Phase.OPEN, 1)
    with pytest.raises(InvalidTransition):
        game.start()


def test_submit_in_lobby_raises():
    with pytest.raises(InvalidTransition):
        Game().submit("P1", X)


def test_submit_after_reveal_raises():
    game = started()
    play_round(game, "YYYY")
    with pytest.raises(InvalidTransition):
        game.submit("P1", X)


def test_submit_after_game_over_raises():
    game = started(rounds=1)
    play_round(game, "YYYY")
    with pytest.raises(InvalidTransition):
        game.submit("P1", X)


def test_submit_unknown_seat_or_card():
    game = started()
    with pytest.raises(UnknownSeat):
        game.submit("P9", X)
    with pytest.raises(GameError):
        game.submit("P1", "Z")  # type: ignore[arg-type]


def test_overwrite_choice_before_reveal():
    game = started()
    game.submit("P1", X)
    game.submit("P1", Y)
    assert game.choice_of("P1") is Y
    for seat in ("P2", "P3", "P4"):
        game.submit(seat, Y)
    assert game.reveal().choices["P1"] is Y


def test_choice_of_and_pending_seats():
    game = Game()
    assert game.pending_seats() == []
    game.start()
    assert game.pending_seats() == list(SEATS)
    assert game.choice_of("P2") is None

    game.submit("P2", X)
    assert game.choice_of("P2") is X
    assert game.pending_seats() == ["P1", "P3", "P4"]

    for seat in ("P1", "P3", "P4"):
        game.submit(seat, Y)
    assert game.pending_seats() == []
    game.reveal()

    # Choices are cleared once revealed and nothing is pending between rounds.
    assert game.choice_of("P2") is None
    assert game.pending_seats() == []
    game.next_round()
    assert game.pending_seats() == list(SEATS)

    with pytest.raises(UnknownSeat):
        game.choice_of("P0")


def test_reveal_with_pending_seats_raises():
    game = started()
    game.submit("P1", X)
    with pytest.raises(NotReady, match="P2, P3, P4"):
        game.reveal()
    assert game.phase is Phase.OPEN


def test_reveal_outside_open_raises():
    with pytest.raises(InvalidTransition):
        Game().reveal()
    game = started()
    play_round(game, "XXXX")
    with pytest.raises(InvalidTransition):
        game.reveal()


def test_reveal_moves_to_revealed_then_next_round():
    game = started()
    play_round(game, "XYYY")
    assert game.phase is Phase.REVEALED
    game.next_round()
    assert (game.phase, game.round_no) == (Phase.OPEN, 2)


def test_next_round_requires_reveal():
    with pytest.raises(InvalidTransition):
        Game().next_round()
    game = started()
    with pytest.raises(InvalidTransition):
        game.next_round()


def test_final_reveal_moves_to_over():
    game = started(rounds=2)
    play_round(game, "YYYY")
    game.next_round()
    play_round(game, "YYYY")
    assert game.phase is Phase.OVER
    assert game.round_no == 2


def test_next_round_after_over_raises():
    game = started(rounds=1)
    play_round(game, "YYYY")
    with pytest.raises(InvalidTransition, match="over"):
        game.next_round()


def test_history_is_an_immutable_snapshot():
    game = started()
    record = play_round(game, "XYYY")
    history = game.history
    assert isinstance(history, tuple)
    assert history == (record,)

    game.next_round()
    play_round(game, "YYYY")
    assert len(history) == 1  # earlier snapshot is unaffected
    assert len(game.history) == 2


def test_round_record_mappings_are_read_only():
    source_choices = {"P1": X, "P2": Y, "P3": Y, "P4": Y}
    source_payoffs = {"P1": 30, "P2": -10, "P3": -10, "P4": -10}
    record = RoundRecord(1, source_choices, source_payoffs, 1)
    source_choices["P1"] = Y
    source_payoffs["P1"] = 0

    assert record.choices["P1"] is X
    assert record.payoffs["P1"] == 30
    assert isinstance(record.choices, Mapping)
    with pytest.raises(TypeError):
        record.choices["P1"] = Y  # type: ignore[index]
    with pytest.raises(TypeError):
        record.payoffs["P1"] = 0  # type: ignore[index]
    assert record.group_total == 0
