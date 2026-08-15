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
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Optional, Union

from highsociety.code.ai.mcts.search import MCTSConfig
from highsociety.code.ai.mcts.stateless_decision import decide_bid, decide_faux_pas_discard
from highsociety.code.gamecore.game_manager.auction_history import AuctionHistory
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager

# One shared pool for every bounded, in-process decide_bid() call across the
# whole process (see decide_bid's own comment for why a bound exists at
# all) -- decisions are CPU-bound and already serialize through the GIL
# regardless of how many worker threads exist, so this isn't about
# concurrency, only about being able to stop *waiting* on a call that's
# run past its turn's own deadline without leaking a fresh
# ThreadPoolExecutor (and its own worker thread) every single bid.
_bounded_call_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="bot-decision")


class BotDecisionService:
    def decide_bid(self, auction_history: AuctionHistory, event_log: list[dict], live_state: dict,
                    username: str, config: MCTSConfig, rng: random.Random,
                    difficulty: str = "custom", timeout: Optional[float] = None) -> Union[list[int], str]:
        """
        difficulty ("easy"/"medium"/"hard", or "custom" for an ad hoc
        MCTSBot) is unused here -- this base implementation always computes
        locally, in-process. It exists on the interface so a subclass that
        DOES care (e.g. WorkerPoolBotDecisionService, routing to one of
        several per-difficulty worker pools) can, without every other
        implementation needing to know or care about pools at all.

        timeout: MCTSBot.get_bid() passes the *actual* seconds left on this
        player's own turn clock (None for an untimed room). Without this,
        a bot's own decision was a fully unbounded, synchronous call --
        gameplay.py's TurnClock deadline (the thing every human player's
        clock actually counts down to) had no effect on it whatsoever, so
        a slow decision (Hard difficulty without a worker pool, or just a
        loaded host) could silently run the whole room's turn past its own
        configured limit. A timeout here runs the same computation on a
        worker thread and stops *waiting* on it once the deadline passes,
        falling back to "pass" (always a legal response, in every auction
        type) rather than let one slow bot decision block the table.
        """
        if timeout is None:
            # None means genuinely no turn limit (an untimed room) --
            # anything else, including 0/0.0 (no time left at all, e.g.
            # WorkerPoolBotDecisionService's own fallback after its pool
            # attempt already used the whole budget), must still go
            # through the bounded path below so it resolves to "pass"
            # immediately instead of being treated as unbounded.
            return decide_bid(auction_history, event_log, live_state, username, config, rng)
        future = _bounded_call_pool.submit(decide_bid, auction_history, event_log, live_state, username, config, rng)
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError:
            # The submitted call keeps running in the background (Python
            # can't forcibly cancel a running thread) and its eventual
            # result is simply discarded -- wasted CPU for one decision,
            # never a leak, and never anything this call waits on again.
            LoggingManager.warning(
                f"bot decision for {username!r} (difficulty={difficulty!r}) exceeded its {timeout:.2f}s "
                "turn budget; falling back to pass"
            )
            return "pass"

    def decide_faux_pas_discard(self, my_status_cards: list[dict]) -> Optional[int]:
        return decide_faux_pas_discard(my_status_cards)


default_decision_service = BotDecisionService()
