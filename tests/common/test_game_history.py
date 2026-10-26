import datetime
import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from highsociety.code.common import elo
from highsociety.code.common.db import game_history
from fake_db import queries as _queries, rows as _rows, save_connection as _save_connection

# Captured before the autouse fixture below mocks it out for every test.
_REAL_RELEASE_CONNECTION = game_history._release_connection


@pytest.fixture(autouse=True)
def reset_schema_cache():
    """_schema_ready is a module-level cache (see ensure_schema's docstring
    for why) — reset it around every test so one test's mocked "schema is
    ready" state can't leak into the next."""
    game_history._schema_ready = False
    yield
    game_history._schema_ready = False


@pytest.fixture(autouse=True)
def reset_player_sessions():
    """_player_sessions (PlayerSession's own module-level cache) is process-
    wide by design (see its own docstring) -- exactly the same reason
    _schema_ready needs resetting around every test, so one test caching
    "alice" can't make a later, unrelated test's mocked cursor calls come
    out short (a cache hit skips a query the mock's fetchone.side_effect
    list still expects to be consumed in order)."""
    game_history._player_sessions = {}
    yield
    game_history._player_sessions = {}


@pytest.fixture(autouse=True)
def mock_release_connection(monkeypatch):
    """_connect() now borrows from a real connection pool (see game_
    history's own module docstring on why), and every call site's release
    counterpart -- _release_connection -- expects a real pooled
    connection object, not the bare MagicMock these tests hand _connect
    in its place. Neutralized by default so a tested function's own
    `finally` block doesn't try to build (and connect) a real
    ThreadedConnectionPool against this file's fake DATABASE_URL. Tests
    that specifically want to assert release happened take this fixture
    as a parameter and assert on the mock directly, in place of the old
    conn.close.assert_called_once()."""
    mock = MagicMock()
    monkeypatch.setattr(game_history, "_release_connection", mock)
    return mock


@pytest.fixture(autouse=True)
def reset_pool_slots(monkeypatch):
    """Tests that call the real _connect() keep the connection without
    ever releasing it (_release_connection is mocked above), so give each
    test a full set of slots rather than letting them leak across tests."""
    monkeypatch.setattr(game_history, "_pool_slots", threading.Semaphore(game_history._MAX_POOL_CONNECTIONS))


@pytest.fixture(autouse=True)
def reset_leaderboard_cache():
    """_leaderboard_cache is process-wide by design (see get_leaderboard's
    own docstring -- the whole point is one shared cache for every
    visitor) -- same reasoning as reset_player_sessions above: reset it
    around every test so one test's cached page can't make a later,
    differently-mocked test see stale cached rows instead of its own
    mocked cursor's data."""
    game_history._leaderboard_cache = {}
    yield
    game_history._leaderboard_cache = {}


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


def _fake_pool(getconn_side_effect):
    """A MagicMock standing in for the module's real ThreadedConnectionPool
    -- getconn_side_effect is a list consumed one item per _connect()
    attempt: an exception instance to simulate that attempt failing (either
    the pool itself couldn't produce a connection, or it handed back a
    connection whose own probe query then fails), or a MagicMock connection
    to simulate a good one. See _connect()'s own docstring for the real
    production incident (Supabase silently dropping idle pooled
    connections after several inactive days) this retry logic exists for."""
    pool = MagicMock()
    pool.getconn.side_effect = getconn_side_effect
    return pool


def _dead_connection():
    """A pooled connection that LOOKS fine (conn.closed is falsy, same as
    any real connection the network silently dropped while idle -- see
    _connect()'s own docstring on why that flag alone can't catch this)
    but fails the moment anything actually tries to use it."""
    conn = MagicMock()
    conn.closed = 0
    conn.cursor.return_value.__enter__.return_value.execute.side_effect = Exception("server closed the connection unexpectedly")
    return conn


def test_connect_discards_a_stale_pooled_connection_and_retries(monkeypatch):
    stale = _dead_connection()
    healthy, _cursor = _fake_connection()
    pool = _fake_pool([stale, healthy])
    monkeypatch.setattr(game_history, "_get_pool", lambda: pool)

    result = game_history._connect()

    assert result is healthy
    pool.putconn.assert_called_once_with(stale, close=True)  # discarded outright, never handed to anyone else


def test_connect_retries_when_the_pool_itself_cannot_produce_a_connection(monkeypatch):
    """Covers the pool.getconn() call raising directly (e.g. exhausted, or
    the database is genuinely unreachable when the pool tries to open a
    brand new connection to satisfy the request) -- distinct from getting
    a connection back that then fails its own probe query."""
    healthy, _cursor = _fake_connection()
    pool = _fake_pool([RuntimeError("connection pool exhausted"), healthy])
    monkeypatch.setattr(game_history, "_get_pool", lambda: pool)

    result = game_history._connect()

    assert result is healthy
    pool.putconn.assert_not_called()  # nothing to discard -- no connection was ever actually obtained on the failed attempt


def test_connect_gives_up_after_exhausting_every_retry_attempt(monkeypatch):
    """A genuine, sustained outage (not just some stale idle connections)
    must still fail -- and fail with the real underlying error, not hang
    retrying forever -- so every existing caller's own `except Exception`
    fail-safe behavior (empty leaderboard, etc.) still kicks in exactly
    as before this retry logic existed."""
    dead_connections = [_dead_connection() for _ in range(game_history._CONNECT_RETRY_ATTEMPTS)]
    pool = _fake_pool(list(dead_connections))
    monkeypatch.setattr(game_history, "_get_pool", lambda: pool)

    with pytest.raises(Exception, match="server closed the connection unexpectedly"):
        game_history._connect()

    assert pool.getconn.call_count == game_history._CONNECT_RETRY_ATTEMPTS
    assert pool.putconn.call_count == game_history._CONNECT_RETRY_ATTEMPTS


def test_connect_waits_for_a_free_connection_instead_of_failing(monkeypatch):
    """psycopg2's pool raises the instant every connection is lent out;
    a burst of requests must queue for one instead."""
    monkeypatch.setattr(game_history, "_MAX_POOL_CONNECTIONS", 2)
    monkeypatch.setattr(game_history, "_pool_slots", threading.Semaphore(2))
    monkeypatch.setattr(game_history, "_release_connection", _REAL_RELEASE_CONNECTION)
    pool = MagicMock()
    pool.getconn.side_effect = lambda: _fake_connection()[0]
    monkeypatch.setattr(game_history, "_get_pool", lambda: pool)

    first, second = game_history._connect(), game_history._connect()
    got_third = threading.Event()
    threading.Thread(target=lambda: (game_history._connect(), got_third.set()), daemon=True).start()
    assert not got_third.wait(0.2)  # both connections are out: the third waits rather than raising

    game_history._release_connection(first)
    assert got_third.wait(2)
    assert pool.getconn.call_count == 3


