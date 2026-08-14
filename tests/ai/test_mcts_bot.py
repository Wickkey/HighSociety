import time

import pytest

from highsociety.code.ai.mcts.search import MCTSConfig
from highsociety.code.ai.mcts_bot import DIFFICULTY_PRESETS, EasyMCTSBot, HardMCTSBot, MCTSBot, MediumMCTSBot
from highsociety.code.ai.pass_bot import PassBot
from highsociety.code.ai.greedy_bot import GreedyBot
from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.game_manager.auction_history import AuctionHistory
from highsociety.code.gamecore.game_manager.auction_information import summarize_card
from highsociety.code.gamecore.game_manager.gameplay import PlayGame

_TINY_CONFIG = MCTSConfig(iterations=10, determinizations=1, exploration_constant=1.4)


@pytest.fixture
def bot():
    return MCTSBot(name="Bot", username="bot", config=_TINY_CONFIG)


def _wire(bot, *others, seed=None):
    """Constructs a real PlayGame around `bot` (+ any other players) purely
    to trigger PlayGame.__init__'s live-reference wiring (get_auction_history/
    get_current_auction_history/get_live_auction_state) -- the same wiring a
    real game gives every player, needed for get_bid() to have anything to
    read now that MCTSBot no longer accumulates its own state."""
    game = PlayGame(players=[bot, *others], mode="cli", seed=seed, auction_history=AuctionHistory())
    return game


def _set_live_auction(game, card, max_bid=0):
    """Manually injects "what's up for auction right now" the same way
    _broadcast_auction_update/_record_auction_history_snapshot would inside
    a real auction loop -- lets a test call bot.get_bid() directly without
    driving the full normal_card_auction()/disgrace_card_auction() loop."""
    game._live_auction_state["card"] = summarize_card(card)
    game._live_auction_state["max_bid"] = max_bid
    game.auction_history.record_turn(game.players)


class TestSendMessage:
    def test_is_a_safe_no_op(self, bot):
        # get_current_auction_history()/get_live_auction_state() (both live
        # references PlayGame wires up) replace everything this used to
        # scrape out of narration text -- confirm it's simply inert now.
        bot.send_message("anything", message_type="PLAYER_INFO")
        bot.send_message("", message_type="GLOBAL_MOVE_INFO")


class TestGetBid:
    def test_returns_pass_with_no_money_left(self, bot):
        other = PassBot(name="Other", username="other")
        game = _wire(bot, other)
        for card in list(bot.money_cards):
            bot.place_bid(card.value)  # no money left -- "pass" is the only legal action
        _set_live_auction(game, Painting(value=5))
        assert bot.get_bid() == "pass"

    def test_returns_a_raise_as_a_single_element_list(self, bot):
        other = PassBot(name="Other", username="other")
        game = _wire(bot, other)
        _set_live_auction(game, Painting(value=5), max_bid=100)  # nothing can beat this except folding
        result = bot.get_bid()
        assert result == "pass" or (isinstance(result, list) and len(result) == 1 and isinstance(result[0], int))

    def test_respects_think_time(self, monkeypatch):
        # tests/conftest.py's autouse fixture no-ops time.sleep suite-wide
        # (skips PlayGame's own human-pacing sleeps) -- verify the *call*
        # happened with the right delay instead of real elapsed time.
        sleeps = []
        monkeypatch.setattr(time, "sleep", lambda seconds: sleeps.append(seconds))
        # think_time=0.2 is below BotInterface.MIN_THINK_TIME_SECONDS (1.8) --
        # every bot's _pace_think_time() floors at that minimum, so 1.8 is
        # actually what should get slept, not the raw configured value.
        slow_bot = MCTSBot(name="Bot", username="bot", config=_TINY_CONFIG, think_time=0.2)
        other = PassBot(name="Other", username="other")
        game = _wire(slow_bot, other)
        _set_live_auction(game, Painting(value=5))
        slow_bot.get_bid()
        assert 1.8 in sleeps


