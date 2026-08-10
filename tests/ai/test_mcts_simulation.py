import pytest

from highsociety.code.ai.mcts.policy import capped_greedy_policy
from highsociety.code.ai.mcts.simulation import (
    SimCard, SimPlayer, SimState, advance_to_actor, evaluate_reward, full_deck_composition,
    is_terminal, legal_actions, rollout_to_end, sample_future_deck, step,
)


def _painting(value):
    return SimCard(kind="Painting", value=value, multiplier=1, is_green=False)


def _prestige():
    return SimCard(kind="PrestigeCard", value=0, multiplier=2, is_green=True)


def _passe():
    return SimCard(kind="Passe", value=-5, multiplier=1, is_green=False)


def _scandale():
    return SimCard(kind="Scandale", value=0, multiplier=0.5, is_green=True)


def _faux_pas():
    return SimCard(kind="FauxPas", value=0, multiplier=1, is_green=False)


def _make_state(hands, current_card, is_disgrace=False, max_bid=0, turn=0, green_count=0,
                 settlement="forfeit", green_card_limit=4, deck=None):
    players = [SimPlayer(username=name, money_cards=sorted(cards)) for name, cards in hands.items()]
    return SimState(
        players=players, turn=turn, deck=list(deck or []), current_card=current_card,
        is_disgrace=is_disgrace, max_bid=max_bid, still_in=[True] * len(players),
        green_count=green_count, settlement=settlement, green_card_limit=green_card_limit,
    )


class TestFullDeckComposition:
    def test_matches_hsconfig_counts(self):
        deck = full_deck_composition()
        assert len(deck) == 16  # 10 paintings + 3 prestige + 3 disgrace, per HSConfig.json defaults
        assert sum(1 for c in deck if c.kind == "Painting") == 10
        assert sum(1 for c in deck if c.kind == "PrestigeCard") == 3
        assert sum(1 for c in deck if c.is_green) == 4  # 3 prestige + 1 scandale

    def test_returns_a_fresh_list_each_time(self):
        a = full_deck_composition()
        a.pop()
        b = full_deck_composition()
        assert len(b) == 16  # mutating a previous call's result must not affect later ones


class TestSampleFutureDeck:
    def test_excludes_revealed_cards_and_keeps_the_rest(self):
        import random
        full = full_deck_composition()
        revealed = full[:3]
        remaining = sample_future_deck(revealed, rng=random.Random(1))
        assert len(remaining) == len(full) - 3
        for card in revealed:
            assert remaining.count(card) == full.count(card) - 1

    def test_raises_if_asked_to_remove_a_card_not_in_the_deck(self):
        import random
        with pytest.raises(ValueError):
            sample_future_deck([SimCard(kind="Painting", value=999, multiplier=1, is_green=False)],
                                rng=random.Random(1))


class TestLegalActions:
    def test_pass_is_always_offered(self):
        state = _make_state({"me": [1, 2, 3], "opp": [1, 2, 3]}, _painting(5))
        assert "pass" in legal_actions(state)

    def test_offers_only_cards_that_would_exceed_the_max_bid(self):
        state = _make_state({"me": [1, 2, 3, 10], "opp": []}, _painting(5), max_bid=2)
        actions = legal_actions(state)
        assert set(actions) == {"pass", 3, 10}  # 1, 2 wouldn't exceed max_bid=2

    def test_accounts_for_what_i_already_committed_this_auction(self):
        state = _make_state({"me": [1, 5], "opp": []}, _painting(5), max_bid=3)
        state.players[0].current_bid_cards = [1]  # already committed 1 -- current_bid == 1
        actions = legal_actions(state)
        assert set(actions) == {"pass", 5}  # 1 + 5 = 6 > 3; nothing else in hand helps

    def test_empty_for_a_terminal_state(self):
        state = _make_state({"me": [], "opp": []}, _painting(5))
        state.game_over = True
        assert legal_actions(state) == []