def test_connect_gives_its_slot_back_when_every_attempt_fails(monkeypatch):
    monkeypatch.setattr(game_history, "_pool_slots", threading.Semaphore(1))
    pool = _fake_pool([RuntimeError("down")] * game_history._CONNECT_RETRY_ATTEMPTS)
    monkeypatch.setattr(game_history, "_get_pool", lambda: pool)

    with pytest.raises(RuntimeError):
        game_history._connect()
    assert game_history._pool_slots.acquire(blocking=False)  # not leaked


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
    fetchone() always returns a fresh, incrementing id -- enough for
    ensure_schema and the simple single-query readers without a real
    Postgres server. record_finished_game's batched writes need
    _save_connection below instead."""
    conn = MagicMock()
    cursor = MagicMock()
    ids = iter(range(1, 1000))
    cursor.fetchone.side_effect = lambda: (next(ids), None, 1000)
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.__enter__.return_value = conn
    return conn, cursor


@pytest.fixture(autouse=True)
def reset_card_id_cache():
    game_history._card_id_cache = None
    yield
    game_history._card_id_cache = None


def test_ensure_schema_runs_every_ddl_statement_once(database_url, mock_release_connection):
    conn, cursor = _fake_connection()
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.ensure_schema()
    assert cursor.execute.call_count == len(game_history._SCHEMA_STATEMENTS)
    assert game_history._schema_ready is True
    mock_release_connection.assert_called_once_with(conn)


def test_ensure_schema_failure_is_caught_and_leaves_schema_not_ready(database_url):
    with patch.object(game_history, "_connect", side_effect=RuntimeError("unreachable")):
        game_history.ensure_schema()  # must not raise
    assert game_history._schema_ready is False


def test_record_finished_game_writes_one_row_per_human_and_bot_participant(database_url, mock_release_connection):
    game_history._schema_ready = True  # skip ensure_schema's own DDL round-trip for this test
    conn, cursor = _save_connection()
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

    assert len(_queries(cursor, "INSERT INTO games")) == 1
    assert [row[0] for row in _rows(cursor, "INSERT INTO players", 4)] == ["alice"]  # not the bot
    assert len(_queries(cursor, "INSERT INTO player_games")) == 1  # one batched statement...
    player_games = _rows(cursor, "INSERT INTO player_games", 9)
    assert len(player_games) == 2  # ...with one row per participant

    # The bot's row must carry a bot_name with no player_id (see the
    # player_xor_bot CHECK constraint in the schema).
    _game_id, player_id, bot_name = player_games[1][:3]
    assert player_id is None
    assert bot_name == "Marble"
    mock_release_connection.assert_called_once_with(conn)


def test_record_finished_game_gives_a_named_bot_its_own_player_id(database_url):
    """A roster bot ("Wagon bot", always Medium -- see common/bot_roster.py)
    resolves to its *own* player_id from the `bots` table, looked up by
    bot_key, alongside its existing bot_name label -- instead of staying
    player_id-less the way a bot with no roster identity still does (see
    test_record_finished_game_writes_one_row_per_human_and_bot_participant,
    unchanged)."""
    game_history._schema_ready = True
    conn, cursor = _save_connection(bots={"wagon": (55, 1000)})
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice",
         "points": 12, "money_left": 3, "is_winner": True, "eliminated": False},
        {"is_bot": True, "username": None, "name": "Wagon bot", "game_username": "wagon bot",
         "points": 5, "money_left": 0, "is_winner": False, "eliminated": True, "difficulty": "medium"},
    ]
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=2, bot_mix=["medium"],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants,
        )

    assert [call.args[1] for call in _queries(cursor, "FROM bots b JOIN")] == [(["wagon"],)]
    bot_row = _rows(cursor, "INSERT INTO player_games", 9)[1]
    assert bot_row[1] == 55  # player_id -- Wagon's own identity, not None
    assert bot_row[2] == "Wagon bot"  # bot_name -- the flavor label, untouched
    assert any(row[1] == 55 for row in _rows(cursor, "INSERT INTO game_results", 6))  # a game_results row too


def test_two_same_difficulty_named_bots_are_two_separate_identities(database_url):
    """The point of named bots: two Medium bots at one table resolve to two
    different player_ids, so their seats, rounds, actions and ratings are
    never merged."""
    game_history._schema_ready = True
    conn, cursor = _save_connection(bots={"milo": (61, 1000), "juno": (62, 1000)})
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice", "game_username": "alice",
         "points": 8, "money_left": 62, "is_winner": False, "eliminated": False},
        {"is_bot": True, "username": None, "name": "Milo bot", "game_username": "milo bot",
         "points": 40, "money_left": 66, "is_winner": True, "eliminated": False, "difficulty": "medium"},
        {"is_bot": True, "username": None, "name": "Juno bot", "game_username": "juno bot",
         "points": 18, "money_left": 27, "is_winner": False, "eliminated": True, "difficulty": "medium"},
    ]
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=3, bot_mix=["medium", "medium"],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants,
        )
    assert [call.args[1] for call in _queries(cursor, "FROM bots b JOIN")] == [(["juno", "milo"],)]  # one lookup
    seat_ids = {row[2]: row[1] for row in _rows(cursor, "INSERT INTO player_games", 9)}
    assert seat_ids == {None: 10, "Milo bot": 61, "Juno bot": 62}
    rated_ids = {row[0] for row in _rows(cursor, "INSERT INTO ratings", 5)}
    assert {61, 62} <= rated_ids  # one ratings row each, not one shared


@pytest.mark.parametrize("name, difficulty", [
    ("Ziggy bot", "hard"),    # a roster name, but Ziggy is always Easy
    ("Ziggy bot", None),      # a heuristic bot that drew a roster name for flavor
    ("Bot3", "medium"),       # overflow name, no roster identity
])
def test_a_bot_without_a_matching_roster_identity_gets_no_player_id(database_url, name, difficulty):
    """Never falls back to the legacy per-difficulty rows: those are frozen
    history, and a mismatched name must not borrow another bot's stats."""
    game_history._schema_ready = True
    conn, cursor = _save_connection()
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice", "game_username": "alice",
         "points": 8, "money_left": 62, "is_winner": True, "eliminated": False},
        {"is_bot": True, "username": None, "name": name, "game_username": name.lower(),
         "points": 4, "money_left": 2, "is_winner": False, "eliminated": False, "difficulty": difficulty},
    ]
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=2, bot_mix=["easy"],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants,
        )
    assert not _queries(cursor, "FROM bots b JOIN")
    bot_row = _rows(cursor, "INSERT INTO player_games", 9)[1]
    assert bot_row[1] is None and bot_row[2] == name


def test_schema_seeds_one_players_and_bots_row_per_roster_bot():
    from highsociety.code.common import bot_roster
    seed_players = next(st for st in game_history._SCHEMA_STATEMENTS
                        if "__bot_ziggy__" in st and st.startswith("INSERT INTO players"))
    seed_bots = next(st for st in game_history._SCHEMA_STATEMENTS
                     if "__bot_ziggy__" in st and st.startswith("INSERT INTO bots"))
    for b in bot_roster.BOT_ROSTER:
        assert f"('{bot_roster.roster_username(b)}', '{b.name}')" in seed_players
        assert f"('{bot_roster.roster_username(b)}', '{b.difficulty}', '{b.key}')" in seed_bots



def test_record_finished_game_resolves_host_username_to_host_player_id(database_url):
    game_history._schema_ready = True
    conn, cursor = _save_connection(players={"hosty": (99, None, 1000)})
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=2, bot_mix=[],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=[], host_username="hosty",
        )
    games_insert = _queries(cursor, "INSERT INTO games")[0]
    # (room_code, seats, started_at, finished_at, host_player_id, ...)
    assert games_insert.args[1][4] == 99
    # A host who isn't seated is upserted, but this game isn't counted for them.
    assert _rows(cursor, "INSERT INTO players", 4) == [("hosty", "hosty", 0, 0)]


def test_a_seated_host_is_upserted_once_with_their_seat_name(database_url):
    """The host and their seat share one upsert row (Postgres rejects an
    upsert that touches the same row twice), keeping the seat's display
    name and counting the game once."""
    game_history._schema_ready = True
    conn, cursor = _save_connection()
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice", "game_username": "alice",
         "points": 8, "money_left": 62, "is_winner": True, "eliminated": False},
    ]
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=1, bot_mix=[],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants, host_username="alice",
        )
    assert _rows(cursor, "INSERT INTO players", 4) == [("alice", "Alice", 1, 1)]


def test_record_finished_game_can_mark_a_crashed_game_as_unsuccessful(database_url):
    """See web_server.py's run_game crash path -- a game that never
    reached final_standings still gets a row, just one that says so."""
    game_history._schema_ready = True
    conn, cursor = _save_connection()
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=2, bot_mix=[],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=[], is_finished_successfully=False,
        )
    games_insert = _queries(cursor, "INSERT INTO games")[0]
    # (room_code, seats, started_at, finished_at, host_player_id, time_control, is_finished_successfully, ...)
    assert games_insert.args[1][6] is False


def test_record_finished_game_increments_games_played_and_won_for_every_human(database_url):
    """Unlike achievements/elo, these aren't gated to a google_id -- every
    human participant counts, matching get_player_profile_stats' own
    (already ungated) COUNT query. Done by the players upsert itself."""
    game_history._schema_ready = True
    conn, cursor = _save_connection()  # every upsert is a guest (google_id None)
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
    # (username, display_name, games_played increment, games_won increment) -- none for a bot
    assert _rows(cursor, "INSERT INTO players", 4) == [("alice", "Alice", 1, 1), ("bob", "Bob", 1, 0)]
    upsert = _queries(cursor, "INSERT INTO players")[0].args[0]
    assert "games_played = players.games_played + EXCLUDED.games_played" in upsert
    assert "games_won = players.games_won + EXCLUDED.games_won" in upsert


def test_record_finished_game_placement_reflects_is_winner_not_raw_points(database_url):
    """The money-elimination rule (gameplay.py's final_standings) means a
    participant can have the *highest* points and still lose outright --
    confirmed live. placement must follow is_winner/eliminated, not a
    naive points sort (which would have ranked the eliminated bot below
    first despite its higher score)."""
    game_history._schema_ready = True
    conn, cursor = _save_connection()
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
    game_results = _rows(cursor, "INSERT INTO game_results", 6)
    placement_by_user = {row[1]: row[5] for row in game_results}
    alice_id = next(row[1] for row in game_results if row[4] == -3)
    assert placement_by_user[alice_id] == 1  # the actual winner, despite fewer points


