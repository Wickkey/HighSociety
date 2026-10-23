from highsociety.code.common.achievements import ACHIEVEMENTS, detect_per_game_achievements


def _standings(*usernames):
    return [{"username": u, "points": 0, "money_left": 0, "active": True, "eliminated": False} for u in usernames]


def test_every_achievement_id_is_unique():
    ids = [a.id for a in ACHIEVEMENTS]
    assert len(ids) == len(set(ids))


def test_no_achievements_from_an_empty_game():
    result = detect_per_game_achievements(_standings("alice", "bob"), set(), [], [])
    assert result == {}


def test_sniper_requires_exactly_one_bid_from_the_winner():
    auction = {
        "auction_type": "normal", "card": {"type": "Painting", "value": 5},
        "recipient": "alice", "money_spent": {"alice": 5, "bob": 0},
        "events": [{"player": "alice", "action": "bid", "amount": 5}, {"player": "bob", "action": "pass"}],
    }
    result = detect_per_game_achievements(_standings("alice", "bob"), set(), [auction], [])
    assert "sniper" in result["alice"]


def test_sniper_not_awarded_when_someone_else_also_bid():
    auction = {
        "auction_type": "normal", "card": {"type": "Painting", "value": 5},
        "recipient": "alice", "money_spent": {"alice": 5, "bob": 0},
        "events": [
            {"player": "bob", "action": "bid", "amount": 3},
            {"player": "alice", "action": "bid", "amount": 5},
            {"player": "bob", "action": "pass"},
        ],
    }
    result = detect_per_game_achievements(_standings("alice", "bob"), set(), [auction], [])
    assert "sniper" not in result.get("alice", set())


def test_free_lunch_on_a_normal_auction_paying_zero():
    auction = {
        "auction_type": "normal", "card": {"type": "Painting", "value": 3},
        "recipient": "alice", "money_spent": {"alice": 0, "bob": 0},
        "events": [{"player": "alice", "action": "bid", "amount": 0}],
    }
    result = detect_per_game_achievements(_standings("alice", "bob"), set(), [auction], [])
    assert "free_lunch" in result["alice"]


def test_free_lunch_excluded_for_a_disgrace_auction_even_though_it_also_pays_zero():
    auction = {
        "auction_type": "disgrace", "card": {"type": "FauxPas", "value": 0},
        "recipient": "alice", "money_spent": {"alice": 0, "bob": 2},
        "events": [{"player": "bob", "action": "bid", "amount": 2}, {"player": "alice", "action": "pass"}],
    }
    result = detect_per_game_achievements(_standings("alice", "bob"), set(), [auction], [])
    assert "free_lunch" not in result.get("alice", set())
    assert "master_of_disgrace" in result["alice"]


def test_minimalist_requires_a_one_value_painting():
    auction = {
        "auction_type": "normal", "card": {"type": "Painting", "value": 1},
        "recipient": "alice", "money_spent": {"alice": 2}, "events": [],
    }
    result = detect_per_game_achievements(_standings("alice"), set(), [auction], [])
    assert "minimalist" in result["alice"]


def test_minimalist_not_awarded_for_a_higher_value_painting():
    auction = {
        "auction_type": "normal", "card": {"type": "Painting", "value": 2},
        "recipient": "alice", "money_spent": {"alice": 2}, "events": [],
    }
    result = detect_per_game_achievements(_standings("alice"), set(), [auction], [])
    assert "minimalist" not in result.get("alice", set())


def test_full_set_requires_all_three_prestige_cards():
    auctions = [
        {"auction_type": "normal", "card": {"type": "PrestigeCard", "value": 0},
         "recipient": "alice", "money_spent": {}, "events": []}
        for _ in range(3)
    ]
    result = detect_per_game_achievements(_standings("alice"), set(), auctions, [])
    assert "full_set" in result["alice"]


def test_full_set_not_awarded_for_only_two_prestige_cards():
    auctions = [
        {"auction_type": "normal", "card": {"type": "PrestigeCard", "value": 0},
         "recipient": "alice", "money_spent": {}, "events": []}
        for _ in range(2)
    ]
    result = detect_per_game_achievements(_standings("alice"), set(), auctions, [])
    assert "full_set" not in result.get("alice", set())


def test_collector_requires_winning_every_type_offered():
    auctions = [
        {"auction_type": "normal", "card": {"type": "Painting", "value": 3},
         "recipient": "alice", "money_spent": {}, "events": []},
        {"auction_type": "normal", "card": {"type": "PrestigeCard", "value": 0},
         "recipient": "alice", "money_spent": {}, "events": []},
    ]
    result = detect_per_game_achievements(_standings("alice"), set(), auctions, [])
    assert "collector" in result["alice"]


def test_collector_not_awarded_when_a_type_went_to_someone_else():
    auctions = [
        {"auction_type": "normal", "card": {"type": "Painting", "value": 3},
         "recipient": "alice", "money_spent": {}, "events": []},
        {"auction_type": "normal", "card": {"type": "PrestigeCard", "value": 0},
         "recipient": "bob", "money_spent": {}, "events": []},
    ]
    result = detect_per_game_achievements(_standings("alice", "bob"), set(), auctions, [])
    assert "collector" not in result.get("alice", set())


