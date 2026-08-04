import pytest

from highsociety.code.gamecore.game_manager.gameplay import PlayGame
from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.components_module.prestige_card import PrestigeCard
from highsociety.code.gamecore.components_module.disgrace_card import FauxPas, Passe


def make_game(players):
    return PlayGame(players=players, mode="cli")


class TestAuctionHelpers:
    def test_count_active_auction_players(self, make_player):
        p1, p2, p3 = make_player("P1"), make_player("P2"), make_player("P3")
        p3.active = False
        game = make_game([p1, p2, p3])
        assert game._count_active_auction_players() == 2

    def test_get_next_player_id_wraps_around(self, make_player):
        game = make_game([make_player("P1"), make_player("P2"), make_player("P3")])
        assert game.get_next_player_id(0) == 1
        assert game.get_next_player_id(2) == 0

    def test_get_auction_winner_skips_inactive_and_withdrawn(self, make_player):
        p1, p2 = make_player("P1"), make_player("P2")
        game = make_game([p1, p2])
        p1.withdraw_bid()  # no longer participating
        assert game._get_auction_winner() == 1

    def test_get_auction_winner_returns_minus_one_when_nobody_active(self, make_player):
        p1, p2 = make_player("P1"), make_player("P2")
        p1.active = False
        p2.active = False
        game = make_game([p1, p2])
        assert game._get_auction_winner() == -1


class TestNormalCardAuction:
    def test_winner_pays_their_winning_bid(self, make_player):
        painting = Painting(value=5)
        bidder = make_player("Bidder", actions=[[10], "pass"])
        rival = make_player("Rival", actions=["pass"])
        game = make_game([bidder, rival])

        money_before = bidder.money_left()
        winner_id = game.normal_card_auction(painting, starting_player_id=0)

        assert winner_id == 0
        assert painting in bidder.status_cards
        assert bidder.money_left() == money_before - 10

    def test_last_remaining_bidder_wins_for_free_when_others_pass(self, make_player):
        """
        Documents intentional behavior: once every other participant has
        passed, the sole remaining player wins without ever being prompted
        (they pay whatever they'd already bid — 0, if nothing).
        """
        painting = Painting(value=5)
        starter = make_player("Starter", actions=["pass"])
        bystander = make_player("Bystander")
        game = make_game([starter, bystander])

        money_before = bystander.money_left()
        winner_id = game.normal_card_auction(painting, starting_player_id=0)

        assert winner_id == 1
        assert painting in bystander.status_cards
        assert bystander.money_left() == money_before

    def test_auction_resets_participation_for_next_round(self, make_player):
        painting = Painting(value=5)
        p1 = make_player("P1", actions=["pass"])
        p2 = make_player("P2")
        game = make_game([p1, p2])
        game.normal_card_auction(painting, starting_player_id=0)

        assert p1.current_participation_in_auction is True
        assert p2.current_participation_in_auction is True


class TestDisgraceCardAuction:
    def test_first_player_to_pass_takes_the_card(self, make_player):
        card = Passe()
        passer = make_player("Passer", actions=["pass"])
        raiser = make_player("Raiser")
        game = make_game([passer, raiser])

        loser_id = game.disgrace_card_auction(current_player_id=0, status_card=card)

        assert loser_id == 0
        assert card in passer.status_cards
        assert card not in raiser.status_cards

    def test_quitting_is_treated_like_passing(self, make_player):
        card = FauxPas()
        quitter = make_player("Quitter", actions=["quit"])
        other = make_player("Other")
        game = make_game([quitter, other])

        loser_id = game.disgrace_card_auction(current_player_id=0, status_card=card)

        assert loser_id == 0
        assert quitter.active is False
        assert card in quitter.status_cards

    def test_forfeit_settlement_is_the_default_and_raised_money_is_lost(self, make_player):
        """
        Per product decision: in a disgrace auction, whoever finally takes the
        card gets their own committed money back (via the pass -> withdraw_bid
        path), but everyone who raised to avoid the card permanently forfeits
        that money — it is never returned to their MoneyCardManager.
        """
        card = Passe()
        raiser = make_player("Raiser", actions=[[10]])
        taker = make_player("Taker", actions=["pass"])
        game = make_game([raiser, taker])

        raiser_money_before = raiser.money_left()
        taker_money_before = taker.money_left()
        loser_id = game.disgrace_card_auction(current_player_id=0, status_card=card)

        assert loser_id == 1
        assert raiser.money_left() == raiser_money_before - 10  # forfeited, never refunded
        assert taker.money_left() == taker_money_before  # taker never bid; nothing to refund


