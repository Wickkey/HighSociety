import datetime
from unittest.mock import MagicMock, patch

import pytest

from highsociety.code.common import elo
from highsociety.code.common.db import game_history


@pytest.fixture(autouse=True)
def reset_schema_cache():
    """_schema_ready is a module-level cache (see ensure_schema's docstring
    for why) — reset it around every test so one test's mocked "schema is
    ready" state can't leak into the next."""
    game_history._schema_ready = False
    yield
    game_history._schema_ready = False


@pytest.fixture
def no_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)


@pytest.fixture
def database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/testdb")


def test_is_configured_reflects_the_env_var(no_database_url, monkeypatch):
    assert game_history.is_configured() is False
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    assert game_history.is_configured() is True


def test_ensure_schema_is_a_silent_no_op_without_a_database(no_database_url):
    game_history.ensure_schema()
    assert game_history._schema_ready is False


def test_record_finished_game_is_a_silent_no_op_without_a_database(no_database_url):
    # Must not raise even though every field below is nonsense -- the whole
    # point is that this never gets far enough to touch any of it.
    result = game_history.record_finished_game(
        room_code="ABCDE", seats=2, bot_mix=["greedy"],
        started_at=datetime.datetime.now(datetime.timezone.utc),
        finished_at=datetime.datetime.now(datetime.timezone.utc),
        participants=[{"is_bot": True, "username": None, "name": "Marble",
                       "points": 5, "money_left": 10, "is_winner": True, "eliminated": False}],
    )
    assert result == {}


def test_record_finished_game_async_spawns_nothing_without_a_database(no_database_url):
    with patch("threading.Thread") as mock_thread:
        game_history.record_finished_game_async(room_code="ABCDE")
        mock_thread.assert_not_called()


def test_record_finished_game_async_calls_on_complete_with_empty_dict_without_a_database(no_database_url):
    """The post-game Elo reveal (web_server.py) relies on on_complete
    always firing, even when there's nothing to compute -- otherwise a
    local dev setup with no DATABASE_URL would leave the reveal polling
    forever instead of resolving immediately to "nothing to show"."""
    seen = []
    game_history.record_finished_game_async(room_code="ABCDE", on_complete=seen.append)
    assert seen == [{}]


def test_record_finished_game_async_calls_on_complete_with_the_real_result(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    seen = []
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game_async(
            on_complete=seen.append,
            room_code="ABCDE", seats=2, bot_mix=[],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=[],
        )
        import time
        time.sleep(0.2)  # give the background thread a moment to actually run
    assert seen == [{}]  # no participants -- nothing rated, but on_complete still fires


def _fake_connection():
    """A MagicMock standing in for a psycopg2 connection whose cursor's
    fetchone() always returns a fresh, incrementing id -- enough to exercise
    ensure_schema/record_finished_game's real SQL-issuing logic without a
    real Postgres server. The second/third columns (None, 1000) stand in
    for google_id/elo -- harmless for callers that only read
    fetchone()[0] (the games-table insert), and match "guest, no linked
    account, default rating" as the default for _upsert_player's
    RETURNING id, google_id, elo."""
    conn = MagicMock()
    cursor = MagicMock()
    ids = iter(range(1, 1000))
    cursor.fetchone.side_effect = lambda: (next(ids), None, 1000)
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.__enter__.return_value = conn
    return conn, cursor


def test_ensure_schema_runs_every_ddl_statement_once(database_url):
    conn, cursor = _fake_connection()
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.ensure_schema()
    assert cursor.execute.call_count == len(game_history._SCHEMA_STATEMENTS)
    assert game_history._schema_ready is True
    conn.close.assert_called_once()


def test_ensure_schema_failure_is_caught_and_leaves_schema_not_ready(database_url):
    with patch.object(game_history, "_connect", side_effect=RuntimeError("unreachable")):
        game_history.ensure_schema()  # must not raise
    assert game_history._schema_ready is False


def test_record_finished_game_writes_one_row_per_human_and_bot_participant(database_url):
    game_history._schema_ready = True  # skip ensure_schema's own DDL round-trip for this test
    conn, cursor = _fake_connection()
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice",
         "points": 12, "money_left": 3, "is_winner": True, "eliminated": False},
        {"is_bot": True, "username": None, "name": "Marble",
         "points": 5, "money_left": 0, "is_winner": False, "eliminated": True},
    ]
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=2, bot_mix=["greedy"],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants,
        )

    queries = [call.args[0] for call in cursor.execute.call_args_list]
    assert sum("INSERT INTO games" in q for q in queries) == 1
    assert sum("INSERT INTO players" in q for q in queries) == 1  # only alice, not the bot
    assert sum("INSERT INTO player_games" in q for q in queries) == 2  # one per participant

    # The bot's row must carry a bot_name with no player_id (see the
    # player_xor_bot CHECK constraint in the schema).
    player_games_calls = [call for call in cursor.execute.call_args_list
                           if "INSERT INTO player_games" in call.args[0]]
    bot_row_params = player_games_calls[1].args[1]
    game_id, player_id, bot_name = bot_row_params[0], bot_row_params[1], bot_row_params[2]
    assert player_id is None
    assert bot_name == "Marble"
    conn.close.assert_called_once()


