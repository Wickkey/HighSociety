from highsociety.code.gamecore.game_manager.disgrace_settlement import ForfeitSettlement, RefundAllSettlement
from highsociety.code.gamecore.player.cliplayer import CLIPlayer


def make_players_with_open_bids():
    p1 = CLIPlayer(name="P1", username="p1")
    p2 = CLIPlayer(name="P2", username="p2")
    p1.place_bid([1, 2])   # still "in" the auction, hasn't withdrawn
    p2.withdraw_bid()      # simulates the player who passed and took the disgrace card
    return p1, p2


def test_forfeit_settlement_leaves_non_passers_money_committed():
    p1, p2 = make_players_with_open_bids()
    money_before = p1.money_left()

    ForfeitSettlement().settle([p1, p2], loser_id=1)

    assert p1.money_left() == money_before  # untouched: still forfeited on next reset
    assert p1.current_bid_value == 3


def test_refund_all_settlement_returns_everyones_money():
    p1, p2 = make_players_with_open_bids()
    money_before = p1.money_left()

    RefundAllSettlement().settle([p1, p2], loser_id=1)

    assert p1.money_left() == money_before + 3
    assert p1.current_bid_value == 0
    assert p1.current_participation_in_auction is False
