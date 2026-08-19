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
