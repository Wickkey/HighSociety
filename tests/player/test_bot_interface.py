import pytest

from highsociety.code.gamecore.player.bot_interface import BotInterface
from highsociety.code.gamecore.player.player import BasePlayer
from highsociety.code.gamecore.player.cliplayer import CLIPlayer
from highsociety.code.gamecore.player.networkplayer import NetworkPlayer
from highsociety.code.gamecore.game_manager.gameplay import PlayGame
from highsociety.code.gamecore.components_module.painting import Painting


def test_base_player_cannot_be_instantiated_directly():
    """
    BasePlayer deliberately leaves get_bid/choose_painting_to_discard/
    send_message unimplemented (that's the bot-facing contract) — a bare
    BasePlayer() is missing them, so it must fail fast at construction
    rather than at the first game turn that calls one of them.
    """
    with pytest.raises(TypeError):
        BasePlayer(name="X", username="x")


def test_a_subclass_missing_a_mandatory_method_cannot_be_instantiated():
    class IncompleteBot(BasePlayer):
        def get_bid(self, timeout=None):
            return "pass"

        def choose_painting_to_discard(self):
            return None

        # send_message intentionally left unimplemented

    with pytest.raises(TypeError):
        IncompleteBot(name="Bot", username="bot")


def test_a_subclass_implementing_every_mandatory_method_instantiates_fine():
    class MinimalBot(BasePlayer):
        def __init__(self, name, username):
            super().__init__(name, username)
            self.active = True

        def get_bid(self, timeout=None):
            return "pass"

        def choose_painting_to_discard(self):
            return None

        def send_message(self, message, message_type=None, created_at=None, **kwargs):
            pass

    bot = MinimalBot(name="Bot", username="bot")
    assert bot.get_bid() == "pass"
    assert bot.active is True


@pytest.mark.parametrize("player_cls", [CLIPlayer, NetworkPlayer])
def test_existing_player_implementations_satisfy_the_interface(player_cls):
    assert issubclass(player_cls, BotInterface)
    assert not player_cls.__abstractmethods__


class TestAuctionHistoryAccess:
    def test_defaults_to_empty_before_ever_joining_a_game(self):
        """A player/bot instantiated standalone -- never handed to a
        PlayGame -- has no auction history source wired up yet."""
        player = CLIPlayer(name="Alice", username="alice")
        assert player.get_auction_history() == []

    def test_matches_the_games_own_history_after_an_auction(self, make_player):
        """PlayGame wires every player straight to its own auction_rounds
        list, so a player's own view should be identical to (not just
        equivalent to) the game's, with no lag or copying."""
        bidder = make_player("Bidder", actions=[[10], "pass"])
        rival = make_player("Rival", actions=["pass"])
        game = PlayGame(players=[bidder, rival], mode="cli")

        game.normal_card_auction(Painting(value=5), starting_player_id=0)

        assert bidder.get_auction_history() == game.get_auction_history()
        assert rival.get_auction_history() == game.get_auction_history()
        assert game.get_auction_history()[0]["recipient"] == "bidder"

    def test_reflects_new_auctions_as_the_game_progresses(self, make_player):
        """The same player object should see auction #2 appear after it
        happens, without being re-wired or re-fetched."""
        p1, p2 = make_player("P1"), make_player("P2")
        game = PlayGame(players=[p1, p2], mode="cli")

        game.normal_card_auction(Painting(value=1), starting_player_id=0)
        assert len(p1.get_auction_history()) == 1

        game.normal_card_auction(Painting(value=2), starting_player_id=0)
        assert len(p1.get_auction_history()) == 2
