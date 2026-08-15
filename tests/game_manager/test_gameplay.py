import time

import pytest

from highsociety.code.ai.pass_bot import PassBot
from highsociety.code.gamecore.game_manager.gameplay import PlayGame
from highsociety.code.gamecore.game_manager.turn_clock import TurnClock
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

    def test_player_move_is_always_accompanied_by_a_fresh_sync_of_round_and_card(self, make_player):
        """
        Regression test for a real, live-reproduced bug: a player's own
        PLAYER_MOVE prompt (which opens their bid panel) and the broadcast
        AUCTION_UPDATE (which drives everyone's round/card/"whose turn"
        header) are two independent messages. On a flaky connection, the
        broadcast can be lost or fail to render while the direct prompt
        still gets through -- leaving a player looking at a live, working
        bid panel under a header frozen on an earlier round's card. See
        _handle_player_turn's own comment for the full story.

        Fix: every PLAYER_MOVE is now immediately followed, on that same
        player's own connection, by a "sync" AUCTION_UPDATE carrying the
        ground-truth round/card/max_bid/turn_player for exactly this turn
        -- so their panel can never be stale relative to the prompt they're
        actually looking at, regardless of what happened to the broadcast.
        """
        painting = Painting(value=5)
        bidder = make_player("Bidder", actions=[[10], "pass"])
        rival = make_player("Rival", actions=["pass"])
        game = make_game([bidder, rival])

        game.normal_card_auction(painting, starting_player_id=0)

        for player in (bidder, rival):
            move_indices = [i for i, m in enumerate(player.sent_messages) if m["message_type"] == "PLAYER_MOVE"]
            assert move_indices, f"{player.username} was never prompted"
            for i in move_indices:
                sync = player.sent_messages[i + 1]
                assert sync["message_type"] == "AUCTION_UPDATE"
                assert sync["data"]["kind"] == "sync"
                assert sync["data"]["turn_player"] == player.username
                assert sync["data"]["card"]["value"] == 5
                assert sync["data"]["round_number"] == 1

    def test_each_turn_gets_its_own_increasing_move_sequence(self, make_player):
        """
        Regression test for a real, live-reproduced bug (narrower than the
        one above): a player's own answer can cross _handle_player_turn's
        second prompt (sent via get_bid(), after the toast-pacing wait --
        see that call site's own comment) in flight, arriving at the
        client just *after* it. Without a way to recognize that second
        prompt as still the *same* decision, the client treated it as a
        fresh turn and re-opened an already-answered, already-greyed move
        panel. _handle_player_turn now stamps player._current_move_seq
        (and the initial PLAYER_MOVE's own data) with a value from
        PlayGame._next_move_sequence() -- this pins that every distinct
        turn gets a strictly increasing one, matching what the player
        object itself was left holding.
        """
        painting = Painting(value=5)
        bidder = make_player("Bidder", actions=[[10], "pass"])
        rival = make_player("Rival", actions=["pass"])
        game = make_game([bidder, rival])

        game.normal_card_auction(painting, starting_player_id=0)

        seqs = []
        for player in (bidder, rival):
            move_msgs = [m for m in player.sent_messages if m["message_type"] == "PLAYER_MOVE"]
            assert move_msgs, f"{player.username} was never prompted"
            for m in move_msgs:
                seqs.append(m["data"]["move_seq"])

        assert all(s is not None for s in seqs)
        assert len(seqs) == len(set(seqs)), f"move_seq values must be unique per turn, got {seqs}"
        # bidder acts first (starting_player_id=0), so their one prompt
        # must carry the lowest sequence number of the whole auction.
        assert min(seqs) in [m["data"]["move_seq"] for m in bidder.sent_messages if m["message_type"] == "PLAYER_MOVE"]


