from highsociety.code.common.elo import DEFAULT_K_FACTOR, compute_elo_deltas


def test_fewer_than_two_players_yields_no_change():
    assert compute_elo_deltas([{"username": "alice", "placement": 1, "rating": 1000}]) == {"alice": 0}
    assert compute_elo_deltas([]) == {}


def test_equal_ratings_two_player_win_loss_is_half_k():
    deltas = compute_elo_deltas([
        {"username": "alice", "placement": 1, "rating": 1000},
        {"username": "bob", "placement": 2, "rating": 1000},
    ])
    assert deltas == {"alice": DEFAULT_K_FACTOR // 2, "bob": -DEFAULT_K_FACTOR // 2}


def test_tied_placement_is_a_draw_no_change_at_equal_rating():
    deltas = compute_elo_deltas([
        {"username": "alice", "placement": 1, "rating": 1000},
        {"username": "bob", "placement": 1, "rating": 1000},
    ])
    assert deltas == {"alice": 0, "bob": 0}


def test_upset_win_gains_more_than_an_expected_win():
    underdog_deltas = compute_elo_deltas([
        {"username": "underdog", "placement": 1, "rating": 800},
        {"username": "favorite", "placement": 2, "rating": 1200},
    ])
    favorite_deltas = compute_elo_deltas([
        {"username": "favorite", "placement": 1, "rating": 1200},
        {"username": "underdog", "placement": 2, "rating": 800},
    ])
    assert underdog_deltas["underdog"] > favorite_deltas["favorite"] > 0


def test_deltas_sum_to_roughly_zero_for_a_multiplayer_game():
    deltas = compute_elo_deltas([
        {"username": "alice", "placement": 1, "rating": 1000},
        {"username": "bob", "placement": 2, "rating": 1000},
        {"username": "carol", "placement": 3, "rating": 1000},
        {"username": "dave", "placement": 4, "rating": 1000},
    ])
    # Individually rounded, so not exactly zero, but close.
    assert abs(sum(deltas.values())) <= 2
    assert deltas["alice"] > deltas["bob"] > deltas["carol"] > deltas["dave"]


def test_winner_gains_and_loser_loses_regardless_of_table_size():
    deltas = compute_elo_deltas([
        {"username": "alice", "placement": 1, "rating": 1000},
        {"username": "bob", "placement": 2, "rating": 1000},
        {"username": "carol", "placement": 3, "rating": 1000},
    ])
    assert deltas["alice"] > 0
    assert deltas["carol"] < 0


def test_placement_not_points_decides_the_pairwise_outcome():
    """Real bug, confirmed live in production: a player eliminated for
    having the least money can still have the game's highest raw score
    (the win condition explicitly ranks eliminated players last
    regardless of points -- see game_history.py's _placement_tier), but
    compute_elo_deltas used to compare raw points directly, so that
    player's Elo still went *up* despite finishing dead last. Passing
    the already-elimination-aware placement instead must rank them
    correctly regardless of how many points they scored."""
    deltas = compute_elo_deltas([
        # Finished 5th (eliminated) despite the highest points of the table.
        {"username": "eliminated_leader", "placement": 5, "rating": 1000},
        {"username": "actual_winner", "placement": 1, "rating": 1000},
        {"username": "second", "placement": 2, "rating": 1000},
        {"username": "third", "placement": 3, "rating": 1000},
        {"username": "fourth", "placement": 4, "rating": 1000},
    ])
    assert deltas["eliminated_leader"] < 0
    assert deltas["actual_winner"] > 0
    assert deltas["eliminated_leader"] < deltas["fourth"]
