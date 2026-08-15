from highsociety.code.ai.pass_bot import PassBot
from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.components_module.disgrace_card import FauxPas
from highsociety.code.gamecore.game_manager.auction_history import AuctionHistory
from highsociety.code.gamecore.game_manager.gameplay import PlayGame


def test_empty_before_any_turn_is_recorded():
    history = AuctionHistory()
    assert history.player_snapshots == {}
    assert history.last_updated_at == 0.0


def test_record_turn_snapshots_every_player_field(make_player):
    alice = make_player("Alice", username="alice")
    alice.add_status_card(Painting(value=7))
    alice.add_status_card(FauxPas())

    history = AuctionHistory()
    history.record_turn([alice])

    snap = history.player_snapshots["alice"]
    assert snap.username == "alice"
    assert snap.is_bot is False  # ScriptedPlayer extends CLIPlayer
    assert sorted(snap.money_cards) == sorted(c.value for c in alice.money_cards)
    # status_cards covers every kind held, not just Paintings.
    assert sorted(c["type"] for c in snap.status_cards) == ["FauxPas", "Painting"]
    painting_summary = next(c for c in snap.status_cards if c["type"] == "Painting")
    assert painting_summary["value"] == 7
    assert snap.current_money_card_bids == []  # nothing committed to a live bid
    assert snap.points == alice.points
    assert snap.holds_faux_pas is True
    assert snap.faux_pas_discarded is False  # matches has_discarded_card
    assert snap.active is True


def test_record_turn_captures_cards_committed_to_a_live_bid(make_player):
    alice = make_player("Alice", username="alice")
    alice.place_bid(3)  # commits the 3-value money card to a live, unresolved bid

    history = AuctionHistory()
    history.record_turn([alice])

    snap = history.player_snapshots["alice"]
    assert snap.current_money_card_bids == [3]
    # money_cards correctly excludes the committed card, same as BasePlayer.money_cards itself.
    assert 3 not in snap.money_cards


def test_record_turn_marks_a_real_bot_as_is_bot():
    bot = PassBot(name="Bot", username="bot")
    history = AuctionHistory()
    history.record_turn([bot])
    assert history.player_snapshots["bot"].is_bot is True


def test_record_turn_overwrites_the_previous_snapshot_for_the_same_player(make_player):
    alice = make_player("Alice", username="alice")
    history = AuctionHistory()
    history.record_turn([alice])
    first_updated_at = history.last_updated_at

    alice.add_status_card(Painting(value=3))
    history.record_turn([alice])

    assert len(history.player_snapshots) == 1  # not accumulating stale entries
    assert history.player_snapshots["alice"].points == alice.points
    assert history.last_updated_at >= first_updated_at


def test_wired_into_a_real_game_stays_current_end_to_end():
    """
    Integration check that PlayGame actually calls back into AuctionHistory
    as the game progresses, for both a normal auction's turn-by-turn bidding
    and its final settled state -- not just that AuctionHistory works in
    isolation (covered above).
    """
    history = AuctionHistory()
    players = [PassBot(name="A", username="a"), PassBot(name="B", username="b")]
    game = PlayGame(players=players, mode="cli", seed=1, auction_history=history)

    assert history.player_snapshots == {}  # nothing yet -- game hasn't started

    game.play_game()

    assert set(history.player_snapshots) == {"a", "b"}
    for p in players:
        snap = history.player_snapshots[p.username]
        assert sorted(snap.money_cards) == sorted(c.value for c in p.money_cards)
        assert snap.points == p.points
