import json
from dataclasses import FrozenInstanceError

import pytest

from wallstreet.errors import (
    GameError,
    InvalidTransition,
    NotReady,
    SeatTaken,
    UnknownGroup,
    UnknownSeat,
)
from wallstreet.game import Game, Phase
from wallstreet.room import Room, RoomRegistry, Seat
from wallstreet.scoring import SEATS, Card
from wallstreet.strategies import STRATEGIES, RandomStrategy

X, Y = Card.X, Card.Y

REAL_GAME = ["XYXX", "YXYY", "YYXX", "XXXY", "XYYY", "YXXY", "XYYY", "YXXX", "XYYY", "XYYY"]


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def reg(clock: FakeClock) -> RoomRegistry:
    registry = RoomRegistry(clock=clock)
    registry.create_groups(3)
    return registry


def join_all(reg: RoomRegistry, group_id: int = 1) -> dict[str, str]:
    return {seat: reg.join(group_id, seat, f"Player {seat}") for seat in SEATS}


def submit_all(reg: RoomRegistry, tokens: dict[str, str], cards: str) -> None:
    for seat, card in zip(SEATS, cards, strict=True):
        reg.submit(tokens[seat], Card(card))


# -- Seat ---------------------------------------------------------------------------------------


def test_seat_properties():
    assert Seat("P1").is_free and not Seat("P1").is_bot
    human = Seat("P1", token="t", name="Ann")
    assert not human.is_free and not human.is_bot
    bot = Seat("P2", name="Bot", strategy=STRATEGIES["Always Y"]())
    assert bot.is_bot and not bot.is_free


# -- groups -------------------------------------------------------------------------------------


def test_create_groups_and_ids(reg: RoomRegistry):
    assert reg.group_ids() == [1, 2, 3]
    assert reg.open_seats() == {g: list(SEATS) for g in (1, 2, 3)}
    reg.create_groups(1)
    assert reg.group_ids() == [1]


@pytest.mark.parametrize("n", [0, -1, 201])
def test_create_groups_bounds(n: int):
    with pytest.raises(ValueError):
        RoomRegistry().create_groups(n)


def test_create_groups_200_allowed():
    reg = RoomRegistry()
    reg.create_groups(200)
    assert len(reg.group_ids()) == 200


def test_create_groups_replaces_game_and_clears_tokens(reg: RoomRegistry):
    token = reg.join(1, "P1", "Ann")
    reg.create_groups(2)
    with pytest.raises(UnknownSeat):
        reg.locate(token)
    assert reg.snapshot(1).seats[0].is_free


def test_reset(reg: RoomRegistry):
    token = reg.join(1, "P1", "Ann")
    reg.reset()
    assert reg.group_ids() == [] and reg.snapshots() == [] and reg.export()["groups"] == []
    with pytest.raises(UnknownSeat):
        reg.locate(token)
    with pytest.raises(UnknownGroup):
        reg.snapshot(1)


def test_unknown_group_everywhere(reg: RoomRegistry):
    for call in (
        lambda: reg.snapshot(9),
        lambda: reg.join(9, "P1", "Ann"),
        lambda: reg.fill_with_bots(9, "Always Y"),
        lambda: reg.start(9),
        lambda: reg.reveal(9),
        lambda: reg.next_round(9),
        lambda: reg.start_timer(10, 9),
        lambda: reg.clear_timer(9),
    ):
        with pytest.raises(UnknownGroup):
            call()


def test_unknown_token_everywhere(reg: RoomRegistry):
    for call in (
        lambda: reg.locate("nope"),
        lambda: reg.leave("nope"),
        lambda: reg.submit("nope", X),
        lambda: reg.my_choice("nope"),
    ):
        with pytest.raises(UnknownSeat):
            call()


# -- join / leave -------------------------------------------------------------------------------


def test_join_returns_unique_token_and_locates(reg: RoomRegistry):
    t1 = reg.join(1, "P1", "  Ann  ")
    t2 = reg.join(2, "P1", "Bob")
    assert t1 != t2 and len(t1) >= 16
    assert reg.locate(t1) == (1, "P1")
    assert reg.locate(t2) == (2, "P1")
    seat = reg.snapshot(1).seats[0]
    assert (seat.name, seat.is_free, seat.is_bot) == ("Ann", False, False)
    assert reg.open_seats()[1] == ["P2", "P3", "P4"]


