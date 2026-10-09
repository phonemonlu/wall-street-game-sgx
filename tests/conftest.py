from collections.abc import Sequence

import pytest

from wallstreet.game import RoundRecord
from wallstreet.scoring import Card
from wallstreet.strategies import STRATEGIES


class AlwaysX:
    """Test double: a bot with a fixed card, so expected scores can be written down."""

    name = "Always X"
    card = Card.X

    def choose(self, seat: str, round_no: int, history: Sequence[RoundRecord]) -> Card:
        return self.card


class AlwaysY(AlwaysX):
    name = "Always Y"
    card = Card.Y


@pytest.fixture
def fixed_bots(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make "Always X" / "Always Y" available wherever a strategy name is accepted (tests only)."""
    for cls in (AlwaysX, AlwaysY):
        monkeypatch.setitem(STRATEGIES, cls.name, cls)
