"""
An embedded bot that picks its bid via Monte Carlo Tree Search (see
highsociety/code/ai/mcts/) instead of a fixed heuristic like GreedyBot/
CappedGreedyBot. Exposed to players only as three tuned difficulty presets
(Easy/Medium/Hard — see DIFFICULTY_PRESETS) rather than as a raw "mcts" bot
type with knobs to configure; the difficulty *is* the bot type, the same way
choosing "greedy" already hides GreedyBot's own fixed behavior.

This class itself is deliberately thin: it holds only per-instance tuning
(the MCTSConfig, think_time) and delegates every actual decision to
highsociety/code/ai/mcts/decision_service.py, gathering the game state that
decision needs from two live references PlayGame.__init__ wires into every
player (get_current_auction_history()/get_live_auction_state() — see
BotInterface) plus its own live BasePlayer properties, rather than
accumulating any state of its own across the game. See
mcts/stateless_decision.py's module docstring for what's exact vs.
approximated once that state becomes a SimState — the one thing nothing
makes exactly knowable is the future status card draw order, sampled fresh
for each decision (mcts/simulation.py's sample_future_deck).
"""
import random
import time
from typing import Optional, Union

from highsociety.code.ai.mcts import decision_service
from highsociety.code.ai.mcts.search import MCTSConfig
from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.game_manager.auction_information import summarize_card
from highsociety.code.gamecore.player.player import BasePlayer

class MCTSBot(BasePlayer):
    def __init__(self, name: str, username: str, config: MCTSConfig, think_time: float = 0,
                 difficulty: str = "custom") -> None:
        """
        think_time: seconds to pause before returning a decision from
        get_bid(), same meaning as every other bot's — purely for a human-
        watchable simulation; the search itself has no use for it (a fresh
        SimState-based search is fast enough that "how long to think" is
        entirely a UX knob, not a real compute budget).

        difficulty: which named DIFFICULTY_PRESETS entry this is ("easy"/
        "medium"/"hard") — Easy/Medium/HardMCTSBot below always pass their
        real name; only matters for routing to the right worker pool if
        default_decision_service is a WorkerPoolBotDecisionService (see
        decision_service.py) — the default "custom" is fine for direct
        MCTSBot construction with an ad hoc config (tests, dev tools).
        """
        super().__init__(name, username)
        self.active = True
        self._config = config
        self._think_time = think_time
        self._difficulty = difficulty

    def send_message(self, message: str, message_type: str = None, created_at: float = None, **kwargs) -> None:
        # Nothing to track -- get_current_auction_history()/
        # get_live_auction_state() (both live references PlayGame.__init__
        # wires up) already give this bot everything it used to have to
        # scrape out of narration text, and more completely.
        pass

    def get_bid(self, timeout: Optional[float] = None) -> Union[list[int], str, None]:
        started_at = time.time()
        self._pace_think_time(timeout)
        # Whatever's left of this turn's own budget *after* the pacing
        # pause above -- passing the original, full timeout again here
        # would let the pause and the actual search each separately run
        # close to the full deadline, adding up to roughly double the
        # real turn budget instead of respecting it.
        remaining_timeout = None if timeout is None else max(0.0, timeout - (time.time() - started_at))
        # A fresh Random() per call, not a shared instance reused across
        # calls: this may cross a process boundary now (see
        # WorkerPoolBotDecisionService) -- pickling a shared rng sends a
        # snapshot of its state, and a worker's own mutations to its copy
        # never propagate back, so reusing one instance across pooled calls
        # would silently make every pooled decision draw the exact same
        # "random" shuffle. A fresh instance sidesteps that entirely (and
        # doesn't touch gameplay.py's own seeded `random` module state,
        # same reasoning as before -- see PlayGame.__init__'s random.seed()).
        rng = random.Random()
        # decide_bid() already returns "pass" or a [value] list -- BotDecisionService
        # just passes that straight through, nothing to re-wrap here.
        #
        # decision_service.default_decision_service, not a `from ... import
        # default_decision_service` name bound once at import time: a
        # `from` import captures whatever the module's attribute pointed to
        # at THAT moment, and never sees a later reassignment (see
        # web_server.py's BOT_POOL_SIZE wiring, which swaps this attribute
        # after mcts_bot.py has already been imported) -- looking it up
        # through the module on every call is what makes that swap actually
        # take effect. Caught live: with a stale binding, BOT_POOL_SIZE
        # silently did nothing -- no worker processes ever spawned, no
        # error either, since the code just kept quietly calling the
        # original in-process BotDecisionService instead.
        return decision_service.default_decision_service.decide_bid(
            auction_history=self.get_current_auction_history(),
            event_log=self.get_auction_history(),
            live_state=self.get_live_auction_state(),
            username=self.username,
            config=self._config,
            rng=rng,
            difficulty=self._difficulty,
            timeout=remaining_timeout,
        )

    def choose_painting_to_discard(self) -> Optional[Painting]:
        my_status_cards = [summarize_card(c) for c in self.status_cards]
        value = decision_service.default_decision_service.decide_faux_pas_discard(my_status_cards)
        if value is None:
            return None
        return next(c for c in self.status_cards if isinstance(c, Painting) and c.value == value)


DIFFICULTY_PRESETS = {
    # Tuned by feel, not measurement — bot_evaluator.py (see repo root) is
    # the intended tool for actually comparing these against each other and
    # adjusting the numbers.
    "easy": MCTSConfig(iterations=20, determinizations=2, exploration_constant=1.4),
    "medium": MCTSConfig(iterations=120, determinizations=5, exploration_constant=1.4),
    "hard": MCTSConfig(iterations=500, determinizations=8, exploration_constant=1.0),
}


class EasyMCTSBot(MCTSBot):
    def __init__(self, name: str, username: str, think_time: float = 0) -> None:
        super().__init__(name, username, config=DIFFICULTY_PRESETS["easy"], think_time=think_time,
                          difficulty="easy")


class MediumMCTSBot(MCTSBot):
    def __init__(self, name: str, username: str, think_time: float = 0) -> None:
        super().__init__(name, username, config=DIFFICULTY_PRESETS["medium"], think_time=think_time,
                          difficulty="medium")


class HardMCTSBot(MCTSBot):
    def __init__(self, name: str, username: str, think_time: float = 0) -> None:
        super().__init__(name, username, config=DIFFICULTY_PRESETS["hard"], think_time=think_time,
                          difficulty="hard")