def test_join_taken_seat(reg: RoomRegistry):
    reg.join(1, "P1", "Ann")
    with pytest.raises(SeatTaken):
        reg.join(1, "P1", "Bob")
    reg.fill_with_bots(1, "Always Y")
    with pytest.raises(SeatTaken):
        reg.join(1, "P2", "Bob")


@pytest.mark.parametrize("name", ["", "   ", "\t"])
def test_join_blank_name(reg: RoomRegistry, name: str):
    with pytest.raises(GameError):
        reg.join(1, "P1", name)
    assert reg.snapshot(1).seats[0].is_free


def test_join_unknown_seat(reg: RoomRegistry):
    with pytest.raises(UnknownSeat):
        reg.join(1, "P9", "Ann")


def test_join_bumps_version(reg: RoomRegistry):
    v = reg.snapshot(1).version
    reg.join(1, "P1", "Ann")
    assert reg.snapshot(1).version == v + 1
    assert reg.snapshot(2).version == 0


def test_leave_in_lobby_frees_seat(reg: RoomRegistry):
    token = reg.join(1, "P1", "Ann")
    v = reg.snapshot(1).version
    reg.leave(token)
    assert reg.snapshot(1).seats[0].is_free
    assert reg.snapshot(1).version == v + 1
    with pytest.raises(UnknownSeat):
        reg.locate(token)
    with pytest.raises(UnknownSeat):
        reg.leave(token)
    reg.join(1, "P1", "Bob")  # seat can be retaken


def test_leave_after_start_raises(reg: RoomRegistry):
    tokens = join_all(reg)
    reg.start(1)
    with pytest.raises(InvalidTransition):
        reg.leave(tokens["P1"])
    assert reg.locate(tokens["P1"]) == (1, "P1")


def test_late_human_can_take_free_seat_and_start_needs_all(reg: RoomRegistry):
    reg.join(1, "P1", "Ann")
    reg.join(1, "P2", "Bob")
    with pytest.raises(NotReady):
        reg.start(1)
    reg.join(1, "P3", "Cid")
    reg.join(1, "P4", "Dee")
    reg.start(1)
    assert reg.snapshot(1).phase is Phase.OPEN


def test_late_human_may_join_free_seat_after_game_started():
    room = Room(1)
    room.game.start()  # simulate a room that is OPEN with free seats
    room.claim("P1", "Ann", "tok")
    assert room.seats["P1"].name == "Ann"


# -- bots ---------------------------------------------------------------------------------------


def test_fill_with_bots_fills_free_seats_with_fresh_instances(reg: RoomRegistry):
    reg.join(1, "P2", "Ann")
    filled = reg.fill_with_bots(1, "Random")
    assert filled == ["P1", "P3", "P4"]
    snap = reg.snapshot(1)
    assert [s.name for s in snap.seats] == ["Bot (Random)", "Ann", "Bot (Random)", "Bot (Random)"]
    assert [s.is_bot for s in snap.seats] == [True, False, True, True]
    room = reg._room(1)
    strategies = [room.seats[label].strategy for label in filled]
    assert all(isinstance(s, RandomStrategy) for s in strategies)
    assert len({id(s) for s in strategies}) == 3
    assert reg.fill_with_bots(1, "Always X") == []
    assert reg.open_seats()[1] == []


def test_fill_with_bots_unknown_strategy(reg: RoomRegistry):
    with pytest.raises(GameError):
        reg.fill_with_bots(1, "Nope")
    assert reg.open_seats()[1] == list(SEATS)


def test_fill_with_bots_version(reg: RoomRegistry):
    reg.fill_with_bots(1, "Always Y")
    v = reg.snapshot(1).version
    assert v == 1
    reg.fill_with_bots(1, "Always Y")  # nothing to fill: no change
    assert reg.snapshot(1).version == v


def test_bots_submit_when_round_opens(reg: RoomRegistry):
    tokens = {"P1": reg.join(1, "P1", "Ann")}
    reg.fill_with_bots(1, "Always X")
    assert not any(s.submitted for s in reg.snapshot(1).seats)  # nothing submitted in LOBBY
    reg.start(1)
    assert [s.submitted for s in reg.snapshot(1).seats] == [False, True, True, True]
    reg.submit(tokens["P1"], Y)
    record = reg.reveal(1)
    assert dict(record.choices) == {"P1": Y, "P2": X, "P3": X, "P4": X}
    assert not any(s.submitted for s in reg.snapshot(1).seats)
    reg.next_round(1)
    assert [s.submitted for s in reg.snapshot(1).seats] == [False, True, True, True]


