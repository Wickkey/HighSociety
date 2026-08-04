#!/usr/bin/env python3
"""
Dev tool: runs a full game between embedded bots and prints it live to the
terminal, exactly like a real CLI game — useful for watching how PassBot /
GreedyBot / CappedGreedyBot actually behave turn by turn, not just their
final scores.

Usage:
    python3 -m highsociety.code.gamecore.dev_tools.simulate_bots
    python3 -m highsociety.code.gamecore.dev_tools.simulate_bots --seed 123
    python3 -m highsociety.code.gamecore.dev_tools.simulate_bots --think-time 0.5
    python3 -m highsociety.code.gamecore.dev_tools.simulate_bots --bots greedy,greedy,pass,capped,capped
"""

import argparse

from highsociety.code.gamecore.game_manager.gameplay import PlayGame
from highsociety.code.gamecore.player.capped_greedy_bot import CappedGreedyBot
from highsociety.code.gamecore.player.greedy_bot import GreedyBot
from highsociety.code.gamecore.player.pass_bot import PassBot

_BOT_TYPES = {
    "pass": PassBot,
    "greedy": GreedyBot,
    "capped": CappedGreedyBot,
}

DEFAULT_BOT_MIX = ["greedy", "greedy", "pass", "capped", "capped"]


def build_players(bot_mix: list, think_time: float) -> list:
    players = []
    counts = {}
    for bot_type in bot_mix:
        counts[bot_type] = counts.get(bot_type, 0) + 1
        username = f"{bot_type}{counts[bot_type]}"
        cls = _BOT_TYPES[bot_type]
        players.append(cls(name=username.capitalize(), username=username, think_time=think_time))
    return players


def main():
    parser = argparse.ArgumentParser(description="Watch embedded bots play a full game live")
    parser.add_argument("--seed", type=int, default=None, help="RNG seed for a reproducible game (default: random)")
    parser.add_argument("--think-time", type=float, default=1.0,
                         help="Seconds each bot pauses before announcing a decision (default: 1.0)")
    parser.add_argument("--bots", type=str, default=",".join(DEFAULT_BOT_MIX),
                         help=f"Comma-separated bot types from {list(_BOT_TYPES)} "
                              f"(default: {','.join(DEFAULT_BOT_MIX)})")
    args = parser.parse_args()

    bot_mix = [b.strip() for b in args.bots.split(",") if b.strip()]
    unknown = set(bot_mix) - set(_BOT_TYPES)
    if unknown:
        parser.error(f"Unknown bot type(s) {sorted(unknown)}; choose from {list(_BOT_TYPES)}")

    players = build_players(bot_mix, args.think_time)
    game = PlayGame(players=players, mode="cli", seed=args.seed)
    game.play_game()


if __name__ == "__main__":
    main()