class TestNormalAuctionStep:
    def test_raising_updates_max_bid_and_moves_to_next_player(self):
        state = _make_state({"me": [1, 5], "opp": [3]}, _painting(5))
        result = step(state, 5)
        assert result.max_bid == 5
        assert result.players[0].money_cards == [1]
        assert result.players[0].current_bid_cards == [5]
        assert result.turn == 1

    def test_step_does_not_mutate_the_input_state(self):
        state = _make_state({"me": [1, 5], "opp": [3]}, _painting(5))
        step(state, 5)
        assert state.players[0].money_cards == [1, 5]  # untouched
        assert state.max_bid == 0

    def test_last_remaining_bidder_wins_for_free_when_the_other_passes(self):
        state = _make_state({"me": [1, 5], "opp": [3]}, _painting(5), turn=1)
        result = step(state, "pass")
        assert result.game_over or result.current_card != _painting(5)  # auction concluded, moved on
        me = next(p for p in result.players if p.username == "me")
        assert any(c.value == 5 and c.kind == "Painting" for c in me.status_cards)
        assert me.current_bid_cards == []  # never had to actually pay since nobody contested

    def test_passing_refunds_committed_cards(self):
        state = _make_state({"me": [1, 5], "opp": [1, 2, 3]}, _painting(5), turn=0, max_bid=3)
        state.players[0].current_bid_cards = [5]
        state.players[0].money_cards = [1]
        result = step(state, "pass")
        me = next(p for p in result.players if p.username == "me")
        assert sorted(me.money_cards) == [1, 5]
        assert me.current_bid_cards == []

    def test_winner_permanently_loses_their_committed_cards(self):
        state = _make_state({"me": [1], "opp": [3]}, _painting(5), turn=1, max_bid=5)
        state.players[0].current_bid_cards = [5]
        result = step(state, "pass")  # opp passes, "me" (already sole remaining) wins
        me = next(p for p in result.players if p.username == "me")
        assert me.current_bid_cards == []
        assert 5 not in me.money_cards  # never refunded -- paid for the card


class TestDisgraceAuctionStep:
    def test_first_to_pass_loses_and_takes_the_card(self):
        state = _make_state({"me": [1, 5], "opp": [3]}, _passe(), is_disgrace=True, turn=0)
        result = step(state, "pass")
        me = next(p for p in result.players if p.username == "me")
        assert any(c.kind == "Passe" for c in me.status_cards)

    def test_default_forfeit_settlement_keeps_raised_money_lost(self):
        state = _make_state({"me": [1, 5], "opp": [10]}, _passe(), is_disgrace=True, turn=1, max_bid=1,
                             settlement="forfeit")
        state.players[0].current_bid_cards = [1]
        state.players[0].money_cards = [5]
        result = step(state, "pass")  # opp passes, takes the card; "me" already raised and forfeits
        me = next(p for p in result.players if p.username == "me")
        assert me.current_bid_cards == []
        assert 1 not in me.money_cards  # forfeited, not refunded

    def test_refund_all_settlement_returns_everyone_their_money(self):
        state = _make_state({"me": [1, 5], "opp": [10]}, _passe(), is_disgrace=True, turn=1, max_bid=1,
                             settlement="refund_all")
        state.players[0].current_bid_cards = [1]
        state.players[0].money_cards = [5]
        result = step(state, "pass")
        me = next(p for p in result.players if p.username == "me")
        assert sorted(me.money_cards) == [1, 5]

    def test_faux_pas_sets_a_pending_discard_obligation(self):
        state = _make_state({"me": [1], "opp": [3]}, _faux_pas(), is_disgrace=True, turn=0)
        result = step(state, "pass")
        assert result.faux_pas_holder == 0


class TestFauxPasResolutionAndDeckAdvancement:
    def test_holder_discards_cheapest_painting_once_they_hold_one(self):
        deck = [_painting(9)]
        state = _make_state({"me": [1], "opp": [3]}, _faux_pas(), is_disgrace=True, turn=0, deck=deck)
        state.players[0].status_cards = [_painting(7), _painting(3)]
        result = step(state, "pass")  # "me" takes FauxPas, already holds two paintings
        me = next(p for p in result.players if p.username == "me")
        assert result.faux_pas_holder is None  # resolved immediately -- already had a painting
        assert sorted(c.value for c in me.status_cards if c.kind == "Painting") == [7]  # 3 discarded

    def test_obligation_carries_over_if_no_painting_is_held_yet(self):
        deck = [_painting(9)]
        state = _make_state({"me": [1], "opp": [3]}, _faux_pas(), is_disgrace=True, turn=0, deck=deck)
        result = step(state, "pass")
        assert result.faux_pas_holder == 0  # still pending -- held no painting at the time