class TestOutOfTurnDeparture:
    """
    A player can go inactive asynchronously — an out-of-turn resign (see
    web_server.py's on_resign) or a mid-auction disconnect — which never
    goes through their own get_bid()/choose_painting_to_discard() return
    value at all, unlike every other way a player leaves an auction. Without
    accounting for this, such a player gets skipped forever without ever
    being subtracted from the auction's remaining-player count — and once
    they're one of only two players left, the real remaining bidder ends up
    re-prompted forever with no one left to actually out-bid.
    """

    def test_normal_auction_ends_correctly_when_a_third_player_goes_inactive_mid_auction(self, make_player):
        painting = Painting(value=5)
        a = make_player("A", actions=[[4], "pass"])
        b = make_player("B")  # never takes a real turn -- goes inactive out from under the loop
        c = make_player("C", actions=["pass"])
        game = make_game([a, b, c])

        # Simulates an out-of-turn resign landing right as A takes their
        # first turn — B never answers a prompt of their own at all.
        original_get_bid = a.get_bid

        def get_bid_and_deactivate_b(timeout=None):
            b.active = False
            a.get_bid = original_get_bid  # only deactivate once
            return original_get_bid(timeout=timeout)

        a.get_bid = get_bid_and_deactivate_b

        winner_id = game.normal_card_auction(painting, starting_player_id=0)

        assert winner_id == 0  # A: B never competes (inactive), C passes
        assert painting in a.status_cards

    def test_normal_auction_does_not_double_count_a_player_already_inactive_before_it_starts(self, make_player):
        """
        Regression test for a related bug found while fixing the above:
        current_participation_in_auction gets reset to True for *every*
        player ahead of each new auction, active or not — so a player who
        was already inactive before this specific auction even began must
        NOT be treated as "just went inactive" the first time the loop
        reaches their slot (that would double-subtract someone
        _count_active_auction_players() had already correctly excluded,
        ending the auction early before the real remaining players ever got
        a turn).
        """
        painting = Painting(value=5)
        a = make_player("A", actions=["pass"])
        b = make_player("B")
        already_gone = make_player("Gone")
        already_gone.active = False
        game = make_game([a, b, already_gone])

        winner_id = game.normal_card_auction(painting, starting_player_id=0)

        assert winner_id == 1  # B, after A's real pass -- not ended prematurely

    def test_disgrace_auction_ends_correctly_when_a_third_player_goes_inactive_mid_auction(self, make_player):
        card = FauxPas()
        a = make_player("A", actions=[[4]])
        b = make_player("B")  # never takes a real turn -- goes inactive out from under the loop
        c = make_player("C", actions=["pass"])
        game = make_game([a, b, c])

        original_get_bid = a.get_bid

        def get_bid_and_deactivate_b(timeout=None):
            b.active = False
            a.get_bid = original_get_bid
            return original_get_bid(timeout=timeout)

        a.get_bid = get_bid_and_deactivate_b

        loser_id = game.disgrace_card_auction(current_player_id=0, status_card=card)

        # Quitting a disgrace auction always means losing it immediately
        # (see the in-turn "quit" branch) — same rule, just triggered
        # asynchronously: B loses the instant their departure is noticed,
        # regardless of whether A/C were still competing.
        assert loser_id == 1
        assert card in b.status_cards

    def test_disgrace_auction_does_not_double_count_a_player_already_inactive_before_it_starts(self, make_player):
        card = Passe()
        a = make_player("A", actions=["pass"])
        b = make_player("B")
        already_gone = make_player("Gone")
        already_gone.active = False
        game = make_game([a, b, already_gone])

        # Starting turn order points at the already-departed player first,
        # so the very first loop iteration exercises the skip branch.
        loser_id = game.disgrace_card_auction(current_player_id=2, status_card=card)

        assert loser_id == 0  # A's own real pass -- "Gone" must not be misattributed as an immediate loss


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
        assert record.money_spent == {"bidder": 10, "rival": 0}
        assert record.cards_spent == {"bidder": [10], "rival": []}

    def test_normal_card_auction_with_no_bidders_records_no_recipient(self, make_player):
        """Documents the "last one standing wins for free" rule showing up correctly
        as everyone at 0 in money_spent, and a normal auction with zero participants
        recording recipient=None."""
        painting = Painting(value=5)
        p1, p2 = make_player("P1"), make_player("P2")
        p1.active = False
        p2.active = False
        game = make_game([p1, p2])

        game.normal_card_auction(painting, starting_player_id=0)

        record = game.auction_rounds[0]
        assert record.events == []
        assert record.recipient is None
        assert record.money_spent == {"p1": 0, "p2": 0}
        assert record.cards_spent == {"p1": [], "p2": []}

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
        # taker's own bid was refunded by passing; raiser's forfeited under
        # the default ForfeitSettlement instead of being returned.
        assert record.money_spent == {"raiser": 10, "taker": 0}
        assert record.cards_spent == {"raiser": [10], "taker": []}

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
        assert reparsed[0]["money_spent"] == {"p1": 3, "p2": 0}
        assert reparsed[0]["cards_spent"] == {"p1": [3], "p2": []}


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

    def test_discard_prompt_gets_its_own_move_sequence(self, make_player):
        """
        Same wiring as normal_card_auction's turn prompts (see
        TestNormalCardAuction.test_each_turn_gets_its_own_increasing_move_sequence)
        applies to the Faux Pas discard prompt too -- handle_faux_pas_penalty
        is a completely separate call site from _handle_player_turn, so it
        needs its own PlayGame._next_move_sequence() call rather than
        accidentally reusing/skipping a value.
        """
        player = make_player("P")
        player.add_status_card(Painting(value=5))
        game = make_game([player, make_player("Q")])

        game.handle_faux_pas_penalty(0)

        assert isinstance(player._current_move_seq, int)

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

    def test_returns_true_immediately_for_an_inactive_player_without_blocking(self, make_player):
        """
        handle_faux_pas_penalty is re-called every remaining round for as
        long as its target keeps not discarding (see play_game()'s main
        loop) — a real NetworkPlayer's choose_painting_to_discard() has no
        timeout, so re-entering it for a player who's gone for good
        (resigned out of turn, or disconnected) would block forever. Proves
        the guard short-circuits *before* ever attempting that call.
        """
        p = make_player("P")
        p.add_status_card(Painting(value=5))
        p.active = False

        def _should_not_be_called():
            raise AssertionError("choose_painting_to_discard() must not be called for an inactive player")
        p.choose_painting_to_discard = _should_not_be_called

        game = make_game([p, make_player("Other")])
        assert game.handle_faux_pas_penalty(0) is True


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

    def test_same_seed_produces_the_same_turn_order_regardless_of_join_order(self, make_player):
        """
        For a real web room, humans get appended to PlayGame.players in
        whatever real-world order their own connection happens to finish
        joining (see web_server.py's ws_player) -- not something the seed has
        any influence over. shuffle_players() must sort by username first so
        the SAME seed reproduces the SAME final turn order regardless of that
        real-world timing, not just when players happen to be constructed in
        the same relative order every time.
        """
        alice1, bob1 = make_player("Alice", username="alice"), make_player("Bob", username="bob")
        game1 = PlayGame(players=[alice1, bob1], mode="cli", seed=123)  # alice, bob
        order1 = [p.username for p in game1.shuffle_players()]

        bob2, alice2 = make_player("Bob", username="bob"), make_player("Alice", username="alice")
        game2 = PlayGame(players=[bob2, alice2], mode="cli", seed=123)  # bob, alice -- reversed
        order2 = [p.username for p in game2.shuffle_players()]

        assert order1 == order2

    def test_bot_shuffle_result_depends_on_creation_order_not_random_names(self):
        """
        A bot's own username is randomly assigned at creation (see
        highsociety/code/ai/bot_names.py), unrelated to the game's seed --
        sorting bots by that name would make the shuffle's *input* order
        (and therefore its result) depend on the random name, not just the
        seed and creation order. Two games with the same seed and the same
        bot creation order, but different (here, deliberately swapped)
        usernames, must still shuffle the "first created" bot to the same
        final position both times -- confirming bot ordering is a function
        of creation order, never of the name they happened to get.
        """
        def make_two_bots(name1, name2):
            return [PassBot(name="Bot1", username=name1), PassBot(name="Bot2", username=name2)]

        players_a = make_two_bots("zeta_bot", "alpha_bot")
        game_a = PlayGame(players=players_a, mode="cli", seed=7)
        game_a.shuffle_players()
        first_created_position_a = game_a.players.index(players_a[0])

        players_b = make_two_bots("alpha_bot", "zeta_bot")  # same creation order, swapped names
        game_b = PlayGame(players=players_b, mode="cli", seed=7)
        game_b.shuffle_players()
        first_created_position_b = game_b.players.index(players_b[0])

        assert first_created_position_a == first_created_position_b


