import pytest

from highsociety.code.gamecore.player.bot_interface import BotInterface
from highsociety.code.gamecore.player.player import BasePlayer
from highsociety.code.gamecore.player.cliplayer import CLIPlayer
from highsociety.code.gamecore.player.networkplayer import NetworkPlayer


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
