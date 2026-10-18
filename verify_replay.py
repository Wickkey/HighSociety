#!/usr/bin/env python3
"""
verify_replay.py

Proves stored games are reproducible using Supabase as the only source of
truth: reads replay logs back out of the database, rebuilds each game move
for move with recording/game_replay.py's replayer (no bots, no live
players), and checks every rebuilt game reaches exactly the logged deck,
seat order, auction winners/prices and final standings.

Usage:
    python3 verify_replay.py --source selfplay --sample 200            # random self-play games
    python3 verify_replay.py --source selfplay --batch rl-v1 --sample 50
    python3 verify_replay.py --source game --ids 812 813                # real website games
Exit code is 0 only if every checked game reproduced.
"""
import argparse
import sys
import time

from dotenv import load_dotenv

from highsociety.code.common.db import game_history
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager
from highsociety.code.gamecore.recording.game_replay import ReplayUnsupported, replay_game


def main(argv=None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Rebuild stored games from Supabase and check they reproduce")
    parser.add_argument("--source", choices=["selfplay", "game"], default="selfplay",
                        help="selfplay_games rows, or game_replays rows for real website games")
    parser.add_argument("--ids", type=int, nargs="*", help="specific selfplay_games.id / games.id values")
    parser.add_argument("--batch", help="only this self-play batch")
    parser.add_argument("--sample", type=int, default=100, help="random rows to check when --ids isn't given")
    args = parser.parse_args(argv)

    if not game_history.is_configured():
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2
    LoggingManager.set_level("WARNING")
    time.sleep = lambda *a, **k: None  # nobody's watching a replay -- skip every pacing pause

    rows = game_history.fetch_replays(args.source, ids=args.ids, batch=args.batch,
                                      sample=None if args.ids else args.sample)
    ok = unsupported = failed = 0
    for row_id, replay in rows:
        try:
            replay_game(replay)
            ok += 1
        except ReplayUnsupported as e:
            unsupported += 1
            print(f"{args.source} {row_id}: not replayable -- {e}")
        except Exception as e:  # noqa: BLE001 -- any failure is a reproducibility failure; report them all
            failed += 1
            print(f"{args.source} {row_id}: DIVERGED -- {type(e).__name__}: {e}")
    print(f"checked {len(rows)}: {ok} reproduced exactly, {failed} diverged, {unsupported} unsupported")
    return 0 if rows and failed == 0 and unsupported == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
