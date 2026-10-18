#!/usr/bin/env python3
"""
scripts/verify_replay.py

Proves stored games are reproducible using Supabase as the only source of
truth. For a website game, the record is assembled from the regular history
tables alone (games.seed, player_games, game_rounds, game_actions); for a
self-play game it's the selfplay_games row. Each game is rebuilt action by
action with recording/game_replay.py's replayer (no bots, no live players)
and must reproduce every action, every auction's card/winner/price and the
final standings exactly.

Usage:
    python3 -m scripts.verify_replay --source selfplay --sample 200            # random self-play games
    python3 -m scripts.verify_replay --source selfplay --batch rl-v1 --sample 50
    python3 -m scripts.verify_replay --source game --ids 812 813                # real website games
Exit code is 0 only if every checked game reproduced.
"""
import argparse
import sys
import time

from dotenv import load_dotenv

from highsociety.code.common.db import game_history
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager
from highsociety.code.gamecore.recording.game_replay import ReplayUnsupported, replay_record


def main(argv=None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Rebuild stored games from Supabase and check they reproduce")
    parser.add_argument("--source", choices=["selfplay", "game"], default="selfplay",
                        help="selfplay_games rows, or real website games rebuilt from game_actions")
    parser.add_argument("--ids", type=int, nargs="*", help="specific selfplay_games.id / games.id values")
    parser.add_argument("--batch", help="only this self-play batch")
    parser.add_argument("--sample", type=int, default=100, help="random rows to check when --ids isn't given")
    args = parser.parse_args(argv)

    if not game_history.is_configured():
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2
    LoggingManager.set_level("WARNING")
    time.sleep = lambda *a, **k: None  # nobody's watching a replay -- skip every pacing pause

    sample = None if args.ids else args.sample
    if args.source == "selfplay":
        rows = game_history.fetch_selfplay_records(ids=args.ids, batch=args.batch, sample=sample)
    else:
        rows = game_history.fetch_game_records(ids=args.ids, sample=sample)
    ok = unsupported = failed = 0
    for row_id, record in rows:
        try:
            replay_record(record)
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
