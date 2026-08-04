from highsociety.code.ai.capped_greedy_bot import CappedGreedyBot
from highsociety.code.ai.greedy_bot import GreedyBot
from highsociety.code.ai.pass_bot import PassBot

# Shared name -> bot class registry, so anything that lets a user pick bots
# by name on the command line (dev_tools/simulate_bots.py, network_server.py
# --bots) stays in sync with what actually exists in this package.
BOT_TYPES = {
    "pass": PassBot,
    "greedy": GreedyBot,
    "capped": CappedGreedyBot,
}