class TestChoosePaintingToDiscard:
    def test_discards_the_cheapest_painting(self, bot):
        bot.add_status_card(Painting(value=7))
        bot.add_status_card(Painting(value=2))
        assert bot.choose_painting_to_discard().value == 2

    def test_returns_none_without_paintings(self, bot):
        assert bot.choose_painting_to_discard() is None


class TestDifficultyPresetsAndFactories:
    def test_all_three_difficulties_are_registered(self):
        assert set(DIFFICULTY_PRESETS.keys()) == {"easy", "medium", "hard"}

    def test_harder_presets_search_at_least_as_much(self):
        easy, medium, hard = DIFFICULTY_PRESETS["easy"], DIFFICULTY_PRESETS["medium"], DIFFICULTY_PRESETS["hard"]
        easy_total = easy.iterations * easy.determinizations
        medium_total = medium.iterations * medium.determinizations
        hard_total = hard.iterations * hard.determinizations
        assert easy_total < medium_total < hard_total

    @pytest.mark.parametrize("cls,difficulty", [(EasyMCTSBot, "easy"), (MediumMCTSBot, "medium"), (HardMCTSBot, "hard")])
    def test_factory_subclasses_use_the_matching_preset(self, cls, difficulty):
        instance = cls(name="Bot", username="bot")
        assert instance._config == DIFFICULTY_PRESETS[difficulty]

    def test_factory_subclasses_match_create_bot_players_call_signature(self):
        # highsociety/code/ai/__init__.py's create_bot_players always calls
        # BOT_TYPES[bot_type](name=name, username=name.lower(), think_time=think_time)
        # -- every difficulty class must accept exactly that.
        for cls in (EasyMCTSBot, MediumMCTSBot, HardMCTSBot):
            instance = cls(name="Bot", username="bot", think_time=1.5)
            assert instance.active is True


class TestFullGameIntegration:
    @pytest.mark.parametrize("cls", [EasyMCTSBot, MediumMCTSBot])
    def test_completes_a_full_game_against_a_pass_bot_without_crashing(self, cls, monkeypatch):
        import time as time_module
        monkeypatch.setattr(time_module, "sleep", lambda *a, **kw: None)  # skip PlayGame's own human-pacing sleeps

        mcts_player = cls(name="Bot", username="bot")
        passer = PassBot(name="Passer", username="passer")
        game = PlayGame(players=[mcts_player, passer], mode="cli", seed=7, auction_history=AuctionHistory())

        game.play_game()

        assert game.winners is not None
        assert len(game.final_standings) == 2

    def test_completes_a_full_three_player_game(self, monkeypatch):
        import time as time_module
        monkeypatch.setattr(time_module, "sleep", lambda *a, **kw: None)

        players = [
            EasyMCTSBot(name="Bot", username="bot"),
            GreedyBot(name="Greedy", username="greedy"),
            PassBot(name="Passer", username="passer"),
        ]
        game = PlayGame(players=players, mode="cli", seed=3, auction_history=AuctionHistory())

        game.play_game()

    def test_completes_a_full_game_with_a_faux_pas_holding_opponent(self, monkeypatch):
        """
        Integration-level check for the fixed accuracy gap: an opponent
        holding an undischarged Faux Pas must not crash or stall the bot's
        own decision-making across a real, full game -- precise assertions
        on faux_pas_holder detection live in test_stateless_decision.py;
        this just confirms the wiring holds up end-to-end.
        """
        import time as time_module
        monkeypatch.setattr(time_module, "sleep", lambda *a, **kw: None)

        players = [
            EasyMCTSBot(name="Bot", username="bot"),
            GreedyBot(name="Greedy", username="greedy"),
        ]
        game = PlayGame(players=players, mode="cli", seed=5, auction_history=AuctionHistory())

        game.play_game()

        assert game.winners is not None
