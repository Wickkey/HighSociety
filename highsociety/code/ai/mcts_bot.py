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
from typing import Optional, Union

from highsociety.code.ai.mcts.decision_service import default_decision_service
from highsociety.code.ai.mcts.search import MCTSConfig
from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.game_manager.auction_information import summarize_card
from highsociety.code.gamecore.player.player import BasePlayer

# A dedicated Random instance per decision, rather than the `random` module's
# shared global state — gameplay.py calls random.seed(...) for reproducible
# games (see PlayGame.__init__), and drawing from the same global generator
# here would both consume from that sequence (breaking reproducibility for
# tests/replays) and make this bot's own sampling accidentally deterministic
# across a whole seeded game instead of independently random each decision.
_process_rng = random.Random()


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
        self._pace_think_time()
        # decide_bid() already returns "pass" or a [value] list -- BotDecisionService
        # just passes that straight through, nothing to re-wrap here.
        return default_decision_service.decide_bid(
            auction_history=self.get_current_auction_history(),
            event_log=self.get_auction_history(),
            live_state=self.get_live_auction_state(),
            username=self.username,
            config=self._config,
            rng=_process_rng,
            difficulty=self._difficulty,
        )

    def choose_painting_to_discard(self) -> Optional[Painting]:
        my_status_cards = [summarize_card(c) for c in self.status_cards]
        value = default_decision_service.decide_faux_pas_discard(my_status_cards)
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
