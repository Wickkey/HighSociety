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


def _seat(username, placement, rating=1000, left=False):
    return {"username": username, "placement": placement, "rating": rating, "left": left}


def test_three_players_one_resigns_the_two_left_get_nothing():
    deltas = compute_elo_deltas([_seat("a", 1), _seat("b", 2), _seat("gone", 3, left=True)])
    assert deltas["a"] == 0
    assert deltas["b"] == 0
    assert deltas["gone"] == round(DEFAULT_K_FACTOR * -0.5)


def test_four_players_one_resigns_the_rest_are_rated_as_a_three_player_game():
    deltas = compute_elo_deltas([_seat("a", 1), _seat("b", 2), _seat("c", 3), _seat("gone", 4, left=True)])
    three_player = compute_elo_deltas([_seat("a", 1), _seat("b", 2), _seat("c", 3)])
    assert {k: deltas[k] for k in "abc"} == three_player
    assert deltas["gone"] < 0


def test_five_players_one_resigns_the_rest_are_rated_as_a_four_player_game():
    seats = [_seat("a", 1), _seat("b", 2), _seat("c", 3), _seat("d", 4)]
    deltas = compute_elo_deltas(seats + [_seat("gone", 5, left=True)])
    assert {k: deltas[k] for k in "abcd"} == compute_elo_deltas(seats)
    assert deltas["gone"] < 0


def test_a_resigner_never_gains_from_tying_a_much_stronger_resigner():
    """Losing to far stronger stayers costs almost nothing, so the tie with a
    strong resigner would put the weak one ahead."""
    deltas = compute_elo_deltas([_seat("a", 1, 3000), _seat("b", 2, 3000), _seat("strong", 3, 3000, left=True),
                                 _seat("weak", 4, 1000, left=True)])
    assert deltas["weak"] == 0
    assert deltas["strong"] < 0


def test_a_resigner_ranks_last_even_if_given_a_better_placement():
    deltas = compute_elo_deltas([_seat("gone", 1, left=True), _seat("a", 2), _seat("b", 3), _seat("c", 4)])
    assert deltas["gone"] == compute_elo_deltas([_seat("a", 1), _seat("b", 1), _seat("c", 1), _seat("gone", 4)])["gone"]


def test_two_resigners_each_take_the_penalty_and_tie_with_each_other():
    deltas = compute_elo_deltas([_seat("a", 1), _seat("b", 2), _seat("c", 3),
                                 _seat("x", 4, left=True), _seat("y", 5, left=True)])
    assert deltas["x"] == deltas["y"] < 0
    assert {k: deltas[k] for k in "abc"} == compute_elo_deltas([_seat("a", 1), _seat("b", 2), _seat("c", 3)])


def test_only_two_left_after_several_resign_means_no_movement_for_them():
    deltas = compute_elo_deltas([_seat("a", 1), _seat("b", 2), _seat("x", 3, left=True), _seat("y", 4, left=True)])
    assert deltas["a"] == deltas["b"] == 0
    assert deltas["x"] == deltas["y"] < 0


def test_an_unrated_seat_still_counts_towards_who_remains():
    """Four seats, one resigned, one a guest: three are still in, so the two
    rated players left are rated against each other, not frozen at zero."""
    standings = [_seat("a", 1), _seat("b", 3), _seat("gone", 4, left=True)]
    deltas = compute_elo_deltas(standings, seats_remaining=3)
    assert deltas["a"] > 0 > deltas["b"]


def test_everyone_resigning_only_ever_costs_rating():
    deltas = compute_elo_deltas([_seat("x", 1, 1400, left=True), _seat("y", 2, 1000, left=True), _seat("z", 3, 1000, left=True)])
    assert all(d <= 0 for d in deltas.values())
    assert deltas["x"] < 0
