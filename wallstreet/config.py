"""Settings read from environment variables."""

import hmac
import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    host_username: str | None = None
    host_password: str | None = None
    refresh_sec: float = 1.0
    rounds: int = 10

    @property
    def host_login_required(self) -> bool:
        return self.host_username is not None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        """Build settings from WSG_HOST_USERNAME, WSG_HOST_PASSWORD, WSG_REFRESH_SEC and WSG_ROUNDS.

        Reads os.environ by default. The host login is off when both credentials are empty; setting
        only one of them is an error, so a typo cannot silently leave the host page open.
        """
        env = os.environ if env is None else env
        defaults = cls()
        refresh_sec = _parse(env, "WSG_REFRESH_SEC", float, defaults.refresh_sec)
        rounds = _parse(env, "WSG_ROUNDS", int, defaults.rounds)
        if refresh_sec <= 0:
            raise ValueError(f"WSG_REFRESH_SEC must be positive, got {refresh_sec}.")
        if rounds < 1:
            raise ValueError(f"WSG_ROUNDS must be at least 1, got {rounds}.")
        host_username = env.get("WSG_HOST_USERNAME", "").strip() or None
        host_password = env.get("WSG_HOST_PASSWORD", "") or None  # spaces may be part of a password
        if (host_username is None) != (host_password is None):
            raise ValueError("Set both WSG_HOST_USERNAME and WSG_HOST_PASSWORD, or neither.")
        return cls(
            host_username=host_username,
            host_password=host_password,
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


def check_login(settings: Settings, username: str, password: str) -> bool:
    """Constant-time host login check; always True when no login is configured."""
    if not settings.host_login_required:
        return True
    assert settings.host_username is not None and settings.host_password is not None
    # Compare both fields every time so the response time does not reveal which one was wrong.
    user_ok = hmac.compare_digest(settings.host_username.encode(), username.strip().encode())
    password_ok = hmac.compare_digest(settings.host_password.encode(), password.encode())
    return user_ok and password_ok
