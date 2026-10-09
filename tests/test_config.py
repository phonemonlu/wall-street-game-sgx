import pytest

from wallstreet.config import Settings, check_login


def test_defaults():
    assert Settings.from_env({}) == Settings(refresh_sec=1.0, rounds=10)


def test_parsing():
    env = {"WSG_HOST_USERNAME": " admin ", "WSG_HOST_PASSWORD": "pa ss", "WSG_REFRESH_SEC": "2.5", "WSG_ROUNDS": "6"}
    expected = Settings(host_username="admin", host_password="pa ss", refresh_sec=2.5, rounds=6)
    assert Settings.from_env(env) == expected
    assert expected.host_login_required


def test_empty_values_fall_back_to_defaults():
    env = {"WSG_HOST_USERNAME": "", "WSG_HOST_PASSWORD": "", "WSG_REFRESH_SEC": "", "WSG_ROUNDS": " "}
    assert Settings.from_env(env) == Settings(refresh_sec=1.0, rounds=10)


def test_reads_os_environ_by_default(monkeypatch):
    monkeypatch.setenv("WSG_HOST_USERNAME", "host")
    monkeypatch.setenv("WSG_HOST_PASSWORD", "9999")
    monkeypatch.setenv("WSG_ROUNDS", "3")
    monkeypatch.delenv("WSG_REFRESH_SEC", raising=False)
    assert Settings.from_env() == Settings(host_username="host", host_password="9999", refresh_sec=1.0, rounds=3)


@pytest.mark.parametrize(
    "env",
    [
        {"WSG_ROUNDS": "ten"},
        {"WSG_ROUNDS": "2.5"},
        {"WSG_ROUNDS": "0"},
        {"WSG_REFRESH_SEC": "fast"},
        {"WSG_REFRESH_SEC": "0"},
        {"WSG_HOST_USERNAME": "admin"},
        {"WSG_HOST_PASSWORD": "secret"},
        {"WSG_HOST_USERNAME": "  ", "WSG_HOST_PASSWORD": "secret"},
    ],
)
def test_invalid_values_raise(env):
    with pytest.raises(ValueError, match="WSG_"):
        Settings.from_env(env)


def test_settings_are_frozen():
    with pytest.raises(AttributeError):
        Settings.from_env({}).rounds = 5  # type: ignore[misc]


def test_check_login():
    assert not Settings().host_login_required
    assert check_login(Settings(), "", "")
    assert check_login(Settings(), "anyone", "anything")
    locked = Settings(host_username="admin", host_password="pässword!")
    assert check_login(locked, "admin", "pässword!")  # non-ASCII must not crash compare_digest
    assert check_login(locked, " admin ", "pässword!")  # stray spaces around the username are ignored
    assert not check_login(locked, "admin", "pässword")
    assert not check_login(locked, "Admin", "pässword!")
    assert not check_login(locked, "admin", " pässword!")
    assert not check_login(locked, "", "")
