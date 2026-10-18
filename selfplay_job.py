#!/usr/bin/env python3
"""
selfplay_job.py

Plays bot-vs-bot games back to back and stores every one in Supabase
(`selfplay_games`, see game_history.py) until a target count is reached --
training data for an RL model. Built to run as a Render Cron Job (see
render.yaml / SELFPLAY.md) but works the same anywhere with DATABASE_URL set.

Resumable by design: each run counts how many games its batch already has
and only plays the remainder, so a run killed mid-way (Render's run time
limit, a deploy, a crash) loses at most the last unflushed handful, and the
next scheduled run simply picks up where it left off. Once the target is
reached every later run exits within seconds.

Every game is replayed from its own log (recording/game_replay.py) before
it's stored -- a game that wouldn't reproduce exactly is counted as a
failure and never written, so every stored row is a verified, replayable
episode.

Configuration (env var, or the matching --flag, which wins):
    DATABASE_URL                     required
    SELFPLAY_TARGET_GAMES    10000   total games wanted in this batch
    SELFPLAY_BATCH           rl-v1   dataset name; change it for a new dataset
    SELFPLAY_BOT_POOL        easy,medium,hard
                                     each seat's bot type is drawn uniformly
                                     from this list, independently per game
    SELFPLAY_PLAYERS         5       seats per game
    SELFPLAY_WORKERS         #CPUs   games played in parallel (processes)
    SELFPLAY_MAX_RUNTIME_SECONDS 39600
                                     stop starting new games after this long
                                     (default 11h, under Render's 12h cron cap)
    SELFPLAY_FLUSH_EVERY     20      games buffered per DB insert

Usage:
    python3 selfplay_job.py
    python3 selfplay_job.py --target-games 50 --batch smoke-test --workers 4
"""
import argparse
import os
import random
import signal
import subprocess
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

from dotenv import load_dotenv

from highsociety.code.ai import BOT_TYPES, create_bot_players
from highsociety.code.common.db import game_history
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager
from highsociety.code.gamecore.game_manager.auction_history import AuctionHistory
from highsociety.code.gamecore.game_manager.gameplay import PlayGame
from highsociety.code.gamecore.recording.game_replay import replay_game

# Stop the whole run (rather than burn hours producing nothing) once this
# many games have failed AND they're more than this fraction of attempts --
# a handful of flukes is worth logging and skipping; a systematic failure
# isn't.
_MAX_FAILURES_ABSOLUTE = 10
_MAX_FAILURE_RATE = 0.01
_DB_RETRY_ATTEMPTS = 5
# After SIGTERM: how long to keep waiting for games already mid-play before
# exiting anyway. Render waits 30s (by default) between SIGTERM and SIGKILL;
# everything already finished is flushed the moment the signal arrives, so
# this window only decides whether a few more in-progress games make it.
_SHUTDOWN_GRACE_SECONDS = 20


def _worker_init() -> None:
    # Bots floor every decision at a human-watchable think time with a real
    # time.sleep (see BotInterface._pace_think_time) -- nobody is watching
    # here, and left in place that alone would add ~2 minutes per game.
    # Process-wide is fine: these processes exist only to run games.
    time.sleep = lambda *args, **kwargs: None
    LoggingManager.set_level("WARNING")
    # Render (and Ctrl-C) signal the whole process group; only the parent
    # decides when to stop, so it can flush what's already finished first.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)


def play_one_game(seed: int, bot_pool: list, num_players: int) -> dict:
    """Plays one full game, verifies it replays exactly from its own log,
    and returns {"replay", "duration_ms"}. Module-level (not a closure) so
    ProcessPoolExecutor can pickle it. Raises if the replay diverges."""
    seat_rng = random.Random(seed)
    bot_mix = [seat_rng.choice(bot_pool) for _ in range(num_players)]
    started = time.perf_counter()
    players = create_bot_players(bot_mix, think_time=0)
    game = PlayGame(players=players, spectators=[], mode="network", seed=seed,
                    turn_duration=None, auction_history=AuctionHistory(), pace_events=False)
    game.play_game()
    duration_ms = int((time.perf_counter() - started) * 1000)
    replay = game.replay_log.to_dict()
    replay_game(replay)
    return {"replay": replay, "duration_ms": duration_ms}


def _engine_version() -> str:
    # Render sets RENDER_GIT_COMMIT for every build; fall back to the local
    # checkout so a run from a laptop is still attributable.
    commit = os.environ.get("RENDER_GIT_COMMIT")
    if commit:
        return commit
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:  # noqa: BLE001 -- purely informational
        return None