def test_record_finished_game_writes_seat_columns_and_winner_id(database_url):
    game_history._schema_ready = True
    conn, cursor = _save_connection()
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
    # Known before the games row is written, so it goes in with them --
    # no follow-up UPDATE.
    assert not _queries(cursor, "UPDATE games")
    winner_id, p1, p2, p3, p4, p5 = _queries(cursor, "INSERT INTO games")[0].args[1][8:]
    assert p1 is not None and winner_id == p1  # alice, seat 1, also the winner
    assert p2 is None  # the bot -- no difficulty info, so no resolvable player_id
    assert p3 is None and p4 is None and p5 is None  # only 2 seats were ever filled


def test_record_finished_game_writes_a_ratings_row_per_rated_participant(database_url):
    game_history._schema_ready = True
    conn, cursor = _save_connection(players={"alice": (10, "g-alice", 1000), "bob": (11, "g-bob", 1000)})
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
    ratings = _rows(cursor, "INSERT INTO ratings", 5)
    assert len(ratings) == 2
    assert len(_queries(cursor, "INSERT INTO ratings")) == 1  # both rows in one statement
    by_player = {row[0]: row for row in ratings}  # user_id -> (user_id, game_id, old, new, change)
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
    naive sum, backed by exactly one ratings row. Named bots each have
    their own player_id now, so this only guards against a bad bots row
    (faked here by giving both bot keys the same id)."""
    game_history._schema_ready = True
    # Bots deliberately at 900, not 1000 -- see below.
    conn, cursor = _save_connection(players={"alice": (10, "g-alice", 1000)},
                                    bots={"milo": (55, 900), "juno": (55, 900)})
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice", "game_username": "alice",
         "points": 8, "money_left": 62, "is_winner": False, "eliminated": False},
        {"is_bot": True, "username": None, "name": "Milo bot", "game_username": "milo bot",
         "points": 40, "money_left": 66, "is_winner": True, "eliminated": False, "difficulty": "medium"},
        {"is_bot": True, "username": None, "name": "Juno bot", "game_username": "juno bot",
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
    # matching the rated_standings this call built internally -- by
    # placement (milo bot won outright = 1st, alice 2nd, juno bot
    # eliminated so ranks last regardless of its own points), not raw
    # points (see elo.compute_elo_deltas' own docstring on why those
    # can disagree). Bots deliberately rated below alice (900 vs 1000),
    # not the same 1000 for everyone -- at equal ratings, one bot seat
    # winning and the other losing produces exactly offsetting +/-16
    # deltas that average to a coincidental *zero* combined change, which
    # would make this test unable to tell "averaged correctly" apart
    # from "silently skipped" (record_finished_game only writes a
    # ratings row/elo update when the combined delta is actually
    # non-zero).
    expected_seat_deltas = elo.compute_elo_deltas([
        {"username": "alice", "placement": 2, "rating": 1000},
        {"username": "milo bot", "placement": 1, "rating": 900},
        {"username": "juno bot", "placement": 3, "rating": 900},
    ])
    expected_bot_delta = round(
        (expected_seat_deltas["milo bot"] + expected_seat_deltas["juno bot"]) / 2
    )

    by_player = {row[0]: row for row in _rows(cursor, "INSERT INTO ratings", 5)}
    assert set(by_player) == {10, 55}  # exactly one ratings row per real identity, not per seat
    assert by_player[55][4] == expected_bot_delta  # averaged, not summed
    assert by_player[10][4] == expected_seat_deltas["alice"]  # a solo seat is unaffected

    elo_updates = _rows(cursor, "UPDATE players SET elo", 2)  # (player_id, change)
    assert [change for player_id, change in elo_updates if player_id == 55] == [expected_bot_delta]  # once

    # Both bot seats' rating_keys resolve to the same, correct combined
    # change in the return value too (each one only matters if a future
    # caller ever looks a bot seat up by name -- today only alice's own
    # entry is actually read, by web_server.py's Elo reveal).
    assert result["alice"]["rating_change"] == expected_seat_deltas["alice"]
    assert result["milo bot"] == result["juno bot"] == {
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
    conn, cursor = _save_connection(players={"alice": (10, "g-alice", 1000)}, bots={"niel": (99, 1000)})
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice", "game_username": "alice",
         "points": 5, "money_left": 0, "is_winner": False, "eliminated": False},
        {"is_bot": True, "username": None, "name": "Niel bot", "game_username": "niel bot",
         "points": 15, "money_left": 5, "is_winner": True, "eliminated": False, "difficulty": "hard"},
    ]
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=2, bot_mix=["hard"],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants,
        )

    by_player = {row[0]: row for row in _rows(cursor, "INSERT INTO ratings", 5)}
    assert set(by_player) == {10, 99}  # both alice AND the bot get rated
    assert by_player[10][4] < 0  # alice (lost to the bot) loses rating
    assert by_player[99][4] > 0  # the bot (won) gains rating

    assert {player_id for player_id, _ in _rows(cursor, "UPDATE players SET elo", 2)} == {10, 99}
    assert not _queries(cursor, "INSERT INTO player_achievements")


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


def _action(seq, seat, action, amount=None, cards=None, round_number=1, reason=None):
    return {"seq": seq, "round": round_number, "seat": seat, "username": "x", "action": action, "amount": amount,
            "cards": cards, "think_ms": 900, "reason": reason, "timestamp": "2026-01-01T00:00:00+00:00"}


def test_record_finished_game_populates_game_rounds_round_players_and_game_actions(database_url):
    game_history._schema_ready = True
    conn, cursor = _save_connection()
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice", "game_username": "alice", "seat": 0,
         "points": 10, "money_left": 5, "is_winner": True, "eliminated": False},
        {"is_bot": False, "username": "bob", "name": "Bob", "game_username": "bob", "seat": 1,
         "points": 0, "money_left": 20, "is_winner": False, "eliminated": False},
    ]
    actions = [_action(1, 0, "bid", 5, [5]), _action(2, 1, "pass"), _action(3, 0, "discard", 3)]
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
            participants=participants, auction_rounds=auction_rounds, seed=77, actions=actions,
        )

    games_params = _queries(cursor, "INSERT INTO games")[0].args[1]
    assert games_params[7] == 77  # the seed -- with game_actions, all a replay needs
    assert [row[8] for row in _rows(cursor, "INSERT INTO player_games", 9)] == [0, 1]  # seats

    game_id, round_number, card_id, winner_id, winning_bid, started_at, ended_at, winner_seat = \
        _rows(cursor, "INSERT INTO game_rounds", 8)[0]
    assert card_id == 105  # Painting 5, from the cards table
    assert round_number == 1
    assert winning_bid == 5
    assert winner_id is not None  # alice's resolved player_id
    assert winner_seat == 0

    # round_players/game_actions are written as one batched multi-row
    # INSERT each (see _record_auction_rounds's own docstring on why).
    round_players_rows = _rows(cursor, "INSERT INTO round_players", 7)
    assert len(round_players_rows) == 2
    assert sorted(row[6] for row in round_players_rows) == ["lost", "won"]  # (..., result) -- index 6

    # One row per action_log entry, not derived from the auction events:
    # (game_id, seq, seat, player_id, action_type, amount, cards, think_ms, reason, round, timestamp)
    rows = _rows(cursor, "INSERT INTO game_actions", 11)
    assert [(r[1], r[2], r[4], r[5], r[6], r[7]) for r in rows] == [
        (1, 0, "BID", 5, [5], 900), (2, 1, "PASS", None, None, 900), (3, 0, "DISCARD", 3, None, 900),
    ]
    alice_id = rows[0][3]
    assert alice_id is not None and rows[2][3] == alice_id and rows[1][3] not in (None, alice_id)


def test_card_ids_are_read_once_per_process_not_once_per_round(database_url):
    """`cards` never changes after the schema seeds it, so one read serves
    every round of every later game -- it used to be one SELECT per
    auction round, each a full round trip to the database."""
    game_history._schema_ready = True
    rounds = [{
        "round_number": n, "auction_type": "normal",
        "card": {"type": "Painting", "value": n, "multiplier": 1, "is_green": False, "description": "x"},
        "events": [], "recipient": None, "money_spent": {}, "cards_spent": {},
        "started_at": "2026-01-01T00:00:00+00:00", "ended_at": "2026-01-01T00:00:01+00:00",
        "starting_money": {}, "ending_money": {},
    } for n in range(1, 4)]
    card_reads = 0
    for _ in range(2):
        conn, cursor = _save_connection()
        with patch.object(game_history, "_connect", return_value=conn):
            game_history.record_finished_game(
                room_code="ABCDE", seats=1, bot_mix=[],
                started_at=datetime.datetime.now(datetime.timezone.utc),
                finished_at=datetime.datetime.now(datetime.timezone.utc),
                participants=[], auction_rounds=rounds,
            )
        card_reads += len(_queries(cursor, "FROM cards"))
        assert [row[2] for row in _rows(cursor, "INSERT INTO game_rounds", 8)] == [101, 102, 103]
    assert card_reads == 1  # two games, three rounds each


def test_actions_of_a_seat_with_no_players_row_are_still_written_by_seat(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    participants = [
        {"is_bot": True, "username": None, "name": "Bot1", "game_username": "bot1", "seat": 0,
         "points": 1, "money_left": 5, "is_winner": True, "eliminated": False},
    ]
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=1, bot_mix=["greedy"],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants, seed=1, actions=[_action(1, 0, "pass")],
        )
    params, = [c.args[1] for c in cursor.execute.call_args_list if "INSERT INTO game_actions" in c.args[0]]
    assert params[2] == 0 and params[3] is None  # seat 0, no player_id


def test_record_finished_game_batches_round_players_and_game_actions_into_one_call_each(database_url):
    """Real production bug, confirmed live: writing one row at a time made
    an ordinary short game's auction-history recording alone take ~11
    seconds (100+ sequential round trips to Supabase), which blew straight
    through the post-game Elo reveal's ~5.6s polling budget even though
    the actual rating write next to it finishes in ~1s. Rounds/players/
    events here are sized so a one-call-per-row version would need 5
    round_players calls and 10 game_actions calls; the fix must produce
    exactly one of each regardless of how many rounds the game had."""
    game_history._schema_ready = True
    conn, cursor = _save_connection()
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice", "game_username": "alice", "seat": 0,
         "points": 10, "money_left": 5, "is_winner": True, "eliminated": False},
        {"is_bot": False, "username": "bob", "name": "Bob", "game_username": "bob", "seat": 1,
         "points": 0, "money_left": 20, "is_winner": False, "eliminated": False},
    ]
    auction_rounds = [{
        "round_number": n, "auction_type": "normal",
        "card": {"type": "Painting", "value": n, "multiplier": 1, "is_green": False, "description": "x"},
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
    } for n in range(1, 6)]  # 5 rounds -- 10 round_players rows, 10 game_actions rows total
    actions = [_action(2 * n - 1 + i, i, "bid" if i == 0 else "pass", 5 if i == 0 else None,
                       [5] if i == 0 else None, round_number=n)
               for n in range(1, 6) for i in range(2)]
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=2, bot_mix=[],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants, auction_rounds=auction_rounds, seed=1, actions=actions,
        )

    queries = [call.args[0] for call in cursor.execute.call_args_list]
    assert sum("INSERT INTO game_rounds" in q for q in queries) == 1
    assert sum("INSERT INTO round_players" in q for q in queries) == 1
    assert sum("INSERT INTO game_actions" in q for q in queries) == 1

    round_players_params, = [call.args[1] for call in cursor.execute.call_args_list
                              if "INSERT INTO round_players" in call.args[0]]
    assert len(round_players_params) == 5 * 2 * 7  # 5 rounds x 2 players x 7 columns

    game_actions_params, = [call.args[1] for call in cursor.execute.call_args_list
                             if "INSERT INTO game_actions" in call.args[0]]
    assert len(game_actions_params) == 5 * 2 * 11  # 5 rounds x 2 actions x 11 columns


def test_record_finished_game_does_not_crash_when_two_same_difficulty_bots_share_a_round(database_url):
    """Real production bug, confirmed live: two bot seats of the same
    difficulty resolve to the SAME shared player_id (see the bots table's
    own design), so round_players' (round_id, player_id) primary key
    collided the moment a round's money data included both seats --
    crashing this whole function and rolling back the entire transaction
    (games/player_games included, since it's all one commit) for any real
    game with 2+ same-difficulty bots. The ON CONFLICT DO NOTHING fix
    means both INSERT attempts still happen (this test asserts that), but
    a real database silently keeps only the first instead of raising.
    Named bots each have their own player_id now, so this only guards
    against a bad bots row (faked here)."""
    game_history._schema_ready = True
    conn, cursor = _save_connection(bots={"milo": (55, 1000), "juno": (55, 1000)})
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice", "game_username": "alice",
         "points": 12, "money_left": 3, "is_winner": True, "eliminated": False},
        {"is_bot": True, "username": None, "name": "Milo bot", "game_username": "milo bot",
         "points": 5, "money_left": 0, "is_winner": False, "eliminated": True, "difficulty": "medium"},
        {"is_bot": True, "username": None, "name": "Juno bot", "game_username": "juno bot",
         "points": 5, "money_left": 0, "is_winner": False, "eliminated": True, "difficulty": "medium"},
    ]
    auction_rounds = [{
        "round_number": 1, "auction_type": "normal",
        "card": {"type": "Painting", "value": 5, "multiplier": 1, "is_green": False, "description": "x"},
        "events": [], "recipient": "alice",
        "money_spent": {"alice": 5, "milo bot": 0, "juno bot": 0},
        "cards_spent": {"alice": [5], "milo bot": [], "juno bot": []},
        "started_at": "2026-01-01T00:00:00+00:00", "ended_at": "2026-01-01T00:00:01+00:00",
        "starting_money": {"alice": 10, "milo bot": 10, "juno bot": 10},
        "ending_money": {"alice": 5, "milo bot": 10, "juno bot": 10},
    }]
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(  # must not raise
            room_code="ABCDE", seats=3, bot_mix=["medium", "medium"],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants, auction_rounds=auction_rounds,
        )

    round_players_calls = _queries(cursor, "INSERT INTO round_players")
    assert len(round_players_calls) == 1
    assert "ON CONFLICT (round_id, player_id) DO NOTHING" in round_players_calls[0].args[0]
    round_players_rows = _rows(cursor, "INSERT INTO round_players", 7)
    assert len(round_players_rows) == 3  # alice + both bot seats, despite the shared player_id
    bot_player_ids = {row[2] for row in round_players_rows} - {10}  # exclude alice's player_id
    assert bot_player_ids == {55}  # both bot seats resolved to the same shared player_id
    # game_results is keyed (game_id, user_id): the shared id gets one row, from the first seat.
    assert [row[1] for row in _rows(cursor, "INSERT INTO game_results", 6)] == [10, 55]


def test_record_finished_game_leaves_winning_bid_null_for_a_disgrace_card(database_url):
    """Per the original request: a disgrace card's "recipient" passed
    first and got stuck with it -- their money_spent is always 0 by
    AuctionRecord's own design, which isn't a real winning bid."""
    game_history._schema_ready = True
    conn, cursor = _save_connection()
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
    game_round = _rows(cursor, "INSERT INTO game_rounds", 8)[0]
    assert game_round[2] == 112  # FauxPas card_id
    assert game_round[4] is None  # winning_bid


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


def test_find_player_by_google_id_returns_the_row_when_found(database_url, mock_release_connection):
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
    mock_release_connection.assert_called_once_with(conn)


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
    cursor.fetchone.return_value = (1, 1234)  # (player_id, elo) -- see PlayerSession
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


def test_get_player_elo_second_call_is_served_from_the_cache_with_no_db_call(database_url):
    """PlayerSession's whole point: a username already read once in this
    process answers again with zero database interaction -- confirmed
    here by making _connect blow up on a second call, which would fail
    the test if the cache weren't actually being used."""
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = None
    cursor.fetchone.return_value = (1, 1234)
    with patch.object(game_history, "_connect", return_value=conn) as mock_connect:
        assert game_history.get_player_elo("alice") == 1234
        assert game_history.get_player_elo("alice") == 1234
        mock_connect.assert_called_once()


def test_record_finished_game_refreshes_an_already_cached_elo(database_url):
    """The write-through half of PlayerSession: once a game actually
    finishes (and its transaction commits), a human participant's
    already-warm cache entry reflects the new rating immediately --
    without needing a fresh read to notice the change."""
    game_history._schema_ready = True
    game_history._player_sessions["alice"] = game_history.PlayerSession(player_id=10, elo=1000)
    conn, cursor = _save_connection(players={"alice": (10, "google-alice", 1000), "bob": (11, "google-bob", 1000)})
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice", "game_username": "alice",
         "points": 10, "money_left": 5, "is_winner": True, "eliminated": False},
        {"is_bot": False, "username": "bob", "name": "Bob", "game_username": "bob",
         "points": 0, "money_left": 20, "is_winner": False, "eliminated": False},
    ]
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=2, bot_mix=[],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants,
        )
    assert game_history._player_sessions["alice"].elo != 1000


