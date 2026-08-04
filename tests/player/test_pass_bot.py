import pytest

from highsociety.code.gamecore.player.pass_bot import PassBot
from highsociety.code.gamecore.game_manager.gameplay import PlayGame
from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.components_module.disgrace_card import Passe


@pytest.fixture
def bot():
    return PassBot(name="Bot", username="bot")


def test_always_passes_regardless_of_state(bot):
    assert bot.get_bid() == "pass"
    bot.send_message("Current Highest Bid: 25", message_type="PLAYER_INFO")
    assert bot.get_bid() == "pass"


def test_send_message_is_a_no_op(bot):
    bot.send_message("anything", message_type="PLAYER_MOVE", extra_kwarg="ignored")


def test_discards_the_first_painting_it_holds(bot):
    bot.add_status_card(Painting(value=3))
    bot.add_status_card(Painting(value=7))
    chosen = bot.choose_painting_to_discard()
    assert chosen is not None
    assert chosen.value in (3, 7)


def test_discard_returns_none_without_any_paintings(bot):
    assert bot.choose_painting_to_discard() is None


def test_never_wins_a_normal_auction_since_it_always_passes(bot):
    """Passing first drops active-bidder count to 1, so the other player wins
    by default without ever needing to bid — for free."""
    other = PassBot(name="Other", username="other")
    game = PlayGame(players=[bot, other], mode="cli")

    winner_id = game.normal_card_auction(Painting(value=5), starting_player_id=0)

    assert game.players[winner_id].username == "other"
    assert game.auction_rounds[0].money_spent == {"bot": 0, "other": 0}


def test_takes_the_disgrace_card_by_passing_first(bot):
    other = PassBot(name="Other", username="other")
    game = PlayGame(players=[bot, other], mode="cli")
    card = Passe()

    loser_id = game.disgrace_card_auction(current_player_id=0, status_card=card)

    assert loser_id == 0
    assert card in bot.status_cards
    assert game.auction_rounds[0].money_spent == {"bot": 0, "other": 0}
