import pytest

from highsociety.code.gamecore.player.capped_greedy_bot import CappedGreedyBot
from highsociety.code.gamecore.player.greedy_bot import GreedyBot
from highsociety.code.gamecore.player.pass_bot import PassBot
from highsociety.code.gamecore.game_manager.gameplay import PlayGame
from highsociety.code.gamecore.components_module.painting import Painting


@pytest.fixture
def bot():
    return CappedGreedyBot(name="Bot", username="bot")


def _tell(bot, highest_bid, card_type, card_value):
    bot.send_message(f"\nAuctioning: {card_type} (value={card_value})", message_type="PLAYER_INFO")
    bot.send_message(f"\nCurrent Highest Bid: {highest_bid}", message_type="PLAYER_INFO")


class TestPaintingBudget:
    def test_bids_normally_within_budget(self, bot):
        """Painting worth 4 -> budget is 3.5x4=14. Highest bid 10 needs an 11,
        the deck has no 11 so the next one up (12) is offered, and 12 is
        still within the 14 budget."""
        _tell(bot, 10, "Painting", 4)
        assert bot.get_bid() == [12]

    def test_passes_when_the_cheapest_winning_card_would_break_the_budget(self, bot):
        """Same painting (budget 14), but highest bid is already 12: the
        minimum winning card would be 15, which busts the 14 budget."""
        _tell(bot, 12, "Painting", 4)
        assert bot.get_bid() == "pass"

    def test_passes_when_the_current_bid_already_exceeds_the_budget(self, bot):
        _tell(bot, 15, "Painting", 4)
        assert bot.get_bid() == "pass"

    def test_accounts_for_money_already_committed_this_auction(self, bot):
        """Budget is still 14 (painting value 4); having already committed a
        1 and a 2 (total 3), a highest bid of 10 needs an 8-or-bigger card,
        and 8 keeps the running total at 11 -- still within budget."""
        bot.place_bid([1])
        bot.place_bid([2])
        _tell(bot, 10, "Painting", 4)
        assert bot.get_bid() == [8]


class TestFlatBudgets:
    def test_prestige_card_budget_is_15(self, bot):
        _tell(bot, 14, "PrestigeCard", 0)
        assert bot.get_bid() == [15]

    def test_prestige_card_passes_once_bidding_would_exceed_15(self, bot):
        _tell(bot, 15, "PrestigeCard", 0)
        assert bot.get_bid() == "pass"

    def test_fauxpas_budget_is_8(self, bot):
        _tell(bot, 7, "FauxPas", 0)
        assert bot.get_bid() == [8]

    def test_fauxpas_passes_at_the_boundary(self, bot):
        _tell(bot, 8, "FauxPas", 0)
        assert bot.get_bid() == "pass"

    def test_passe_budget_is_15(self, bot):
        _tell(bot, 14, "Passe", -5)
        assert bot.get_bid() == [15]

    def test_scandale_budget_is_20(self, bot):
        _tell(bot, 19, "Scandale", 0)
        assert bot.get_bid() == [20]


def test_choose_painting_to_discard_returns_a_held_painting(bot):
    bot.add_status_card(Painting(value=7))
    assert bot.choose_painting_to_discard().value == 7


def test_choose_painting_to_discard_returns_none_without_paintings(bot):
    assert bot.choose_painting_to_discard() is None


def test_loses_to_an_uncapped_greedy_bot_once_its_budget_runs_out(bot):
    """Painting worth 2 caps this bot's spend at 7. An uncapped GreedyBot
    keeps minimally raising past that, so the capped bot should eventually
    drop out and lose, refunded to 0, while the winner ends up paying more
    than the capped bot's own budget."""
    rival = GreedyBot(name="Rival", username="rival")
    game = PlayGame(players=[bot, rival], mode="cli")

    winner_id = game.normal_card_auction(Painting(value=2), starting_player_id=0)
    record = game.auction_rounds[0]

    assert game.players[winner_id].username == "rival"
    assert record.money_spent["bot"] == 0
    assert record.money_spent["rival"] > 7


def test_wins_cheaply_against_a_pass_bot(bot):
    passer = PassBot(name="Passer", username="passer")
    game = PlayGame(players=[bot, passer], mode="cli")

    winner_id = game.normal_card_auction(Painting(value=5), starting_player_id=0)

    assert game.players[winner_id].username == "bot"
    assert game.auction_rounds[0].money_spent == {"bot": 1, "passer": 0}
