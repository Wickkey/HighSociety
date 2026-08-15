import random
import threading
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


def _broke_game_facing_a_high_bid():
    """Bot down to a single 1-value card; opponent already bid 50 -- the
    only correct decision is "pass" (no single card can legally beat 50)."""
    bot = PassBot(name="BotA", username="bot_a")
    for card in list(bot.money_cards):
        if card.value != 1:
            bot.place_bid(card.value)  # commit away everything except the 1
    other = PassBot(name="OtherA", username="other_a")
    history = AuctionHistory()
    history.record_turn([bot, other])
    live_state = {"round_number": 1, "card": summarize_card(Painting(value=8)),
                  "max_bid": 50, "turn_player": "bot_a"}
    return history, live_state, "bot_a"


def _rich_uncontested_game():
    """Bot has its full starting hand; nobody has bid yet -- the only
    sensible decision is a raise (a [value] list), never "pass"."""
    bot = PassBot(name="BotB", username="bot_b")
    other = PassBot(name="OtherB", username="other_b")
    history = AuctionHistory()
    history.record_turn([bot, other])
    live_state = {"round_number": 1, "card": summarize_card(Painting(value=8)),
                  "max_bid": 0, "turn_player": "bot_b"}
    return history, live_state, "bot_b"


class TestConcurrentGamesDontCrossContaminate:
    """
    Two deliberately different, easily-distinguishable game states, decided
    concurrently (two real threads, sharing one pool) many times over --
    if the shared pool ever mixed up which response belongs to which
    request, it would show up as an obviously wrong decision (the broke bot
    "raising" with money it doesn't have, or the rich uncontested bot
    inexplicably "passing"), not just a subtly-off one.
    """

    def test_two_concurrent_games_each_get_their_own_correct_decision(self):
        config = MCTSConfig(iterations=30, determinizations=2, exploration_constant=1.4)
        service = WorkerPoolBotDecisionService(pool_size=2, request_timeout_seconds=15.0)
        results = {}

        def run_many(make_game, key, rounds=8):
            outcomes = []
            for i in range(rounds):
                history, live_state, username = make_game()
                outcomes.append(
                    service.decide_bid(history, [], live_state, username, config,
                                        random.Random(i), difficulty="medium")
                )
            results[key] = outcomes

        t_broke = threading.Thread(target=run_many, args=(_broke_game_facing_a_high_bid, "broke"))
        t_rich = threading.Thread(target=run_many, args=(_rich_uncontested_game, "rich"))
        t_broke.start()
        t_rich.start()
        t_broke.join(timeout=60)
        t_rich.join(timeout=60)

        assert results["broke"] == ["pass"] * 8, \
            f"broke bot should always pass -- got {results['broke']}"
        assert all(isinstance(o, list) for o in results["rich"]), \
            f"rich, uncontested bot should always raise, never pass -- got {results['rich']}"
