import threading

from loadtest import stress
from wallstreet.room import RoomRegistry


def test_stress_cli_passes_on_small_run(capsys):
    assert stress.main(["--groups", "3", "--threads", "2", "--readers", "2"]) == 0
    assert "OK: all invariants hold." in capsys.readouterr().out


def test_crashing_reader_is_reported_not_ignored():
    class Broken(RoomRegistry):
        def snapshot(self, group_id: int):
            raise RuntimeError("boom")

    reg = Broken()
    reg.create_groups(1)
    log = stress.VersionLog()
    stress.reader(reg, stress.Recorder(), log, threading.Event(), seed=0)  # returns instead of looping
    assert log.violations == ["reader 0: RuntimeError: boom"]
