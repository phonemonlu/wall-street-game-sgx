"""Settings read from environment variables."""

import hmac
import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    host_pin: str | None = None
    refresh_sec: float = 1.0
    rounds: int = 10

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        """Build settings from WSG_HOST_PIN, WSG_REFRESH_SEC and WSG_ROUNDS (default: os.environ)."""
        env = os.environ if env is None else env
        defaults = cls()
        refresh_sec = _parse(env, "WSG_REFRESH_SEC", float, defaults.refresh_sec)
        rounds = _parse(env, "WSG_ROUNDS", int, defaults.rounds)
        if refresh_sec <= 0:
            raise ValueError(f"WSG_REFRESH_SEC must be positive, got {refresh_sec}.")
        if rounds < 1:
            raise ValueError(f"WSG_ROUNDS must be at least 1, got {rounds}.")
        return cls(
            host_pin=env.get("WSG_HOST_PIN", "").strip() or None,
            refresh_sec=refresh_sec,
            rounds=rounds,
        )


def _parse[T](env: Mapping[str, str], key: str, kind: type[T], default: T) -> T:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        return kind(raw)  # type: ignore[call-arg]
    except ValueError:
        raise ValueError(f"{key} must be a {kind.__name__}, got {raw!r}.") from None


def check_pin(expected: str | None, given: str) -> bool:
    """Constant-time PIN check; always True when no PIN is configured."""
    if expected is None:
        return True
    return hmac.compare_digest(expected.encode(), given.encode())
