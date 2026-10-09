"""Thread-safe rooms (one per group) and the registry the UI talks to.

Locking rules:
* ``RoomRegistry._lock`` guards ``_groups`` and the token index; it is held only briefly.
* Each ``Room.lock`` (an RLock) guards that room's game, seats, timer and version.
* Lock order is room -> registry. The registry lock is never held while waiting on a room lock.
"""

import secrets
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from wallstreet.errors import (
    GameError,
    InvalidTransition,
    NotReady,
    SeatTaken,
    UnknownGroup,
    UnknownSeat,
)
from wallstreet.game import Game, Phase, RoundRecord
from wallstreet.scoring import SEATS, Card, multiplier, player_label
from wallstreet.strategies import STRATEGIES, SmartBot, Strategy, smart_choices

MAX_GROUPS = 200
MAX_NAME_LEN = 30


@dataclass
class Seat:
    label: str
    token: str | None = None
    name: str = ""
    strategy: Strategy | None = None

    @property
    def is_bot(self) -> bool:
        return self.strategy is not None

    @property
    def is_free(self) -> bool:
        return self.token is None and self.strategy is None


@dataclass(frozen=True)
class SeatView:
    label: str
    name: str
    is_bot: bool
    is_free: bool
    submitted: bool


@dataclass(frozen=True)
class RoomSnapshot:
    """Immutable view of a room. Never contains unrevealed cards."""

    group_id: int
    phase: Phase
    round_no: int
    rounds: int
    multiplier: int
    next_multiplier: int
    seats: tuple[SeatView, ...]
    history: tuple[RoundRecord, ...]
    totals: Mapping[str, int]
    group_total: int
    timer_ends_at: float | None
    version: int


