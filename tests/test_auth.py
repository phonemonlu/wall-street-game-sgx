import threading

import pytest

from wallstreet.auth import HOST_LOGIN_TTL_SEC, HostSessions


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_issued_token_is_valid_until_it_expires():
    clock = FakeClock()
    sessions = HostSessions(ttl_sec=60, clock=clock)
    token = sessions.issue()
    assert sessions.is_valid(token)
    clock.now += 59
    assert sessions.is_valid(token)
    clock.now += 1
    assert not sessions.is_valid(token)
    clock.now -= 30  # once expired, a token stays dead even if the clock goes back
    assert not sessions.is_valid(token)


def test_default_lifetime_is_twelve_hours():
    assert HOST_LOGIN_TTL_SEC == 12 * 3600
    clock = FakeClock()
    sessions = HostSessions(clock=clock)
    token = sessions.issue()
    clock.now += 12 * 3600 - 1
    assert sessions.is_valid(token)
    clock.now += 1
    assert not sessions.is_valid(token)


def test_tokens_are_unique_and_unguessable():
    sessions = HostSessions()
    tokens = {sessions.issue() for _ in range(200)}
    assert len(tokens) == 200
    assert all(len(token) >= 32 for token in tokens)


@pytest.mark.parametrize("token", [None, "", "made-up"])
def test_unknown_tokens_are_invalid(token):
    sessions = HostSessions()
    sessions.issue()
    assert not sessions.is_valid(token)


def test_revoke_logs_out_only_that_token():
    sessions = HostSessions()
    first, second = sessions.issue(), sessions.issue()
    sessions.revoke(first)
    assert not sessions.is_valid(first)
    assert sessions.is_valid(second)
    sessions.revoke(first)  # revoking twice is harmless
    sessions.revoke(None)
    sessions.revoke("made-up")


def test_expired_tokens_are_purged():
    clock = FakeClock()
    sessions = HostSessions(ttl_sec=10, clock=clock)
    for _ in range(5):
        sessions.issue()
    clock.now += 10
    sessions.issue()
    assert len(sessions) == 1


def test_rejects_non_positive_ttl():
    with pytest.raises(ValueError):
        HostSessions(ttl_sec=0)


def test_concurrent_issue_and_check():
    sessions = HostSessions()
    issued: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        for _ in range(200):
            token = sessions.issue()
            assert sessions.is_valid(token)
            with lock:
                issued.append(token)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(set(issued)) == 1600
    assert all(sessions.is_valid(token) for token in issued)