def _with_db_retry(fn, *args, **kwargs):
    for attempt in range(1, _DB_RETRY_ATTEMPTS + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 -- retried, then re-raised
            if attempt == _DB_RETRY_ATTEMPTS:
                raise
            delay = 2 ** attempt
            print(f"database call failed ({e}); retrying in {delay}s", flush=True)
            time.sleep(delay)


class _Stop(Exception):
    pass


def _env(name: str, default):
    return os.environ.get(name) or default


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Play bot-vs-bot games into Supabase for RL training")
    parser.add_argument("--target-games", type=int, default=int(_env("SELFPLAY_TARGET_GAMES", 10000)))
    parser.add_argument("--batch", default=_env("SELFPLAY_BATCH", "rl-v1"))
    parser.add_argument("--bot-pool", default=_env("SELFPLAY_BOT_POOL", "easy,medium,hard"))
    parser.add_argument("--players", type=int, default=int(_env("SELFPLAY_PLAYERS", 5)))
    parser.add_argument("--workers", type=int, default=int(_env("SELFPLAY_WORKERS", os.cpu_count() or 1)))
    parser.add_argument("--max-runtime-seconds", type=float,
                        default=float(_env("SELFPLAY_MAX_RUNTIME_SECONDS", 11 * 3600)))
    parser.add_argument("--flush-every", type=int, default=int(_env("SELFPLAY_FLUSH_EVERY", 20)))
    args = parser.parse_args(argv)
    args.bot_pool = [b.strip() for b in args.bot_pool.split(",") if b.strip()]
    unknown = set(args.bot_pool) - set(BOT_TYPES)
    if not args.bot_pool or unknown:
        parser.error(f"--bot-pool must name bot types from {sorted(BOT_TYPES)} (unknown: {sorted(unknown)})")
    if not 2 <= args.players <= 5:
        parser.error("--players must be between 2 and 5")
    return args


def run(args) -> int:
    LoggingManager.set_level("WARNING")
    started = time.monotonic()
    deadline = started + args.max_runtime_seconds
    engine_version = _engine_version()

    already = _with_db_retry(game_history.count_selfplay_games, args.batch)
    remaining = args.target_games - already
    print(f"batch={args.batch!r}: {already}/{args.target_games} games stored; pool={args.bot_pool}, "
          f"players={args.players}, workers={args.workers}, engine={engine_version}", flush=True)
    if remaining <= 0:
        print("target reached -- nothing to do.", flush=True)
        return 0

    stop_requested = False

    def _on_signal(signum, _frame):
        nonlocal stop_requested
        if stop_requested:
            raise _Stop()  # a second signal: stop waiting on in-flight games too
        stop_requested = True
        print(f"received signal {signum}: finishing in-flight games, flushing, then exiting", flush=True)

    previous_handlers = {sig: signal.signal(sig, _on_signal) for sig in (signal.SIGTERM, signal.SIGINT)}

    seeder = random.SystemRandom()
    buffer, stored, failures, attempted = [], 0, 0, 0
    durations = []

    def _flush():
        nonlocal buffer, stored
        if buffer:
            _with_db_retry(game_history.insert_selfplay_games, args.batch, buffer, engine_version)
            stored += len(buffer)
            buffer = []

    def _report():
        elapsed = time.monotonic() - started
        rate = stored / elapsed * 3600 if elapsed else 0
        avg = sum(durations) / len(durations) / 1000 if durations else 0
        print(f"[{elapsed / 60:6.1f} min] stored {already + stored}/{args.target_games} "
              f"(+{stored} this run, {rate:.0f}/h, avg {avg:.1f}s/game/worker, {failures} failed)", flush=True)

    executor = ProcessPoolExecutor(max_workers=args.workers, initializer=_worker_init)
    in_flight = {}
    grace_deadline = None
    try:
        while True:
            if stop_requested and grace_deadline is None:
                # Drop games still queued (never started), save everything
                # already finished right now, then give only the games
                # genuinely mid-play a short window to land.
                for future in [f for f in in_flight if f.cancel()]:
                    del in_flight[future]
                _flush()
                grace_deadline = time.monotonic() + _SHUTDOWN_GRACE_SECONDS
            if grace_deadline is not None and time.monotonic() > grace_deadline:
                print(f"abandoning {len(in_flight)} unfinished game(s) after the shutdown grace period", flush=True)
                break
            can_start = (not stop_requested and time.monotonic() < deadline
                         and attempted < remaining + failures)
            while can_start and len(in_flight) < args.workers * 2 and attempted < remaining + failures:
                seed = seeder.randrange(2 ** 62)
                in_flight[executor.submit(play_one_game, seed, args.bot_pool, args.players)] = seed
                attempted += 1
            if not in_flight:
                break
            done, _ = wait(in_flight, timeout=5, return_when=FIRST_COMPLETED)
            for future in done:
                seed = in_flight.pop(future)
                try:
                    episode = future.result()
                except Exception as e:  # noqa: BLE001 -- counted, logged, and bounded below
                    failures += 1
                    print(f"game with seed {seed} failed: {type(e).__name__}: {e}", flush=True)
                    if failures >= _MAX_FAILURES_ABSOLUTE and failures > _MAX_FAILURE_RATE * attempted:
                        print(f"too many failures ({failures}/{attempted}) -- aborting the run", flush=True)
                        stop_requested = True
                        deadline = 0
                    continue
                durations.append(episode["duration_ms"])
                buffer.append(episode)
                if len(buffer) >= args.flush_every or grace_deadline is not None:
                    _flush()
                    _report()
    except _Stop:
        print("second signal -- abandoning in-flight games", flush=True)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
        _flush()
        _report()

    total = already + stored
    if failures >= _MAX_FAILURES_ABSOLUTE and failures > _MAX_FAILURE_RATE * attempted:
        return 1
    if total >= args.target_games:
        print(f"target of {args.target_games} reached.", flush=True)
    else:
        print(f"stopped at {total}/{args.target_games}; the next run resumes from here.", flush=True)
    return 0


def main(argv=None) -> int:
    load_dotenv()
    args = parse_args(argv)
    if not game_history.is_configured():
        print("DATABASE_URL is not set -- nowhere to store games.", file=sys.stderr)
        return 2
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