def test_bot_added_to_open_round_submits_immediately():
    room = Room(1)
    room.claim("P1", "Ann", "tok")
    room.game.start()  # OPEN with free seats (not reachable via registry.start)
    room.add_bots("Always X")
    assert room.game.pending_seats() == ["P1"]
    assert room.game.choice_of("P2") is X


def test_all_bot_strategies_play_full_games(reg: RoomRegistry):
    for name in STRATEGIES:
        reg.create_groups(1)
        reg.fill_with_bots(1, name)
        reg.start(1)
        for _ in range(9):
            reg.reveal(1)
            reg.next_round(1)
        reg.reveal(1)
        snap = reg.snapshot(1)
        assert snap.phase is Phase.OVER and len(snap.history) == 10


def test_tit_for_tat_bots_see_history(reg: RoomRegistry):
    token = reg.join(1, "P1", "Ann")
    reg.fill_with_bots(1, "Tit for Tat")
    reg.start(1)
    reg.submit(token, X)
    reg.reveal(1)
    reg.next_round(1)
    room = reg._room(1)
    assert [room.game.choice_of(s) for s in ("P2", "P3", "P4")] == [X, X, X]


# -- play ---------------------------------------------------------------------------------------


def test_submit_and_my_choice(reg: RoomRegistry):
    tokens = join_all(reg)
    with pytest.raises(InvalidTransition):
        reg.submit(tokens["P1"], X)  # LOBBY
    reg.start(1)
    assert reg.my_choice(tokens["P1"]) is None
    reg.submit(tokens["P1"], X)
    assert reg.my_choice(tokens["P1"]) is X
    reg.submit(tokens["P1"], Y)  # overwrite before reveal
    assert reg.my_choice(tokens["P1"]) is Y
    assert reg.my_choice(tokens["P2"]) is None


def test_reveal_and_next_round_errors(reg: RoomRegistry):
    tokens = join_all(reg)
    with pytest.raises(InvalidTransition):
        reg.reveal(1)
    reg.start(1)
    with pytest.raises(InvalidTransition):
        reg.start(1)
    with pytest.raises(InvalidTransition):
        reg.next_round(1)
    reg.submit(tokens["P1"], X)
    with pytest.raises(NotReady):
        reg.reveal(1)


def test_failed_mutation_does_not_bump_version(reg: RoomRegistry):
    v = reg.snapshot(1).version
    for call in (lambda: reg.start(1), lambda: reg.reveal(1), lambda: reg.next_round(1)):
        with pytest.raises(GameError):
            call()
    assert reg.snapshot(1).version == v


def test_every_mutation_bumps_version(reg: RoomRegistry):
    versions = [reg.snapshot(1).version]

    def step(action):
        action()
        versions.append(reg.snapshot(1).version)

    tokens = {}
    for seat in SEATS:
        step(lambda seat=seat: tokens.setdefault(seat, reg.join(1, seat, seat)))
    step(lambda: reg.start(1))
    for seat in SEATS:
        step(lambda seat=seat: reg.submit(tokens[seat], X))
    step(lambda: reg.start_timer(30, 1))
    step(lambda: reg.reveal(1))
    step(lambda: reg.clear_timer(1))
    step(lambda: reg.next_round(1))
    assert all(b == a + 1 for a, b in zip(versions, versions[1:]))


def test_full_game_via_tokens_matches_game(reg: RoomRegistry):
    tokens = join_all(reg, 2)
    reference = Game()
    reg.start(2)
    reference.start()
    for round_no, cards in enumerate(REAL_GAME, start=1):
        snap = reg.snapshot(2)
        assert (snap.phase, snap.round_no) == (Phase.OPEN, round_no)
        submit_all(reg, tokens, cards)
        for seat, card in zip(SEATS, cards, strict=True):
            reference.submit(seat, Card(card))
        record = reg.reveal(2)
        assert record == reference.reveal()
        if round_no < 10:
            reg.next_round(2)
            reference.next_round()
    snap = reg.snapshot(2)
    assert snap.phase is Phase.OVER
    assert dict(snap.totals) == reference.totals() == {"P1": 270, "P2": -90, "P3": -50, "P4": -130}
    assert snap.group_total == reference.group_total() == 0
    assert snap.history == reference.history
    with pytest.raises(InvalidTransition):
        reg.next_round(2)
    # other groups untouched
    assert reg.snapshot(1).phase is Phase.LOBBY


