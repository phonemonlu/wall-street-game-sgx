# Architecture & interface contract

Streamlit app for the Wall Street Game (4-player iterated prisoner's dilemma, 10 rounds,
bonus multipliers ×3 / ×5 / ×10 in rounds 5 / 8 / 10). Python 3.12+, `streamlit>=1.37`, `pandas`.

## Layers (imports only point downward; only `app.py` / `views.py` import streamlit)

```
app.py          entry point: page config + routing via st.query_params
views.py        render functions (host, player, join, leaderboard, table)
wallstreet/
  room.py       Room + RoomRegistry  (thread-safe facade the UI talks to)
  game.py       Game state machine   (pure, single-threaded, no locks / IO)
  strategies.py bot strategies
  scoring.py    payoff rules         (pure functions)
  config.py     Settings from env
  errors.py     domain exceptions
```

## Rules (`wallstreet/scoring.py`)

| # of X | X players each | Y players each |
|---|---|---|
| 0 | – | +10 |
| 1 | +30 | −10 |
| 2 | +20 | −20 |
| 3 | +10 | −30 |
| 4 | −10 | – |

All payoffs × `multiplier(round_no)`: `{5: 3, 8: 5, 10: 10}`, else 1.

```python
SEATS: tuple[str, ...] = ("P1", "P2", "P3", "P4")
class Card(StrEnum): X = "X"; Y = "Y"
PAYOFF: dict[int, tuple[int | None, int | None]]   # n_x -> (x_points, y_points)
BONUS_ROUNDS: dict[int, int] = {5: 3, 8: 5, 10: 10}
def multiplier(round_no: int) -> int
def score_round(choices: Mapping[str, Card], round_no: int) -> dict[str, int]
    # requires exactly the 4 SEATS as keys, else raises errors.NotReady
```

## Errors (`wallstreet/errors.py`)

```python
class GameError(Exception)           # base; str(e) is user-facing
class InvalidTransition(GameError)   # action not allowed in current phase
class NotReady(GameError)            # reveal with pending seats
class SeatTaken(GameError)
class UnknownGroup(GameError)
class UnknownSeat(GameError)         # bad seat label or unknown token
class AuthError(GameError)
```

## Game (`wallstreet/game.py`)

```python
class Phase(StrEnum): LOBBY="lobby"; OPEN="open"; REVEALED="revealed"; OVER="over"

@dataclass(frozen=True)
class RoundRecord:
    round_no: int
    choices: Mapping[str, Card]      # seat -> card
    payoffs: Mapping[str, int]       # seat -> points (multiplier applied)
    multiplier: int
    @property
    def group_total(self) -> int     # sum(payoffs)

class Game:
    def __init__(self, rounds: int = 10)
    rounds: int
    phase: Phase
    round_no: int                    # 0 in LOBBY, 1..rounds afterwards
    def start(self) -> None          # LOBBY -> OPEN, round 1
    def submit(self, seat: str, card: Card) -> None   # only in OPEN; may overwrite until reveal
    def choice_of(self, seat: str) -> Card | None     # current, unrevealed choice
    def pending_seats(self) -> list[str]
    def reveal(self) -> RoundRecord  # OPEN -> REVEALED (or OVER if last round); NotReady if pending
    def next_round(self) -> None     # REVEALED -> OPEN, round_no += 1
    @property
    def history(self) -> tuple[RoundRecord, ...]      # revealed rounds only
    def totals(self) -> dict[str, int]                # seat -> cumulative points, all SEATS present
    def group_total(self) -> int
```
Note: after reveal of the final round the phase goes straight to OVER.

## Strategies (`wallstreet/strategies.py`)

```python
class Strategy(Protocol):
    name: str
    def choose(self, seat: str, round_no: int, history: Sequence[RoundRecord]) -> Card
```
Implementations: `AlwaysY`, `AlwaysX`, `TitForTat` (X iff any *other* seat played X in last
revealed round; Y in round 1), `GrimTrigger` (Y until any other seat ever plays X, then X forever),
`BonusDefector` (X in bonus rounds, else Y), `RandomStrategy(p_x: float = 0.5, seed: int | None = None)`.
`STRATEGIES: dict[str, Callable[[], Strategy]]` keyed by display name.

## Room & registry (`wallstreet/room.py`)