class TestGameEndingConditions:
    def test_final_green_card_ends_the_game_without_auctioning_it(self):
        deck = [_prestige()]
        state = _make_state({"me": [1, 5], "opp": [3]}, _painting(5), turn=1, max_bid=5,
                             green_count=3, green_card_limit=4, deck=deck)
        result = step(state, "pass")
        assert result.game_over is True
        assert result.green_count == 4

    def test_non_final_green_card_still_gets_auctioned(self):
        deck = [_prestige()]
        state = _make_state({"me": [1, 5], "opp": [3]}, _painting(5), turn=1, max_bid=5,
                             green_count=1, green_card_limit=4, deck=deck)
        result = step(state, "pass")
        assert result.game_over is False
        assert result.current_card.kind == "PrestigeCard"
        assert result.green_count == 2

    def test_empty_deck_ends_the_game(self):
        state = _make_state({"me": [1, 5], "opp": [3]}, _painting(5), turn=1, max_bid=5, deck=[])
        result = step(state, "pass")
        assert result.game_over is True

    def test_fewer_than_two_active_players_ends_the_game(self):
        state = _make_state({"me": [1, 5], "opp": [3]}, _painting(5), turn=1, max_bid=5,
                             deck=[_painting(9)])
        state.players[1].active = False  # only "me" remains active after this auction
        result = step(state, "pass")
        assert result.game_over is True


class TestAdvanceAndRollout:
    def test_advance_to_actor_stops_the_instant_its_the_actors_turn(self):
        state = _make_state({"me": [1, 5], "opp": [3]}, _painting(5), turn=0)
        result = advance_to_actor(state, actor_idx=0, policy=capped_greedy_policy)
        assert result is state  # no-op, no cloning, when already the actor's turn

    def test_advance_to_actor_fast_forwards_through_opponents(self):
        deck = [_painting(3)]
        state = _make_state({"me": [1, 5], "opp": [10]}, _painting(5), turn=1, deck=deck)
        result = advance_to_actor(state, actor_idx=0, policy=capped_greedy_policy)
        assert result.game_over or result.turn == 0

    def test_rollout_to_end_always_reaches_a_terminal_state(self):
        deck = [_painting(v) for v in range(1, 9)]
        state = _make_state({"me": [1, 2, 4, 8], "opp": [1, 2, 4, 8]}, _painting(9), turn=0, deck=deck)
        result = rollout_to_end(state, capped_greedy_policy)
        assert is_terminal(result)


class TestEvaluateReward:
    def test_sole_winner_gets_full_reward(self):
        state = _make_state({"me": [10], "opp": [1]}, _painting(1))
        state.players[0].status_cards = [_painting(10)]
        state.players[1].status_cards = [_painting(1)]
        assert evaluate_reward(state, me_idx=0) == 1.0
        assert evaluate_reward(state, me_idx=1) == 0.0

    def test_tie_splits_the_reward(self):
        state = _make_state({"me": [10], "opp": [10]}, _painting(1))
        state.players[0].status_cards = [_painting(5)]
        state.players[1].status_cards = [_painting(5)]
        assert evaluate_reward(state, me_idx=0) == 0.5
        assert evaluate_reward(state, me_idx=1) == 0.5

    def test_lowest_money_is_eliminated_regardless_of_points(self):
        state = _make_state({"me": [], "opp": [10]}, _painting(1))
        state.players[0].status_cards = [_painting(50)]  # far more points, but broke
        state.players[1].status_cards = [_painting(1)]
        assert evaluate_reward(state, me_idx=0) == 0.0  # eliminated on money alone
        assert evaluate_reward(state, me_idx=1) == 1.0

    def test_tied_lowest_money_eliminates_nobody(self):
        state = _make_state({"me": [], "opp": []}, _painting(1))
        state.players[0].status_cards = [_painting(9)]
        state.players[1].status_cards = [_painting(1)]
        assert evaluate_reward(state, me_idx=0) == 1.0  # tied-for-lowest-money isn't eliminated; wins on points

    def test_passe_subtracts_before_multiplying(self):
        # A Passe (-5) plus a Prestige (x2): (10 + -5) * 2 = 10, not 10*2 - 5 = 15.
        state = _make_state({"me": [10], "opp": [10]}, _painting(1))
        state.players[0].status_cards = [_painting(10), _passe(), _prestige()]
        state.players[1].status_cards = [_painting(9)]
        assert evaluate_reward(state, me_idx=0) == 1.0  # 10 > 9

    def test_scandale_halves_the_total(self):
        state = _make_state({"me": [10], "opp": [10]}, _painting(1))
        state.players[0].status_cards = [_painting(10), _scandale()]  # (10)*0.5 = 5
        state.players[1].status_cards = [_painting(4)]
        assert evaluate_reward(state, me_idx=0) == 1.0  # 5 > 4

    def test_inactive_players_are_never_winners(self):
        state = _make_state({"me": [10], "opp": [10]}, _painting(1))
        state.players[0].status_cards = [_painting(20)]
        state.players[0].active = False
        state.players[1].status_cards = [_painting(1)]
        assert evaluate_reward(state, me_idx=0) == 0.0
        assert evaluate_reward(state, me_idx=1) == 1.0