def test_record_finished_game_gives_a_bot_with_known_difficulty_a_real_player_id(database_url):
    """BACKEND_REWORK.MD: a bot participant whose difficulty is known
    resolves to the shared per-difficulty player_id from the `bots` table
    (alongside its existing bot_name flavor label), instead of staying
    player_id-less the way a bot with no difficulty info still does (see
    test_record_finished_game_writes_one_row_per_human_and_bot_participant,
    unchanged)."""
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = iter([
        (1,),               # INSERT INTO games ... RETURNING id
        (10, None, 1000),   # _upsert_player(alice) -- guest, no google_id
        (55, 1000),         # SELECT b.player_id, p.elo FROM bots b JOIN players p ... WHERE difficulty = 'hard'
    ])
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice",
         "points": 12, "money_left": 3, "is_winner": True, "eliminated": False},
        {"is_bot": True, "username": None, "name": "Wagon bot", "game_username": "wagonbot",
         "points": 5, "money_left": 0, "is_winner": False, "eliminated": True, "difficulty": "hard"},
    ]
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=2, bot_mix=["hard"],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants,
        )

    queries_and_params = [(call.args[0], call.args[1]) for call in cursor.execute.call_args_list]
    bot_lookups = [(q, p) for q, p in queries_and_params if "FROM bots" in q]
    assert bot_lookups == [
        ("SELECT b.player_id, p.elo FROM bots b JOIN players p ON p.id = b.player_id WHERE b.difficulty = %s",
         ("hard",)),
    ]

    player_games_inserts = [p for q, p in queries_and_params if "INSERT INTO player_games" in q]
    bot_row = player_games_inserts[1]
    assert bot_row[1] == 55  # player_id -- the shared "hard" identity, not None
    assert bot_row[2] == "Wagon bot"  # bot_name -- the flavor label, untouched

    game_results_inserts = [p for q, p in queries_and_params if "INSERT INTO game_results" in q]
    assert any(p[1] == 55 for p in game_results_inserts)  # bot gets a game_results row too now


def test_record_finished_game_resolves_host_username_to_host_player_id(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = iter([
        (99, None, 1000),  # _upsert_player(host) -- resolved before the games INSERT
        (1,),               # INSERT INTO games ... RETURNING id
    ])
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=2, bot_mix=[],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=[], host_username="hosty",
        )
    games_insert = next(call for call in cursor.execute.call_args_list if "INSERT INTO games" in call.args[0])
    # (room_code, seats, started_at, finished_at, host_player_id, time_control, is_finished_successfully)
    assert games_insert.args[1][4] == 99


def test_record_finished_game_can_mark_a_crashed_game_as_unsuccessful(database_url):
    """See web_server.py's run_game crash path -- a game that never
    reached final_standings still gets a row, just one that says so."""
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = iter([(1,)])  # just the games insert -- no participants
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=2, bot_mix=[],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=[], is_finished_successfully=False,
        )
    games_insert = next(call for call in cursor.execute.call_args_list if "INSERT INTO games" in call.args[0])
    assert games_insert.args[1][-1] is False


def test_record_finished_game_increments_games_played_and_won_for_every_human(database_url):
    """Unlike achievements/elo, these aren't gated to a google_id -- every
    human participant counts, matching get_player_profile_stats' own
    (already ungated) COUNT query."""
    game_history._schema_ready = True
    conn, cursor = _fake_connection()  # default fixture: google_id=None for every upsert
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice",
         "points": 12, "money_left": 3, "is_winner": True, "eliminated": False},
        {"is_bot": False, "username": "bob", "name": "Bob",
         "points": 5, "money_left": 0, "is_winner": False, "eliminated": False},
    ]
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=2, bot_mix=[],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants,
        )
    updates = [call.args[1] for call in cursor.execute.call_args_list
               if call.args[0].startswith("UPDATE players SET games_played")]
    assert len(updates) == 2  # one per human, none for a bot
    assert sorted(params[0] for params in updates) == [0, 1]  # one winner (+1), one loser (+0)


def test_record_finished_game_placement_reflects_is_winner_not_raw_points(database_url):
    """The money-elimination rule (gameplay.py's final_standings) means a
    participant can have the *highest* points and still lose outright --
    confirmed live. placement must follow is_winner/eliminated, not a
    naive points sort (which would have ranked the eliminated bot below
    first despite its higher score)."""
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    participants = [
        {"is_bot": True, "username": None, "name": "Juno bot",
         "points": 164, "money_left": 1, "is_winner": False, "eliminated": True},
        {"is_bot": False, "username": "alice", "name": "Alice",
         "points": -3, "money_left": 106, "is_winner": True, "eliminated": False},
    ]
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=2, bot_mix=["easy"],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants,
        )
    game_results_inserts = [call.args[1] for call in cursor.execute.call_args_list
                             if "INSERT INTO game_results" in call.args[0]]
    placement_by_user = {p[1]: p[5] for p in game_results_inserts}
    alice_id = next(p[1] for p in game_results_inserts if p[4] == -3)
    assert placement_by_user[alice_id] == 1  # the actual winner, despite fewer points


def test_record_finished_game_writes_seat_columns_and_winner_id(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice",
         "points": 12, "money_left": 3, "is_winner": True, "eliminated": False},
        {"is_bot": True, "username": None, "name": "Marble",
         "points": 5, "money_left": 0, "is_winner": False, "eliminated": True},  # no difficulty known
    ]
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=2, bot_mix=["greedy"],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants,
        )
    seat_update = next(call for call in cursor.execute.call_args_list
                        if call.args[0].strip().startswith("UPDATE games SET winner_id"))
    winner_id, p1, p2, p3, p4, p5, game_id = seat_update.args[1]
    assert p1 is not None and winner_id == p1  # alice, seat 1, also the winner
    assert p2 is None  # the bot -- no difficulty info, so no resolvable player_id
    assert p3 is None and p4 is None and p5 is None  # only 2 seats were ever filled


