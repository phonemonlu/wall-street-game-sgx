import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pytest

from wallstreet.errors import InvalidTransition, NotReady, SeatTaken
from wallstreet.game import Phase
from wallstreet.room import RoomRegistry
from wallstreet.scoring import SEATS, Card


def test_join_race_200_threads_40_seats():
    reg = RoomRegistry()
    reg.create_groups(10)
    targets = [(g, seat) for g in range(1, 11) for seat in SEATS]
    barrier = threading.Barrier(200)

    def attempt(i: int) -> tuple[str, object]:
        group_id, seat = targets[i % len(targets)]
        barrier.wait()
        try:
            return "ok", reg.join(group_id, seat, f"player-{i}")
        except SeatTaken as err:
            return "taken", err

    with ThreadPoolExecutor(max_workers=200) as pool:
        results = list(pool.map(attempt, range(200)))

    tokens = [value for kind, value in results if kind == "ok"]
    assert len(tokens) == 40
    assert Counter(kind for kind, _ in results) == {"ok": 40, "taken": 160}
    assert len(set(tokens)) == 40
    assert sorted(reg.locate(t) for t in tokens) == sorted(targets)
    assert all(seats == [] for seats in reg.open_seats().values())
    for snap in reg.snapshots():
        assert snap.version == 4


def test_concurrent_submit_and_reveal_one_reveal_per_round():
    reg = RoomRegistry()
    reg.create_groups(1)
    tokens = {seat: reg.join(1, seat, seat) for seat in SEATS}
    reg.start(1)
    per_seat = 8
    n_threads = per_seat * len(SEATS)

    def play_round(round_no: int) -> list[str]:
        barrier = threading.Barrier(n_threads)

        def worker(i: int) -> list[str]:
            seat = SEATS[i % len(SEATS)]
            card = Card.X if (i + round_no) % 3 == 0 else Card.Y
            outcomes = []
            barrier.wait()
            for action in (lambda: reg.submit(tokens[seat], card), lambda: reg.reveal(1)):
                try:
                    action()
                    outcomes.append("ok")
                except (InvalidTransition, NotReady) as err:
                    outcomes.append(type(err).__name__)
            return outcomes

        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            return list(pool.map(worker, range(n_threads)))

    for round_no in range(1, 11):
        results = play_round(round_no)
        reveals = Counter(r[1] for r in results)
        assert reveals["ok"] == 1, reveals
        assert set(reveals) <= {"ok", "InvalidTransition", "NotReady"}
        assert set(r[0] for r in results) <= {"ok", "InvalidTransition"}
        assert len(reg.snapshot(1).history) == round_no
        if round_no < 10:
            reg.next_round(1)

    snap = reg.snapshot(1)
    assert snap.phase is Phase.OVER
    assert dict(snap.totals) == {
        seat: sum(r.payoffs[seat] for r in snap.history) for seat in SEATS
    }


@pytest.mark.parametrize("strategy", ["Random", "Smart"])
def test_parallel_bot_games(strategy: str):
    reg = RoomRegistry()
    reg.create_groups(50)
    stop = threading.Event()
    seen: dict[int, list[int]] = {g: [] for g in reg.group_ids()}
    reader_errors: list[str] = []

    def reader() -> None:
        last: dict[int, int] = {}
        try:
            while not stop.is_set():
                for snap in reg.snapshots():
                    if snap.version < last.get(snap.group_id, -1):
                        reader_errors.append(f"group {snap.group_id} went back to v{snap.version}")
                    last[snap.group_id] = snap.version
        except Exception as err:  # a crashed reader thread must fail the test, not be ignored
            reader_errors.append(repr(err))

    def play(group_id: int) -> None:
        reg.fill_with_bots(group_id, strategy)
        seen[group_id].append(reg.snapshot(group_id).version)
        reg.start(group_id)
        while True:
            seen[group_id].append(reg.snapshot(group_id).version)
            reg.reveal(group_id)
            if reg.snapshot(group_id).phase is Phase.OVER:
                break
            reg.next_round(group_id)
        seen[group_id].append(reg.snapshot(group_id).version)

    readers = [threading.Thread(target=reader) for _ in range(4)]
    for t in readers:
        t.start()
    try:
        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(play, reg.group_ids()))
    finally:
        stop.set()
        for t in readers:
            t.join()

    assert reader_errors == []
    for snap in reg.snapshots():
        assert snap.phase is Phase.OVER
        assert len(snap.history) == 10
        assert [r.round_no for r in snap.history] == list(range(1, 11))
        assert dict(snap.totals) == {
            seat: sum(r.payoffs[seat] for r in snap.history) for seat in SEATS
        }
        assert snap.group_total == sum(r.group_total for r in snap.history)
        versions = seen[snap.group_id]
        assert all(b > a for a, b in zip(versions, versions[1:]))
        # fill + start + 10 reveals + 9 next_rounds
        assert snap.version == 1 + 1 + 10 + 9