def test_record_finished_game_does_not_refresh_the_cache_if_the_transaction_fails(database_url):
    """A failure anywhere in record_finished_game's single transaction
    rolls back every write it made (games/ratings/players.elo included) --
    the in-memory cache must never show a change the database itself
    threw away, so the refresh only happens after a clean commit (see
    record_finished_game's own comment on cache_refreshes)."""
    game_history._schema_ready = True
    game_history._player_sessions["alice"] = game_history.PlayerSession(player_id=10, elo=1000)
    conn, cursor = _save_connection(players={"alice": (10, "google-alice", 1000), "bob": (11, "google-bob", 1000)})
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice", "game_username": "alice",
         "points": 10, "money_left": 5, "is_winner": True, "eliminated": False},
        {"is_bot": False, "username": "bob", "name": "Bob", "game_username": "bob",
         "points": 0, "money_left": 20, "is_winner": False, "eliminated": False},
    ]
    with patch.object(game_history, "_connect", return_value=conn), \
         patch.object(game_history, "_record_auction_rounds", side_effect=RuntimeError("boom")):
        result = game_history.record_finished_game(
            room_code="ABCDE", seats=2, bot_mix=[],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants, auction_rounds=[{"round_number": 1}],
        )
    assert result == {}
    assert game_history._player_sessions["alice"].elo == 1000  # unchanged -- the write never committed