def test_record_finished_game_writes_a_ratings_row_per_rated_participant(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = iter([
        (1,),                    # INSERT INTO games ... RETURNING id
        (10, "g-alice", 1000),   # _upsert_player(alice) RETURNING id, google_id, elo
        (1,),                    # win-count SELECT for alice (her 1st win)
        (11, "g-bob", 1000),     # _upsert_player(bob) RETURNING id, google_id, elo
    ])
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice",
         "points": 15, "money_left": 5, "is_winner": True, "eliminated": False},
        {"is_bot": False, "username": "bob", "name": "Bob",
         "points": 5, "money_left": 0, "is_winner": False, "eliminated": False},
    ]
    with patch.object(game_history, "_connect", return_value=conn):
        result = game_history.record_finished_game(
            room_code="ABCDE", seats=2, bot_mix=[],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants,
        )
    ratings_inserts = [call.args[1] for call in cursor.execute.call_args_list
                       if "INSERT INTO ratings" in call.args[0]]
    assert len(ratings_inserts) == 2
    by_player = {row[0]: row for row in ratings_inserts}  # user_id -> (user_id, game_id, old, new, change)
    assert by_player[10][2] == 1000  # old_rating
    assert by_player[10][3] == by_player[10][2] + by_player[10][4]  # new_rating == old + change
    assert by_player[10][4] > 0  # alice (winner) gains
    assert by_player[11][4] < 0  # bob (loser) loses

    # The return value is what web_server.py's post-game Elo reveal
    # actually reads -- keyed by rating_key (a human's real username).
    assert result["alice"] == {"old_rating": 1000, "new_rating": by_player[10][3], "rating_change": by_player[10][4]}
    assert result["bob"] == {"old_rating": 1000, "new_rating": by_player[11][3], "rating_change": by_player[11][4]}


def test_record_finished_game_averages_elo_deltas_for_same_difficulty_bot_seats(database_url):
    """Real bug, confirmed live against production (game 469): two bot
    seats sharing one player_id (same difficulty) each got their own
    independently-computed delta applied, summing onto the shared
    identity as if it were two separate players -- inflating its rating
    swing, and writing two ratings rows that both claimed the same stale
    "before" value despite the first update having already landed.
    Deltas are now grouped by player_id and averaged: one identity
    occupying N seats gets exactly one rating change, worth 1/N of the
    naive sum, backed by exactly one ratings row."""
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = iter([
        (1,),                    # INSERT INTO games ... RETURNING id
        (10, "g-alice", 1000),   # _upsert_player(alice)
        (55, 1000),               # bots lookup for medium bot A
        (55, 1000),               # bots lookup for medium bot B -- same shared player_id
    ])
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice", "game_username": "alice",
         "points": 8, "money_left": 62, "is_winner": False, "eliminated": False},
        {"is_bot": True, "username": None, "name": "Medium bot A", "game_username": "medium bot a",
         "points": 40, "money_left": 66, "is_winner": True, "eliminated": False, "difficulty": "medium"},
        {"is_bot": True, "username": None, "name": "Medium bot B", "game_username": "medium bot b",
         "points": 18, "money_left": 27, "is_winner": False, "eliminated": True, "difficulty": "medium"},
    ]
    with patch.object(game_history, "_connect", return_value=conn):
        result = game_history.record_finished_game(
            room_code="ABCDE", seats=3, bot_mix=["medium", "medium"],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants,
        )

    # Ground truth: what each seat's delta would independently be, exactly
    # matching the rated_standings this call built internally.
    expected_seat_deltas = elo.compute_elo_deltas([
        {"username": "alice", "points": 8, "rating": 1000},
        {"username": "medium bot a", "points": 40, "rating": 1000},
        {"username": "medium bot b", "points": 18, "rating": 1000},
    ])
    expected_bot_delta = round(
        (expected_seat_deltas["medium bot a"] + expected_seat_deltas["medium bot b"]) / 2
    )

    ratings_inserts = [call.args[1] for call in cursor.execute.call_args_list
                       if "INSERT INTO ratings" in call.args[0]]
    by_player = {row[0]: row for row in ratings_inserts}
    assert set(by_player) == {10, 55}  # exactly one ratings row per real identity, not per seat
    assert by_player[55][4] == expected_bot_delta  # averaged, not summed
    assert by_player[10][4] == expected_seat_deltas["alice"]  # a solo seat is unaffected

    elo_updates = [call.args[1] for call in cursor.execute.call_args_list
                   if call.args[0].startswith("UPDATE players SET elo")]
    bot_elo_update = next(p for p in elo_updates if p[1] == 55)
    assert bot_elo_update[0] == expected_bot_delta  # applied exactly once, not twice

    # Both bot seats' rating_keys resolve to the same, correct combined
    # change in the return value too (each one only matters if a future
    # caller ever looks a bot seat up by name -- today only alice's own
    # entry is actually read, by web_server.py's Elo reveal).
    assert result["alice"]["rating_change"] == expected_seat_deltas["alice"]
    assert result["medium bot a"] == result["medium bot b"] == {
        "old_rating": by_player[55][2], "new_rating": by_player[55][3], "rating_change": expected_bot_delta,
    }


def test_record_finished_game_rates_bot_games_but_never_gives_bots_achievements(database_url):
    """Confirmed with the user: bot games should move a human's Elo too --
    the common case is solo-vs-bots, so gating rating to human-vs-human
    alone left Elo essentially inert in production (see elo.py's own
    docstring). Bots get a real, evolving elo of their own (never shown
    to players anywhere), but must never unlock an achievement, even when
    they win."""
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = iter([
        (1,),                    # INSERT INTO games ... RETURNING id
        (10, "g-alice", 1000),   # _upsert_player(alice) RETURNING id, google_id, elo
        (99, 1000),              # SELECT b.player_id, p.elo FROM bots ... WHERE difficulty = 'hard'
    ])
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice", "game_username": "alice",
         "points": 5, "money_left": 0, "is_winner": False, "eliminated": False},
        {"is_bot": True, "username": None, "name": "Wagon bot", "game_username": "wagonbot",
         "points": 15, "money_left": 5, "is_winner": True, "eliminated": False, "difficulty": "hard"},
    ]
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=2, bot_mix=["hard"],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants,
        )

    queries_and_params = [(call.args[0], call.args[1]) for call in cursor.execute.call_args_list]
    ratings_inserts = [p for q, p in queries_and_params if "INSERT INTO ratings" in q]
    assert len(ratings_inserts) == 2  # both alice AND the bot get rated
    by_player = {row[0]: row for row in ratings_inserts}  # user_id -> (user_id, game_id, old, new, change)
    assert by_player[10][4] < 0  # alice (lost to the bot) loses rating
    assert by_player[99][4] > 0  # the bot (won) gains rating

    elo_updates = [p for q, p in queries_and_params if q.startswith("UPDATE players SET elo")]
    assert {p[1] for p in elo_updates} == {10, 99}  # both players' elo actually updated

    assert not any("INSERT INTO player_achievements" in q for q, _ in queries_and_params)


