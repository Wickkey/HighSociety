import time

import pytest

from highsociety.code.ai.mcts.search import MCTSConfig
from highsociety.code.ai.mcts_bot import DIFFICULTY_PRESETS, EasyMCTSBot, HardMCTSBot, MCTSBot, MediumMCTSBot
from highsociety.code.ai.pass_bot import PassBot
from highsociety.code.ai.greedy_bot import GreedyBot
from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.components_module.prestige_card import PrestigeCard
from highsociety.code.gamecore.game_manager.gameplay import PlayGame

_TINY_CONFIG = MCTSConfig(iterations=10, determinizations=1, exploration_constant=1.4)


@pytest.fixture
def bot():
    return MCTSBot(name="Bot", username="bot", config=_TINY_CONFIG)


def _tell(bot, message, message_type="PLAYER_INFO"):
    bot.send_message(message, message_type=message_type)


class TestMessageTracking:
    def test_tracks_current_highest_bid(self, bot):
        _tell(bot, "\nCurrent Highest Bid: 7")
        assert bot._current_max_bid == 7

    def test_tracks_auctioned_card_type_and_value(self, bot):
        _tell(bot, "\nAuctioning: Painting (value=5)")
        assert bot._current_card_type == "Painting"
        assert bot._current_card_value == 5

    def test_tracks_negative_valued_cards(self, bot):
        _tell(bot, "\nAuctioning: Passe (value=-5)")
        assert bot._current_card_value == -5

    def test_overhears_opponent_usernames_from_turn_narration(self, bot):
        _tell(bot, "alice's turn. Player is playing..", message_type="GLOBAL_MOVE_INFO")
        _tell(bot, "bob's turn. Player is playing..", message_type="GLOBAL_MOVE_INFO")
        assert bot._overheard_usernames == {"alice", "bob"}

    def test_ignores_unrelated_message_types(self, bot):
        _tell(bot, "\nCurrent Highest Bid: 99", message_type="INPUT_ERROR")
        assert bot._current_max_bid == 0


class TestKnownOpponentUsernames:
    def test_falls_back_to_overheard_usernames_before_any_auction_concludes(self, bot):
        _tell(bot, "alice's turn. Player is playing..", message_type="GLOBAL_MOVE_INFO")
        assert bot._known_opponent_usernames() == ["alice"]

    def test_falls_back_to_a_placeholder_with_zero_information(self, bot):
        assert bot._known_opponent_usernames() == ["?opponent"]

    def test_uses_the_exact_roster_from_auction_history_once_available(self, bot):
        other = PassBot(name="Other", username="other")
        game = PlayGame(players=[bot, other], mode="cli")
        game.normal_card_auction(Painting(value=5), starting_player_id=0)
        assert bot._known_opponent_usernames() == ["other"]


class TestBuildState:
    def test_my_own_hand_is_exact(self, bot):
        _tell(bot, "\nAuctioning: Painting (value=5)")
        _tell(bot, "\nCurrent Highest Bid: 0")
        state, me_idx = bot._build_state()
        assert state.players[me_idx].money_cards == sorted(c.value for c in bot.money_cards)

    def test_reconstructs_an_opponents_remaining_hand_from_history(self, bot):
        other = GreedyBot(name="Other", username="other")
        game = PlayGame(players=[bot, other], mode="cli")
        game.normal_card_auction(Painting(value=5), starting_player_id=1)  # other goes first, bids 1

        _tell(bot, "\nAuctioning: Painting (value=3)")
        _tell(bot, "\nCurrent Highest Bid: 0")
        state, me_idx = bot._build_state()
        opponent = next(p for p in state.players if p.username == "other")
        full = sorted(c.value for c in PassBot(name="x", username="x").money_cards)
        spent = game.auction_rounds[0].cards_spent["other"]
        assert opponent.money_cards == sorted(set(full) - set(spent))

    def test_reconstructs_an_opponents_won_status_cards(self, bot):
        other = GreedyBot(name="Other", username="other")
        game = PlayGame(players=[other, bot], mode="cli")  # other starts and wins for free
        game.normal_card_auction(Painting(value=7), starting_player_id=0)

        _tell(bot, "\nAuctioning: Painting (value=3)")
        _tell(bot, "\nCurrent Highest Bid: 0")
        state, me_idx = bot._build_state()
        opponent = next(p for p in state.players if p.username == "other")
        assert any(c.kind == "Painting" and c.value == 7 for c in opponent.status_cards)

    def test_marks_a_quit_opponent_as_inactive(self, bot):
        quitter = PassBot(name="Quitter", username="quitter")
        game = PlayGame(players=[bot, quitter], mode="cli")
        quitter.active = False  # simulate a quit having already happened
        # A quit is recorded via a disgrace or normal auction's "quit" event;
        # simplest reliable way to produce one here is to drive it directly.
        from highsociety.code.gamecore.components_module.disgrace_card import Passe
        quitter.active = True
        original_get_bid = quitter.get_bid
        quitter.get_bid = lambda timeout=None: "quit"
        game.disgrace_card_auction(current_player_id=1, status_card=Passe())

        _tell(bot, "\nAuctioning: Painting (value=3)")
        _tell(bot, "\nCurrent Highest Bid: 0")
        state, me_idx = bot._build_state()
        opponent = next(p for p in state.players if p.username == "quitter")
        assert opponent.active is False

    def test_reflects_my_own_pending_faux_pas_obligation(self, bot):
        bot.add_status_card(Painting(value=4))
        from highsociety.code.gamecore.components_module.disgrace_card import FauxPas
        bot.add_status_card(FauxPas())
        assert bot.holds_faux_pas is True

        _tell(bot, "\nAuctioning: Painting (value=3)")
        _tell(bot, "\nCurrent Highest Bid: 0")
        state, me_idx = bot._build_state()
        assert state.faux_pas_holder == me_idx


class TestGetBid:
    def test_returns_pass_as_a_bare_string(self, bot):
        # No money at all -- "pass" is the only legal action regardless of
        # the search itself.
        for card in list(bot.money_cards):
            bot.place_bid(card.value)
        _tell(bot, "\nAuctioning: Painting (value=5)")
        _tell(bot, "\nCurrent Highest Bid: 0")
        assert bot.get_bid() == "pass"

    def test_returns_a_raise_as_a_single_element_list(self, bot):
        _tell(bot, "\nAuctioning: Painting (value=5)")
        _tell(bot, "\nCurrent Highest Bid: 100")  # nothing can beat this except folding
        result = bot.get_bid()
        assert result == "pass" or (isinstance(result, list) and len(result) == 1)

    def test_respects_think_time(self, monkeypatch):
        # tests/conftest.py's autouse fixture no-ops time.sleep suite-wide
        # (skips PlayGame's own human-pacing sleeps) -- verify the *call*
        # happened with the right delay instead of real elapsed time.
        sleeps = []
        monkeypatch.setattr(time, "sleep", lambda seconds: sleeps.append(seconds))
        slow_bot = MCTSBot(name="Bot", username="bot", config=_TINY_CONFIG, think_time=0.2)
        _tell(slow_bot, "\nAuctioning: Painting (value=5)")
        _tell(slow_bot, "\nCurrent Highest Bid: 0")
        slow_bot.get_bid()
        assert 0.2 in sleeps


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
        game = PlayGame(players=[mcts_player, passer], mode="cli", seed=7)

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
        game = PlayGame(players=players, mode="cli", seed=3)

        game.play_game()

        assert game.winners is not None
        assert len(game.final_standings) == 3
