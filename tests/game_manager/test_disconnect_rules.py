"""
What a dropped connection does to the game itself: who wins, who pays, and
what happens when the seat comes back. Connection plumbing lives in
tests/network/; these drive the engine directly with scripted seats.
"""
from highsociety.code.gamecore.components_module.disgrace_card import FauxPas, Passe
from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.game_manager.gameplay import PlayGame


def make_game(players):
    return PlayGame(players=players, mode="cli")


def run_on_turn(player, turn, effect):
    """Runs `effect` as `player` starts their `turn`-th turn (1-based)."""
    original = player.get_bid
    seen = {"turns": 0}

    def wrapped(timeout=None):
        seen["turns"] += 1
        if seen["turns"] == turn:
            effect()
        return original(timeout=timeout)

    player.get_bid = wrapped


class TestReconnectMidAuction:
    def test_a_seat_passed_over_mid_auction_cannot_take_the_card_after_returning(self, make_player):
        """B drops during A's turn and is passed over, then returns during
        C's turn. When A passes, C holds the only bid. B, who sits earlier at
        the table and bid nothing, used to win the card for free."""
        painting = Painting(value=5)
        a = make_player("A", actions=[[4], "pass"])
        b = make_player("B")
        c = make_player("C", actions=[[6]])
        game = make_game([a, b, c])

        run_on_turn(a, 1, lambda: setattr(b, "active", False))
        run_on_turn(c, 1, lambda: setattr(b, "active", True))
        game.normal_card_auction(painting, starting_player_id=0)

        assert painting in c.status_cards
        assert painting not in b.status_cards

    def test_a_seat_absent_at_the_start_waits_for_the_next_auction(self, make_player):
        """Same stale count, for a seat already gone when the auction began."""
        painting = Painting(value=5)
        low = make_player("Low", actions=[[1], "pass"])
        returning = make_player("Returning", actions=[[8]])
        high = make_player("High", actions=[[2, 3]])
        game = make_game([low, returning, high])

        returning.active = False
        run_on_turn(high, 1, lambda: setattr(returning, "active", True))

        game.normal_card_auction(painting, starting_player_id=0)

        assert painting in high.status_cards
        assert painting not in returning.status_cards


class TestMoneyOnDisconnect:
    def test_a_dropped_seat_gets_its_bid_back_when_passed_over(self, make_player):
        painting = Painting(value=5)
        dropper = make_player("Dropper", actions=[[6]])
        rival = make_player("Rival", actions=[[8], "pass"])
        third = make_player("Third", actions=["pass"])
        game = make_game([dropper, rival, third])
        money_before = dropper.money_left()

        run_on_turn(rival, 1, lambda: setattr(dropper, "active", False))
        game.normal_card_auction(painting, starting_player_id=0)

        assert painting in rival.status_cards
        assert dropper.money_left() == money_before

    def test_a_disgrace_loser_who_dropped_keeps_their_money(self, make_player):
        """The first to leave a disgrace auction takes the card and keeps
        their money. Leaving by disconnect is the same rule."""
        card = Passe()
        dropper = make_player("Dropper", actions=[[6]])
        rival = make_player("Rival", actions=[[8]])
        game = make_game([dropper, rival])
        money_before = dropper.money_left()

        run_on_turn(rival, 1, lambda: setattr(dropper, "active", False))
        loser_id = game.disgrace_card_auction(current_player_id=0, status_card=card)

        assert game.players[loser_id] is dropper
        assert dropper.money_left() == money_before

    def test_the_result_does_not_charge_a_seat_that_was_refunded(self, make_player):
        painting = Painting(value=5)
        dropper = make_player("Dropper", actions=[[6]])
        rival = make_player("Rival", actions=[[8], "pass"])
        third = make_player("Third", actions=["pass"])
        game = make_game([dropper, rival, third])

        run_on_turn(rival, 1, lambda: setattr(dropper, "active", False))
        game.normal_card_auction(painting, starting_player_id=0)

        assert game.auction_rounds[-1].money_spent["dropper"] == 0


