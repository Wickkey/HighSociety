#!/usr/bin/env python3
"""
bot_evaluator.py

Runs many full games between the embedded bots (see highsociety/code/ai/)
and reports each bot's aggregate performance to a CSV: how many matches it
played, how many it won, and its average normalized rank across all of them.

The same bot type can appear more than once in --bots (e.g.
"greedy,greedy,pass") to seat it twice in every simulated game -- both
seats' results are pooled into that one bot's row, doubling its sample size
per run without needing more simulations.

Usage:
    python3 bot_evaluator.py
    python3 bot_evaluator.py --bots greedy,greedy,pass,capped --num-simulations 10
    python3 bot_evaluator.py --bots greedy,pass --num-simulations 500 --output results.csv
    python3 bot_evaluator.py --bots greedy,pass,capped --seed 42   # reproducible run
"""

import argparse
import contextlib
import csv
import sys
import time
from collections import defaultdict

from highsociety.code.ai import BOT_TYPES, create_bot_players
from highsociety.code.common.utils.utility import validate_player_count
from highsociety.code.gamecore.game_manager.gameplay import PlayGame


@contextlib.contextmanager
def _no_pacing_sleep():
    """
    PlayGame paces the pre-game countdown and every toast-worthy broadcast
    (bids, passes, auction results — see gameplay.py's _pace_toast_event)
    with real time.sleep() calls, purely so a human's browser/terminal has
    time to read each one. Nobody's watching a bulk bot-vs-bot simulation,
    and left in place those sleeps make a single game take over a minute
    instead of a fraction of a second — the exact reason
    tests/conftest.py's autouse fixture no-ops time.sleep for the whole
    test suite. Same fix here, but scoped to actually running simulations
    (restored afterward) rather than a bare module-level reassignment,
    which would silently and permanently disable time.sleep process-wide
    the instant anything imports this module for any reason.
    """
    original_sleep = time.sleep
    time.sleep = lambda *args, **kwargs: None
    try:
        yield
    finally:
        time.sleep = original_sleep

DEFAULT_BOT_MIX = ["greedy", "capped", "pass"]


def compute_ranks(final_standings: list[dict]) -> list[int]:
    """
    Standard competition ranking (1, 1, 3, 4, ...) over one game's
    final_standings, mirroring PlayGame.determine_winner()'s own rules:
    an eliminated player (money-eliminated -- see determine_winner) always
    ranks below every non-eliminated player regardless of points, and ties
    within a tier share the same rank.
    """
    order = sorted(
        range(len(final_standings)),
        key=lambda i: (final_standings[i]["eliminated"], -final_standings[i]["points"]),
    )
    ranks = [0] * len(final_standings)
    current_rank = 1
    for pos, idx in enumerate(order):
        if pos > 0:
            prev = order[pos - 1]
            same_tier = (
                final_standings[idx]["eliminated"] == final_standings[prev]["eliminated"]
                and final_standings[idx]["points"] == final_standings[prev]["points"]
            )
            if not same_tier:
                current_rank = pos + 1
        ranks[idx] = current_rank
    return ranks


def run_simulations(bot_mix: list[str], num_simulations: int, think_time: float,
                     seed: int = None, progress: bool = True) -> dict:
    """
    Plays num_simulations full games with one seat per entry of bot_mix and
    returns per-bot-type aggregate stats:
        {bot_type: {"matches": int, "wins": int, "rank_sum": float}}
    rank_sum accumulates rank/num_players per seat-instance, so dividing by
    "matches" afterward gives that bot's average normalized rank.
    """
    stats = defaultdict(lambda: {"matches": 0, "wins": 0, "rank_sum": 0.0})
    num_players = len(bot_mix)

    with _no_pacing_sleep():
        for sim in range(num_simulations):
            if progress:
                print(f"\rSimulating game {sim + 1}/{num_simulations}...", end="", flush=True)

            players = create_bot_players(bot_mix, think_time=think_time)
            game_seed = None if seed is None else seed + sim
            # mode="network" + no spectators means nothing is ever printed to
            # the terminal per-decision (NetworkHost only forwards to
            # players/spectators, and bot players' own send_message() doesn't
            # print) -- CLI mode would otherwise narrate every single bid.
            game = PlayGame(players=players, spectators=[], mode="network", seed=game_seed)
            game.play_game()

            winner_usernames = {w.username for w in (game.winners or [])}
            ranks = compute_ranks(game.final_standings)

            for bot_type, player, rank in zip(bot_mix, players, ranks):
                s = stats[bot_type]
                s["matches"] += 1
                s["rank_sum"] += rank / num_players
                if player.username in winner_usernames:
                    s["wins"] += 1

    if progress:
        print()  # move past the in-place progress line
    return stats