def test_record_finished_game_does_not_rate_a_bot_with_unknown_difficulty(database_url):
    """A bot with no resolvable player_id (no difficulty given -- the
    legacy/non-web path) has nothing to attach a rating to; must not
    crash and must simply be excluded from elo.compute_elo_deltas."""
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice", "game_username": "alice",
         "points": 5, "money_left": 0, "is_winner": True, "eliminated": False},
        {"is_bot": True, "username": None, "name": "Marble", "game_username": "marble",
         "points": 0, "money_left": 0, "is_winner": False, "eliminated": True},
    ]
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=2, bot_mix=["greedy"],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants,
        )
    queries = [call.args[0] for call in cursor.execute.call_args_list]
    assert not any("INSERT INTO ratings" in q for q in queries)  # guest alice + unrated bot -- nobody to rate


def test_record_finished_game_populates_game_rounds_round_players_and_game_actions(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice", "game_username": "alice",
         "points": 10, "money_left": 5, "is_winner": True, "eliminated": False},
        {"is_bot": False, "username": "bob", "name": "Bob", "game_username": "bob",
         "points": 0, "money_left": 20, "is_winner": False, "eliminated": False},
    ]
    auction_rounds = [{
        "round_number": 1, "auction_type": "normal",
        "card": {"type": "Painting", "value": 5, "multiplier": 1, "is_green": False, "description": "x"},
        "events": [
            {"player": "alice", "action": "bid", "amount": 5, "cards": [5],
             "timestamp": "2026-01-01T00:00:00+00:00"},
            {"player": "bob", "action": "pass", "amount": None, "cards": None,
             "timestamp": "2026-01-01T00:00:01+00:00"},
        ],
        "recipient": "alice",
        "money_spent": {"alice": 5, "bob": 0},
        "cards_spent": {"alice": [5], "bob": []},
        "started_at": "2026-01-01T00:00:00+00:00",
        "ended_at": "2026-01-01T00:00:01+00:00",
        "starting_money": {"alice": 10, "bob": 20},
        "ending_money": {"alice": 5, "bob": 20},
    }]
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=2, bot_mix=[],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants, auction_rounds=auction_rounds,
        )

    queries_and_params = [(call.args[0], call.args[1]) for call in cursor.execute.call_args_list]

    card_lookup = [(q, p) for q, p in queries_and_params if "SELECT card_id FROM cards" in q]
    assert card_lookup == [("SELECT card_id FROM cards WHERE type = %s AND value = %s LIMIT 1", ("Painting", 5))]

    game_id, round_number, card_id, winner_id, winning_bid, started_at, ended_at = next(
        p for q, p in queries_and_params if "INSERT INTO game_rounds" in q)
    assert round_number == 1
    assert winning_bid == 5
    assert winner_id is not None  # alice's resolved player_id

    round_players_inserts = [p for q, p in queries_and_params if "INSERT INTO round_players" in q]
    assert len(round_players_inserts) == 2
    results = sorted(p[6] for p in round_players_inserts)  # (..., result) -- index 6
    assert results == ["lost", "won"]

    game_actions_inserts = [p for q, p in queries_and_params if "INSERT INTO game_actions" in q]
    assert len(game_actions_inserts) == 2
    action_types = sorted(p[2] for p in game_actions_inserts)  # (..., action_type, ...) -- index 2
    assert action_types == ["BID", "PASS"]


def test_record_finished_game_does_not_crash_when_two_same_difficulty_bots_share_a_round(database_url):
    """Real production bug, confirmed live: two bot seats of the same
    difficulty resolve to the SAME shared player_id (see the bots table's
    own design), so round_players' (round_id, player_id) primary key
    collided the moment a round's money data included both seats --
    crashing this whole function and rolling back the entire transaction
    (games/player_games included, since it's all one commit) for any real
    game with 2+ same-difficulty bots. The ON CONFLICT DO NOTHING fix
    means both INSERT attempts still happen (this test asserts that), but
    a real database silently keeps only the first instead of raising."""
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = iter([
        (1,),              # INSERT INTO games ... RETURNING id
        (10, None, 1000),  # _upsert_player(alice)
        (55, 1000),        # bots lookup for "medium bot A"
        (55, 1000),        # bots lookup for "medium bot B" -- same player_id as A
        (99,),             # SELECT card_id FROM cards ...
        (777,),            # INSERT INTO game_rounds ... RETURNING id
    ])
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice", "game_username": "alice",
         "points": 12, "money_left": 3, "is_winner": True, "eliminated": False},
        {"is_bot": True, "username": None, "name": "Medium bot A", "game_username": "medium bot a",
         "points": 5, "money_left": 0, "is_winner": False, "eliminated": True, "difficulty": "medium"},
        {"is_bot": True, "username": None, "name": "Medium bot B", "game_username": "medium bot b",
         "points": 5, "money_left": 0, "is_winner": False, "eliminated": True, "difficulty": "medium"},
    ]
    auction_rounds = [{
        "round_number": 1, "auction_type": "normal",
        "card": {"type": "Painting", "value": 5, "multiplier": 1, "is_green": False, "description": "x"},
        "events": [], "recipient": "alice",
        "money_spent": {"alice": 5, "medium bot a": 0, "medium bot b": 0},
        "cards_spent": {"alice": [5], "medium bot a": [], "medium bot b": []},
        "started_at": "2026-01-01T00:00:00+00:00", "ended_at": "2026-01-01T00:00:01+00:00",
        "starting_money": {"alice": 10, "medium bot a": 10, "medium bot b": 10},
        "ending_money": {"alice": 5, "medium bot a": 10, "medium bot b": 10},
    }]
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(  # must not raise
            room_code="ABCDE", seats=3, bot_mix=["medium", "medium"],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants, auction_rounds=auction_rounds,
        )

    round_players_calls = [call for call in cursor.execute.call_args_list
                            if "INSERT INTO round_players" in call.args[0]]
    assert len(round_players_calls) == 3  # alice + both bot seats, despite the shared player_id
    for call in round_players_calls:
        assert "ON CONFLICT (round_id, player_id) DO NOTHING" in call.args[0]
    bot_player_ids = {call.args[1][2] for call in round_players_calls} - {10}  # exclude alice's player_id
    assert bot_player_ids == {55}  # both bot seats resolved to the same shared player_id