def test_create_google_player_is_a_no_op_without_a_database(no_database_url):
    assert game_history.create_google_player("g-123", "a@example.com", "alice", "Alice") is False


def test_create_google_player_inserts_a_row_and_returns_true(database_url, mock_release_connection):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    with patch.object(game_history, "_connect", return_value=conn):
        result = game_history.create_google_player("g-123", "a@example.com", "alice", "Alice")
    assert result is True
    query, params = cursor.execute.call_args.args
    assert "INSERT INTO players" in query
    assert params == ("alice", "Alice", "g-123", "a@example.com")
    mock_release_connection.assert_called_once_with(conn)


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


def test_create_guest_player_inserts_a_row_with_no_google_id_or_email(database_url, mock_release_connection):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    with patch.object(game_history, "_connect", return_value=conn):
        result = game_history.create_guest_player("CrimsonNaruto482")
    assert result is True
    query, params = cursor.execute.call_args.args
    assert "INSERT INTO players" in query
    assert params == ("CrimsonNaruto482", "CrimsonNaruto482")
    mock_release_connection.assert_called_once_with(conn)


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


def test_rename_player_updates_the_existing_row(database_url, mock_release_connection):
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
    mock_release_connection.assert_called_once_with(conn)


def test_rename_player_forgets_the_cached_entry_for_the_old_name(database_url):
    """Otherwise whoever claims "alice" next is served the renamed
    account's cached id/elo (and so its stats)."""
    game_history._schema_ready = True
    game_history._cache_player_session("alice", 7, 1234)
    conn, cursor = _fake_connection()
    cursor.rowcount = 1
    with patch.object(game_history, "username_is_taken", return_value=False), \
            patch.object(game_history, "_connect", return_value=conn):
        assert game_history.rename_player("alice", "bob") is True
    assert game_history._peek_player_session("alice") is None


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
    conn, cursor = _save_connection(players={"alice": (10, "g-alice", 1000), "bob": (11, "g-bob", 1000)},
                                    win_counts={10: 1})  # alice's 1st win
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

    # Every linked player is counted, and only after this game's own rows are in.
    win_count = _queries(cursor, "FROM player_games WHERE player_id = ANY")
    assert [call.args[1] for call in win_count] == [(3, [10, 11])]
    assert cursor.execute.call_args_list.index(win_count[0]) > \
        cursor.execute.call_args_list.index(_queries(cursor, "INSERT INTO player_games")[0])
    achievements = _rows(cursor, "INSERT INTO player_achievements", 2)
    assert len(_queries(cursor, "INSERT INTO player_achievements")) == 1
    # first_win (win count 1) + the per-game "sniper" unlock passed in.
    assert (10, "first_win") in achievements
    assert (10, "sniper") in achievements
    assert not any(player_id == 11 for player_id, _ in achievements)  # bob didn't win, earned nothing

    elo_updates = dict(_rows(cursor, "UPDATE players SET elo", 2))  # player_id -> change
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
    created_at = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    last_seen_at = datetime.datetime(2026, 2, 1, tzinfo=datetime.timezone.utc)
    cursor.fetchone.side_effect = iter([
        (1, 1000),                    # (player id, elo)
        (4, 3),                       # (games_played, wins)
        (2.5, 10.0, 8.0),             # (avg_placement, avg_points, avg_money_remaining)
        (created_at, last_seen_at),   # (created_at, last_seen_at)
    ])
    with patch.object(game_history, "_connect", return_value=conn):
        result = game_history.get_player_profile_stats("alice")
    assert result == {
        "games_played": 4, "wins": 3, "win_rate": 0.75, "elo": 1000,
        "avg_placement": 2.5, "avg_points": 10.0, "avg_money_remaining": 8.0,
        "created_at": created_at.isoformat(), "last_played_at": last_seen_at.isoformat(),
    }


