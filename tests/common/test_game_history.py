import datetime
from unittest.mock import MagicMock, patch

import pytest

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
    game_history.record_finished_game(
        room_code="ABCDE", seats=2, bot_mix=["greedy"],
        started_at=datetime.datetime.now(datetime.timezone.utc),
        finished_at=datetime.datetime.now(datetime.timezone.utc),
        participants=[{"is_bot": True, "username": None, "name": "Marble",
                       "points": 5, "money_left": 10, "is_winner": True, "eliminated": False}],
    )


def test_record_finished_game_async_spawns_nothing_without_a_database(no_database_url):
    with patch("threading.Thread") as mock_thread:
        game_history.record_finished_game_async(room_code="ABCDE")
        mock_thread.assert_not_called()


def _fake_connection():
    """A MagicMock standing in for a psycopg2 connection whose cursor's
    fetchone() always returns a fresh, incrementing id -- enough to exercise
    ensure_schema/record_finished_game's real SQL-issuing logic without a
    real Postgres server."""
    conn = MagicMock()
    cursor = MagicMock()
    ids = iter(range(1, 1000))
    cursor.fetchone.side_effect = lambda: (next(ids),)
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
