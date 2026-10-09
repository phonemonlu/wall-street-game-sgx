"""Stress-test RoomRegistry: all-bot groups play full games while readers poll snapshots.

    python -m loadtest.stress --groups 50 --threads 32 --strategy Random
"""

import argparse
import random
import statistics
import sys
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from wallstreet.game import Phase
from wallstreet.room import RoomRegistry
from wallstreet.scoring import SEATS
from wallstreet.strategies import STRATEGIES


@dataclass
class Recorder:
    """Thread-safe latency collector, keyed by operation name."""

    samples: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    lock: threading.Lock = field(default_factory=threading.Lock)

    def timed[T](self, op: str, fn: Callable[[], T]) -> T:
        start = time.perf_counter()
        try:
            return fn()
        finally:
            elapsed = time.perf_counter() - start
            with self.lock:
                self.samples[op].append(elapsed)

    def total(self) -> int:
        return sum(len(v) for v in self.samples.values())


@dataclass
class VersionLog:
    """Records version regressions seen by any thread."""

    last: dict[int, int] = field(default_factory=dict)
    violations: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def observe(self, group_id: int, version: int, local: dict[int, int]) -> None:
        if version < local.get(group_id, -1):
            with self.lock:
                self.violations.append(f"group {group_id}: version went back to {version}")
        local[group_id] = version


def play_group(reg: RoomRegistry, rec: Recorder, group_id: int, strategy: str) -> None:
    rec.timed("fill_with_bots", lambda: reg.fill_with_bots(group_id, strategy))
    rec.timed("start", lambda: reg.start(group_id))
    while True:
        rec.timed("reveal", lambda: reg.reveal(group_id))
        if reg.snapshot(group_id).phase is Phase.OVER:
            return
        rec.timed("next_round", lambda: reg.next_round(group_id))


def reader(reg: RoomRegistry, rec: Recorder, log: VersionLog, stop: threading.Event, seed: int) -> None:
    rng = random.Random(seed)
    ids = reg.group_ids()
    local: dict[int, int] = {}
    while not stop.is_set():
        group_id = rng.choice(ids)
        try:
            snap = rec.timed("snapshot", lambda: reg.snapshot(group_id))
            log.observe(group_id, snap.version, local)
            if rng.random() < 0.05:  # the host/leaderboard pages poll everything
                for snap in rec.timed("snapshots", reg.snapshots):
                    log.observe(snap.group_id, snap.version, local)
                rec.timed("leaderboard", reg.leaderboard)
        except Exception as err:  # a dead reader must fail the run, not vanish silently
            with log.lock:
                log.violations.append(f"reader {seed}: {type(err).__name__}: {err}")
            return


def check_invariants(reg: RoomRegistry, rounds: int, log: VersionLog) -> list[str]:
    problems = list(log.violations)
    expected_version = 1 + 1 + rounds + (rounds - 1)  # fill, start, reveals, next_rounds
    for snap in reg.snapshots():
        g = f"group {snap.group_id}"
        if snap.phase is not Phase.OVER:
            problems.append(f"{g}: phase {snap.phase}, expected over")
        if [r.round_no for r in snap.history] != list(range(1, rounds + 1)):
            problems.append(f"{g}: history has {len(snap.history)} rounds, expected {rounds}")
        for seat in SEATS:
            paid = sum(r.payoffs[seat] for r in snap.history)
            if snap.totals[seat] != paid:
                problems.append(f"{g} {seat}: total {snap.totals[seat]} != sum of payoffs {paid}")
        if snap.group_total != sum(r.group_total for r in snap.history):
            problems.append(f"{g}: group total mismatch")
        if snap.version != expected_version:
            problems.append(f"{g}: version {snap.version}, expected {expected_version}")
        if not all(seat.is_bot for seat in snap.seats):
            problems.append(f"{g}: not every seat is a bot")
    return problems


def percentile(sorted_ms: list[float], q: float) -> float:
    if len(sorted_ms) == 1:
        return sorted_ms[0]
    return statistics.quantiles(sorted_ms, n=100, method="inclusive")[int(q) - 1]


def format_table(rec: Recorder, wall: float) -> str:
    header = f"{'operation':<16}{'count':>9}{'p50 ms':>10}{'p95 ms':>10}{'p99 ms':>10}{'max ms':>10}"
    lines = [header, "-" * len(header)]
    for op in sorted(rec.samples):
        ms = sorted(s * 1000 for s in rec.samples[op])
        lines.append(
            f"{op:<16}{len(ms):>9}{percentile(ms, 50):>10.3f}{percentile(ms, 95):>10.3f}"
            f"{percentile(ms, 99):>10.3f}{ms[-1]:>10.3f}"
        )
    lines.append("-" * len(header))
    total = rec.total()
    lines.append(f"total ops {total} in {wall:.3f} s = {total / wall:,.0f} ops/sec")
    return "\n".join(lines)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--groups", type=int, default=50, help="number of all-bot groups (1-200)")
    parser.add_argument("--threads", type=int, default=32, help="worker threads playing games")
    parser.add_argument("--readers", type=int, default=8, help="threads polling snapshot()")
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--strategy", choices=sorted(STRATEGIES), default="Random")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    reg = RoomRegistry(rounds=args.rounds)
    rec = Recorder()
    log = VersionLog()
    stop = threading.Event()

    started = time.perf_counter()
    rec.timed("create_groups", lambda: reg.create_groups(args.groups))
    readers = [
        threading.Thread(target=reader, args=(reg, rec, log, stop, i), daemon=True)
        for i in range(args.readers)
    ]
    for t in readers:
        t.start()
    try:
        with ThreadPoolExecutor(max_workers=args.threads) as pool:
            futures = [pool.submit(play_group, reg, rec, g, args.strategy) for g in reg.group_ids()]
            errors = [str(f.exception()) for f in futures if f.exception() is not None]
    finally:
        stop.set()
        for t in readers:
            t.join()
    wall = time.perf_counter() - started

    print(
        f"{args.groups} groups x {args.rounds} rounds, strategy {args.strategy!r}, "
        f"{args.threads} worker threads, {args.readers} reader threads\n"
    )
    print(format_table(rec, wall))
    problems = errors + check_invariants(reg, args.rounds, log)
    if problems:
        print(f"\nFAIL: {len(problems)} invariant violation(s):", file=sys.stderr)
        for problem in problems[:20]:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("\nOK: all invariants hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