def test_get_player_profile_stats_zero_games_has_zero_win_rate_not_a_division_error(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    now = datetime.datetime.now(datetime.timezone.utc)
    cursor.fetchone.side_effect = iter([(1, 1000), (0, 0), (None, None, None), (now, now)])
    with patch.object(game_history, "_connect", return_value=conn):
        result = game_history.get_player_profile_stats("alice")
    assert result["games_played"] == 0
    assert result["wins"] == 0
    assert result["win_rate"] == 0.0
    assert result["elo"] == 1000
    assert result["avg_placement"] is None
    assert result["avg_points"] is None
    assert result["avg_money_remaining"] is None


def test_get_player_profile_stats_averages_are_none_when_no_game_results_rows_exist(database_url):
    """A player whose only games predate the game_results table (added
    after player_games) has real games_played/wins but no averages to
    report -- None, not a wrong 0."""
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    now = datetime.datetime.now(datetime.timezone.utc)
    cursor.fetchone.side_effect = iter([(1, 1000), (5, 2), (None, None, None), (now, now)])
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
    now = datetime.datetime.now(datetime.timezone.utc)
    cursor.fetchone.side_effect = iter([(1, 1234), (2, 1), (1.0, 15.0, 5.0), (now, now)])
    with patch.object(game_history, "_connect", return_value=conn):
        result = game_history.get_player_profile_stats("alice")
    assert result["elo"] == 1234
    # Only one connection ever opened for the whole call.
    assert conn.__enter__.call_count == 1


def test_get_player_profile_stats_includes_player_since_and_last_played(database_url):
    """created_at/last_seen_at are read fresh by player_id every call,
    never served from the id/elo cache -- see this function's own comment
    on why (last_seen_at changes on every game finished)."""
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    created_at = datetime.datetime(2025, 6, 1, tzinfo=datetime.timezone.utc)
    last_seen_at = datetime.datetime(2026, 3, 15, tzinfo=datetime.timezone.utc)
    cursor.fetchone.side_effect = iter([(1, 1000), (2, 1), (1.0, 5.0, 3.0), (created_at, last_seen_at)])
    with patch.object(game_history, "_connect", return_value=conn):
        result = game_history.get_player_profile_stats("alice")
    assert result["created_at"] == created_at.isoformat()
    assert result["last_played_at"] == last_seen_at.isoformat()


def test_get_player_profile_stats_failure_is_caught_not_raised(database_url):
    game_history._schema_ready = True
    with patch.object(game_history, "_connect", side_effect=RuntimeError("unreachable")):
        assert game_history.get_player_profile_stats("alice") is None


# --------------------------------------------------- game history / leaderboard --

def test_get_recent_games_is_empty_without_a_database(no_database_url):
    assert game_history.get_recent_games("alice") == {"games": [], "has_more": False}


def test_get_recent_games_returns_none_for_an_unknown_username(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = None
    cursor.fetchone.return_value = None
    with patch.object(game_history, "_connect", return_value=conn):
        assert game_history.get_recent_games("nobody") == {"games": [], "has_more": False}


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
        [(100, True, 12), (101, False, None)],  # this player's own (is_winner, rating_change) per game
    ]
    with patch.object(game_history, "_connect", return_value=conn):
        result = game_history.get_recent_games("alice")
    games = result["games"]
    assert result["has_more"] is False
    assert [g["game_id"] for g in games] == [100, 101]
    assert games[0]["placement"] == 1
    assert {o["name"] for o in games[0]["opponents"]} == {"alice", "Marble"}
    assert games[1]["opponents"] == [
        {"name": "alice", "is_bot": False, "is_winner": False},
        {"name": "bob", "is_bot": False, "is_winner": True},
    ]
    assert games[0]["is_winner"] is True
    assert games[0]["rating_change"] == 12
    assert games[1]["is_winner"] is False
    assert games[1]["rating_change"] is None


def test_get_recent_games_sets_has_more_when_another_page_exists(database_url):
    """Fetches limit+1 rows to know whether another page exists, without a
    separate COUNT(*) -- see FRONTEND_FIXES.MD's 10-at-a-time pagination
    ask. The (limit+1)th row is trimmed off the returned list."""
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = None
    cursor.fetchone.return_value = (1,)
    finished_at = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    cursor.fetchall.side_effect = [
        [(100, finished_at, 1), (101, finished_at, 2)],  # limit=1 + 1 lookahead row = 2 rows back
        [(100, "alice", False, True), (101, "alice", False, False)],
        [(100, True, 5)],  # only the kept (limit=1) game needs an own-result row
    ]
    with patch.object(game_history, "_connect", return_value=conn):
        result = game_history.get_recent_games("alice", limit=1, offset=0)
    assert result["has_more"] is True
    assert [g["game_id"] for g in result["games"]] == [100]  # only `limit` rows kept


def test_get_recent_games_rating_change_is_none_for_an_unrated_game(database_url):
    """A game this player wasn't rated in (guest account, all-bot practice
    game, or a game predating the ratings table) reports rating_change as
    None -- never a fake 0, which would misleadingly read as "no change"
    rather than "not applicable"."""
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = None
    cursor.fetchone.return_value = (1,)
    finished_at = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    cursor.fetchall.side_effect = [
        [(100, finished_at, 1)],
        [(100, "alice", False, True)],
        [],  # LEFT JOIN found no ratings row at all for this game -- own_result_by_game stays empty
    ]
    with patch.object(game_history, "_connect", return_value=conn):
        result = game_history.get_recent_games("alice")
    assert result["games"][0]["rating_change"] is None
    assert result["games"][0]["is_winner"] is None


def test_get_recent_games_failure_is_caught_not_raised(database_url):
    game_history._schema_ready = True
    with patch.object(game_history, "_connect", side_effect=RuntimeError("unreachable")):
        assert game_history.get_recent_games("alice") == {"games": [], "has_more": False}


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
        ("alice", False, None, 15, 5, True, False, 1),
        ("Marble", True, "__bot_hard__", 5, 0, False, True, 2),
    ])
    with patch.object(game_history, "_connect", return_value=conn):
        result = game_history.get_game_detail(42)
    assert result["game_id"] == 42
    assert len(result["participants"]) == 2
    assert result["participants"][0] == {
        "name": "alice", "is_bot": False, "bot_profile_username": None, "points": 15, "money_left": 5,
        "is_winner": True, "eliminated": False, "placement": 1,
    }
    # The bot seat's flavor name ("Marble") is untouched, but it also
    # carries the shared difficulty identity's real username to link to.
    assert result["participants"][1]["name"] == "Marble"
    assert result["participants"][1]["bot_profile_username"] == "__bot_hard__"


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
    conn, cursor = _save_connection(bots={"milo": (55, 1000), "juno": (55, 1000)})
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice", "game_username": "alice",
         "points": 8, "money_left": 62, "is_winner": False, "eliminated": False},
        {"is_bot": True, "username": None, "name": "Milo bot", "game_username": "milo bot",
         "points": 40, "money_left": 66, "is_winner": True, "eliminated": False, "difficulty": "medium"},
        {"is_bot": True, "username": None, "name": "Juno bot", "game_username": "juno bot",
         "points": 18, "money_left": 27, "is_winner": False, "eliminated": True, "difficulty": "medium"},
    ]
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=3, bot_mix=["medium", "medium"],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants,
        )

    # (game_id, player_id, bot_name, points, money_left, is_winner, eliminated, placement, seat)
    placements_by_name = {row[2] or "alice": row[7] for row in _rows(cursor, "INSERT INTO player_games", 9)}
    assert placements_by_name == {"alice": 2, "Milo bot": 1, "Juno bot": 3}


def test_get_leaderboard_excludes_guests_bots_and_the_never_played_by_query(database_url):
    """Doesn't fake a real WHERE-clause result (that's Postgres' job) --
    just confirms the query text actually filters all three, and the
    shape of what comes back."""
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchall = MagicMock(return_value=[("alice", 1200, 10, 6)])
    with patch.object(game_history, "_connect", return_value=conn):
        result = game_history.get_leaderboard()
    query = cursor.execute.call_args.args[0]
    assert "google_id IS NOT NULL" in query
    assert "games_played > 0" in query  # a linked account that never played is just noise on the board
    assert "NOT IN (SELECT player_id FROM bots)" in query
    assert result == {"rows": [{"username": "alice", "elo": 1200, "games_played": 10, "games_won": 6}],
                       "has_more": False}


def test_get_leaderboard_sets_has_more_when_another_page_exists(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchall = MagicMock(return_value=[("alice", 1200, 10, 6), ("bob", 1100, 5, 2)])
    with patch.object(game_history, "_connect", return_value=conn):
        result = game_history.get_leaderboard(limit=1, offset=0)
    assert result["has_more"] is True
    assert [r["username"] for r in result["rows"]] == ["alice"]  # only `limit` rows kept


def test_get_leaderboard_is_cached_across_calls_with_no_further_db_hit(database_url):
    """The leaderboard is identical for every visitor -- confirmed here by
    making _connect blow up on a second call for the same page, which
    would fail the test if the cache weren't actually being used."""
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchall = MagicMock(return_value=[("alice", 1200, 10, 6)])
    with patch.object(game_history, "_connect", return_value=conn) as mock_connect:
        first = game_history.get_leaderboard(limit=20, offset=0)
        second = game_history.get_leaderboard(limit=20, offset=0)
        mock_connect.assert_called_once()
    assert first == second


def test_get_leaderboard_cache_is_scoped_per_page(database_url):
    """A different (limit, offset) page is a genuinely different cache
    entry -- confirmed by requiring a second real _connect() call for a
    different offset."""
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchall = MagicMock(return_value=[("alice", 1200, 10, 6)])
    with patch.object(game_history, "_connect", return_value=conn) as mock_connect:
        game_history.get_leaderboard(limit=20, offset=0)
        game_history.get_leaderboard(limit=20, offset=20)
        assert mock_connect.call_count == 2


def test_record_finished_game_invalidates_the_leaderboard_cache(database_url, mock_release_connection):
    """A rated game just changed someone's standing -- don't make every
    visitor wait out the rest of the leaderboard's TTL to see it. Every
    cached page is invalidated (a rank shift can move players across
    page boundaries too), but the default first page is immediately
    re-fetched and reinstalled -- see _warm_leaderboard_cache -- so the
    very next real visitor gets a warm cache hit with fresh data instead
    of the query this invalidation would otherwise have pushed onto
    them."""
    game_history._schema_ready = True
    game_history._leaderboard_cache[(20, 0)] = (time.monotonic(), {"rows": ["stale"], "has_more": False})
    game_history._leaderboard_cache[(20, 20)] = (time.monotonic(), {"rows": ["stale-page-2"], "has_more": False})
    conn, cursor = _save_connection(players={"alice": (10, "google-alice", 1000), "bob": (11, "google-bob", 1000)},
                                    leaderboard_rows=[("alice", 1016, 1, 1)])
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice", "game_username": "alice",
         "points": 10, "money_left": 5, "is_winner": True, "eliminated": False},
        {"is_bot": False, "username": "bob", "name": "Bob", "game_username": "bob",
         "points": 0, "money_left": 20, "is_winner": False, "eliminated": False},
    ]
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=2, bot_mix=[],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants,
        )
    # The stale second page is gone, not silently kept around.
    assert (20, 20) not in game_history._leaderboard_cache
    # The default first page isn't just cleared -- it's already warm
    # again with the freshly re-fetched result, not the old stale one.
    assert (20, 0) in game_history._leaderboard_cache
    _, warmed = game_history._leaderboard_cache[(20, 0)]
    assert warmed == {"rows": [{"username": "alice", "elo": 1016, "games_played": 1, "games_won": 1}],
                       "has_more": False}