# -- snapshot -----------------------------------------------------------------------------------


def test_snapshot_lobby_defaults(reg: RoomRegistry):
    snap = reg.snapshot(1)
    assert snap.group_id == 1 and snap.phase is Phase.LOBBY and snap.round_no == 0
    assert snap.rounds == 10 and snap.multiplier == 1 and snap.next_multiplier == 1
    assert snap.history == () and dict(snap.totals) == dict.fromkeys(SEATS, 0)
    assert snap.group_total == 0 and snap.timer_ends_at is None and snap.version == 0
    assert [s.label for s in snap.seats] == list(SEATS)


def test_snapshot_multipliers(reg: RoomRegistry):
    reg.fill_with_bots(1, "Always Y")
    reg.start(1)
    seen = []
    for round_no in range(1, 11):
        snap = reg.snapshot(1)
        seen.append((snap.round_no, snap.multiplier, snap.next_multiplier))
        reg.reveal(1)
        if round_no < 10:
            reg.next_round(1)
    assert seen[3] == (4, 1, 3)
    assert seen[4] == (5, 3, 1)
    assert seen[6] == (7, 1, 5)
    assert seen[8] == (9, 1, 10)
    assert seen[9] == (10, 10, 1)


def test_snapshot_never_exposes_unrevealed_cards(reg: RoomRegistry):
    tokens = join_all(reg)
    reg.start(1)
    reg.submit(tokens["P1"], X)
    snap = reg.snapshot(1)
    assert [s.submitted for s in snap.seats] == [True, False, False, False]
    assert snap.history == ()
    text = repr(snap)
    assert "Card" not in text and "'X'" not in text


def test_snapshot_is_immutable(reg: RoomRegistry):
    snap = reg.snapshot(1)
    with pytest.raises(FrozenInstanceError):
        snap.version = 5  # type: ignore[misc]
    with pytest.raises(TypeError):
        snap.totals["P1"] = 5  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        snap.seats[0].name = "x"  # type: ignore[misc]


def test_snapshot_is_detached_from_later_changes(reg: RoomRegistry):
    before = reg.snapshot(1)
    reg.fill_with_bots(1, "Always X")
    reg.start(1)
    reg.reveal(1)
    assert before.phase is Phase.LOBBY and before.history == () and before.seats[0].is_free


def test_snapshots_in_group_order(reg: RoomRegistry):
    assert [s.group_id for s in reg.snapshots()] == [1, 2, 3]


# -- *_all --------------------------------------------------------------------------------------


def test_start_reveal_next_all_report_skipped(reg: RoomRegistry):
    reg.fill_with_bots(1, "Always Y")
    reg.fill_with_bots(2, "Always X")
    skipped = reg.start_all()
    assert list(skipped) == [3] and "free seats" in skipped[3]
    assert reg.snapshot(1).phase is Phase.OPEN and reg.snapshot(2).phase is Phase.OPEN
    skipped = reg.reveal_all()
    assert list(skipped) == [3]
    assert reg.snapshot(1).totals["P1"] == 10 and reg.snapshot(2).totals["P1"] == -10
    skipped = reg.next_all()
    assert list(skipped) == [3]
    assert reg.snapshot(1).round_no == 2


def test_reveal_all_skips_pending(reg: RoomRegistry):
    tokens = join_all(reg, 1)
    reg.fill_with_bots(2, "Always Y")
    reg.fill_with_bots(3, "Always Y")
    assert reg.start_all() == {}
    skipped = reg.reveal_all()
    assert list(skipped) == [1] and "P1" in skipped[1]
    reg.submit(tokens["P1"], X)
    assert reg.my_choice(tokens["P1"]) is X


# -- timer --------------------------------------------------------------------------------------


def test_timer_single_group(reg: RoomRegistry, clock: FakeClock):
    reg.start_timer(60, 2)
    assert reg.snapshot(2).timer_ends_at == 1060.0
    assert reg.snapshot(1).timer_ends_at is None
    clock.now = 2000.0
    reg.start_timer(30, 2)
    assert reg.snapshot(2).timer_ends_at == 2030.0
    reg.clear_timer(2)
    assert reg.snapshot(2).timer_ends_at is None


