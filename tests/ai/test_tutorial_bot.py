import pytest

from highsociety.code.ai import BOT_TYPES, create_tutorial_bots
from highsociety.code.ai.tutorial_bot import TutorialBot
from highsociety.code.ai.greedy_bot import GreedyBot
from highsociety.code.gamecore.components_module.disgrace_card import Passe
from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.dev_tools.inspect_seed import draw_order_for_seed, find_green_cutoff
from highsociety.code.gamecore.game_manager.gameplay import PlayGame

# web_server.py's _TUTORIAL_SEED -- duplicated here (not imported) so this
# regression test doesn't need flask/flask-sock installed just to check a
# plain integer (see test_web_server.py's own importorskip for why the web
# layer has an extra dependency this test file otherwise avoids entirely).
# If web_server.py's constant ever changes, update this to match and rerun
# -- see inspect_seed.py's own module docstring for how to pick a new one.
_TUTORIAL_SEED = 8


@pytest.fixture
def bot():
    return TutorialBot(name="Bot", username="bot")


def _tell(bot, highest_bid, card_type, card_value):
    """Starts a *new* auction for the given card, then reports the current
    highest bid -- exactly what gameplay.py sends right before every
    get_bid() call at the top of an auction. Only call this once per
    auction; see _bid_update for a later turn in the *same* auction, which
    must not re-trigger the "Auctioning:" match (that's what resets
    _raises_this_auction, so re-sending it mid-auction would silently
    reset the very limit these tests are checking)."""
    bot.send_message(f"\nAuctioning: {card_type} (value={card_value})", message_type="PLAYER_INFO")
    _bid_update(bot, highest_bid)


def _bid_update(bot, highest_bid):
    """A later turn within the *same*, already-started auction."""
    bot.send_message(f"\nCurrent Highest Bid: {highest_bid}", message_type="PLAYER_INFO")


def test_not_registered_in_the_shared_bot_registry():
    """TutorialBot must never be selectable by a real host, the CLI, or the
    bot evaluator -- only ever reachable via create_tutorial_bots."""
    assert "tutorial" not in BOT_TYPES
    assert TutorialBot not in BOT_TYPES.values()


def test_create_tutorial_bots_returns_two_distinct_tutorial_bots():
    bots = create_tutorial_bots()
    assert len(bots) == 2
    assert all(isinstance(b, TutorialBot) for b in bots)
    assert bots[0].username != bots[1].username


class TestNormalAuctionBudget:
    def test_bids_within_its_painting_budget(self, bot):
        """Painting worth 4 -> budget is 2.5x4=10. Highest bid 8 needs a 9,
        the deck has no 9 so the next one up (10) is offered, still within
        budget."""
        _tell(bot, 8, "Painting", 4)
        assert bot.get_bid() == [10]

    def test_passes_once_the_painting_budget_is_exceeded(self, bot):
        _tell(bot, 10, "Painting", 4)
        assert bot.get_bid() == "pass"

    def test_prestige_card_flat_budget(self, bot):
        _tell(bot, 11, "PrestigeCard", 0)
        assert bot.get_bid() == [12]

    def test_prestige_card_passes_past_its_budget(self, bot):
        _tell(bot, 12, "PrestigeCard", 0)
        assert bot.get_bid() == "pass"


class TestDisgraceAuctionBacksOff:
    """The whole point of this bot: a human who keeps raising always
    succeeds in dumping a disgrace card on it, rather than the bot
    contesting forever."""

    def test_raises_once_then_always_passes_for_the_rest_of_the_auction(self, bot):
        _tell(bot, 0, "Passe", -5)
        assert bot.get_bid() == [1]  # one raise, well within Passe's budget
        _bid_update(bot, 1)
        assert bot.get_bid() == "pass"
        # Still passes even if a human backs off and the "highest bid" the
        # bot itself would need to beat drops back down -- the raise limit
        # is per-auction, not "until it'd be too expensive".
        _bid_update(bot, 0)
        assert bot.get_bid() == "pass"

    def test_raise_limit_resets_for_a_new_disgrace_auction(self, bot):
        _tell(bot, 0, "Passe", -5)
        bot.get_bid()
        _bid_update(bot, 1)
        assert bot.get_bid() == "pass"
        # A fresh disgrace card starts a fresh auction -- send_message's
        # "Auctioning:" match resets _raises_this_auction.
        _tell(bot, 0, "Scandale", 0)
        assert bot.get_bid() == [1]

    def test_normal_auctions_are_unaffected_by_the_disgrace_raise_limit(self, bot):
        # Painting worth 9 -> budget 2.5x9=22.5.
        _tell(bot, 5, "Painting", 9)
        assert bot.get_bid() == [6]
        bot.place_bid([6])  # commit it, as the real game engine would
        _bid_update(bot, 8)
        assert bot.get_bid() == [3]  # keeps raising -- not a disgrace auction


def test_choose_painting_to_discard_returns_a_held_painting(bot):
    bot.add_status_card(Painting(value=7))
    assert bot.choose_painting_to_discard().value == 7


def test_choose_painting_to_discard_returns_none_without_paintings(bot):
    assert bot.choose_painting_to_discard() is None


class TestCuratedTutorialSeed:
    """Guards the "guaranteed teaching moments" property of _TUTORIAL_SEED
    (web_server.py) against a future engine/config change silently breaking
    it -- e.g. a reordered card_factory, a changed green_card_limit, or a
    different disgrace_card_types list in HSConfig.json could all quietly
    reshuffle what this exact seed produces. See _TUTORIAL_SEED's own
    comment in web_server.py for why this specific seed was picked."""

    @pytest.fixture(scope="class")
    def order_and_cutoff(self):
        order = draw_order_for_seed(_TUTORIAL_SEED)
        return order, find_green_cutoff(order)

    def test_hits_a_disgrace_auction_before_the_game_ends(self, order_and_cutoff):
        order, cutoff = order_and_cutoff
        disgrace_types = ("FauxPas(", "Passe(", "Scandale(")
        assert any(order[i].startswith(disgrace_types) for i in range(cutoff))

    def test_hits_a_green_card_before_the_game_ends(self, order_and_cutoff):
        order, cutoff = order_and_cutoff
        assert any("color=green" in order[i] for i in range(cutoff))

    def test_ends_naturally_via_the_green_card_limit_within_a_short_game(self, order_and_cutoff):
        order, cutoff = order_and_cutoff
        assert cutoff < len(order)  # the 4th green card is actually reached, not left in the deck
        assert cutoff <= 10  # short enough to stay a "quick first game", not a marathon


def test_disgrace_backoff_lets_a_persistent_rival_dump_the_card_on_it(bot):
    """End-to-end through the real disgrace auction loop (mirrors
    test_capped_greedy_bot.py's style): a GreedyBot never backs off, so it
    reliably outlasts this bot's one-raise limit and leaves it holding the
    card -- standing in for a human who keeps raising to avoid a disgrace
    card themselves."""
    raiser = GreedyBot(name="Raiser", username="raiser")
    game = PlayGame(players=[bot, raiser], mode="cli")

    recipient_id = game.disgrace_card_auction(current_player_id=0, status_card=Passe())

    assert game.players[recipient_id].username == "bot"