def test_async_save_hands_over_the_elo_result_before_rewarming_the_leaderboard(database_url):
    """The finished screen polls for these numbers; re-fetching the
    leaderboard is one more trip to the database, so it waits until
    on_complete has the result."""
    game_history._schema_ready = True
    conn, _cursor = _save_connection(players={"alice": (10, "google-alice", 1000), "bob": (11, "google-bob", 1000)})
    order = []
    done = __import__("threading").Event()
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice", "game_username": "alice",
         "points": 10, "money_left": 5, "is_winner": True, "eliminated": False},
        {"is_bot": False, "username": "bob", "name": "Bob", "game_username": "bob",
         "points": 0, "money_left": 20, "is_winner": False, "eliminated": False},
    ]

    def warm():
        order.append("rewarm")
        done.set()

    with patch.object(game_history, "_connect", return_value=conn), \
         patch.object(game_history, "_warm_leaderboard_cache", side_effect=warm):
        game_history.record_finished_game_async(
            on_complete=lambda result: order.append(("elo", sorted(result))),
            room_code="ABCDE", seats=2, bot_mix=[],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants,
        )
        assert done.wait(5)
    assert order == [("elo", ["alice", "bob"]), "rewarm"]


def test_warm_leaderboard_cache_leaves_it_clear_on_a_fetch_failure(database_url):
    """A transient DB hiccup right after a game finishes shouldn't install
    a worse (empty) result over what a normal lazy fetch would have
    produced on the next real request -- see _fetch_leaderboard_page's
    own docstring on why None (not an empty page) means "leave it
    alone"."""
    game_history._schema_ready = True
    with patch.object(game_history, "_connect", side_effect=RuntimeError("db unreachable")):
        game_history._warm_leaderboard_cache()
    assert (20, 0) not in game_history._leaderboard_cache


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


def test_no_game_actions_rows_without_an_action_log(database_url, mock_release_connection):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=2, bot_mix=[],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=[],
        )
    assert not any("INSERT INTO game_actions" in c.args[0] for c in cursor.execute.call_args_list)


def test_schema_no_longer_creates_a_separate_replay_table():
    assert not any("game_replays" in st for st in game_history._SCHEMA_STATEMENTS)


def _episode(seed):
    return {"duration_ms": 1234, "record": {
        "version": 1, "seed": seed,
        "seats": [{"seat": 1, "username": "a", "kind": "easy"}, {"seat": 0, "username": "b", "kind": "hard"}],
        "rounds": [{}, {}, {}], "actions": [{}] * 7,
        "standings": [{"seat": 0, "is_winner": False}, {"seat": 1, "is_winner": True}],
    }}


def test_insert_selfplay_games_batches_every_episode_into_one_insert(database_url, mock_release_connection):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.insert_selfplay_games("rl-v1", [_episode(1), _episode(2)], engine_version="abc")
    (call,) = cursor.execute.call_args_list
    assert call.args[0].startswith("INSERT INTO selfplay_games")
    params = call.args[1]
    assert len(params) == 20  # 2 rows x 10 columns
    batch, seed, seat_kinds, winner_seats, num_rounds, num_decisions, duration_ms, engine, version, _ = params[:10]
    # seat_kinds in seat order
    assert (batch, seed, json.loads(seat_kinds), json.loads(winner_seats)) == ("rl-v1", 1, ["hard", "easy"], [1])
    assert (num_rounds, num_decisions, duration_ms, engine, version) == (3, 7, 1234, "abc", 1)


def test_insert_selfplay_games_raises_instead_of_swallowing_a_database_error(database_url, mock_release_connection):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.execute.side_effect = RuntimeError("connection reset")
    with patch.object(game_history, "_connect", return_value=conn):
        with pytest.raises(RuntimeError):
            game_history.insert_selfplay_games("rl-v1", [_episode(1)])


def test_selfplay_helpers_refuse_to_run_without_a_database(no_database_url):
    with pytest.raises(RuntimeError):
        game_history.count_selfplay_games("rl-v1")


def test_fetch_selfplay_records_samples_within_a_batch(database_url, mock_release_connection):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchall.return_value = [(3, {"version": 1})]
    with patch.object(game_history, "_connect", return_value=conn):
        rows = game_history.fetch_selfplay_records(batch="rl-v1", sample=5)
    assert rows == [(3, {"version": 1})]
    query, params = cursor.execute.call_args.args
    assert "FROM selfplay_games" in query and "batch = %s" in query and "random()" in query
    assert params == ["rl-v1", 5]


def test_fetch_selfplay_records_reads_a_whole_batch_in_id_pages(database_url, mock_release_connection, monkeypatch):
    """A whole batch in one query ran past the statement timeout (rl-v1,
    10,000 games) -- it's read in keyset pages on id instead."""
    game_history._schema_ready = True
    monkeypatch.setattr(game_history, "_SELFPLAY_FETCH_PAGE_SIZE", 2)
    conn, cursor = _fake_connection()
    pages = [[(1, {"g": 1}), (2, {"g": 2})], [(5, {"g": 5}), (7, {"g": 7})], [(9, {"g": 9})]]
    cursor.fetchall.side_effect = pages
    with patch.object(game_history, "_connect", return_value=conn):
        rows = game_history.fetch_selfplay_records(batch="rl-v1")
    assert rows == [row for page in pages for row in page]
    calls = [c.args for c in cursor.execute.call_args_list]
    assert len(calls) == 3
    for query, _ in calls:
        assert "batch = %s" in query and "id > %s" in query and "ORDER BY id LIMIT %s" in query
    assert [params for _, params in calls] == [["rl-v1", 0, 2], ["rl-v1", 2, 2], ["rl-v1", 7, 2]]


def test_the_leaderboard_cache_lives_three_months_because_writes_invalidate_it():
    assert game_history._LEADERBOARD_CACHE_TTL_SECONDS == 90 * 24 * 60 * 60


def test_a_rename_clears_the_cached_leaderboard(database_url, mock_release_connection):
    """The board lists players by username -- with a 3-month TTL, a renamed
    player would otherwise keep showing under the old name."""
    game_history._schema_ready = True
    game_history._leaderboard_cache[(20, 0)] = (time.monotonic(), {"rows": [{"username": "alice"}], "has_more": False})
    conn, cursor = _fake_connection()
    cursor.rowcount = 1
    with patch.object(game_history, "username_is_taken", return_value=False), \
            patch.object(game_history, "_connect", return_value=conn):
        assert game_history.rename_player("alice", "bob") is True
    assert game_history._leaderboard_cache == {}


def test_a_game_with_no_rating_change_still_clears_the_cached_leaderboard(database_url, mock_release_connection):
    """games_played/games_won are on the board too, and they move even when
    nobody's Elo does (a guest-only game here)."""
    game_history._schema_ready = True
    game_history._leaderboard_cache[(20, 0)] = (time.monotonic(), {"rows": ["stale"], "has_more": False})
    conn, cursor = _save_connection()
    participants = [
        {"is_bot": False, "username": "alice", "name": "Alice", "game_username": "alice", "seat": 0,
         "points": 5, "money_left": 3, "is_winner": True, "eliminated": False},
        {"is_bot": False, "username": "bob", "name": "Bob", "game_username": "bob", "seat": 1,
         "points": 1, "money_left": 9, "is_winner": False, "eliminated": False},
    ]
    with patch.object(game_history, "_connect", return_value=conn):
        game_history.record_finished_game(
            room_code="ABCDE", seats=2, bot_mix=[],
            started_at=datetime.datetime.now(datetime.timezone.utc),
            finished_at=datetime.datetime.now(datetime.timezone.utc),
            participants=participants,
        )
    assert game_history._leaderboard_cache == {}


# ------------------------------------------------------------ login tokens --