def ranked_rows(stats: dict):
    """Yields (bot_type, matches, wins, avg_rank, win_rate), best bot first
    (most wins, then lowest/best average rank as the tiebreaker)."""
    def sort_key(item):
        _, s = item
        avg_rank = s["rank_sum"] / s["matches"] if s["matches"] else float("inf")
        return (-s["wins"], avg_rank)

    for bot_type, s in sorted(stats.items(), key=sort_key):
        matches = s["matches"]
        avg_rank = s["rank_sum"] / matches if matches else 0.0
        win_rate = s["wins"] / matches if matches else 0.0
        yield bot_type, matches, s["wins"], avg_rank, win_rate


def write_csv(stats: dict, output_path: str) -> None:
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["bot_name", "total_matches_played", "num_wins", "average_ranking", "win_rate"])
        for bot_type, matches, wins, avg_rank, win_rate in ranked_rows(stats):
            writer.writerow([bot_type, matches, wins, f"{avg_rank:.4f}", f"{win_rate:.4f}"])


def print_summary(stats: dict) -> None:
    print(f"\n{'bot':<16} {'matches':>8} {'wins':>6} {'win_rate':>9} {'avg_rank':>9}")
    print("-" * 52)
    for bot_type, matches, wins, avg_rank, win_rate in ranked_rows(stats):
        print(f"{bot_type:<16} {matches:>8} {wins:>6} {win_rate:>9.1%} {avg_rank:>9.4f}")


def main():
    parser = argparse.ArgumentParser(
        description="Play many bot-vs-bot High Society games and rank the bots by win rate / average finish.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--bots", type=str, default=",".join(DEFAULT_BOT_MIX),
        help=f"Comma-separated bot types from {list(BOT_TYPES)}, one per seat -- repeat a type "
             f"(e.g. greedy,greedy,pass) to seat it twice and pool both seats' results into one row "
             f"(default: {','.join(DEFAULT_BOT_MIX)})",
    )
    parser.add_argument("--num-simulations", type=int, default=10,
                         help="How many full games to simulate (default: 10)")
    parser.add_argument("--think-time", type=float, default=0,
                         help="Seconds each bot pauses before deciding -- purely cosmetic, doesn't "
                              "affect decision quality, so 0 keeps bulk runs fast (default: 0)")
    parser.add_argument("--seed", type=int, default=None,
                         help="Base RNG seed -- game i uses seed+i, for a reproducible batch of "
                              "simulations. Omit for a fresh random sequence each run (default).")
    parser.add_argument("--output", type=str, default="bot_evaluator_results.csv",
                         help="CSV file to write results to (default: bot_evaluator_results.csv)")
    parser.add_argument("--quiet", action="store_true", help="Suppress the per-game progress line")
    args = parser.parse_args()

    bot_mix = [b.strip() for b in args.bots.split(",") if b.strip()]
    error = validate_player_count(len(bot_mix))
    if error:
        parser.error(f"--bots has {len(bot_mix)} entries: {error}")
    unknown = sorted(set(bot_mix) - set(BOT_TYPES))
    if unknown:
        parser.error(f"Unknown bot type(s) {unknown}; choose from {list(BOT_TYPES)}")
    if args.num_simulations < 1:
        parser.error("--num-simulations must be at least 1")

    print(f"Running {args.num_simulations} simulation(s) with bots: {bot_mix}")
    stats = run_simulations(bot_mix, args.num_simulations, args.think_time, args.seed,
                             progress=not args.quiet)

    write_csv(stats, args.output)
    print_summary(stats)
    print(f"\nFull results written to {args.output}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
