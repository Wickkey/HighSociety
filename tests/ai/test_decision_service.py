import random
import threading
import time

from highsociety.code.ai.mcts import decision_service
from highsociety.code.ai.mcts.decision_service import BotDecisionService
from highsociety.code.ai.mcts.search import MCTSConfig
from highsociety.code.ai.pass_bot import PassBot
from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.game_manager.auction_history import AuctionHistory
from highsociety.code.gamecore.game_manager.auction_information import summarize_card

_CONFIG = MCTSConfig(iterations=1, determinizations=1, exploration_constant=1.4)


def _history_and_live_state():
    bot = PassBot(name="Bot", username="bot")
    other = PassBot(name="Other", username="other")
    history = AuctionHistory()
    history.record_turn([bot, other])
    live_state = {"round_number": 1, "card": summarize_card(Painting(value=5)), "max_bid": 0, "turn_player": "bot"}
    return history, live_state


def test_decide_bid_with_no_timeout_is_unbounded_as_before(monkeypatch):
    """timeout=None (an untimed room, or any caller that hasn't opted into
    bounding) must behave exactly like before this feature existed -- wait
    for the real result, however long it takes."""
    def slow_decide_bid(*args, **kwargs):
        threading.Event().wait(0.2)
        return [3]

    monkeypatch.setattr(decision_service, "decide_bid", slow_decide_bid)
    history, live_state = _history_and_live_state()
    service = BotDecisionService()

    result = service.decide_bid(history, [], live_state, "bot", _CONFIG, random.Random(1))
    assert result == [3]


def test_decide_bid_falls_back_to_pass_when_it_exceeds_its_turn_budget(monkeypatch):
    """
    Regression test for a real gap: a bot's own decision was a fully
    unbounded call regardless of how much time was actually left on this
    player's own turn clock (see decide_bid's own comment) -- a slow
    decision (Hard difficulty without a worker pool, or just a loaded
    host) could silently run the whole room's turn past its configured
    limit, something every human player's own clock is held to strictly.
    A timeout must stop waiting and fall back to "pass" instead.
    """
    def slow_decide_bid(*args, **kwargs):
        threading.Event().wait(2.0)
        return [3]  # never actually reached before the timeout fires

    monkeypatch.setattr(decision_service, "decide_bid", slow_decide_bid)
    history, live_state = _history_and_live_state()
    service = BotDecisionService()

    started = time.time()
    result = service.decide_bid(history, [], live_state, "bot", _CONFIG, random.Random(1), timeout=0.2)
    elapsed = time.time() - started

    assert result == "pass"
    assert elapsed < 1.0, f"should give up near its own timeout, took {elapsed}s"


def test_decide_bid_zero_timeout_resolves_to_pass_immediately_not_unbounded(monkeypatch):
    """
    0 (as opposed to None) means "bounded, with essentially no time left"
    -- e.g. WorkerPoolBotDecisionService's own fallback after its pool
    attempt already used up the whole budget. `if not timeout` would treat
    0 the same as None (falsy) and run the real, unbounded decision
    instead of resolving to "pass" right away.
    """
    def slow_decide_bid(*args, **kwargs):
        threading.Event().wait(2.0)
        return [3]

    monkeypatch.setattr(decision_service, "decide_bid", slow_decide_bid)
    history, live_state = _history_and_live_state()
    service = BotDecisionService()

    started = time.time()
    result = service.decide_bid(history, [], live_state, "bot", _CONFIG, random.Random(1), timeout=0.0)
    elapsed = time.time() - started

    assert result == "pass"
    assert elapsed < 1.0, f"should resolve near-instantly, took {elapsed}s"