@dataclass(eq=False)
class Room:
    group_id: int
    rounds: int = 10
    game: Game = field(init=False)
    seats: dict[str, Seat] = field(init=False)
    timer_ends_at: float | None = field(default=None, init=False)
    version: int = field(default=0, init=False)
    lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.game = Game(rounds=self.rounds)
        self.seats = {label: Seat(label) for label in SEATS}

    def snapshot(self) -> RoomSnapshot:
        with self.lock:
            game = self.game
            round_no = game.round_no
            return RoomSnapshot(
                group_id=self.group_id,
                phase=game.phase,
                round_no=round_no,
                rounds=game.rounds,
                multiplier=multiplier(round_no) if round_no else 1,
                next_multiplier=multiplier(round_no + 1),
                seats=tuple(self._seat_view(seat) for seat in self.seats.values()),
                history=game.history,
                totals=MappingProxyType(game.totals()),
                group_total=game.group_total(),
                timer_ends_at=self.timer_ends_at,
                version=self.version,
            )

    # -- internal helpers; called by RoomRegistry --------------------------------------------

    def seat(self, label: str) -> Seat:
        try:
            return self.seats[label]
        except KeyError:
            raise UnknownSeat(f"Unknown seat {label!r}; expected one of {', '.join(SEATS)}.") from None

    def seat_for_token(self, label: str, token: str) -> Seat:
        """The seat still held by ``token``; UnknownSeat if it was released meanwhile."""
        seat = self.seat(label)
        if seat.token != token:
            raise UnknownSeat("Unknown player token.")
        return seat

    def claim(self, label: str, name: str, token: str) -> None:
        with self.lock:
            seat = self.seat(label)
            if not seat.is_free:
                raise SeatTaken(f"Seat {label} in group {self.group_id} is already taken.")
            seat.token, seat.name = token, name
            self._bump()

    def rename(self, label: str, token: str, name: str) -> None:
        """Set the seat's name: any time while unnamed, otherwise only before the game starts."""
        with self.lock:
            seat = self.seat_for_token(label, token)
            if seat.name and self.game.phase is not Phase.LOBBY:
                raise InvalidTransition("Names are locked once the game has started.")
            seat.name = name
            self._bump()

    def release(self, label: str, token: str) -> None:
        with self.lock:
            seat = self.seat_for_token(label, token)
            if self.game.phase is not Phase.LOBBY:
                raise InvalidTransition("You can only leave before the game starts.")
            seat.token, seat.name = None, ""
            self._bump()

    def add_bots(self, strategy_name: str) -> list[str]:
        factory = _strategy_factory(strategy_name)
        with self.lock:
            filled = [seat for seat in self.seats.values() if seat.is_free]
            for seat in filled:
                seat.strategy, seat.name = factory(), f"Bot ({strategy_name})"
            if filled:
                if self.game.phase is Phase.OPEN:
                    self._bots_submit(filled)
                self._bump()
            return [seat.label for seat in filled]

    def make_bot(self, label: str, strategy_name: str) -> str | None:
        """Hand ``label`` (human or free, any phase) to a bot; returns the evicted token, if any."""
        factory = _strategy_factory(strategy_name)
        with self.lock:
            seat = self.seat(label)
            if seat.is_bot:
                raise GameError(f"Seat {label} in group {self.group_id} is already a bot.")
            old_token = seat.token
            seat.token, seat.strategy, seat.name = None, factory(), f"Bot ({strategy_name})"
            if self.game.phase is Phase.OPEN:
                self._bots_submit([seat])
            self._bump()
            return old_token

    def start(self) -> None:
        with self.lock:
            if self.game.phase is not Phase.LOBBY:
                raise InvalidTransition("The game has already started.")
            if free := [seat.label for seat in self.seats.values() if seat.is_free]:
                raise NotReady(f"Group {self.group_id} still has free seats: {', '.join(free)}.")
            self.game.start()
            self._bots_submit(self.seats.values())
            self._bump()

    def submit(self, label: str, token: str, card: Card) -> None:
        with self.lock:
            self.seat_for_token(label, token)
            self.game.submit(label, card)
            self._bump()

    def choice_of(self, label: str, token: str) -> Card | None:
        with self.lock:
            self.seat_for_token(label, token)
            return self.game.choice_of(label)

    def reveal(self) -> RoundRecord:
        with self.lock:
            self._smart_bots_decide()
            record = self.game.reveal()
            if self.game.phase is Phase.OVER:
                self.timer_ends_at = None  # no more negotiating once the game is over
            self._bump()
            return record

    def next_round(self) -> None:
        with self.lock:
            self.game.next_round()
            self.timer_ends_at = None  # the negotiation break ends when the next round opens
            self._bots_submit(self.seats.values())
            self._bump()

    def set_timer(self, ends_at: float | None) -> None:
        with self.lock:
            self.timer_ends_at = ends_at
            self._bump()

    def _bots_submit(self, seats: Iterable[Seat]) -> None:
        history = self.game.history
        for seat in seats:
            if seat.strategy is not None:
                round_no = self.game.round_no
                self.game.submit(seat.label, seat.strategy.choose(seat.label, round_no, history))

    def _smart_bots_decide(self) -> None:
        """Replace smart bots' placeholder cards with their best reply to everyone's final cards.

        Runs under the room lock right before the reveal, so no human can change a card afterwards.
        Does nothing unless every seat has chosen; game.reveal() then reports what is missing.
        """
        if self.game.phase is not Phase.OPEN or self.game.pending_seats():
            return
        smart = [seat.label for seat in self.seats.values() if isinstance(seat.strategy, SmartBot)]
        if not smart:
            return
        choices = {label: self.game.choice_of(label) for label in SEATS}
        picks = smart_choices(choices, smart, self.game.round_no, self.game.totals())  # type: ignore[arg-type]
        for label, card in picks.items():
            self.game.submit(label, card)

    def _seat_view(self, seat: Seat) -> SeatView:
        return SeatView(
            label=seat.label,
            name=seat.name,
            is_bot=seat.is_bot,
            is_free=seat.is_free,
            submitted=self.game.phase is Phase.OPEN and self.game.choice_of(seat.label) is not None,
        )

    def _bump(self) -> None:
        self.version += 1


