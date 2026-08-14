"""
A single injectable seam between MCTSBot and the actual decision logic
(stateless_decision.py) -- today's implementation just calls those pure
functions directly, in-process. The point of routing through a class
instance rather than MCTSBot calling decide_bid()/decide_faux_pas_discard()
itself: swapping this for a real networked bot-service client later (per
BACKEND_REWORK.MD's "the three different-level bots are always alive"
goal -- bot compute living outside any single game's process, reachable by
several concurrent rooms) becomes a matter of pointing
`default_decision_service` at a different implementation of this same
interface, not rewriting MCTSBot.
"""
import random
from typing import Optional, Union

from highsociety.code.ai.mcts.search import MCTSConfig
from highsociety.code.ai.mcts.stateless_decision import decide_bid, decide_faux_pas_discard
from highsociety.code.gamecore.game_manager.auction_history import AuctionHistory


class BotDecisionService:
    def decide_bid(self, auction_history: AuctionHistory, event_log: list[dict], live_state: dict,
                    username: str, config: MCTSConfig, rng: random.Random,
                    difficulty: str = "custom") -> Union[list[int], str]:
        """
        difficulty ("easy"/"medium"/"hard", or "custom" for an ad hoc
        MCTSBot) is unused here -- this base implementation always computes
        locally, in-process. It exists on the interface so a subclass that
        DOES care (e.g. WorkerPoolBotDecisionService, routing to one of
        several per-difficulty worker pools) can, without every other
        implementation needing to know or care about pools at all.
        """
        return decide_bid(auction_history, event_log, live_state, username, config, rng)

    def decide_faux_pas_discard(self, my_status_cards: list[dict]) -> Optional[int]:
        return decide_faux_pas_discard(my_status_cards)


default_decision_service = BotDecisionService()