class TestAuctionHistory:
    def test_normal_card_auction_records_the_full_event_sequence(self, make_player):
        painting = Painting(value=5)
        bidder = make_player("Bidder", actions=[[10], "pass"])
        rival = make_player("Rival", actions=["pass"])
        game = make_game([bidder, rival])

        game.normal_card_auction(painting, starting_player_id=0)

        assert len(game.auction_rounds) == 1
        record = game.auction_rounds[0]
        assert record.round_number == 1
        assert record.auction_type == "normal"
        assert record.card == {
            "type": "Painting", "value": 5, "multiplier": 1,
            "is_green": False, "description": "Painting Card with value 5",
        }
        assert [e.to_dict() for e in record.events] == [
            {"player": "bidder", "action": "bid", "amount": 10, "cards": [10]},
            {"player": "rival", "action": "pass", "amount": None, "cards": None},
        ]
        assert record.recipient == "bidder"
        assert record.price_paid == 10
        assert record.cards_paid == [10]

    def test_normal_card_auction_with_no_bidders_records_no_recipient(self, make_player):
        """Documents the "last one standing wins for free" rule showing up correctly
        as price_paid=0, and a normal auction with zero participants recording recipient=None."""
        painting = Painting(value=5)
        p1, p2 = make_player("P1"), make_player("P2")
        p1.active = False
        p2.active = False
        game = make_game([p1, p2])

        game.normal_card_auction(painting, starting_player_id=0)

        record = game.auction_rounds[0]
        assert record.events == []
        assert record.recipient is None
        assert record.price_paid == 0
        assert record.cards_paid == []

    def test_disgrace_card_auction_records_recipient_and_the_forfeit_events(self, make_player):
        card = Passe()
        raiser = make_player("Raiser", actions=[[10]])
        taker = make_player("Taker", actions=["pass"])
        game = make_game([raiser, taker])

        game.disgrace_card_auction(current_player_id=0, status_card=card)

        assert len(game.auction_rounds) == 1
        record = game.auction_rounds[0]
        assert record.auction_type == "disgrace"
        assert record.card["type"] == "Passe"
        assert [e.to_dict() for e in record.events] == [
            {"player": "raiser", "action": "bid", "amount": 10, "cards": [10]},
            {"player": "taker", "action": "pass", "amount": None, "cards": None},
        ]
        assert record.recipient == "taker"
        assert record.price_paid == 0  # taker's own bid was refunded by passing
        assert record.cards_paid == []  # nothing paid; nothing to itemize

    def test_round_numbers_increment_across_multiple_auctions(self, make_player):
        p1, p2 = make_player("P1"), make_player("P2")
        game = make_game([p1, p2])

        game.normal_card_auction(Painting(value=1), starting_player_id=0)
        game.normal_card_auction(Painting(value=2), starting_player_id=0)

        assert [r.round_number for r in game.auction_rounds] == [1, 2]

    def test_get_auction_history_returns_json_serializable_dicts(self, make_player):
        import json

        p1, p2 = make_player("P1", actions=[[3], "pass"]), make_player("P2", actions=["pass"])
        game = make_game([p1, p2])
        game.normal_card_auction(Painting(value=4), starting_player_id=0)

        history = game.get_auction_history()
        reparsed = json.loads(json.dumps(history))

        assert reparsed == history
        assert reparsed[0]["recipient"] == "p1"
        assert reparsed[0]["price_paid"] == 3


class TestFauxPasPenalty:
    def test_no_paintings_returns_false(self, make_player):
        player = make_player("P")
        game = make_game([player, make_player("Q")])
        assert game.handle_faux_pas_penalty(0) is False

    def test_discards_a_painting_and_returns_true(self, make_player):
        player = make_player("P")
        player.add_status_card(Painting(value=5))
        game = make_game([player, make_player("Q")])

        assert game.handle_faux_pas_penalty(0) is True
        assert player.status_cards == ()

    def test_none_choice_is_handled_without_crashing(self, make_player):
        """
        Regression test: choose_painting_to_discard() can legitimately return
        None (e.g. a disconnected NetworkPlayer); handle_faux_pas_penalty must
        not blindly call .value on it.
        """
        player = make_player("P")
        player.add_status_card(Painting(value=5))
        player.choose_painting_to_discard = lambda: None
        game = make_game([player, make_player("Q")])

        assert game.handle_faux_pas_penalty(0) is False
        assert len(player.status_cards) == 1


