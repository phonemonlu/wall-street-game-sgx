"""Host login sessions: random tokens kept on the server, like seat tokens, that expire."""

import secrets
import threading
import time
from collections.abc import Callable

HOST_LOGIN_TTL_SEC = 12 * 3600


class HostSessions:
    """Thread-safe store of host login tokens.

    The token travels in the host page's URL so a refresh keeps the host logged in. Keeping the
    tokens here (rather than signing them) means Log out really ends the session, and a server
    restart logs every host out together with wiping the games.
    """

    def __init__(self, ttl_sec: float = HOST_LOGIN_TTL_SEC, clock: Callable[[], float] = time.time) -> None:
        if ttl_sec <= 0:
            raise ValueError("ttl_sec must be positive.")
        self._ttl = ttl_sec
        self._clock = clock
        self._expires: dict[str, float] = {}
        self._lock = threading.Lock()

    def issue(self) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            now = self._clock()
            self._purge(now)
            self._expires[token] = now + self._ttl
        return token

    def is_valid(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            expires = self._expires.get(token)
            if expires is None:
                return False
            if self._clock() >= expires:
                del self._expires[token]
                return False
            return True

    def revoke(self, token: str | None) -> None:
        with self._lock:
            self._expires.pop(token or "", None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._expires)

    def _purge(self, now: float) -> None:
        for token in [t for t, expires in self._expires.items() if now >= expires]:
            del self._expires[token]