def test_a_login_token_is_stored_only_as_its_hash(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.rowcount = 1
    with patch.object(game_history, "_connect", return_value=conn):
        token = game_history.issue_login_token("alice")
    assert token and len(token) >= 40
    query, params = cursor.execute.call_args.args
    assert "INSERT INTO player_logins" in query
    assert params == (game_history._hash_login_token(token), "alice")
    assert token not in params


def test_no_login_token_for_a_username_with_no_account(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.rowcount = 0
    with patch.object(game_history, "_connect", return_value=conn):
        assert game_history.issue_login_token("nobody") is None


def test_login_token_matches_checks_the_hash_against_the_username(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = None
    cursor.fetchone.return_value = (1,)
    with patch.object(game_history, "_connect", return_value=conn):
        assert game_history.login_token_matches("alice", "secret") is True
    query, params = cursor.execute.call_args.args
    assert params == (game_history._hash_login_token("secret"), "alice")

    cursor.fetchone.return_value = None
    with patch.object(game_history, "_connect", return_value=conn):
        assert game_history.login_token_matches("alice", "wrong") is False


def test_a_missing_login_token_never_matches(database_url):
    game_history._schema_ready = True
    with patch.object(game_history, "_connect", side_effect=AssertionError("no query needed")):
        assert game_history.login_token_matches("alice", None) is False
        assert game_history.login_token_matches("alice", "") is False


def test_login_token_matches_is_unknown_when_the_database_errors(database_url):
    """None, not False: a database blip mustn't lock every player out."""
    game_history._schema_ready = True
    with patch.object(game_history, "_connect", side_effect=RuntimeError("down")):
        assert game_history.login_token_matches("alice", "secret") is None


def test_login_token_matches_is_unknown_without_a_database(no_database_url):
    assert game_history.login_token_matches("alice", "secret") is None


def test_first_login_token_is_refused_once_the_name_is_claimed(database_url):
    """The SELECT only finds a guest with no tokens yet; nothing found means
    a Google account, or a name another device already holds."""
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = None
    cursor.fetchone.return_value = None
    with patch.object(game_history, "_connect", return_value=conn):
        assert game_history.issue_first_login_token("alice") is None
    assert not any("INSERT INTO player_logins" in c.args[0] for c in cursor.execute.call_args_list)


def test_first_login_token_for_an_unclaimed_guest(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = None
    cursor.fetchone.return_value = (42,)
    with patch.object(game_history, "_connect", return_value=conn):
        token = game_history.issue_first_login_token("alice")
    assert token
    select = next(c.args[0] for c in cursor.execute.call_args_list if c.args[0].lstrip().startswith("SELECT"))
    assert "google_id IS NULL" in select and "NOT EXISTS" in select
    insert_query, insert_params = cursor.execute.call_args.args
    assert "INSERT INTO player_logins" in insert_query
    assert insert_params == (game_history._hash_login_token(token), 42)


def test_display_ratings_cover_only_rated_players(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchall.return_value = [("alice", 1110), ("__bot_ziggy__", 1012)]
    with patch.object(game_history, "_connect", return_value=conn):
        result = game_history.get_display_ratings(["alice", "guest1", "__bot_ziggy__", "alice"])
    assert result == {"alice": 1110, "__bot_ziggy__": 1012}
    query, (usernames,) = cursor.execute.call_args.args
    assert "google_id IS NOT NULL" in query and "FROM bots" in query  # guests are unrated
    assert usernames == ["__bot_ziggy__", "alice", "guest1"]


def test_display_ratings_are_empty_on_a_database_error(database_url):
    game_history._schema_ready = True
    with patch.object(game_history, "_connect", side_effect=RuntimeError("down")):
        assert game_history.get_display_ratings(["alice"]) == {}


def test_ping_touches_the_database_and_hands_the_connection_back(database_url, mock_release_connection):
    conn, _cursor = _fake_connection()
    with patch.object(game_history, "_connect", return_value=conn):
        assert game_history.ping() is True
    mock_release_connection.assert_called_once_with(conn)


def test_ping_reports_failure_instead_of_raising(database_url):
    with patch.object(game_history, "_connect", side_effect=RuntimeError("paused")):
        assert game_history.ping() is False


def test_record_finished_game_awards_games_played_and_win_streak_milestones(database_url):
    game_history._schema_ready = True
    conn, cursor = _save_connection(
        players={"alice": (10, "g-alice", 1000), "bob": (11, "g-bob", 1000)},
        # (games played, wins, wins among the last 3 games)
        career={10: (50, 25, 3), 11: (10, 0, 0)},
    )
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
        )

    achievements = set(_rows(cursor, "INSERT INTO player_achievements", 2))
    assert {(10, "dynasty"), (10, "veteran"), (10, "socialite"), (10, "on_a_roll")} <= achievements
    # bob's 10 games without a win count as both games played and losses.
    assert {a for p, a in achievements if p == 11} == {"socialite", "thick_skin"}


def test_record_finished_game_awards_elo_levels_from_the_new_rating(database_url):
    game_history._schema_ready = True
    # alice crosses 1100 with this win; bob is still above 1200 after
    # losing some rating this game.
    conn, cursor = _save_connection(players={"alice": (10, "g-alice", 1095), "bob": (11, "g-bob", 1250)})
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
        )

    achievements = set(_rows(cursor, "INSERT INTO player_achievements", 2))
    assert (10, "rising_star") in achievements and (10, "connoisseur") not in achievements
    assert {(11, "rising_star"), (11, "connoisseur")} <= achievements
    assert (11, "aristocrat") not in achievements


# ------------------------------------------- achievements from stored games --

_T = [datetime.datetime(2026, 1, d, tzinfo=datetime.timezone.utc) for d in range(1, 29)]


def test_career_unlocks_are_dated_by_the_game_that_crossed_the_threshold():
    games = [(_T[i], won) for i, won in enumerate([False, True, True, True] + [False] * 8)]
    unlocked = game_history._career_unlocks(games, ratings=[(1050, _T[1]), (1110, _T[3])], current_elo=1090)
    assert unlocked["first_win"] == _T[1]
    assert unlocked["hat_trick"] == _T[3] and unlocked["on_a_roll"] == _T[3]
    assert unlocked["socialite"] == _T[9]  # 10th game
    assert "thick_skin" not in unlocked  # 9 losses
    # Peak rating counts, even though the current one is back under 1100.
    assert unlocked["rising_star"] == _T[3]


def _seat(pid, seat, points, money, winner=False, eliminated=False):
    return (pid, seat, points, money, winner, eliminated)


def test_stored_game_without_round_data_awards_only_standings_achievements():
    seats = [_seat(1, 0, 30, 10, winner=True), _seat(None, 1, 18, 9), _seat(None, 2, 40, 0, eliminated=True)]
    unlocked = game_history._stored_game_unlocks(1, seats, ["hard", "hard"], rounds=[], actions=[])
    assert {"landslide", "giant_slayer", "hard_times"} <= unlocked
    # No rounds stored: never "took no disgrace card" / "never passed".
    assert not {"clean_hands", "fearless", "wallflower"} & unlocked


def test_stored_game_rebuilds_auctions_from_rounds_and_actions():
    seats = [_seat(1, 0, 30, 10, winner=True), _seat(2, 1, 18, 9)]
    rounds = [(1, "Painting", 10, 1, 0, 20, {1: 20, 2: 0}),
              (2, "Passe", -5, None, 1, None, {1: 0, 2: 0})]  # bot-less seat 1 took the Passe, by seat
    actions = [(1, 1, 0, "BID"), (1, 2, 1, "PASS"), (2, 2, 1, "PASS")]
    unlocked = game_history._stored_game_unlocks(1, seats, [], rounds, actions)
    assert {"masterpiece", "high_roller", "sniper", "clean_hands"} <= unlocked
    assert "fearless" in unlocked  # player 1 never passed


def test_stored_game_with_rounds_but_no_actions_skips_action_achievements():
    seats = [_seat(1, 0, 30, 10, winner=True), _seat(2, 1, 18, 9)]
    rounds = [(1, "Painting", 10, 1, 0, 5, {1: 5})]
    unlocked = game_history._stored_game_unlocks(1, seats, [], rounds, actions=[])
    assert "masterpiece" in unlocked
    assert not {"sniper", "fearless"} & unlocked


def test_backfill_skips_when_the_catalog_version_is_unchanged(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = None
    cursor.fetchone.return_value = (game_history.achievement_catalog.catalog_version(),)
    with patch.object(game_history, "_connect", return_value=conn), \
            patch.object(game_history, "recompute_achievements") as recompute:
        assert game_history.backfill_achievements_if_catalog_changed() is None
    recompute.assert_not_called()


def test_backfill_records_the_version_only_after_a_successful_rebuild(database_url):
    game_history._schema_ready = True
    conn, cursor = _fake_connection()
    cursor.fetchone.side_effect = None
    cursor.fetchone.return_value = ("old-version",)
    with patch.object(game_history, "_connect", return_value=conn), \
            patch.object(game_history, "recompute_achievements", return_value=None):
        assert game_history.backfill_achievements_if_catalog_changed() is None
    assert not _queries(cursor, "INSERT INTO app_state")

    with patch.object(game_history, "_connect", return_value=conn), \
            patch.object(game_history, "recompute_achievements", return_value=5):
        assert game_history.backfill_achievements_if_catalog_changed() == 5
    assert _queries(cursor, "INSERT INTO app_state")
