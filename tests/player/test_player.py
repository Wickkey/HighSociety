import pytest

from highsociety.code.gamecore.player.cliplayer import CLIPlayer
from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.components_module.prestige_card import PrestigeCard
from highsociety.code.gamecore.components_module.disgrace_card import FauxPas, Scandale


@pytest.fixture
def player():
    return CLIPlayer(name="Alice", username="alice")


def test_new_player_starts_with_configured_money(player):
    assert sorted(m.value for m in player.money_cards) == [1, 2, 3, 4, 6, 8, 10, 12, 15, 20, 25]
    assert player.money_left() == sum([1, 2, 3, 4, 6, 8, 10, 12, 15, 20, 25])
    assert player.points == 0
    assert player.status_cards == ()


def test_place_bid_moves_cards_from_hand_to_bid(player):
    total_before = player.money_left()
    placed = player.place_bid([1, 2])
    assert placed == 3
    assert player.current_bid_value == 3
    assert player.money_left() == total_before - 3
    assert sorted(c.value for c in player.current_money_card_bids) == [1, 2]


def test_place_bid_accumulates_across_calls(player):
    player.place_bid(1)
    total = player.place_bid(2)
    assert total == 3
    assert player.current_bid_value == 3


def test_withdraw_bid_returns_money_and_marks_out(player):
    player.place_bid([1, 2])
    total_before_withdraw = player.money_left()
    player.withdraw_bid()
    assert player.money_left() == total_before_withdraw + 3
    assert player.current_bid_value == 0
    assert player.current_money_card_bids == ()
    assert player.current_participation_in_auction is False


def test_withdraw_bid_twice_is_a_safe_noop(player):
    player.place_bid(1)
    player.withdraw_bid()
    money_after_first = player.money_left()
    player.withdraw_bid()  # already withdrawn; should not double-refund or error
    assert player.money_left() == money_after_first


def test_reset_auction_attributes_reopens_participation(player):
    player.place_bid(1)
    player.withdraw_bid()
    assert player.current_participation_in_auction is False
    player.reset_auction_attributes()
    assert player.current_participation_in_auction is True
    assert player.current_bid_value == 0


def test_add_status_card_updates_points(player):
    player.add_status_card(Painting(value=5))
    assert player.points == 5
    player.add_status_card(Painting(value=3))
    assert player.points == 8


def test_prestige_card_multiplies_points(player):
    player.add_status_card(Painting(value=5))
    player.add_status_card(PrestigeCard())
    assert player.points == 10


def test_scandale_halves_points(player):
    player.add_status_card(Painting(value=10))
    player.add_status_card(Scandale())
    assert player.points == 5.0


def test_faux_pas_sets_holds_flag(player):
    assert player.holds_faux_pas is False
    player.add_status_card(FauxPas())
    assert player.holds_faux_pas is True


def test_discard_painting_card_removes_and_recalculates(player):
    player.add_status_card(Painting(value=5))
    player.add_status_card(Painting(value=3))
    assert player.points == 8

    discarded = player.discard_painting_card(5)
    assert isinstance(discarded, Painting) and discarded.value == 5
    assert player.points == 3
    assert len(player.status_cards) == 1


def test_discard_painting_card_missing_value_returns_none(player):
    player.add_status_card(Painting(value=5))
    assert player.discard_painting_card(999) is None
    assert len(player.status_cards) == 1


def test_discard_painting_card_flips_has_discarded_card(player):
    player.add_status_card(FauxPas())
    player.add_status_card(Painting(value=5))
    assert player.has_discarded_card is False

    player.discard_painting_card(5)
    assert player.has_discarded_card is True


def test_discard_painting_card_missing_value_does_not_flip_has_discarded_card(player):
    player.add_status_card(FauxPas())
    player.add_status_card(Painting(value=5))
    player.discard_painting_card(999)  # no matching card -- nothing actually discarded
    assert player.has_discarded_card is False


def test_a_new_faux_pas_resets_has_discarded_card(player):
    player.add_status_card(FauxPas())
    player.add_status_card(Painting(value=5))
    player.discard_painting_card(5)
    assert player.has_discarded_card is True

    player.add_status_card(FauxPas())  # picked up again -- a fresh discard obligation
    assert player.has_discarded_card is False