```python
@dataclass
class Seat:
    label: str                       # "P1".."P4"
    token: str | None = None         # secrets.token_urlsafe(16) when claimed by a human
    name: str = ""
    strategy: Strategy | None = None # set => bot
    @property is_bot -> bool; @property is_free -> bool (no token and no strategy)

@dataclass(frozen=True)
class SeatView:
    label: str; name: str; is_bot: bool; is_free: bool; submitted: bool

@dataclass(frozen=True)
class RoomSnapshot:                  # immutable, safe to render; NEVER contains unrevealed cards
    group_id: int
    phase: Phase
    round_no: int
    rounds: int
    multiplier: int                  # multiplier of current round_no (1 in LOBBY)
    next_multiplier: int             # multiplier of round_no + 1 (for "negotiation break" prompt)
    seats: tuple[SeatView, ...]
    history: tuple[RoundRecord, ...]
    totals: Mapping[str, int]
    group_total: int
    timer_ends_at: float | None      # time.time() epoch seconds
    version: int

class Room:
    group_id: int
    def snapshot(self) -> RoomSnapshot
    # internal helpers used by RoomRegistry; all mutations happen under self.lock (threading.RLock)
    # and bump self.version. Bots auto-submit whenever a round opens (start/next_round) and when
    # a bot is added to an OPEN round.

class RoomRegistry:
    def __init__(self, rounds: int = 10, clock: Callable[[], float] = time.time)
    def create_groups(self, n: int) -> None        # replaces any existing groups (fresh game)
    def group_ids(self) -> list[int]
    def snapshot(self, group_id: int) -> RoomSnapshot
    def snapshots(self) -> list[RoomSnapshot]
    def open_seats(self) -> dict[int, list[str]]  # group_id -> free seat labels
    def join(self, group_id: int, seat: str, name: str) -> str   # returns token; SeatTaken / UnknownGroup / UnknownSeat
    def locate(self, token: str) -> tuple[int, str]             # (group_id, seat) or UnknownSeat
    def leave(self, token: str) -> None
    def fill_with_bots(self, group_id: int, strategy_name: str) -> list[str]  # fills free seats, returns labels
    def replace_with_bot(self, group_id: int, seat: str, strategy_name: str) -> None
        # any phase: a human (or free) seat becomes a bot; the human's token is removed from the
        # index (UnknownSeat afterwards); in OPEN the bot submits at once (overriding a pending
        # human pick). GameError if already a bot / unknown strategy; UnknownSeat / UnknownGroup.
    def start(self, group_id: int) -> None         # LOBBY->OPEN; NotReady if any seat free
    def submit(self, token: str, card: Card) -> None
    def my_choice(self, token: str) -> Card | None
    def reveal(self, group_id: int) -> RoundRecord
    def next_round(self, group_id: int) -> None
    def start_all(self) / reveal_all(self) / next_all(self) -> dict[int, str]
        # apply to every group; returns {group_id: reason} for SKIPPED groups (str of the GameError)
    def start_timer(self, seconds: int, group_id: int | None = None) -> None  # None = all groups
    def clear_timer(self, group_id: int | None = None) -> None
    def leaderboard(self) -> list[dict]   # rows: {"rank","group","seat","name","bot","points"} sorted desc
    def group_leaderboard(self) -> list[dict]  # rows: {"rank","group","points"} sorted desc
    def export(self) -> dict              # JSON-serialisable full results
    def reset(self) -> None               # drop all groups
```
Concurrency: a registry lock guards the group dict + token index; each Room has its own RLock so groups
never block each other. Tokens are unguessable; a token maps to exactly one seat.

## Config (`wallstreet/config.py`)

```python
@dataclass(frozen=True)
class Settings:
    host_pin: str | None      # WSG_HOST_PIN (empty => None)
    refresh_sec: float        # WSG_REFRESH_SEC, default 1.0
    rounds: int               # WSG_ROUNDS, default 10
    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings"
def check_pin(expected: str | None, given: str) -> bool   # hmac.compare_digest; True if expected is None
```

## UI (`app.py`, `views.py`)

* `?role=host` → host view (PIN gate if `WSG_HOST_PIN` set, else yellow warning
  "No host PIN set: anyone with this URL can control the game. Set WSG_HOST_PIN to lock it.").
* `?seat=<token>` → player view (token in URL so browser refresh keeps the seat).
* otherwise → join view.
* Results table columns `RD | Total | P1 | P2 | P3 | P4`; cells `"X +10"` / `"Y -30"`; Total = round group total.
  Footer: `P1: 270 P2: -90 P3: -50 P4: -130`. Header `Group N | Group total T`.
* Live parts use `@st.fragment(run_every=settings.refresh_sec)`.
* `views.py` keeps logic in pure helpers that never touch streamlit and are unit-tested directly:
  `results_frame(snapshot) -> DataFrame` (revealed rounds only), `totals_line(snapshot) -> str`,
  `round_label`, `bonus_hint`, `seconds_left`, `countdown_text`, `pending_seats`, `seat_status`.
  Render functions: `render_banner(settings)`, `render_round_status(snapshot)`,
  `render_board(snapshot, title_seat)` ("Game over" heading, `Group N | Group total T`, table,
  footer), `join_view(registry)`, `player_view(registry, settings, token)`,
  `host_view(registry, settings)`.
* Host per-group panels are tabs (≤ 8 groups) or a selectbox with an overview table (> 8 groups).
  Widget callbacks read widget values from `st.session_state` when they fire, so a value changed in
  the same interaction as a click is never stale.