def test_record_finished_game_leaves_winning_bid_null_for_a_disgrace_card(database_url):
    """Per the original request: a disgrace card's "recipient" passed
    first and got stuck with it -- their money_spent is always 0 by
    AuctionRecord's own design, which isn't a real winning bid."""
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice", "game_username": "alice",
         "points": 0, "money_left": 20, "is_winner": True, "eliminated": False},
    ]
    auction_rounds = [{
        "round_number": 1, "auction_type": "disgrace",
        "card": {"type": "FauxPas", "value": 0, "multiplier": 1, "is_green": False, "description": "x"},
        "events": [], "recipient": "alice",
        "money_spent": {"alice": 0}, "cards_spent": {"alice": []},
        "started_at": "2026-01-01T00:00:00+00:00", "ended_at": "2026-01-01T00:00:01+00:00",
        "starting_money": {"alice": 20}, "ending_money": {"alice": 20},
    }]
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=1, bot_mix=[],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants, auction_rounds=auction_rounds,
        )
    game_rounds_insert = next(call.args[1] for call in cursor.execute.call_args_list
                               if "INSERT INTO game_rounds" in call.args[0])
    winning_bid = game_rounds_insert[4]
    assert winning_bid is None


def test_record_finished_game_skips_round_players_and_actions_for_an_unresolvable_bot(database_url):
    """A bot with no known difficulty has no resolvable player_id (see the
    existing legacy-bot test above) -- its round/action data is simply
    skipped, not written with a dangling player_id."""
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    participants = [
        {"is_bot": True, "username": None, "name": "Marble", "game_username": "marble",
         "points": 5, "money_left": 0, "is_winner": False, "eliminated": True},
    ]
    auction_rounds = [{
        "round_number": 1, "auction_type": "normal",
        "card": {"type": "Painting", "value": 3, "multiplier": 1, "is_green": False, "description": "x"},
        "events": [{"player": "marble", "action": "pass", "amount": None, "cards": None,
                    "timestamp": "2026-01-01T00:00:00+00:00"}],
        "recipient": None,
        "money_spent": {"marble": 0},
        "cards_spent": {"marble": []},
        "started_at": "2026-01-01T00:00:00+00:00", "ended_at": "2026-01-01T00:00:01+00:00",
        "starting_money": {"marble": 10}, "ending_money": {"marble": 10},
    }]
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=1, bot_mix=["greedy"],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants, auction_rounds=auction_rounds,
        )

    queries = [call.args[0] for call in cursor.execute.call_args_list]
    assert sum("INSERT INTO game_rounds" in q for q in queries) == 1  # the round itself is still recorded
    assert not any("INSERT INTO round_players" in q for q in queries)
    assert not any("INSERT INTO game_actions" in q for q in queries)


def test_record_finished_game_failure_is_caught_not_raised(database_url):
    game_history._schema_ready = True
    with patch.object(game_history, "_connect", side_effect=RuntimeError("unreachable")):
        game_history.record_finished_game(
            room_code="ABCDE", seats=2, bot_mix=[],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=[],
        )  # must not raise