def test_timer_all_groups(reg: RoomRegistry, clock: FakeClock):
    reg.start_timer(45)
    assert {s.timer_ends_at for s in reg.snapshots()} == {1045.0}
    reg.clear_timer()
    assert {s.timer_ends_at for s in reg.snapshots()} == {None}


@pytest.mark.parametrize("seconds", [0, -5])
def test_timer_must_be_positive(reg: RoomRegistry, seconds: int):
    with pytest.raises(ValueError):
        reg.start_timer(seconds)


def test_default_clock_is_wall_time():
    import time

    reg = RoomRegistry()
    reg.create_groups(1)
    before = time.time()
    reg.start_timer(10)
    assert before + 10 <= reg.snapshot(1).timer_ends_at <= time.time() + 10


# -- leaderboards & export ----------------------------------------------------------------------


def play_bots_to_end(reg: RoomRegistry, group_id: int) -> None:
    reg.start(group_id)
    while True:
        reg.reveal(group_id)
        if reg.snapshot(group_id).phase is Phase.OVER:
            return
        reg.next_round(group_id)


def test_leaderboard_competition_ranking(reg: RoomRegistry):
    reg.fill_with_bots(1, "Always Y")  # each seat: 10 * (7 + 3 + 5 + 10) = 250
    reg.fill_with_bots(2, "Always X")  # each seat: -250
    reg.join(3, "P1", "Ann")
    for g in (1, 2):
        play_bots_to_end(reg, g)
    rows = reg.leaderboard()
    assert len(rows) == 9  # free seats are left out
    assert [r["rank"] for r in rows] == [1, 1, 1, 1, 5, 6, 6, 6, 6]
    assert [(r["group"], r["seat"], r["points"]) for r in rows[:5]] == [
        (1, "P1", 250), (1, "P2", 250), (1, "P3", 250), (1, "P4", 250), (3, "P1", 0)
    ]
    assert rows[4] == {"rank": 5, "group": 3, "seat": "P1", "name": "Ann", "bot": False, "points": 0}
    assert rows[0]["name"] == "Bot (Always Y)" and rows[0]["bot"] is True
    assert list(rows[0]) == ["rank", "group", "seat", "name", "bot", "points"]


def test_group_leaderboard(reg: RoomRegistry):
    reg.fill_with_bots(1, "Always X")
    reg.fill_with_bots(3, "Always Y")
    play_bots_to_end(reg, 1)
    play_bots_to_end(reg, 3)
    assert reg.group_leaderboard() == [
        {"rank": 1, "group": 3, "points": 1000},
        {"rank": 2, "group": 2, "points": 0},
        {"rank": 3, "group": 1, "points": -1000},
    ]


def test_group_leaderboard_ties(reg: RoomRegistry):
    rows = reg.group_leaderboard()
    assert [(r["rank"], r["group"]) for r in rows] == [(1, 1), (1, 2), (1, 3)]


def test_export_is_json_serialisable(reg: RoomRegistry):
    tokens = join_all(reg, 1)
    reg.fill_with_bots(2, "Bonus Defector")
    reg.start(1)
    submit_all(reg, tokens, "XYXX")
    reg.reveal(1)
    data = json.loads(json.dumps(reg.export()))
    assert data["rounds"] == 10
    assert [g["group"] for g in data["groups"]] == [1, 2, 3]
    g1 = data["groups"][0]
    assert g1["seats"][0] == {"seat": "P1", "name": "Player P1", "bot": False, "total": 10}
    assert g1["rounds"] == [
        {
            "round": 1,
            "choices": {"P1": "X", "P2": "Y", "P3": "X", "P4": "X"},
            "payoffs": {"P1": 10, "P2": -30, "P3": 10, "P4": 10},
            "multiplier": 1,
            "group_total": 0,
        }
    ]
    assert type(g1["rounds"][0]["choices"]["P1"]) is str
    assert data["groups"][1]["seats"][0]["bot"] is True
    assert data["groups"][1]["rounds"] == []
    assert type(reg.export()["groups"][0]["rounds"][0]["choices"]["P1"]) is str


def test_custom_rounds():
    reg = RoomRegistry(rounds=3)
    reg.create_groups(1)
    reg.fill_with_bots(1, "Always Y")
    play_bots_to_end(reg, 1)
    snap = reg.snapshot(1)
    assert snap.rounds == 3 and len(snap.history) == 3 and snap.totals["P1"] == 30
