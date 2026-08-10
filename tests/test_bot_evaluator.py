from bot_evaluator import compute_ranks, ranked_rows, run_simulations


def test_compute_ranks_orders_by_points_and_puts_eliminated_last():
    standings = [
        {"username": "a", "points": 10, "eliminated": False},
        {"username": "b", "points": 20, "eliminated": False},
        {"username": "c", "points": 99, "eliminated": True},  # money-eliminated -- ranks last regardless
    ]
    assert compute_ranks(standings) == [2, 1, 3]


def test_compute_ranks_gives_tied_players_the_same_rank_and_skips_ahead():
    # Two-way tie for 1st, then a clear 3rd -- standard competition ranking
    # (1, 1, 3), not (1, 1, 2).
    standings = [
        {"username": "a", "points": 15, "eliminated": False},
        {"username": "b", "points": 15, "eliminated": False},
        {"username": "c", "points": 5, "eliminated": False},
    ]
    assert compute_ranks(standings) == [1, 1, 3]


def test_run_simulations_pools_seats_that_share_a_bot_type():
    # "greedy" appears twice: both seats' results should land in one row.
    stats = run_simulations(["greedy", "greedy", "pass"], num_simulations=4, think_time=0, seed=1, progress=False)

    assert stats["greedy"]["matches"] == 8  # 2 seats x 4 simulations
    assert stats["pass"]["matches"] == 4  # 1 seat x 4 simulations
    # Every seat-instance's win/loss and rank must have been counted.
    assert stats["greedy"]["wins"] + stats["pass"]["wins"] == 4  # exactly one winner per game (no ties expected here, but >=)
    for bot_stats in stats.values():
        assert 0 <= bot_stats["rank_sum"] / bot_stats["matches"] <= 1


def test_run_simulations_is_reproducible_with_the_same_seed():
    stats_a = run_simulations(["greedy", "pass"], num_simulations=5, think_time=0, seed=42, progress=False)
    stats_b = run_simulations(["greedy", "pass"], num_simulations=5, think_time=0, seed=42, progress=False)
    assert dict(stats_a) == dict(stats_b)


def test_run_simulations_with_multiple_workers_matches_sequential_aggregate_shape():
    """Parallel execution (see ProcessPoolExecutor in run_simulations) must
    produce the same *shape* of aggregate result as sequential -- summed
    stats can't depend on which worker process happened to finish a given
    game, only on how many games were actually played."""
    stats = run_simulations(["greedy", "greedy", "pass"], num_simulations=4, think_time=0,
                             seed=1, progress=False, workers=2)
    assert stats["greedy"]["matches"] == 8
    assert stats["pass"]["matches"] == 4
    assert stats["greedy"]["wins"] + stats["pass"]["wins"] == 4


def test_ranked_rows_sorts_most_wins_first_then_best_average_rank():
    stats = {
        "underdog": {"matches": 10, "wins": 1, "rank_sum": 8.0},
        "champion": {"matches": 10, "wins": 7, "rank_sum": 2.0},
        "middling": {"matches": 10, "wins": 1, "rank_sum": 4.0},
    }
    names_in_order = [row[0] for row in ranked_rows(stats)]
    # champion (most wins) first; between the two 1-win bots, the one with
    # the better (lower) average rank comes next.
    assert names_in_order == ["champion", "middling", "underdog"]