def test_record_finished_game_async_runs_in_a_background_thread(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game_async(
            room_code="ABCDE", seats=2, bot_mix=[],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=[],
        )
        # Give the background thread a moment to actually run.
        import time
        time.sleep(0.2)
    assert any("INSERT INTO games" in call.args[0] for call in cursor.execute.call_args_list)


# ------------------------------------------------------ Google auth lookups --

def test_find_player_by_google_id_is_none_without_a_database(no_database_url):
    assert game_history.find_player_by_google_id("g-123") is None


def test_find_player_by_google_id_returns_the_row_when_found(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = None
    cursor.fetchone.return_value = ("alice", "Alice")
    with patch.object(game_history, "_connect", return_value=conn):
        result = game_history.find_player_by_google_id("g-123")
    assert result == {"username": "alice", "display_name": "Alice"}
    query, params = cursor.execute.call_args.args
    assert "google_id" in query
    assert params == ("g-123",)
    conn.close.assert_called_once()


def test_find_player_by_google_id_returns_none_when_not_found(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = None
    cursor.fetchone.return_value = None
    with patch.object(game_history, "_connect", return_value=conn):
        assert game_history.find_player_by_google_id("g-nope") is None


def test_find_player_by_google_id_failure_is_caught_not_raised(database_url):
    game_history._schema_ready = True
    with patch.object(game_history, "_connect", side_effect=RuntimeError("unreachable")):
        assert game_history.find_player_by_google_id("g-123") is None  # must not raise


def test_username_is_taken_fails_safe_toward_taken_without_a_database(no_database_url):
    assert game_history.username_is_taken("alice") is True


def test_username_is_taken_reflects_whether_a_row_exists(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()

    cursor.fetchone.side_effect = None
    cursor.fetchone.return_value = (1,)
    with patch.object(game_history, "_connect", return_value=conn):
        assert game_history.username_is_taken("alice") is True

    cursor.fetchone.return_value = None
    with patch.object(game_history, "_connect", return_value=conn):
        assert game_history.username_is_taken("bob") is False


def test_username_is_taken_fails_safe_toward_taken_on_a_db_error(database_url):
    game_history._schema_ready = True
    with patch.object(game_history, "_connect", side_effect=RuntimeError("unreachable")):
        assert game_history.username_is_taken("alice") is True


def test_get_player_elo_defaults_to_1000_without_a_database(no_database_url):
    assert game_history.get_player_elo("alice") == 1000


def test_get_player_elo_returns_the_stored_value(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = None
    cursor.fetchone.return_value = (1234,)
    with patch.object(game_history, "_connect", return_value=conn):
        assert game_history.get_player_elo("alice") == 1234


def test_get_player_elo_defaults_to_1000_when_the_player_has_no_row(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = None
    cursor.fetchone.return_value = None
    with patch.object(game_history, "_connect", return_value=conn):
        assert game_history.get_player_elo("nobody") == 1000


def test_get_player_elo_defaults_to_1000_on_a_db_error(database_url):
    game_history._schema_ready = True
    with patch.object(game_history, "_connect", side_effect=RuntimeError("unreachable")):
        assert game_history.get_player_elo("alice") == 1000


def test_create_google_player_is_a_no_op_without_a_database(no_database_url):
    assert game_history.create_google_player("g-123", "a@example.com", "alice", "Alice") is False


def test_create_google_player_inserts_a_row_and_returns_true(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    with patch.object(game_history, "_connect", return_value=conn):
        result = game_history.create_google_player("g-123", "a@example.com", "alice", "Alice")
    assert result is True
    query, params = cursor.execute.call_args.args
    assert "INSERT INTO players" in query
    assert params == ("alice", "Alice", "g-123", "a@example.com")
    conn.close.assert_called_once()


def test_create_google_player_returns_false_instead_of_raising_on_a_uniqueness_race(database_url):
    """
    A second tab claiming the same username (or the same Google account
    signing in twice concurrently) hits the table's own UNIQUE
    constraints even though username_is_taken's own earlier check passed
    -- this must resolve to a clean False the caller can turn into a
    re-prompt, not an unhandled exception that would 500 the request.
    """
    game_history._schema_ready = True
    with patch.object(game_history, "_connect", side_effect=RuntimeError("duplicate key value")):
        result = game_history.create_google_player("g-123", "a@example.com", "alice", "Alice")
    assert result is False


# ---------------------------------------------------------------- guests --

def test_create_guest_player_is_a_no_op_without_a_database(no_database_url):
    assert game_history.create_guest_player("alice") is False


def test_create_guest_player_inserts_a_row_with_no_google_id_or_email(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    with patch.object(game_history, "_connect", return_value=conn):
        result = game_history.create_guest_player("CrimsonNaruto482")
    assert result is True
    query, params = cursor.execute.call_args.args
    assert "INSERT INTO players" in query
    assert params == ("CrimsonNaruto482", "CrimsonNaruto482")
    conn.close.assert_called_once()


def test_create_guest_player_returns_false_instead_of_raising_on_a_uniqueness_race(database_url):
    game_history._schema_ready = True
    with patch.object(game_history, "_connect", side_effect=RuntimeError("duplicate key value")):
        result = game_history.create_guest_player("alice")
    assert result is False


def test_rename_player_is_a_no_op_without_a_database(no_database_url):
    assert game_history.rename_player("alice", "bob") is False


def test_rename_player_refuses_when_the_new_username_is_taken(database_url):
    game_history._schema_ready = True
    with patch.object(game_history, "username_is_taken", return_value=True):
        assert game_history.rename_player("alice", "bob") is False


def test_rename_player_updates_the_existing_row(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.rowcount = 1
    with patch.object(game_history, "username_is_taken", return_value=False), \
            patch.object(game_history, "_connect", return_value=conn):
        result = game_history.rename_player("alice", "bob")
    assert result is True
    query, params = cursor.execute.call_args_list[0].args
    assert "UPDATE players" in query
    assert params == ("bob", "bob", "alice")
    conn.close.assert_called_once()


def test_rename_player_falls_back_to_inserting_when_the_old_username_never_existed(database_url):
    """
    A profile that predates this reservation system (or was created while
    the database was unconfigured) has no players row to UPDATE -- the
    rename should still succeed by just reserving new_username fresh
    rather than failing outright.
    """
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.rowcount = 0
    with patch.object(game_history, "username_is_taken", return_value=False), \
            patch.object(game_history, "_connect", return_value=conn):
        result = game_history.rename_player("never-reserved", "bob")
    assert result is True
    queries = [call.args[0] for call in cursor.execute.call_args_list]
    assert sum("UPDATE players" in q for q in queries) == 1
    assert sum("INSERT INTO players" in q for q in queries) == 1


def test_rename_player_returns_false_instead_of_raising_on_a_uniqueness_race(database_url):
    game_history._schema_ready = True
    with patch.object(game_history, "username_is_taken", return_value=False), \
            patch.object(game_history, "_connect", side_effect=RuntimeError("duplicate key value")):
        result = game_history.rename_player("alice", "bob")
    assert result is False


# ------------------------------------------------- achievements + elo --

def test_record_finished_game_writes_achievements_and_elo_for_linked_participants(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = iter([
        (1,),                    # INSERT INTO games ... RETURNING id
        (10, "g-alice", 1000),   # _upsert_player(alice) RETURNING id, google_id, elo
        (1,),                    # win-count SELECT for alice (this is her 1st win)
        (11, "g-bob", 1000),     # _upsert_player(bob) RETURNING id, google_id, elo
    ])
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice",
         "points": 15, "money_left": 5, "is_winner": True, "eliminated": False},
        {"is_bot": False, "username": "bob", "name": "Bob",
         "points": 5, "money_left": 0, "is_winner": False, "eliminated": False},
    ]
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=2, bot_mix=[],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants,
            achievement_unlocks={"alice": {"sniper"}},
        )

    queries_and_params = [(call.args[0], call.args[1]) for call in cursor.execute.call_args_list]
    achievement_inserts = [p for q, p in queries_and_params if "INSERT INTO player_achievements" in q]
    # first_win (win count 1) + the per-game "sniper" unlock passed in.
    assert (10, "first_win") in achievement_inserts
    assert (10, "sniper") in achievement_inserts
    assert not any(pid == 11 for pid, _ in achievement_inserts)  # bob didn't win, earned nothing

    elo_updates = {params[1]: params[0] for q, params in queries_and_params if q.startswith("UPDATE players SET elo")}
    assert elo_updates[10] > 0   # alice (winner) gains
    assert elo_updates[11] < 0   # bob (loser) loses


def test_record_finished_game_skips_achievements_and_elo_for_a_guest(database_url):
    """The default _fake_connection() fixture returns google_id=None --
    matches a guest, who should get no player_achievements/elo writes at
    all even though they won."""
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    participants = [
        {"is_bot": False, "username": "guest123", "name": "guest123",
         "points": 10, "money_left": 5, "is_winner": True, "eliminated": False},
    ]
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=1, bot_mix=[],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants,
            achievement_unlocks={"guest123": {"sniper"}},
        )
    queries = [call.args[0] for call in cursor.execute.call_args_list]
    assert not any("INSERT INTO player_achievements" in q for q in queries)
    assert not any(q.startswith("UPDATE players SET elo") for q in queries)


def test_get_player_achievements_is_empty_without_a_database(no_database_url):
    assert game_history.get_player_achievements("alice") == []


def test_get_player_achievements_returns_the_unlocked_ids(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchall = MagicMock(return_value=[("first_win",), ("sniper",)])
    with patch.object(game_history, "_connect", return_value=conn):
        result = game_history.get_player_achievements("alice")
    assert result == ["first_win", "sniper"]


def test_get_player_achievements_failure_is_caught_not_raised(database_url):
    game_history._schema_ready = True
    with patch.object(game_history, "_connect", side_effect=RuntimeError("unreachable")):
        assert game_history.get_player_achievements("alice") == []


def test_get_player_profile_stats_is_none_without_a_database(no_database_url):
    assert game_history.get_player_profile_stats("alice") is None


def test_get_player_profile_stats_returns_none_for_an_unknown_username(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = None
    cursor.fetchone.return_value = None
    with patch.object(game_history, "_connect", return_value=conn):
        assert game_history.get_player_profile_stats("nobody") is None


def test_get_player_profile_stats_computes_win_rate(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = iter([
        (1, 1000),         # (player id, elo)
        (4, 3),            # (games_played, wins)
        (2.5, 10.0, 8.0),  # (avg_placement, avg_points, avg_money_remaining)
    ])
    with patch.object(game_history, "_connect", return_value=conn):
        result = game_history.get_player_profile_stats("alice")
    assert result == {
        "games_played": 4, "wins": 3, "win_rate": 0.75, "elo": 1000,
        "avg_placement": 2.5, "avg_points": 10.0, "avg_money_remaining": 8.0,
    }


def test_get_player_profile_stats_zero_games_has_zero_win_rate_not_a_division_error(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = iter([(1, 1000), (0, 0), (None, None, None)])
    with patch.object(game_history, "_connect", return_value=conn):
        result = game_history.get_player_profile_stats("alice")
    assert result == {
        "games_played": 0, "wins": 0, "win_rate": 0.0, "elo": 1000,
        "avg_placement": None, "avg_points": None, "avg_money_remaining": None,
    }


def test_get_player_profile_stats_averages_are_none_when_no_game_results_rows_exist(database_url):
    """A player whose only games predate the game_results table (added
    after player_games) has real games_played/wins but no averages to
    report -- None, not a wrong 0."""
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = iter([(1, 1000), (5, 2), (None, None, None)])
    with patch.object(game_history, "_connect", return_value=conn):
        result = game_history.get_player_profile_stats("alice")
    assert result["games_played"] == 5
    assert result["avg_placement"] is None
    assert result["avg_points"] is None
    assert result["avg_money_remaining"] is None


def test_get_player_profile_stats_includes_elo_from_the_same_connection(database_url):
    """elo comes from the same players-row lookup as the id -- see this
    function's own docstring on why (avoids a second, separate DB
    connection/round-trip that a naive get_player_elo() call would add)."""
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = iter([(1, 1234), (2, 1), (1.0, 15.0, 5.0)])
    with patch.object(game_history, "_connect", return_value=conn):
        result = game_history.get_player_profile_stats("alice")
    assert result["elo"] == 1234
    # Only one connection ever opened for the whole call.
    assert conn.__enter__.call_count == 1


def test_get_player_profile_stats_failure_is_caught_not_raised(database_url):
    game_history._schema_ready = True
    with patch.object(game_history, "_connect", side_effect=RuntimeError("unreachable")):
        assert game_history.get_player_profile_stats("alice") is None


# --------------------------------------------------- game history / leaderboard --

def test_get_recent_games_is_empty_without_a_database(no_database_url):
    assert game_history.get_recent_games("alice") == []


def test_get_recent_games_returns_none_for_an_unknown_username(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = None
    cursor.fetchone.return_value = None
    with patch.object(game_history, "_connect", return_value=conn):
        assert game_history.get_recent_games("nobody") == []


def test_get_recent_games_groups_opponents_by_game(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = None
    cursor.fetchone.return_value = (1,)  # player id lookup
    finished_at = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    cursor.fetchall.side_effect = [
        [(100, finished_at, 1), (101, finished_at, 2)],  # the two games + this player's placement
        [  # every participant across both games, in one query
            (100, "alice", False, True),
            (100, "Marble", True, False),
            (101, "alice", False, False),
            (101, "bob", False, True),
        ],
    ]
    with patch.object(game_history, "_connect", return_value=conn):
        result = game_history.get_recent_games("alice")
    assert [g["game_id"] for g in result] == [100, 101]
    assert result[0]["placement"] == 1
    assert {o["name"] for o in result[0]["opponents"]} == {"alice", "Marble"}
    assert result[1]["opponents"] == [
        {"name": "alice", "is_bot": False, "is_winner": False},
        {"name": "bob", "is_bot": False, "is_winner": True},
    ]


def test_get_recent_games_failure_is_caught_not_raised(database_url):
    game_history._schema_ready = True
    with patch.object(game_history, "_connect", side_effect=RuntimeError("unreachable")):
        assert game_history.get_recent_games("alice") == []


def test_get_game_detail_returns_none_for_an_unknown_game(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = None
    cursor.fetchone.return_value = None
    with patch.object(game_history, "_connect", return_value=conn):
        assert game_history.get_game_detail(999) is None


def test_get_game_detail_returns_every_participant_ordered_by_placement(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    finished_at = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    cursor.fetchone.side_effect = None
    cursor.fetchone.return_value = (finished_at,)
    cursor.fetchall = MagicMock(return_value=[
        ("alice", False, 15, 5, True, False, 1),
        ("Marble", True, 5, 0, False, True, 2),
    ])
    with patch.object(game_history, "_connect", return_value=conn):
        result = game_history.get_game_detail(42)
    assert result["game_id"] == 42
    assert len(result["participants"]) == 2
    assert result["participants"][0] == {
        "name": "alice", "is_bot": False, "points": 15, "money_left": 5,
        "is_winner": True, "eliminated": False, "placement": 1,
    }


def test_get_game_detail_reads_placement_from_player_games_not_game_results(database_url):
    """Real bug, confirmed live: joining through game_results (PRIMARY KEY
    (game_id, user_id)) gave two same-difficulty bot seats -- which share
    one player_id -- the exact same placement, since the join can't tell
    the seats apart. player_games.placement is this table's own per-seat
    column, immune to that collapse."""
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = None
    cursor.fetchone.return_value = (datetime.datetime.now(datetime.timezone.utc),)
    cursor.fetchall = MagicMock(return_value=[])
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.get_game_detail(42)
    query = cursor.execute.call_args_list[-1].args[0]
    assert "game_results" not in query
    assert "pg.placement" in query


def test_record_finished_game_gives_distinct_placements_to_same_difficulty_bot_seats(database_url):
    """Real bug, confirmed live: two bot seats of the same difficulty
    share one player_id, but each seat still gets its own correct,
    distinct placement written to player_games -- game_results (keyed by
    (game_id, user_id)) is a separate, deliberately-deduplicated table
    and is not what get_game_detail reads placement from any more."""
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = iter([
        (1,),              # INSERT INTO games ... RETURNING id
        (10, None, 1000),  # _upsert_player(alice)
        (55, 1000),        # bots lookup for the winning medium bot
        (55, 1000),        # bots lookup for the eliminated medium bot -- same player_id
    ])
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice", "game_username": "alice",
         "points": 8, "money_left": 62, "is_winner": False, "eliminated": False},
        {"is_bot": True, "username": None, "name": "Milo bot", "game_username": "milo bot",
         "points": 40, "money_left": 66, "is_winner": True, "eliminated": False, "difficulty": "medium"},
        {"is_bot": True, "username": None, "name": "Ziggy bot", "game_username": "ziggy bot",
         "points": 18, "money_left": 27, "is_winner": False, "eliminated": True, "difficulty": "medium"},
    ]
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=3, bot_mix=["medium", "medium"],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants,
        )

    player_games_inserts = [call.args[1] for call in cursor.execute.call_args_list
                             if "INSERT INTO player_games" in call.args[0]]
    # (game_id, player_id, bot_name, points, money_left, is_winner, eliminated, placement)
    placements_by_name = {row[2] or "alice": row[7] for row in player_games_inserts}
    assert placements_by_name == {"alice": 2, "Milo bot": 1, "Ziggy bot": 3}


def test_get_leaderboard_excludes_guests_and_bots_by_query(database_url):
    """Doesn't fake a real WHERE-clause result (that's Postgres' job) --
    just confirms the query text actually filters both, and the shape of
    what comes back."""
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchall = MagicMock(return_value=[("alice", 1200, 10, 6)])
    with patch.object(game_history, "_connect", return_value=conn):
        result = game_history.get_leaderboard()
    query = cursor.execute.call_args.args[0]
    assert "google_id IS NOT NULL" in query
    assert "NOT IN (SELECT player_id FROM bots)" in query
    assert result == [{"username": "alice", "elo": 1200, "games_played": 10, "games_won": 6}]


def test_get_rating_history_is_empty_without_a_database(no_database_url):
    assert game_history.get_rating_history("alice") == []


def test_get_rating_history_returns_rows_oldest_first(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    t1 = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    t2 = datetime.datetime(2026, 1, 2, tzinfo=datetime.timezone.utc)
    cursor.fetchall = MagicMock(return_value=[(1000, 1016, t1), (1016, 1005, t2)])
    with patch.object(game_history, "_connect", return_value=conn):
        result = game_history.get_rating_history("alice")
    assert result == [
        {"old_rating": 1000, "new_rating": 1016, "created_at": t1.isoformat()},
        {"old_rating": 1016, "new_rating": 1005, "created_at": t2.isoformat()},
    ]