def _clean_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise GameError("Please enter your name.")
    if len(name) > MAX_NAME_LEN:
        raise GameError(f"Names can be at most {MAX_NAME_LEN} characters.")
    return name


def _strategy_factory(strategy_name: str) -> Callable[[], Strategy]:
    factory = STRATEGIES.get(strategy_name)
    if factory is None:
        raise GameError(f"Unknown strategy {strategy_name!r}; choose one of {', '.join(STRATEGIES)}.")
    return factory


def _ranked(rows: list[dict], key: str = "points") -> list[dict]:
    """Competition ranking (1, 1, 3) on already-sorted rows."""
    for i, row in enumerate(rows):
        same_as_previous = i > 0 and rows[i - 1][key] == row[key]
        row["rank"] = rows[i - 1]["rank"] if same_as_previous else i + 1
    return rows


class RoomRegistry:
    def __init__(self, rounds: int = 10, clock: Callable[[], float] = time.time) -> None:
        self.rounds = rounds
        self._clock = clock
        self._lock = threading.Lock()
        self._groups: dict[int, Room] = {}
        self._tokens: dict[str, tuple[int, str]] = {}

    # -- groups -------------------------------------------------------------------------------

    def create_groups(self, n: int) -> None:
        if not 1 <= n <= MAX_GROUPS:
            raise ValueError(f"Number of groups must be between 1 and {MAX_GROUPS}, got {n}.")
        rooms = {gid: Room(gid, self.rounds) for gid in range(1, n + 1)}
        with self._lock:
            self._groups = rooms
            self._tokens.clear()

    def reset(self) -> None:
        with self._lock:
            self._groups = {}
            self._tokens.clear()

    def group_ids(self) -> list[int]:
        with self._lock:
            return sorted(self._groups)

    def _room(self, group_id: int) -> Room:
        with self._lock:
            room = self._groups.get(group_id)
        if room is None:
            raise UnknownGroup(f"There is no group {group_id}.")
        return room

    def _rooms(self) -> list[Room]:
        with self._lock:
            return [self._groups[gid] for gid in sorted(self._groups)]

    def _by_token(self, token: str) -> tuple[Room, str]:
        with self._lock:
            entry = self._tokens.get(token)
            room = self._groups.get(entry[0]) if entry else None
        if entry is None or room is None:
            raise UnknownSeat("Unknown player token.")
        return room, entry[1]

    # -- reads --------------------------------------------------------------------------------

    def snapshot(self, group_id: int) -> RoomSnapshot:
        return self._room(group_id).snapshot()

    def snapshots(self) -> list[RoomSnapshot]:
        return [room.snapshot() for room in self._rooms()]

    def open_seats(self) -> dict[int, list[str]]:
        result = {}
        for snap in self.snapshots():
            result[snap.group_id] = [seat.label for seat in snap.seats if seat.is_free]
        return result

    def locate(self, token: str) -> tuple[int, str]:
        room, label = self._by_token(token)
        return room.group_id, label

    def my_choice(self, token: str) -> Card | None:
        room, label = self._by_token(token)
        return room.choice_of(label, token)

    # -- players ------------------------------------------------------------------------------

    def join(self, group_id: int, seat: str, name: str) -> str:
        return self._claim(group_id, seat, _clean_name(name))

    def take_seat(self, group_id: int, seat: str) -> str:
        """Claim a free seat before choosing a name (see ``rename``); returns the seat token."""
        return self._claim(group_id, seat, "")

    def rename(self, token: str, name: str) -> None:
        room, label = self._by_token(token)
        room.rename(label, token, _clean_name(name))

    def _claim(self, group_id: int, seat: str, name: str) -> str:
        room = self._room(group_id)
        token = secrets.token_urlsafe(16)
        with room.lock:
            room.claim(seat, name, token)
            with self._lock:
                stale = self._groups.get(group_id) is not room
                if not stale:
                    self._tokens[token] = (group_id, seat)
        if stale:  # create_groups/reset replaced the room while we were joining
            raise UnknownGroup(f"Group {group_id} was replaced; please join again.")
        return token

    def leave(self, token: str) -> None:
        room, label = self._by_token(token)
        room.release(label, token)
        with self._lock:
            self._tokens.pop(token, None)

    def submit(self, token: str, card: Card) -> None:
        room, label = self._by_token(token)
        room.submit(label, token, card)

    # -- host ---------------------------------------------------------------------------------

    def fill_with_bots(self, group_id: int, strategy_name: str) -> list[str]:
        return self._room(group_id).add_bots(strategy_name)

    def replace_with_bot(self, group_id: int, seat: str, strategy_name: str) -> None:
        """Hand a human (or free) seat to a bot in any phase; the human's token stops working."""
        room = self._room(group_id)
        with room.lock:
            old_token = room.make_bot(seat, strategy_name)
            if old_token is not None:
                with self._lock:
                    self._tokens.pop(old_token, None)

    def start(self, group_id: int) -> None:
        self._room(group_id).start()

    def reveal(self, group_id: int) -> RoundRecord:
        return self._room(group_id).reveal()

    def next_round(self, group_id: int) -> None:
        self._room(group_id).next_round()

    def start_all(self) -> dict[int, str]:
        return self._apply_all(Room.start)

    def reveal_all(self) -> dict[int, str]:
        return self._apply_all(Room.reveal)

    def next_all(self) -> dict[int, str]:
        return self._apply_all(Room.next_round)

    def _apply_all(self, action: Callable[[Room], object]) -> dict[int, str]:
        skipped: dict[int, str] = {}
        for room in self._rooms():
            try:
                action(room)
            except GameError as err:
                skipped[room.group_id] = str(err)
        return skipped

    def start_timer(self, seconds: int, group_id: int | None = None) -> None:
        if seconds <= 0:
            raise ValueError(f"Timer seconds must be positive, got {seconds}.")
        ends_at = self._clock() + seconds
        for room in self._targets(group_id):
            room.set_timer(ends_at)

    def clear_timer(self, group_id: int | None = None) -> None:
        for room in self._targets(group_id):
            room.set_timer(None)

    def _targets(self, group_id: int | None) -> list[Room]:
        return self._rooms() if group_id is None else [self._room(group_id)]

    # -- results ------------------------------------------------------------------------------

    def leaderboard(self) -> list[dict]:
        rows = [
            {
                "rank": 0,
                "group": snap.group_id,
                "seat": player_label(snap.group_id, seat.label),
                "name": seat.name or player_label(snap.group_id, seat.label),
                "bot": seat.is_bot,
                "points": snap.totals[seat.label],
            }
            for snap in self.snapshots()
            for seat in snap.seats
            if not seat.is_free
        ]
        rows.sort(key=lambda r: (-r["points"], r["group"], r["seat"]))
        return _ranked(rows)

    def group_leaderboard(self) -> list[dict]:
        rows = [{"rank": 0, "group": snap.group_id, "points": snap.group_total} for snap in self.snapshots()]
        rows.sort(key=lambda r: (-r["points"], r["group"]))
        return _ranked(rows)

    def export(self) -> dict:
        return {"rounds": self.rounds, "groups": [_export_group(snap) for snap in self.snapshots()]}


def _export_group(snap: RoomSnapshot) -> dict:
    gid = snap.group_id
    return {
        "group": snap.group_id,
        "phase": str(snap.phase),
        "group_total": snap.group_total,
        "seats": [
            {
                "seat": player_label(gid, s.label),
                "name": s.name or player_label(gid, s.label),  # a seat taken but never named
                "bot": s.is_bot,
                "total": snap.totals[s.label],
            }
            for s in snap.seats
        ],
        "rounds": [
            {
                "round": r.round_no,
                "choices": {player_label(gid, seat): str(card) for seat, card in r.choices.items()},
                "payoffs": {player_label(gid, seat): points for seat, points in r.payoffs.items()},
                "multiplier": r.multiplier,
                "group_total": r.group_total,
            }
            for r in snap.history
        ],
    }
