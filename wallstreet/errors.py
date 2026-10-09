"""Domain exceptions. ``str(error)`` is always safe to show to the user."""


class GameError(Exception):
    """Base class for all game errors."""


class InvalidTransition(GameError):
    """The action is not allowed in the current phase."""


class NotReady(GameError):
    """The action needs every seat to be filled or to have submitted."""


class SeatTaken(GameError):
    """The seat is already claimed."""


class UnknownGroup(GameError):
    """No group with that id exists."""


class UnknownSeat(GameError):
    """Bad seat label or unknown player token."""


class AuthError(GameError):
    """Wrong or missing host login."""