class TestDetermineWinner:
    def test_lowest_money_player_is_eliminated_when_unique(self, make_player):
        rich = make_player("Rich")
        poor = make_player("Poor")
        rich.add_status_card(Painting(value=10))
        poor.add_status_card(Painting(value=100))
        poor.place_bid([m.value for m in poor.money_cards])  # spend everything -> lowest money

        game = make_game([rich, poor])
        winners = game.determine_winner()

        assert [w.username for w in winners] == ["rich"]

    def test_ties_on_points_return_multiple_winners(self, make_player):
        p1 = make_player("P1")
        p2 = make_player("P2")
        p1.add_status_card(Painting(value=10))
        p2.add_status_card(Painting(value=10))

        game = make_game([p1, p2])
        winners = game.determine_winner()

        assert {w.username for w in winners} == {"p1", "p2"}

    def test_inactive_players_are_never_winners(self, make_player):
        active = make_player("Active")
        quit_player = make_player("Quitter")
        active.add_status_card(Painting(value=1))
        quit_player.add_status_card(Painting(value=100))
        quit_player.active = False

        game = make_game([active, quit_player])
        winners = game.determine_winner()

        assert [w.username for w in winners] == ["active"]

    def test_sole_active_player_wins_outright_despite_low_money(self, make_player):
        """
        Regression test: with everyone else quit, the lone remaining active
        player used to be spuriously eliminated by the "lowest money" rule
        (trivially true for a set of one) and by comparing against inactive
        players' leftover money, producing "no winner" for a clearly-decided game.
        """
        survivor = make_player("Survivor")
        survivor.add_status_card(Painting(value=10))
        survivor.place_bid([m.value for m in survivor.money_cards])  # spend everything

        quitter1 = make_player("Quitter1")
        quitter2 = make_player("Quitter2")
        quitter1.active = False
        quitter2.active = False
        # quitters kept all their money; far more than the survivor has left.

        game = make_game([survivor, quitter1, quitter2])
        winners = game.determine_winner()

        assert [w.username for w in winners] == ["survivor"]

    def test_tied_lowest_money_eliminates_nobody(self, make_player):
        p1 = make_player("P1")
        p2 = make_player("P2")
        p1.add_status_card(Painting(value=10))
        p2.add_status_card(Painting(value=3))
        p1.place_bid([m.value for m in p1.money_cards])
        p2.place_bid([m.value for m in p2.money_cards])  # both now at 0 money: a tie

        game = make_game([p1, p2])
        winners = game.determine_winner()

        assert [w.username for w in winners] == ["p1"]  # nobody eliminated for money; higher points wins


class TestFullGameSmoke:
    def test_game_completes_without_error_when_everyone_passes(self, make_player):
        players = [make_player("A"), make_player("B"), make_player("C")]
        game = make_game(players)
        game.play_game()  # should run to completion without raising

        # Deck should have been drawn down (game only stops early via green-card limit).
        assert game.status_card_manager.get_card_count() < 16

    def test_two_games_in_the_same_process_get_independent_decks(self, make_player):
        game1 = make_game([make_player("A"), make_player("B")])
        card_count_1 = game1.status_card_manager.get_card_count()

        game2 = make_game([make_player("C"), make_player("D")])
        card_count_2 = game2.status_card_manager.get_card_count()

        assert card_count_1 == card_count_2 == 16
        assert game1.status_card_manager is not game2.status_card_manager


class TestSeededDeterminism:
    def test_same_seed_produces_the_same_deck_order(self, make_player):
        p1 = [make_player("A"), make_player("B")]
        p2 = [make_player("C"), make_player("D")]

        game1 = PlayGame(players=p1, mode="cli", seed=42)
        order1 = []
        while not game1.status_card_manager.is_empty():
            order1.append(repr(game1.status_card_manager.remove_top_card()))

        game2 = PlayGame(players=p2, mode="cli", seed=42)
        order2 = []
        while not game2.status_card_manager.is_empty():
            order2.append(repr(game2.status_card_manager.remove_top_card()))

        assert order1 == order2

    def test_different_seeds_produce_different_deck_orders(self, make_player):
        game1 = PlayGame(players=[make_player("A"), make_player("B")], mode="cli", seed=1)
        order1 = []
        while not game1.status_card_manager.is_empty():
            order1.append(repr(game1.status_card_manager.remove_top_card()))

        game2 = PlayGame(players=[make_player("C"), make_player("D")], mode="cli", seed=2)
        order2 = []
        while not game2.status_card_manager.is_empty():
            order2.append(repr(game2.status_card_manager.remove_top_card()))

        assert order1 != order2

    def test_same_seed_and_same_scripted_decisions_reproduce_the_same_outcome(self, make_player):
        """
        The core guarantee behind the seed+script testing workflow: fixing the
        seed makes the whole game (deck order, player shuffle, starting
        player) reproducible, so the same ScriptedPlayer actions against the
        same seed always land on the exact same final result.
        """
        def run():
            players = [make_player("A"), make_player("B"), make_player("C")]
            game = PlayGame(players=players, mode="cli", seed=999)
            game.play_game()
            return [(p.username, p.points, p.money_left()) for p in game.players]

        assert run() == run()
