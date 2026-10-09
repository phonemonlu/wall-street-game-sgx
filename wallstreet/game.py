"""Game state machine for one group (pure, single-threaded)."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from wallstreet.errors import GameError, InvalidTransition, NotReady, UnknownSeat
from wallstreet.scoring import SEATS, Card, multiplier, score_round


class Phase(StrEnum):
    LOBBY = "lobby"
    OPEN = "open"
    REVEALED = "revealed"
    OVER = "over"


@dataclass(frozen=True)
class RoundRecord:
    round_no: int
    choices: Mapping[str, Card]
    payoffs: Mapping[str, int]
    multiplier: int

    def __post_init__(self) -> None:
        # Freeze the mappings so a record can be shared safely after the round is revealed.
        object.__setattr__(self, "choices", MappingProxyType(dict(self.choices)))
        object.__setattr__(self, "payoffs", MappingProxyType(dict(self.payoffs)))

    @property
    def group_total(self) -> int:
        return sum(self.payoffs.values())


@dataclass
class Game:
    """LOBBY -> OPEN <-> REVEALED ... -> OVER (the final reveal goes straight to OVER)."""

    rounds: int = 10
    phase: Phase = field(default=Phase.LOBBY, init=False)
    round_no: int = field(default=0, init=False)
    _choices: dict[str, Card] = field(default_factory=dict, init=False, repr=False)
    _history: list[RoundRecord] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.rounds < 1:
            raise ValueError("A game needs at least one round.")

    def start(self) -> None:
        self._require(Phase.LOBBY, "The game has already started.")
        self.phase = Phase.OPEN
        self.round_no = 1

    def submit(self, seat: str, card: Card) -> None:
        self._check_seat(seat)
        self._require(Phase.OPEN, f"Round {self.round_no} is not open for choices.")
        try:
            self._choices[seat] = Card(card)
        except ValueError:
            raise GameError(f"Unknown card {card!r}; choose X or Y.") from None

    def choice_of(self, seat: str) -> Card | None:
        """The seat's current, unrevealed choice (None outside an open round)."""
        self._check_seat(seat)
        return self._choices.get(seat)

    def pending_seats(self) -> list[str]:
        """Seats that still have to choose in the open round ([] in any other phase)."""
        if self.phase is not Phase.OPEN:
            return []
        return [seat for seat in SEATS if seat not in self._choices]

    def reveal(self) -> RoundRecord:
        self._require(Phase.OPEN, "There is no open round to reveal.")
        if pending := self.pending_seats():
            raise NotReady(f"Still waiting for {', '.join(pending)}.")
        record = RoundRecord(
            round_no=self.round_no,
            choices=self._choices,
            payoffs=score_round(self._choices, self.round_no),
            multiplier=multiplier(self.round_no),
        )
        self._history.append(record)
        self._choices = {}
        self.phase = Phase.OVER if self.round_no >= self.rounds else Phase.REVEALED
        return record

    def next_round(self) -> None:
        if self.phase is Phase.OVER:
            raise InvalidTransition("The game is over.")
        self._require(Phase.REVEALED, f"Reveal round {self.round_no} before starting the next one.")
        self.round_no += 1
        self.phase = Phase.OPEN

    @property
    def history(self) -> tuple[RoundRecord, ...]:
        return tuple(self._history)

    def totals(self) -> dict[str, int]:
        totals = dict.fromkeys(SEATS, 0)
        for record in self._history:
            for seat, points in record.payoffs.items():
                totals[seat] += points
        return totals

    def group_total(self) -> int:
        return sum(record.group_total for record in self._history)

    def _require(self, phase: Phase, message: str) -> None:
        if self.phase is not phase:
            raise InvalidTransition(message)

    @staticmethod
    def _check_seat(seat: str) -> None:
        if seat not in SEATS:
            raise UnknownSeat(f"Unknown seat {seat!r}; expected one of {', '.join(SEATS)}.")
