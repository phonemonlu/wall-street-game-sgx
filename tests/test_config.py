import pytest

from wallstreet.config import Settings, check_pin


def test_defaults():
    assert Settings.from_env({}) == Settings(host_pin=None, refresh_sec=1.0, rounds=10)


def test_parsing():
    env = {"WSG_HOST_PIN": "4321", "WSG_REFRESH_SEC": "2.5", "WSG_ROUNDS": "6"}
    assert Settings.from_env(env) == Settings(host_pin="4321", refresh_sec=2.5, rounds=6)


def test_empty_values_fall_back_to_defaults():
    env = {"WSG_HOST_PIN": "", "WSG_REFRESH_SEC": "", "WSG_ROUNDS": " "}
    assert Settings.from_env(env) == Settings(host_pin=None, refresh_sec=1.0, rounds=10)


def test_reads_os_environ_by_default(monkeypatch):
    monkeypatch.setenv("WSG_HOST_PIN", "9999")
    monkeypatch.setenv("WSG_ROUNDS", "3")
    monkeypatch.delenv("WSG_REFRESH_SEC", raising=False)
    assert Settings.from_env() == Settings(host_pin="9999", refresh_sec=1.0, rounds=3)


@pytest.mark.parametrize(
    "env",
    [
        {"WSG_ROUNDS": "ten"},
        {"WSG_ROUNDS": "2.5"},
        {"WSG_ROUNDS": "0"},
        {"WSG_REFRESH_SEC": "fast"},
        {"WSG_REFRESH_SEC": "0"},
    ],
)
def test_invalid_values_raise(env):
    with pytest.raises(ValueError, match="WSG_"):
        Settings.from_env(env)


def test_settings_are_frozen():
    with pytest.raises(AttributeError):
        Settings.from_env({}).rounds = 5  # type: ignore[misc]


def test_check_pin():
    assert check_pin(None, "")
    assert check_pin(None, "anything")
    assert check_pin("1234", "1234")
    assert not check_pin("1234", "1235")
    assert not check_pin("1234", "")
    assert check_pin("pïn", "pïn")  # non-ASCII must not crash compare_digest
