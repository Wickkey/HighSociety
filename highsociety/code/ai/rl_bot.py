"""
An embedded bot driven by a neural network trained by self-play reinforcement
learning (the research code lives in .RL/, outside the deployed app), instead
of a search like MCTSBot or a fixed heuristic like CappedGreedyBot.

Offered in the lobby as the "Insane" difficulty (see common/bot_roster.py):
InsaneBot plays whichever weights file weights/active.json names for
"insane", so shipping a better-trained network is: export it with
.RL/v2/export_v2.py --activate, commit, deploy -- no code change.

Like MCTSBot this class is deliberately thin and keeps no state of its own
across the game: every decision is a pure function of the public game state
PlayGame wires into every player (get_current_auction_history()/
get_auction_history()/get_live_auction_state()), delegated to rl/decision.py.
The network runs in numpy (rl/network.py) -- a few milliseconds per move, so
no worker pool is needed.
"""
import json
import time
from typing import Optional, Union

from highsociety.code.ai.rl import decision
from highsociety.code.ai.rl.network import WEIGHTS_DIR, load_network
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager
from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.player.player import BasePlayer


def active_weights(difficulty: str) -> str:
    """Name of the weights file a difficulty plays with (weights/active.json)."""
    return json.loads((WEIGHTS_DIR / "active.json").read_text())[difficulty]


class RLBot(BasePlayer):
    def __init__(self, name: str, username: str, weights: str, think_time: float = 0,
                 difficulty: str = "custom") -> None:
        """
        weights: name of a weights/<name>.npz file (+ its <name>.json manifest).
        think_time: same meaning as every other bot's -- purely a pause for a
        human-watchable game; the decision itself takes milliseconds.
        difficulty: "insane" for the lobby preset (game_history.py attributes
        finished games to roster bots by it), "custom" for ad hoc use.
        """
        super().__init__(name, username)
        self.active = True
        self._net = load_network(weights)
        self._weights = weights
        self._think_time = think_time
        self._difficulty = difficulty

    @property
    def difficulty(self) -> str:
        return self._difficulty

    @property
    def weights(self) -> str:
        return self._weights

    def send_message(self, message: str, message_type: str = None, created_at: float = None, **kwargs) -> None:
        pass    # everything it needs is read from the live game state at decision time

    def get_bid(self, timeout: Optional[float] = None) -> Union[list[int], str, None]:
        started_at = time.time()
        try:
            move = decision.decide_bid(
                self._net,
                auction_history=self.get_current_auction_history(),
                event_log=self.get_auction_history(),
                live_state=self.get_live_auction_state(),
                username=self.username,
            )
        except Exception as e:     # never stall or crash a live game over a bot bug: pass instead
            LoggingManager.warning(f"RLBot {self.username}: decision failed ({e!r}); passing")
            move = "pass"
        self._pace_think_time(timeout, started_at=started_at)
        return move

    def choose_painting_to_discard(self) -> Optional[Painting]:
        value = decision.decide_faux_pas_discard([{"type": type(c).__name__, "value": c.value}
                                                  for c in self.status_cards])
        if value is None:
            return None
        return next(c for c in self.status_cards if isinstance(c, Painting) and c.value == value)


class InsaneBot(RLBot):
    """The lobby's "Insane" difficulty: the RL network named in weights/active.json."""

    def __init__(self, name: str, username: str, think_time: float = 0) -> None:
        super().__init__(name, username, weights=active_weights("insane"), think_time=think_time,
                         difficulty="insane")
