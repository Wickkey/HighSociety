import pytest

from highsociety.code.ai.greedy_bot import GreedyBot
from highsociety.code.ai.pass_bot import PassBot
from highsociety.code.gamecore.game_manager.gameplay import PlayGame
from highsociety.code.gamecore.components_module.painting import Painting


@pytest.fixture
def bot():
    return GreedyBot(name="Bot", username="bot")


def _tell_highest_bid(bot, value):
    bot.send_message(f"\nCurrent Highest Bid: {value}", message_type="PLAYER_INFO")


def test_bids_the_least_possible_when_nothing_has_been_bid_yet(bot):
    """Starting cash is [1, 2, 3, 4, 6, 8, 10, 12, 15, 20, 25]; with no bid on
    the table yet, the cheapest card (1) is already enough to lead."""
    _tell_highest_bid(bot, 0)
    assert bot.get_bid() == [1]


def test_bids_the_exact_card_that_beats_the_highest_bid_when_it_has_one(bot):
    """Highest bid is 2; the bot holds a 3, so it raises with exactly that,
    matching the game's own example: current bid 2 -> bid 3."""
    _tell_highest_bid(bot, 2)
    assert bot.get_bid() == [3]


def test_bids_the_closest_bigger_card_when_the_exact_one_is_missing(bot):
    """Highest bid is 4: the minimum winning card would be 5, but the deck
    has no 5-value card (goes 4 -> 6), so it bids the next one up, 6."""
    _tell_highest_bid(bot, 4)
    assert bot.get_bid() == [6]


def test_passes_when_no_single_card_is_big_enough(bot):
    """25 is the biggest card in the deck; nothing beats a highest bid of 25."""
    _tell_highest_bid(bot, 25)
    assert bot.get_bid() == "pass"


def test_accounts_for_money_already_committed_this_auction(bot):
    """After committing a 1 earlier this auction, a highest bid of 3 needs a
    card worth >= 3 more (not >= 4) to bring the running total past 3."""
    bot.place_bid([1])
    _tell_highest_bid(bot, 3)
    assert bot.get_bid() == [3]


def test_never_combines_multiple_cards_into_one_bid(bot):
    """Highest bid is 23, so the target is 24 -- exactly 20+4, which a
    card-combining bot might reach for. GreedyBot only ever offers one card,
    so it jumps to the next single card up, 25, instead."""
    _tell_highest_bid(bot, 23)
    assert bot.get_bid() == [25]


def test_choose_painting_to_discard_returns_a_held_painting(bot):
    bot.add_status_card(Painting(value=7))
    assert bot.choose_painting_to_discard().value == 7


def test_choose_painting_to_discard_returns_none_without_paintings(bot):
    assert bot.choose_painting_to_discard() is None


def test_wins_a_normal_auction_against_a_pass_bot_for_the_cheapest_card(bot):
    passer = PassBot(name="Passer", username="passer")
    game = PlayGame(players=[bot, passer], mode="cli")

    winner_id = game.normal_card_auction(Painting(value=5), starting_player_id=0)

    assert game.players[winner_id].username == "bot"
    assert game.auction_rounds[0].money_spent == {"bot": 1, "passer": 0}
    assert game.auction_rounds[0].cards_spent == {"bot": [1], "passer": []}


def test_two_greedy_bots_ratchet_each_other_up_in_minimal_steps(bot):
    """Each bot only ever raises by the smallest card that beats the other,
    so the price should climb slowly rather than jumping straight to
    someone's biggest card."""
    other = GreedyBot(name="Other", username="other")
    game = PlayGame(players=[bot, other], mode="cli")

    winner_id = game.normal_card_auction(Painting(value=5), starting_player_id=0)
    record = game.auction_rounds[0]

    winner = game.players[winner_id]
    loser = other if winner.username == "bot" else bot
    # The loser dropped out instead of paying, so they're refunded to 0.
    assert record.money_spent[loser.username] == 0
    # The bidding must have gone through more than one back-and-forth raise
    # before someone ran out of small enough cards.
    bid_events = [e for e in record.events if e.action == "bid"]
    assert len(bid_events) > 2
