from highsociety.code.common.elo import DEFAULT_K_FACTOR, compute_elo_deltas


def test_fewer_than_two_players_yields_no_change():
    assert compute_elo_deltas([{"username": "alice", "points": 10, "rating": 1000}]) == {"alice": 0}
    assert compute_elo_deltas([]) == {}


def test_equal_ratings_two_player_win_loss_is_half_k():
    deltas = compute_elo_deltas([
        {"username": "alice", "points": 10, "rating": 1000},
        {"username": "bob", "points": 5, "rating": 1000},
    ])
    assert deltas == {"alice": DEFAULT_K_FACTOR // 2, "bob": -DEFAULT_K_FACTOR // 2}


def test_tied_points_is_a_draw_no_change_at_equal_rating():
    deltas = compute_elo_deltas([
        {"username": "alice", "points": 7, "rating": 1000},
        {"username": "bob", "points": 7, "rating": 1000},
    ])
    assert deltas == {"alice": 0, "bob": 0}


def test_upset_win_gains_more_than_an_expected_win():
    underdog_deltas = compute_elo_deltas([
        {"username": "underdog", "points": 10, "rating": 800},
        {"username": "favorite", "points": 5, "rating": 1200},
    ])
    favorite_deltas = compute_elo_deltas([
        {"username": "favorite", "points": 10, "rating": 1200},
        {"username": "underdog", "points": 5, "rating": 800},
    ])
    assert underdog_deltas["underdog"] > favorite_deltas["favorite"] > 0


def test_deltas_sum_to_roughly_zero_for_a_multiplayer_game():
    deltas = compute_elo_deltas([
        {"username": "alice", "points": 15, "rating": 1000},
        {"username": "bob", "points": 10, "rating": 1000},
        {"username": "carol", "points": 5, "rating": 1000},
        {"username": "dave", "points": 0, "rating": 1000},
    ])
    # Individually rounded, so not exactly zero, but close.
    assert abs(sum(deltas.values())) <= 2
    assert deltas["alice"] > deltas["bob"] > deltas["carol"] > deltas["dave"]


def test_winner_gains_and_loser_loses_regardless_of_table_size():
    deltas = compute_elo_deltas([
        {"username": "alice", "points": 20, "rating": 1000},
        {"username": "bob", "points": 10, "rating": 1000},
        {"username": "carol", "points": 0, "rating": 1000},
    ])
    assert deltas["alice"] > 0
    assert deltas["carol"] < 0
