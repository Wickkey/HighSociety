from highsociety.code.ai.capped_greedy_bot import CappedGreedyBot
from highsociety.code.ai.greedy_bot import GreedyBot
from highsociety.code.ai.pass_bot import PassBot
from highsociety.code.ai.mcts_bot import EasyMCTSBot, HardMCTSBot, MediumMCTSBot
from highsociety.code.ai.bot_names import assign_bot_names

# Shared name -> bot class registry, so anything that lets a user pick bots
# by name on the command line (dev_tools/simulate_bots.py, network_server.py
# --bots, bot_evaluator.py) stays in sync with what actually exists in this
# package. "pass"/"greedy"/"capped" stay registered here for exactly that —
# scripts/tuning/evaluation keep full access to the plain heuristic bots —
# but the web lobby only ever offers "easy"/"medium"/"hard" (see
# web_server.py's UI, which never lists the other three as choices even
# though the server would happily accept them).
BOT_TYPES = {
    "pass": PassBot,
    "greedy": GreedyBot,
    "capped": CappedGreedyBot,
    "easy": EasyMCTSBot,
    "medium": MediumMCTSBot,
    "hard": HardMCTSBot,
}


def create_bot_players(bot_mix: list[str], think_time: float = 1.5, taken_usernames: set = None) -> list:
    """
    Build bot instances from a list of type names (e.g. ["greedy", "greedy",
    "pass"]) — the single shared implementation for every entry point that
    creates bots (main.py, network_server.py's --bots flag, web_server.py,
    dev_tools/simulate_bots.py), so bot naming logic (see bot_names.py) only
    lives in one place instead of being reimplemented per caller.

    taken_usernames: usernames already seated (e.g. in a room a bot is being
    added to after creation) that must not collide with the name assigned
    here — see web_server.py's "add a bot to the lobby" endpoint.
    """
    names = assign_bot_names(len(bot_mix), taken=taken_usernames)
    return [
        BOT_TYPES[bot_type](name=name, username=name.lower(), think_time=think_time)
        for bot_type, name in zip(bot_mix, names)
    ]
