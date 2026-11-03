"""
Training-only opponents for v2, and a table helper that can seat them.

VariableCappedBot: the engine's CappedGreedyBot (raise with the cheapest card that beats the high bid,
up to a budget per card), but with its own appetite. The engine bot's budgets are 3.5 x a painting's
value, Prestige $15, Faux Pas $8, Passe $15, Scandale $20; here the painting multiple is `appetite`
and the flat budgets scale by appetite / 3.5. Drawn per game (default uniform 2.0-5.0), so the table
mixes conservative bots (2.0: $20 max for the 10-painting) and aggressive ones (5.0: $50) -- the
network can't learn one fixed price to beat. Progress checks keep the standard engine bot (3.5).

This sets how opponents bid; it does not feed into the reward.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))     # .RL

import rl_seat as rs                                                       # noqa: E402
from highsociety.code.ai.capped_greedy_bot import CappedGreedyBot         # noqa: E402

BASE_APPETITE = 3.5
_FLAT = {"PrestigeCard": 15, "FauxPas": 8, "Passe": 15, "Scandale": 20}    # the engine bot's flat budgets
VCAPPED = "vcapped"                                                         # lineup token for --bot-lineups


class VariableCappedBot(CappedGreedyBot):
    def __init__(self, name, username, appetite=BASE_APPETITE, think_time=0):
        super().__init__(name, username, think_time=think_time)
        self.appetite = appetite

    def _spend_limit(self, card_type, card_value):          # the engine bot calls self._spend_limit(...)
        if card_type == "Painting":
            return self.appetite * card_value
        flat = _FLAT.get(card_type)
        return float("inf") if flat is None else flat * self.appetite / BASE_APPETITE


def play_table(seats, seed):
    """
    rl_seat.play_selfplay, but a seat may also be a ready-made engine player (e.g. VariableCappedBot):
    seats[i] is a policy (-> RLSeat), an engine-bot name ("medium", "capped", ...), or a BasePlayer.
    Returns (record, rl_seats) like play_selfplay (None where the seat isn't an RLSeat).
    """
    from highsociety.code.ai import create_bot_players
    from highsociety.code.gamecore.game_manager.auction_history import AuctionHistory
    from highsociety.code.gamecore.game_manager.gameplay import PlayGame
    from highsociety.code.gamecore.player.player import BasePlayer
    from highsociety.code.gamecore.recording.game_replay import game_record

    if not 3 <= len(seats) <= 5:
        raise ValueError("a table needs 3-5 seats")
    rl = [None if isinstance(p, (str, BasePlayer)) else rs.RLSeat(p, name=f"RL {i}", username=f"rl {i}")
          for i, p in enumerate(seats)]
    with rs._fast():
        named = iter(create_bot_players([p for p in seats if isinstance(p, str)], think_time=0))
        players = [r if r is not None else (p if isinstance(p, BasePlayer) else next(named))
                   for r, p in zip(rl, seats)]
        game = PlayGame(players=players, spectators=[], mode="network", seed=seed, turn_duration=None,
                        auction_history=AuctionHistory(), pace_events=False, preseated=True)
        for r in rl:
            if r is not None:
                r.attach(game)
        game.play_game()
    return game_record(game), rl