def test_giant_slayer_requires_a_win_with_a_hard_bot_at_the_table():
    result = detect_per_game_achievements(_standings("alice"), {"alice"}, [], ["hard", "easy"])
    assert "giant_slayer" in result["alice"]


def test_giant_slayer_not_awarded_without_a_hard_bot():
    result = detect_per_game_achievements(_standings("alice"), {"alice"}, [], ["easy"])
    assert "giant_slayer" not in result.get("alice", set())


def test_giant_slayer_not_awarded_for_a_loss_even_with_a_hard_bot():
    result = detect_per_game_achievements(_standings("alice"), set(), [], ["hard"])
    assert "giant_slayer" not in result.get("alice", set())


def test_fearless_requires_a_win_with_no_pass_fold_or_quit():
    auction = {
        "auction_type": "normal", "card": {"type": "Painting", "value": 3},
        "recipient": "alice", "money_spent": {"alice": 5},
        "events": [{"player": "alice", "action": "bid", "amount": 5}],
    }
    result = detect_per_game_achievements(_standings("alice", "bob"), {"alice"}, [auction], [])
    assert "fearless" in result["alice"]


def test_fearless_not_awarded_if_the_winner_ever_passed():
    auctions = [
        {"auction_type": "normal", "card": {"type": "Painting", "value": 2},
         "recipient": "alice", "money_spent": {"alice": 2},
         "events": [{"player": "bob", "action": "pass"}, {"player": "alice", "action": "bid", "amount": 2}]},
        {"auction_type": "normal", "card": {"type": "Painting", "value": 5},
         "recipient": "bob", "money_spent": {"bob": 5},
         "events": [{"player": "bob", "action": "bid", "amount": 5}]},
    ]
    result = detect_per_game_achievements(_standings("alice", "bob"), {"bob"}, auctions, [])
    assert "fearless" not in result.get("bob", set())


def _painting_win(username, value, paid, auction_type="normal"):
    return {
        "auction_type": auction_type, "card": {"type": "Painting", "value": value},
        "recipient": username, "money_spent": {username: paid},
        "events": [{"player": username, "action": "bid", "amount": paid}],
    }


def _scored(*rows):
    """rows: (username, points, money_left, eliminated)"""
    return [{"username": u, "points": p, "money_left": m, "active": True, "eliminated": e} for u, p, m, e in rows]


def test_masterpiece_for_the_top_painting_only():
    result = detect_per_game_achievements(_standings("alice", "bob"), set(),
                                          [_painting_win("alice", 10, 4), _painting_win("bob", 9, 4)], [])
    assert "masterpiece" in result["alice"]
    assert "masterpiece" not in result.get("bob", set())


def test_high_roller_needs_20_on_one_card():
    result = detect_per_game_achievements(_standings("alice", "bob"), set(),
                                          [_painting_win("alice", 5, 20), _painting_win("bob", 5, 19)], [])
    assert "high_roller" in result["alice"]
    assert "high_roller" not in result.get("bob", set())


def test_clean_hands_lost_by_taking_a_disgrace_card():
    disgrace = {
        "auction_type": "disgrace", "card": {"type": "Passe", "value": 0},
        "recipient": "bob", "money_spent": {"bob": 0}, "events": [{"player": "bob", "action": "pass"}],
    }
    result = detect_per_game_achievements(_standings("alice", "bob"), {"alice", "bob"}, [disgrace], [])
    assert "clean_hands" in result["alice"]
    assert "clean_hands" not in result["bob"]


def test_landslide_and_photo_finish_margins_ignore_the_eliminated_player():
    standings = _scored(("alice", 30, 10, False), ("bob", 20, 8, False), ("carol", 40, 1, True))
    result = detect_per_game_achievements(standings, {"alice"}, [], ["easy", "easy"])
    assert "landslide" in result["alice"]
    assert "photo_finish" not in result["alice"]

    standings = _scored(("alice", 21, 10, False), ("bob", 20, 8, False), ("carol", 40, 1, True))
    result = detect_per_game_achievements(standings, {"alice"}, [], [])
    assert "photo_finish" in result["alice"]
    assert "landslide" not in result["alice"]


def test_a_shared_win_is_a_photo_finish_for_both():
    standings = _scored(("alice", 20, 10, False), ("bob", 20, 8, False))
    result = detect_per_game_achievements(standings, {"alice", "bob"}, [], [])
    assert "photo_finish" in result["alice"] and "photo_finish" in result["bob"]


def test_narrow_escape_is_one_money_above_the_eliminated_player():
    standings = _scored(("alice", 10, 5, False), ("bob", 10, 6, False), ("carol", 10, 4, True))
    result = detect_per_game_achievements(standings, set(), [], [])
    assert "narrow_escape" in result["alice"]
    assert "narrow_escape" not in result.get("bob", set())


def test_good_company_needs_another_human_and_hard_times_two_hard_bots():
    standings = _standings("alice", "bob", "hard-1")
    result = detect_per_game_achievements(standings, {"alice"}, [], ["hard"])
    assert "good_company" in result["alice"]
    assert "hard_times" not in result["alice"]

    standings = _standings("alice", "hard-1", "hard-2")
    result = detect_per_game_achievements(standings, {"alice"}, [], ["hard", "hard"])
    assert "good_company" not in result["alice"]
    assert "hard_times" in result["alice"]