class TestTopBidderDrops:
    def test_the_card_is_not_lost_when_the_only_rival_passes(self, make_player):
        """A bids and drops, B passes. A's bid still stands, so A gets the
        card and pays for it. It used to vanish: no winner, and A's money
        gone with it."""
        painting = Painting(value=5)
        a = make_player("A", actions=[[6]])
        b = make_player("B", actions=["pass"])
        game = make_game([a, b])
        money_before = a.money_left()

        # Dropped as A's own bid goes in, so the engine has seen it by B's turn.
        run_on_turn(a, 1, lambda: setattr(a, "active", False))
        winner_id = game.normal_card_auction(painting, starting_player_id=0)

        assert winner_id == 0
        assert painting in a.status_cards
        assert a.money_left() == money_before - 6

    def test_a_resigned_seat_cannot_win(self, make_player):
        painting = Painting(value=5)
        a = make_player("A", actions=[[6]])
        b = make_player("B", actions=["pass"])
        game = make_game([a, b])
        money_before = a.money_left()

        def resign():
            a.resigned = True
            a.active = False

        run_on_turn(a, 1, resign)
        winner_id = game.normal_card_auction(painting, starting_player_id=0)

        assert winner_id == 1
        assert painting in b.status_cards
        assert a.money_left() == money_before

    def test_the_price_falls_back_when_the_top_bidder_resigns(self, make_player):
        """The rest need only beat the bids still on the table, not a
        refunded one nobody can ever match."""
        painting = Painting(value=5)
        top = make_player("Top", actions=[[25]])
        low = make_player("Low", actions=[[1], "pass"])
        last = make_player("Last", actions=[[2]])
        game = make_game([top, low, last])

        def resign():
            top.resigned = True
            top.active = False

        run_on_turn(top, 1, resign)
        game.normal_card_auction(painting, starting_player_id=0)

        assert painting in last.status_cards
        assert game.auction_rounds[-1].money_spent["last"] == 2


class TestFauxPasCannotBeDodged:
    def test_a_disconnected_holder_still_owes_the_discard_after_returning(self, make_player):
        holder = make_player("Holder")
        holder.add_status_card(FauxPas())
        holder.add_status_card(Painting(value=9))
        holder.active = False
        game = make_game([holder, make_player("Other")])

        assert game.handle_faux_pas_penalty(0) is False  # still owed, and never blocks
        assert any(isinstance(c, Painting) for c in holder.status_cards)

        holder.active = True
        assert game.handle_faux_pas_penalty(0) is True
        assert not any(isinstance(c, Painting) for c in holder.status_cards)

    def test_a_resigned_holder_is_settled_for_good(self, make_player):
        holder = make_player("Holder")
        holder.add_status_card(Painting(value=9))
        holder.resigned = True
        holder.active = False
        game = make_game([holder, make_player("Other")])

        assert game.handle_faux_pas_penalty(0) is True


class _ComesBack:
    """Stands in for NetworkPlayer.wait_for_return on a scripted seat."""

    def __init__(self, player, returns):
        self.player = player
        self.returns = returns
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.returns:
            self.player.active = True
        return self.returns


class TestGraceOutsideYourOwnTurn:
    def test_a_seat_back_within_grace_keeps_its_place_in_a_normal_auction(self, make_player):
        painting = Painting(value=5)
        a = make_player("A", actions=[[6], "pass"])
        b = make_player("B", actions=[[8]])
        c = make_player("C", actions=["pass"])
        game = make_game([a, b, c])

        b.wait_for_return = _ComesBack(b, returns=True)
        run_on_turn(a, 1, lambda: setattr(b, "active", False))
        game.normal_card_auction(painting, starting_player_id=0)

        assert b.wait_for_return.calls == 1
        assert painting in b.status_cards

    def test_a_seat_back_within_grace_does_not_lose_a_disgrace_auction(self, make_player):
        card = Passe()
        a = make_player("A", actions=[[6]])
        b = make_player("B", actions=[[8]])
        c = make_player("C", actions=["pass"])
        game = make_game([a, b, c])

        b.wait_for_return = _ComesBack(b, returns=True)
        run_on_turn(a, 1, lambda: setattr(b, "active", False))
        loser_id = game.disgrace_card_auction(current_player_id=0, status_card=card)

        assert game.players[loser_id] is c
        assert card not in b.status_cards

    def test_a_blip_at_a_round_boundary_does_not_end_a_two_seat_game(self, make_player):
        a = make_player("A")
        b = make_player("B")
        game = make_game([a, b])

        b.active = False
        b.wait_for_return = _ComesBack(b, returns=True)
        game.play_game()

        assert b.wait_for_return.calls == 1
        assert game.auction_rounds, "the game ended before a single auction"