class TestTurnDuration:
    """
    turn_duration lets a caller (e.g. web_server.py, letting a host pick a
    per-move timer in the lobby form) override HSConfig.json's
    game_settings.rules.time_per_move on a per-game basis — see
    PlayGame.__init__'s _TURN_DURATION_UNSET sentinel. Deadline arithmetic
    itself is TurnClock's job (see TestTurnClock below) — this class only
    covers how __TURN_DURATION gets resolved.
    """

    def test_default_uses_configured_value_which_is_currently_no_limit(self, make_player):
        game = PlayGame(players=[make_player("A"), make_player("B")], mode="cli")
        assert game.turn_duration is None

    def test_explicit_turn_duration_overrides_the_config_default(self, make_player):
        game = PlayGame(players=[make_player("A"), make_player("B")], mode="cli", turn_duration=30)
        assert game.turn_duration == 30

    def test_explicit_none_disables_the_timer(self, make_player):
        game = PlayGame(players=[make_player("A"), make_player("B")], mode="cli", turn_duration=None)
        assert game.turn_duration is None


class TestTurnClock:
    def test_no_duration_never_expires(self):
        clock = TurnClock(None)
        clock.start()
        assert clock.remaining() is None
        assert not clock.expired()

    def test_remaining_counts_down_from_the_configured_duration(self):
        clock = TurnClock(30)
        clock.start()
        assert clock.remaining() == pytest.approx(30, abs=1)
        assert not clock.expired()

    def test_expired_once_remaining_hits_zero(self):
        clock = TurnClock(30)
        clock.start()
        clock.expires_at = time.time() - 1  # simulate time having passed
        assert clock.expired()
        assert clock.remaining() < 0

    def test_remaining_and_expired_are_none_before_start(self):
        clock = TurnClock(30)
        assert clock.remaining() is None
        assert not clock.expired()
