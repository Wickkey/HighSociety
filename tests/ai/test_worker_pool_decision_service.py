import random
import time

import pytest

from highsociety.code.ai.mcts.search import MCTSConfig
from highsociety.code.ai.mcts.stateless_decision import decide_bid as decide_bid_direct
from highsociety.code.ai.mcts.worker_pool_decision_service import WorkerPoolBotDecisionService
from highsociety.code.ai.pass_bot import PassBot
from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.game_manager.auction_history import AuctionHistory
from highsociety.code.gamecore.game_manager.auction_information import summarize_card

_TINY_CONFIG = MCTSConfig(iterations=10, determinizations=1, exploration_constant=1.4)


def _history_and_live_state():
    bot = PassBot(name="Bot", username="bot")
    other = PassBot(name="Other", username="other")
    history = AuctionHistory()
    history.record_turn([bot, other])
    live_state = {"round_number": 1, "card": summarize_card(Painting(value=5)), "max_bid": 0, "turn_player": "bot"}
    return history, live_state


class TestLazyPoolLifecycle:
    def test_no_pools_before_any_call(self):
        service = WorkerPoolBotDecisionService(pool_size=1)
        assert service._pools == {}

    def test_real_pool_computes_a_legal_decision(self):
        """
        The one real, end-to-end round trip through an actual subprocess --
        confirms a worker genuinely computes (not just that the mock
        plumbing works), including pickling AuctionHistory/MCTSConfig/
        random.Random across the process boundary and back.
        """
        history, live_state = _history_and_live_state()
        service = WorkerPoolBotDecisionService(pool_size=1, request_timeout_seconds=10.0)
        action = service.decide_bid(history, [], live_state, "bot", _TINY_CONFIG, random.Random(1),
                                     difficulty="easy")
        assert action == "pass" or (isinstance(action, list) and len(action) == 1 and isinstance(action[0], int))
        assert "easy" in service._pools

    def test_reuses_the_same_pool_for_repeated_calls_with_the_same_difficulty(self):
        history, live_state = _history_and_live_state()
        service = WorkerPoolBotDecisionService(pool_size=1, request_timeout_seconds=10.0)
        service.decide_bid(history, [], live_state, "bot", _TINY_CONFIG, random.Random(1), difficulty="easy")
        pool_after_first = service._pools["easy"]
        service.decide_bid(history, [], live_state, "bot", _TINY_CONFIG, random.Random(2), difficulty="easy")
        assert service._pools["easy"] is pool_after_first

    def test_separate_pools_for_separate_difficulties(self):
        history, live_state = _history_and_live_state()
        service = WorkerPoolBotDecisionService(pool_size=1, request_timeout_seconds=10.0)
        service.decide_bid(history, [], live_state, "bot", _TINY_CONFIG, random.Random(1), difficulty="easy")
        service.decide_bid(history, [], live_state, "bot", _TINY_CONFIG, random.Random(1), difficulty="medium")
        assert set(service._pools.keys()) == {"easy", "medium"}
        assert service._pools["easy"] is not service._pools["medium"]

    def test_reap_idle_pools_tears_down_after_timeout_and_recreates_lazily(self):
        history, live_state = _history_and_live_state()
        service = WorkerPoolBotDecisionService(pool_size=1, idle_timeout_seconds=0.01,
                                                request_timeout_seconds=10.0)
        service.decide_bid(history, [], live_state, "bot", _TINY_CONFIG, random.Random(1), difficulty="easy")
        assert "easy" in service._pools

        time.sleep(0.05)
        service.reap_idle_pools()
        assert service._pools == {}

        # Next call re-creates it lazily, same as if it had never run.
        service.decide_bid(history, [], live_state, "bot", _TINY_CONFIG, random.Random(1), difficulty="easy")
        assert "easy" in service._pools

    def test_reap_idle_pools_leaves_recently_used_pools_alone(self):
        history, live_state = _history_and_live_state()
        service = WorkerPoolBotDecisionService(pool_size=1, idle_timeout_seconds=300.0,
                                                request_timeout_seconds=10.0)
        service.decide_bid(history, [], live_state, "bot", _TINY_CONFIG, random.Random(1), difficulty="easy")
        service.reap_idle_pools()
        assert "easy" in service._pools  # 300s timeout, nowhere near stale yet


class TestFallback:
    def test_falls_back_to_local_computation_when_the_pool_fails(self):
        """A pool that can't even accept work (submit() itself raises) must
        not crash the caller -- the decision still comes back, computed
        locally instead."""
        history, live_state = _history_and_live_state()
        service = WorkerPoolBotDecisionService(pool_size=1, request_timeout_seconds=10.0)

        class BrokenPool:
            def submit(self, *args, **kwargs):
                raise RuntimeError("simulated worker crash")

        service._pools["easy"] = BrokenPool()
        service._last_used_at["easy"] = time.time()

        action = service.decide_bid(history, [], live_state, "bot", _TINY_CONFIG, random.Random(1),
                                     difficulty="easy")
        assert action == "pass" or (isinstance(action, list) and len(action) == 1 and isinstance(action[0], int))

    def test_falls_back_when_the_future_times_out(self):
        history, live_state = _history_and_live_state()
        service = WorkerPoolBotDecisionService(pool_size=1, request_timeout_seconds=10.0)

        class NeverFinishes:
            def result(self, timeout=None):
                import concurrent.futures
                raise concurrent.futures.TimeoutError()

        class SlowPool:
            def submit(self, *args, **kwargs):
                return NeverFinishes()

        service._pools["easy"] = SlowPool()
        service._last_used_at["easy"] = time.time()

        action = service.decide_bid(history, [], live_state, "bot", _TINY_CONFIG, random.Random(1),
                                     difficulty="easy")
        assert action == "pass" or (isinstance(action, list) and len(action) == 1 and isinstance(action[0], int))


class TestBaseServiceIgnoresDifficulty:
    def test_default_decision_service_still_computes_locally_regardless_of_difficulty(self):
        """Sanity check that the base BotDecisionService (what everyone gets
        unless BOT_POOL_SIZE is set) is unaffected by the new difficulty
        parameter -- confirms adding it didn't change default behavior."""
        from highsociety.code.ai.mcts.decision_service import BotDecisionService
        history, live_state = _history_and_live_state()
        service = BotDecisionService()
        action = service.decide_bid(history, [], live_state, "bot", _TINY_CONFIG, random.Random(1),
                                     difficulty="hard")
        direct = decide_bid_direct(history, [], live_state, "bot", _TINY_CONFIG, random.Random(1))
        assert action == direct  # same seed, same everything -- identical result
