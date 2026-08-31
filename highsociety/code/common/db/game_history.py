"""
Persists finished games to a Postgres database (see README's "Game history
database" section) so a player could eventually be shown their own past
games. Entirely optional: unless the DATABASE_URL environment variable is
set, every public function here is a no-op, so this can never affect an
existing local/dev/test setup that hasn't configured a database. A game
whose write fails or is slow also never blocks the actual game-over message
reaching players — see record_finished_game_async.

Schema (see _SCHEMA_STATEMENTS for the exact DDL; the core three tables
below, plus bots/game_results/ratings/cards/meta added for
BACKEND_REWORK.MD's rework — see record_finished_game's own docstring for
how those fit together):
  players       One row per distinct human username ever seen, plus 3
                reserved rows representing the shared identity of each bot
                difficulty tier (see `bots`). `username` is the identity
                key today, since there's no login system yet — the
                nullable google_id/email columns exist so a future Google
                Sign-In can attach a *real* identity to an existing
                players.id later without changing any foreign key that
                already points at it.
  games         One row per finished (or crashed/aborted — see
                is_finished_successfully) game, including each rematch (a
                rematch is just another call to record_finished_game).
  player_games  The "key that maps players to their past games": one row
                per (game, participant). A bot participant is recorded by
                flavor name (bot_name) same as always, and — when its
                difficulty is known — also by the shared per-difficulty
                player_id from `bots`, so it's no longer forced to be
                identity-less the way a genuinely unique-per-bot row would
                have to be.
"""
import datetime
import os
import threading
import time
from typing import Callable, Optional

from highsociety.code.common import elo
from highsociety.code.common.achievements import WIN_COUNT_MILESTONES
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager

_DATABASE_URL_ENV = "DATABASE_URL"

_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS players (
        id SERIAL PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        google_id TEXT UNIQUE,
        email TEXT UNIQUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS games (
        id SERIAL PRIMARY KEY,
        room_code TEXT NOT NULL,
        seats INTEGER NOT NULL,
        bot_mix JSONB NOT NULL DEFAULT '[]',
        started_at TIMESTAMPTZ NOT NULL,
        finished_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS player_games (
        id SERIAL PRIMARY KEY,
        game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
        player_id INTEGER REFERENCES players(id) ON DELETE CASCADE,
        bot_name TEXT,
        points INTEGER NOT NULL,
        money_left INTEGER NOT NULL,
        is_winner BOOLEAN NOT NULL DEFAULT FALSE,
        eliminated BOOLEAN NOT NULL DEFAULT FALSE,
        CONSTRAINT player_xor_bot CHECK ((player_id IS NULL) <> (bot_name IS NULL))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_player_games_player_id ON player_games (player_id)",
    "CREATE INDEX IF NOT EXISTS idx_player_games_game_id ON player_games (game_id)",
    # A migration, not part of the original CREATE TABLE -- IF NOT EXISTS
    # there only skips the whole statement for a table that already exists
    # in production, so a new column needs its own idempotent ALTER TABLE
    # to actually reach it. 1000 is a placeholder starting rating (see
    # matchmaking.py's docstring) -- every player starts equal until
    # there's real post-match rating logic to diverge them.
    "ALTER TABLE players ADD COLUMN IF NOT EXISTS elo INTEGER NOT NULL DEFAULT 1000",
    """
    CREATE TABLE IF NOT EXISTS player_achievements (
        id SERIAL PRIMARY KEY,
        player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
        achievement_id TEXT NOT NULL,
        unlocked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (player_id, achievement_id)
    )
    """,

    # ---------------------------------------------------------------------
    # Everything below was added for BACKEND_REWORK.MD's schema rework.
    # Same idempotent style as above throughout -- every statement here is
    # safe to re-run on every process start against the live production DB.
    # ---------------------------------------------------------------------

    "ALTER TABLE players ADD COLUMN IF NOT EXISTS games_played INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE players ADD COLUMN IF NOT EXISTS games_won INTEGER NOT NULL DEFAULT 0",

    # One row per difficulty tier, pointing at a dedicated players row (see
    # the seed INSERTs below) -- NOT one row per bot flavor name. A "Wagon
    # bot" this game and a "Pip bot" next game can both be Hard difficulty;
    # there's no meaningful persistent identity behind the random flavor
    # name itself; there is behind the difficulty tier, which is what a
    # game_actions/game_rounds row actually wants to attribute to.
    """
    CREATE TABLE IF NOT EXISTS bots (
        player_id INTEGER PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
        difficulty TEXT NOT NULL CHECK (difficulty IN ('easy', 'medium', 'hard'))
    )
    """,
    # Postgres has no `ADD CONSTRAINT IF NOT EXISTS` -- drop-then-recreate
    # on every startup is how this codebase already achieves idempotency
    # for anything that isn't a plain column/table (see this file's own
    # comment above the `elo` column). Relaxed from a strict xor: bot rows
    # now also carry a real player_id (the shared per-difficulty id above)
    # alongside their existing bot_name flavor label -- every row already
    # satisfies this looser check, since the old constraint was stricter.
    "ALTER TABLE player_games DROP CONSTRAINT IF EXISTS player_xor_bot",
    "ALTER TABLE player_games DROP CONSTRAINT IF EXISTS player_or_bot",
    "ALTER TABLE player_games ADD CONSTRAINT player_or_bot "
    "CHECK (player_id IS NOT NULL OR bot_name IS NOT NULL)",

    # Per-SEAT placement, deliberately NOT read from game_results (whose
    # PRIMARY KEY (game_id, user_id) collapses two same-difficulty bot
    # seats sharing one player_id into a single row -- correct for that
    # table's own per-*account* purpose, but wrong here: confirmed live,
    # get_game_detail's old game_results join gave both bot seats the
    # exact same placement, since the join can only match by player_id,
    # not by which physical seat it was). This column is this table's own
    # source of truth for "what did *this seat* place", independent of
    # how many other seats might share its player_id.
    "ALTER TABLE player_games ADD COLUMN IF NOT EXISTS placement INTEGER",
    # Backfill for every row written before this column existed -- ranks
    # each game's own seats by the identical tier+points rule
    # record_finished_game's _placement_tier already uses (winner first,
    # eliminated last, else by descending points), computed fresh per
    # seat from player_games' own data rather than borrowed from
    # game_results, so it's correct even for the shared-bot-seat case
    # this column exists to fix.
    """
    WITH ranked AS (
        SELECT id, ROW_NUMBER() OVER (
            PARTITION BY game_id
            ORDER BY CASE WHEN is_winner THEN 0 WHEN eliminated THEN 2 ELSE 1 END, -points
        ) AS computed_placement
        FROM player_games
        WHERE placement IS NULL
    )
    UPDATE player_games pg SET placement = ranked.computed_placement
    FROM ranked WHERE pg.id = ranked.id
    """,

    # `bot_mix` (still present, see this file's own module docstring on
    # `games`) is no longer populated for new rows -- these are its
    # richer, per-seat replacement. All nullable: an untimed 2-seat game
    # only ever fills player_1/player_2, and a seat's occupant (human or
    # bot) might fail to resolve to a player_id in some edge case without
    # that blocking the rest of the write.
    "ALTER TABLE games ADD COLUMN IF NOT EXISTS host_player_id INTEGER REFERENCES players(id)",
    "ALTER TABLE games ADD COLUMN IF NOT EXISTS time_control INTEGER",
    "ALTER TABLE games ADD COLUMN IF NOT EXISTS winner_id INTEGER REFERENCES players(id)",
    # True unless explicitly written False -- until this rework, a crashed/
    # aborted game (see web_server.py's run_game exception path) left no
    # row at all rather than one marked failed, so every row that existed
    # before this column was added did, in fact, reach a clean finish.
    "ALTER TABLE games ADD COLUMN IF NOT EXISTS is_finished_successfully BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE games ADD COLUMN IF NOT EXISTS player_1 INTEGER REFERENCES players(id)",
    "ALTER TABLE games ADD COLUMN IF NOT EXISTS player_2 INTEGER REFERENCES players(id)",
    "ALTER TABLE games ADD COLUMN IF NOT EXISTS player_3 INTEGER REFERENCES players(id)",
    "ALTER TABLE games ADD COLUMN IF NOT EXISTS player_4 INTEGER REFERENCES players(id)",
    "ALTER TABLE games ADD COLUMN IF NOT EXISTS player_5 INTEGER REFERENCES players(id)",

    # A per-(game, participant) summary purpose-built for "show me X's last
    # N games" -- overlaps in content with player_games (which stays the
    # source of truth for bot_name/eliminated/the xor-relaxed player_id),
    # but keyed and shaped for that specific query instead of general use.
    """
    CREATE TABLE IF NOT EXISTS game_results (
        game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
        num_players INTEGER NOT NULL,
        final_money INTEGER NOT NULL,
        final_score INTEGER NOT NULL,
        placement INTEGER NOT NULL,
        PRIMARY KEY (game_id, user_id)
    )
    """,

    # Full rating history -- players.elo (updated in place since it was
    # first added) stays the fast "current rating" read matchmaking.py
    # uses; this is the append-only audit trail alongside it.
    """
    CREATE TABLE IF NOT EXISTS ratings (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
        game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
        old_rating INTEGER NOT NULL,
        new_rating INTEGER NOT NULL,
        rating_change INTEGER NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ratings_user_id ON ratings (user_id)",

    # A pure metadata mirror of the fixed card set in HSConfig.json's
    # game_settings.rules (painting_values/prestige_card_count/
    # disgrace_card_types) -- not deduplicated by (name, type, value): the
    # 3 identical Prestige cards deliberately get 3 distinct card_id rows,
    # since the request was for a stable id per physical card, not per
    # card design. No natural unique key to ON CONFLICT against, so this
    # only seeds once, guarded by "the table is still empty" rather than
    # per-row conflict handling.
    """
    CREATE TABLE IF NOT EXISTS cards (
        card_id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        type TEXT NOT NULL,
        value INTEGER NOT NULL
    )
    """,
    """
    INSERT INTO cards (name, type, value)
    SELECT * FROM (VALUES
        ('Painting (1)', 'Painting', 1), ('Painting (2)', 'Painting', 2),
        ('Painting (3)', 'Painting', 3), ('Painting (4)', 'Painting', 4),
        ('Painting (5)', 'Painting', 5), ('Painting (6)', 'Painting', 6),
        ('Painting (7)', 'Painting', 7), ('Painting (8)', 'Painting', 8),
        ('Painting (9)', 'Painting', 9), ('Painting (10)', 'Painting', 10),
        ('Prestige Card', 'PrestigeCard', 0), ('Prestige Card', 'PrestigeCard', 0),
        ('Prestige Card', 'PrestigeCard', 0),
        ('Faux Pas', 'FauxPas', 0), ('Passe', 'Passe', -5), ('Scandale', 'Scandale', 0)
    ) AS v(name, type, value)
    WHERE NOT EXISTS (SELECT 1 FROM cards)
    """,
    # Added after `cards` already existed in production -- a separate
    # ALTER (not part of the CREATE TABLE above) plus a backfill, same
    # idempotent shape as every other post-hoc column in this file. Values
    # straight from each card class's own constructor (components_module/
    # {painting,prestige_card,disgrace_card}.py) -- Painting/FauxPas/Passe
    # multiply by 1 (i.e. don't scale points), PrestigeCard doubles,
    # Scandale halves.
    "ALTER TABLE cards ADD COLUMN IF NOT EXISTS multiplier NUMERIC",
    """
    UPDATE cards SET multiplier = CASE type
        WHEN 'Painting' THEN 1
        WHEN 'PrestigeCard' THEN 2
        WHEN 'FauxPas' THEN 1
        WHEN 'Passe' THEN 1
        WHEN 'Scandale' THEN 0.5
    END
    WHERE multiplier IS NULL
    """,

    # One-line description per table, for anyone poking around the
    # database directly without this file's own docstrings in front of
    # them. A real unique key (table_name) exists here, so ON CONFLICT
    # DO NOTHING is the natural idempotency guard, unlike `cards` above.
    """
    CREATE TABLE IF NOT EXISTS meta (
        table_name TEXT PRIMARY KEY,
        description TEXT NOT NULL
    )
    """,
    """
    INSERT INTO meta (table_name, description) VALUES
        ('players', 'One row per distinct human username ever seen, plus 3 reserved rows (see bots) representing the shared identity of each bot difficulty tier.'),
        ('games', 'One row per finished (or crashed/aborted -- see is_finished_successfully) game, including each rematch.'),
        ('player_games', 'One row per (game, participant), human or bot -- the join between players/bots and games.'),
        ('player_achievements', 'Unlocked achievement ids per player, Google-linked accounts only.'),
        ('bots', 'Maps the 3 reserved per-difficulty players rows to their difficulty tier.'),
        ('game_results', 'One row per (game, participant) shaped for "show me a player''s last N games".'),
        ('ratings', 'Append-only Elo rating history, one row per rated participant per game.'),
        ('cards', 'Metadata mirror of the fixed status-card set -- one row per physical card, not deduplicated by design/value.'),
        ('game_rounds', 'One row per auction/round played, derived from PlayGame.get_auction_history() at game-end.'),
        ('round_players', 'One row per (round, participant) -- the money/result detail behind each game_rounds row.'),
        ('game_actions', 'One row per bid/pass/fold/quit action across every round, derived from the same auction history.'),
        ('meta', 'This table.')
    ON CONFLICT (table_name) DO NOTHING
    """,

    # The 3 reserved bot-tier identities themselves -- namespaced usernames
    # that can never collide with a real guest/Google username (see
    # guest_username.py's own generator, which never produces a "__"
    # prefix).
    """
    INSERT INTO players (username, display_name) VALUES
        ('__bot_easy__', 'Easy Bot'), ('__bot_medium__', 'Medium Bot'), ('__bot_hard__', 'Hard Bot')
    ON CONFLICT (username) DO NOTHING
    """,
    """
    INSERT INTO bots (player_id, difficulty)
    SELECT p.id, v.difficulty FROM players p
    JOIN (VALUES ('__bot_easy__', 'easy'), ('__bot_medium__', 'medium'), ('__bot_hard__', 'hard'))
        AS v(username, difficulty) ON p.username = v.username
    ON CONFLICT (player_id) DO NOTHING
    """,

    # Derived entirely from PlayGame.get_auction_history() at game-end (see
    # _record_auction_rounds) -- no live-path DB writes anywhere. card_id
    # is picked by matching (type, value) against `cards`; for a repeated
    # card (the 3 identical Prestige cards) this is genuinely arbitrary --
    # the game's own data never tags *which* physical card a given auction
    # drew, so there's no more specific truth available to recover.
    """
    CREATE TABLE IF NOT EXISTS game_rounds (
        id SERIAL PRIMARY KEY,
        game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
        round_number INTEGER NOT NULL,
        card_id INTEGER REFERENCES cards(card_id),
        winner_id INTEGER REFERENCES players(id),
        winning_bid INTEGER,
        started_at TIMESTAMPTZ,
        ended_at TIMESTAMPTZ
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_game_rounds_game_id ON game_rounds (game_id)",

    """
    CREATE TABLE IF NOT EXISTS round_players (
        game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
        round_id INTEGER NOT NULL REFERENCES game_rounds(id) ON DELETE CASCADE,
        player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
        starting_money INTEGER,
        ending_money INTEGER,
        amount_paid INTEGER,
        result TEXT NOT NULL,
        PRIMARY KEY (round_id, player_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_round_players_player_id ON round_players (player_id)",

    # `round` (not `round_number`, matching the request text verbatim) --
    # this table is the raw per-action log; game_rounds already owns the
    # per-auction summary, so "round" here is just enough context to
    # locate which auction a given action belongs to without a FK into
    # game_rounds (rounds are 1-indexed per game, not given their own
    # stable id until game_rounds is built from the same history).
    """
    CREATE TABLE IF NOT EXISTS game_actions (
        id SERIAL PRIMARY KEY,
        game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
        player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
        action_type TEXT NOT NULL,
        amount INTEGER,
        round INTEGER NOT NULL,
        timestamp TIMESTAMPTZ NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_game_actions_game_id ON game_actions (game_id)",
]

_schema_ready = False
_schema_lock = threading.Lock()


def is_configured() -> bool:
    return bool(os.environ.get(_DATABASE_URL_ENV))


# Every one of this module's DB-touching functions used to open a brand
# new psycopg2.connect() and close() it when done -- correct, but a full
# TCP+TLS handshake to a remote Supabase Postgres instance on every
# single call. Measured live: ~400ms just for that handshake, vs ~150ms
# for the same query on a connection that's already open -- roughly 3.5x
# slower than it needs to be, on every request. A small pool of already-
# established connections, reused across calls, removes that handshake
# from the common case entirely. minconn/maxconn are deliberately modest
# (not "as many as this process could use") -- this app still uses
# DATABASE_URL's *direct* connection (not Supabase's own pooler), which
# has its own real ceiling on live connections that a future move to
# their pooler endpoint would raise independently of this.
_MIN_POOL_CONNECTIONS = 1
_MAX_POOL_CONNECTIONS = 10
_pool = None
_pool_lock = threading.Lock()


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                # Imported lazily (same reasoning as psycopg2 itself
                # below): only actually needed once DATABASE_URL is set.
                from psycopg2.pool import ThreadedConnectionPool
                _pool = ThreadedConnectionPool(
                    _MIN_POOL_CONNECTIONS, _MAX_POOL_CONNECTIONS, os.environ[_DATABASE_URL_ENV],
                )
    return _pool


def _connect():
    # Imported lazily so merely importing this module (which web_server.py
    # does unconditionally) never fails for a dev/test setup that hasn't
    # installed psycopg2 — only actually needed once DATABASE_URL is set.
    import psycopg2  # noqa: F401 -- see _get_pool's own lazy psycopg2.pool import
    return _get_pool().getconn()


def _release_connection(conn) -> None:
    """Returns `conn` to the pool instead of actually closing the
    TCP+TLS connection -- every _connect() call site's own `finally`
    block calls this now, in place of the plain conn.close() it used to
    call before this module pooled connections. Every caller already
    runs its queries inside a `with conn, conn.cursor() as cur:` block,
    which commits (or rolls back, on an exception) before this ever
    runs -- so the connection handed back here is always already in a
    clean, reusable state, never mid-transaction.
    """
    pool = _get_pool()
    if conn.closed:
        # Already dead (e.g. the network dropped mid-query) -- tell the
        # pool to discard it outright rather than recycling a connection
        # nothing could actually use.
        pool.putconn(conn, close=True)
    else:
        pool.putconn(conn)


def ensure_schema() -> None:
    """
    Idempotent (CREATE TABLE/INDEX IF NOT EXISTS) — safe to call on every
    process start, which matters because it needs to run identically whether
    the app is started directly (`python3 web_server.py`) or imported fresh
    by each of gunicorn's own worker processes in production.

    Announces its outcome either way (configured-and-ready, configured-but-
    failed, or not configured at all) via a plain print() — deliberately
    not just LoggingManager, which writes to a local log file a hosting
    platform's dashboard typically never surfaces (Render et al. only
    capture stdout/stderr). Without this, a deployment that forgot to set
    DATABASE_URL in its host's dashboard (only a local .env file gets
    picked up automatically — see web_server.py's load_dotenv() call)
    silently recorded nothing, with zero signal anywhere that it was ever
    supposed to. That's exactly what happened in production here: real
    games went unrecorded for days with no error, no warning, nothing that
    would even suggest checking — confirmed by querying the database
    directly and finding only local test data, none of the real games
    actually played on the live site.
    """
    global _schema_ready
    if not is_configured():
        print("game_history: DATABASE_URL not set -- game history logging is DISABLED.")
        return
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        conn = None
        try:
            conn = _connect()
            with conn, conn.cursor() as cur:
                for statement in _SCHEMA_STATEMENTS:
                    cur.execute(statement)
            _schema_ready = True
            print("game_history: DATABASE_URL configured, schema ready -- game history logging is ACTIVE.")
        except Exception as e:  # noqa: BLE001 — a DB hiccup at startup must never crash the app
            print(f"game_history: DATABASE_URL is set but schema setup FAILED, game history logging is DISABLED: {e}")
            LoggingManager.warning(f"game_history.ensure_schema failed, game history disabled: {e}")
        finally:
            if conn is not None:
                _release_connection(conn)


def _upsert_player(cur, username: str, display_name: str) -> tuple:
    """Returns (player_id, google_id, elo) -- the caller (record_finished_game)
    uses google_id to decide whether this player is a real linked account
    achievements/rating changes should actually apply to, vs. a guest
    whose identity resets on every browser clear (see achievements.py's
    own module docstring), and elo as that player's rating *before* this
    game, for elo.compute_elo_deltas."""
    cur.execute(
        """
        INSERT INTO players (username, display_name)
        VALUES (%s, %s)
        ON CONFLICT (username) DO UPDATE SET display_name = EXCLUDED.display_name, last_seen_at = now()
        RETURNING id, google_id, elo
        """,
        (username, display_name),
    )
    return cur.fetchone()


def find_player_by_google_id(google_id: str) -> Optional[dict]:
    """
    Looks up an existing account by Google's stable per-user id (the
    verified ID token's "sub" claim -- see web_server.py's
    /api/auth/google). Returns {"username", "display_name"} if this
    Google account has signed in before, or None otherwise -- covering
    both "no database configured" and "genuinely a first-time sign-in"
    the same way, since the caller treats them identically either way
    (both mean "ask for a username").
    """
    if not is_configured():
        return None
    ensure_schema()
    if not _schema_ready:
        return None
    conn = None
    try:
        conn = _connect()
        with conn, conn.cursor() as cur:
            cur.execute("SELECT username, display_name FROM players WHERE google_id = %s", (google_id,))
            row = cur.fetchone()
            if row is None:
                return None
            return {"username": row[0], "display_name": row[1]}
    except Exception as e:  # noqa: BLE001 -- a DB hiccup here must never crash the sign-in request
        LoggingManager.warning(f"game_history.find_player_by_google_id failed: {e}")
        return None
    finally:
        if conn is not None:
            _release_connection(conn)


def username_is_taken(username: str) -> bool:
    """
    Whether `username` is already claimed by any existing players row
    (Google-linked, or a plain guest who's simply finished a game before
    -- see _upsert_player). Checked before create_google_player commits
    to one, so a first-time Google sign-in gets a clear "pick another
    name" instead of a raw uniqueness-constraint error surfacing as a
    generic failure.

    Fails safe toward "taken": no database, or the check itself erroring,
    both report True. A false "taken" just means the caller has to try a
    different name; a false "available" would let create_google_player's
    own INSERT crash on the exact constraint this was meant to head off.
    """
    if not is_configured():
        return True
    ensure_schema()
    if not _schema_ready:
        return True
    conn = None
    try:
        conn = _connect()
        with conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM players WHERE username = %s", (username,))
            return cur.fetchone() is not None
    except Exception as e:  # noqa: BLE001
        LoggingManager.warning(f"game_history.username_is_taken failed: {e}")
        return True
    finally:
        if conn is not None:
            _release_connection(conn)


_DEFAULT_ELO = 1000


class PlayerSession:
    """
    Per-username, in-memory cache of {player_id, elo} -- today just enough
    to make elo reads instant, with room to grow (a last_seen_at for
    daily-visit tracking, a reward-coin balance, ...) later without new
    plumbing per feature; see this module's own history for the actual
    design discussion this came out of.

    The database is always the source of truth. This is a read-through/
    write-through cache in front of it, never an independent store: a
    session is only ever created from a genuine DB read (get_player_
    session, on a real cache miss), and only ever updated in place from a
    genuine DB write (record_finished_game's own elo update, right after
    it commits). Deliberately *not* tied to any live connection or
    per-tab session -- any ordinary request that already names a
    username (a profile view, a matchmaking join, a game finishing) is a
    fine moment to populate or refresh this. A server restart just wipes
    it; the next request for any given username pays one real DB read
    and the process is right back to being fully warm for that username,
    same as day one -- there's nothing to migrate or persist about the
    cache itself.
    """
    __slots__ = ("player_id", "elo")

    def __init__(self, player_id: int, elo: int):
        self.player_id = player_id
        self.elo = elo


_player_sessions: dict = {}
_player_sessions_lock = threading.Lock()


def _peek_player_session(username: str) -> Optional[PlayerSession]:
    """Cache-only lookup, no DB fallback -- for a caller (get_player_
    profile_stats) that's about to open its own connection regardless and
    wants to skip a redundant id/elo query only on an actual hit, not pay
    for a second connection just to check."""
    with _player_sessions_lock:
        return _player_sessions.get(username)


def _cache_player_session(username: str, player_id: int, elo: int) -> None:
    """Populates the cache from a row a caller just read anyway (never a
    dedicated read of its own) -- setdefault so a slower concurrent
    caller for the same username can't clobber a fresher entry another
    thread already installed."""
    with _player_sessions_lock:
        _player_sessions.setdefault(username, PlayerSession(player_id, elo))


def _refresh_cached_elo(username: str, elo: int) -> None:
    """Called the instant record_finished_game actually commits a new
    elo for `username` -- keeps an already-warm cache entry from ever
    going stale, without waiting on anything to expire. A no-op if this
    process has never cached this username (nothing to refresh; the next
    real read will just populate it fresh from the database, which
    already reflects this write)."""
    with _player_sessions_lock:
        session = _player_sessions.get(username)
        if session is not None:
            session.elo = elo


def get_player_session(username: str) -> Optional[PlayerSession]:
    """The cached (player_id, elo) pair for `username`, doing exactly one
    real DB read the first time this process ever needs it and reusing
    that from then on (see PlayerSession's own docstring for the full
    read-through/write-through contract). None only if the username
    genuinely has no players row yet, there's no database configured, or
    the lookup itself errors.
    """
    session = _peek_player_session(username)
    if session is not None:
        return session
    if not is_configured():
        return None
    ensure_schema()
    if not _schema_ready:
        return None
    conn = None
    try:
        conn = _connect()
        with conn, conn.cursor() as cur:
            cur.execute("SELECT id, elo FROM players WHERE username = %s", (username,))
            row = cur.fetchone()
            if row is None:
                return None
            _cache_player_session(username, row[0], row[1])
            return _peek_player_session(username)
    except Exception as e:  # noqa: BLE001
        LoggingManager.warning(f"game_history.get_player_session failed: {e}")
        return None
    finally:
        if conn is not None:
            _release_connection(conn)


def get_player_elo(username: str) -> int:
    """
    A player's current rating, for matchmaking.py to pair by -- 1000 (the
    same default every new row gets, see the players table's own DEFAULT)
    whenever there's nothing better to go on: no database, the player
    genuinely doesn't have a row yet (a matchmaking ticket can exist before
    any players write has happened, e.g. DATABASE_URL unset locally), or
    the lookup itself errors. Matchmaking degrades gracefully to "everyone
    is equal" in every one of those cases rather than failing to queue at
    all.

    Routed through get_player_session's cache -- a warm entry (this
    username has been read or written before in this process) answers
    with no database call at all.
    """
    session = get_player_session(username)
    return session.elo if session is not None else _DEFAULT_ELO


def get_player_achievements(username: str) -> list:
    """
    Unlocked achievement ids for `username` -- always [] for a guest
    (their players row, if any, has no google_id, so record_finished_game
    never wrote any player_achievements rows for it), no database, or any
    lookup error.
    """
    if not is_configured():
        return []
    ensure_schema()
    if not _schema_ready:
        return []
    conn = None
    try:
        conn = _connect()
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT pa.achievement_id FROM player_achievements pa
                JOIN players p ON p.id = pa.player_id
                WHERE p.username = %s
                """,
                (username,),
            )
            return [row[0] for row in cur.fetchall()]
    except Exception as e:  # noqa: BLE001
        LoggingManager.warning(f"game_history.get_player_achievements failed: {e}")
        return []
    finally:
        if conn is not None:
            _release_connection(conn)


def get_player_profile_stats(username: str) -> Optional[dict]:
    """
    {"games_played", "wins", "win_rate", "avg_placement", "avg_points",
    "avg_money_remaining", "elo", "created_at", "last_played_at"} for any
    known username, guest or Google-linked -- unlike achievements, profile
    stats aren't gated to
    linked accounts, since this is just a factual record of games already
    played under that exact username, nothing tied to a persistent
    identity guarantee. None if the username has no players row at all
    (never played, or no database) -- distinguishes "never played" from
    "played zero games" for the caller, though today's UI renders both as
    an empty profile either way.

    `elo`/`player_id` come from the PlayerSession cache (see its own
    docstring) when this username is already warm in this process --
    zero extra database round trips for that part on a cache hit, on top
    of the original "one connection instead of two" fix this docstring
    used to describe. On a cold cache, this still reads id+elo in the
    very same connection as the two queries below (matching that
    original fix exactly) rather than paying for get_player_session's
    own separate connection, then populates the cache from that same row
    so the *next* call is warm.

    The three averages come from `game_results` (added after
    `player_games`) rather than `player_games` itself -- a player whose
    only games predate that table's introduction has real games_played/
    wins here but None for all three averages, since there's genuinely no
    placement/points/money data recorded for those older rows. The
    caller/UI shows that as an empty stat, not a wrong zero.
    """
    if not is_configured():
        return None
    ensure_schema()
    if not _schema_ready:
        return None
    cached = _peek_player_session(username)
    conn = None
    try:
        conn = _connect()
        with conn, conn.cursor() as cur:
            if cached is not None:
                player_id, elo = cached.player_id, cached.elo
            else:
                cur.execute("SELECT id, elo FROM players WHERE username = %s", (username,))
                row = cur.fetchone()
                if row is None:
                    return None
                player_id, elo = row
                _cache_player_session(username, player_id, elo)
            cur.execute(
                "SELECT count(*), count(*) FILTER (WHERE is_winner) FROM player_games WHERE player_id = %s",
                (player_id,),
            )
            games_played, wins = cur.fetchone()
            win_rate = (wins / games_played) if games_played else 0.0
            cur.execute(
                "SELECT avg(placement), avg(final_score), avg(final_money) "
                "FROM game_results WHERE user_id = %s",
                (player_id,),
            )
            avg_placement, avg_points, avg_money_remaining = cur.fetchone()
            # Read fresh by player_id regardless of the id/elo cache hit
            # above -- last_seen_at changes on every game this player
            # finishes (see _upsert_player), so serving it from that cache
            # (which only ever stores id/elo) would show a stale value for
            # the rest of this process's life once cached once.
            cur.execute("SELECT created_at, last_seen_at FROM players WHERE id = %s", (player_id,))
            created_at, last_seen_at = cur.fetchone()
            return {
                "games_played": games_played, "wins": wins, "win_rate": win_rate, "elo": elo,
                "avg_placement": float(avg_placement) if avg_placement is not None else None,
                "avg_points": float(avg_points) if avg_points is not None else None,
                "avg_money_remaining": float(avg_money_remaining) if avg_money_remaining is not None else None,
                "created_at": created_at.isoformat(),
                # Deliberately named for what this data actually is --
                # last_seen_at only updates when a game finishes (see
                # _upsert_player), not on every page load/heartbeat, so
                # it's "last time this player finished a game", not real
                # presence. The UI should present it that way, not as a
                # live online/offline indicator.
                "last_played_at": last_seen_at.isoformat(),
            }
    except Exception as e:  # noqa: BLE001
        LoggingManager.warning(f"game_history.get_player_profile_stats failed: {e}")
        return None
    finally:
        if conn is not None:
            _release_connection(conn)


def get_global_stats() -> Optional[dict]:
    """
    {"total_games", "total_players"} across the whole site -- shown on the
    home screen ("less accurate is fine" per the request that asked for
    this), so this is deliberately just two plain counts, not anything
    scoped to "active" in any time-windowed sense. total_players excludes
    the 3 reserved bot identities (see the `bots` table) -- a home-page
    visitor asking "how many people have played this" shouldn't have that
    number inflated by internal bookkeeping rows nobody signed up as.
    None (not zero) when unavailable, so the caller can tell "no database"
    apart from "genuinely zero games so far" and choose not to render the
    section at all rather than show a wrong zero.
    """
    if not is_configured():
        return None
    ensure_schema()
    if not _schema_ready:
        return None
    conn = None
    try:
        conn = _connect()
        with conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM games")
            total_games = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM players WHERE id NOT IN (SELECT player_id FROM bots)")
            total_players = cur.fetchone()[0]
            return {"total_games": total_games, "total_players": total_players}
    except Exception as e:  # noqa: BLE001
        LoggingManager.warning(f"game_history.get_global_stats failed: {e}")
        return None
    finally:
        if conn is not None:
            _release_connection(conn)


def get_recent_games(username: str, limit: int = 20, offset: int = 0) -> dict:
    """
    This player's most recent games, newest first -- {"games": [{"game_id",
    "finished_at", "placement", "is_winner", "rating_change", "opponents":
    [{"name", "is_bot", "is_winner"}, ...]}, ...], "has_more": bool}.
    `opponents` includes every seat at the table (not just non-`username`
    ones) since the caller (the "My Games" list, and the home screen's
    Recent Games widget) wants to show who was actually at the table, this
    player included. The top-level `is_winner`/`rating_change` are this
    specific player's own outcome for that game (for the Account screen's
    Recent Activity feed) -- `rating_change` is null for any game they
    weren't rated in (guest account, all-bot practice game, or a game that
    predates the ratings table), never a fake 0. {"games": [],
    "has_more": False} on any failure or no database -- an empty list
    renders as "no games yet", never an error, for what's a purely
    supplementary view.

    `offset`/`has_more` back "My Games"' pagination (see FRONTEND_FIXES.MD --
    10 at a time rather than loading a player's whole history in one
    shot): fetches one extra row beyond `limit` to know whether another
    page exists, without a separate COUNT(*) query.
    """
    empty = {"games": [], "has_more": False}
    if not is_configured():
        return empty
    ensure_schema()
    if not _schema_ready:
        return empty
    conn = None
    try:
        conn = _connect()
        with conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM players WHERE username = %s", (username,))
            row = cur.fetchone()
            if row is None:
                return empty
            player_id = row[0]
            cur.execute(
                """
                SELECT g.id, g.finished_at, gr.placement
                FROM game_results gr
                JOIN games g ON g.id = gr.game_id
                WHERE gr.user_id = %s
                ORDER BY g.finished_at DESC
                LIMIT %s OFFSET %s
                """,
                (player_id, limit + 1, offset),
            )
            rows = cur.fetchall()
            has_more = len(rows) > limit
            rows = rows[:limit]
            games = [{"game_id": r[0], "finished_at": r[1].isoformat(), "placement": r[2]} for r in rows]
            if not games:
                return {"games": [], "has_more": False}
            game_ids = [g["game_id"] for g in games]
            # One query for every participant across all of these games,
            # rather than one query per game -- grouped back below.
            cur.execute(
                """
                SELECT pg.game_id, COALESCE(p.username, pg.bot_name) AS name,
                       p.id IS NULL AS is_bot, pg.is_winner
                FROM player_games pg
                LEFT JOIN players p ON p.id = pg.player_id AND p.id NOT IN (SELECT player_id FROM bots)
                WHERE pg.game_id = ANY(%s)
                """,
                (game_ids,),
            )
            opponents_by_game = {}
            for game_id, name, is_bot, is_winner in cur.fetchall():
                opponents_by_game.setdefault(game_id, []).append(
                    {"name": name, "is_bot": is_bot, "is_winner": is_winner})
            for g in games:
                g["opponents"] = opponents_by_game.get(g["game_id"], [])
            # This player's own outcome + Elo delta for each game, for the
            # Account screen's Recent Activity feed -- one more batched,
            # indexed query (game_id = ANY(...), same pattern as the
            # opponents query above) rather than one query per game.
            # rating_change is null for any game this specific player
            # wasn't rated in (a guest account, an all-bot practice game,
            # or a game that predates the ratings table) -- the caller
            # should render that as "no Elo change shown", never a fake 0.
            cur.execute(
                """
                SELECT pg.game_id, pg.is_winner, r.rating_change
                FROM player_games pg
                LEFT JOIN ratings r ON r.game_id = pg.game_id AND r.user_id = pg.player_id
                WHERE pg.player_id = %s AND pg.game_id = ANY(%s)
                """,
                (player_id, game_ids),
            )
            own_result_by_game = {gid: (is_winner, rating_change) for gid, is_winner, rating_change in cur.fetchall()}
            for g in games:
                is_winner, rating_change = own_result_by_game.get(g["game_id"], (None, None))
                g["is_winner"] = is_winner
                g["rating_change"] = rating_change
            return {"games": games, "has_more": has_more}
    except Exception as e:  # noqa: BLE001
        LoggingManager.warning(f"game_history.get_recent_games failed: {e}")
        return empty
    finally:
        if conn is not None:
            _release_connection(conn)


def get_game_detail(game_id: int) -> Optional[dict]:
    """
    Full per-participant breakdown of one game -- {"game_id",
    "finished_at", "participants": [{"name", "is_bot", "points",
    "money_left", "is_winner", "eliminated", "placement"}, ...]},
    ordered by placement. No access check tied to any particular
    username -- consistent with this app's existing stateless,
    sessionless trust model (e.g. /api/auth/username/change's own
    docstring), and a finished game's results aren't sensitive. None if
    the game_id doesn't exist or on any failure.
    """
    if not is_configured():
        return None
    ensure_schema()
    if not _schema_ready:
        return None
    conn = None
    try:
        conn = _connect()
        with conn, conn.cursor() as cur:
            cur.execute("SELECT finished_at FROM games WHERE id = %s", (game_id,))
            row = cur.fetchone()
            if row is None:
                return None
            finished_at = row[0]
            # Reads pg.placement (this table's own per-SEAT column), not a
            # join through game_results -- that table's PRIMARY KEY
            # (game_id, user_id) intentionally collapses two same-
            # difficulty bot seats sharing one player_id into one row,
            # which used to give both seats the same placement here (a
            # real bug, confirmed live: two bots both showing "#1").
            cur.execute(
                """
                SELECT COALESCE(p.username, pg.bot_name) AS name, p.id IS NULL AS is_bot,
                       pg.points, pg.money_left, pg.is_winner, pg.eliminated, pg.placement
                FROM player_games pg
                LEFT JOIN players p ON p.id = pg.player_id AND p.id NOT IN (SELECT player_id FROM bots)
                WHERE pg.game_id = %s
                ORDER BY pg.placement NULLS LAST
                """,
                (game_id,),
            )
            participants = [
                {"name": name, "is_bot": is_bot, "points": points, "money_left": money_left,
                 "is_winner": is_winner, "eliminated": eliminated, "placement": placement}
                for name, is_bot, points, money_left, is_winner, eliminated, placement in cur.fetchall()
            ]
            return {"game_id": game_id, "finished_at": finished_at.isoformat(), "participants": participants}
    except Exception as e:  # noqa: BLE001
        LoggingManager.warning(f"game_history.get_game_detail failed: {e}")
        return None
    finally:
        if conn is not None:
            _release_connection(conn)


# 20s made sense back when this TTL was the *only* staleness bound --
# now that record_finished_game eagerly invalidates (and re-warms) this
# same cache the instant a rated game actually commits (see
# _warm_leaderboard_cache below), this TTL is just a safety net for
# whatever that path doesn't cover (a transient DB hiccup during the
# eager refresh, or -- if this app ever runs more than the single
# gunicorn worker it does today -- another process's own cache having no
# other way to hear about the write). None of that calls for anything
# close to real-time, so this is set conservatively long rather than
# tuned to feel instant: an out-of-band leaderboard change (a direct DB
# edit, say) surfaces within 10 minutes worst-case, which is already far
# tighter than anyone is realistically checking a leaderboard for
# unexplained staleness.
_LEADERBOARD_CACHE_TTL_SECONDS = 600
_leaderboard_cache: dict = {}  # (limit, offset) -> (cached_at_monotonic, result)
_leaderboard_cache_lock = threading.Lock()


def _fetch_leaderboard_page(limit: int, offset: int) -> Optional[dict]:
    """The actual query behind one leaderboard page -- {"rows", "has_more"}
    -- with no caching of its own. None (not the {"rows": [], ...} shape
    get_leaderboard returns publicly) on any failure/no-database case, so
    a caller like _warm_leaderboard_cache below can tell "genuinely
    nothing to show" apart from "couldn't refresh it, leave the existing
    cached copy alone" -- overwriting a good cached page with an empty
    one on a transient DB hiccup would be strictly worse than just
    leaving it stale a little longer. Split out of get_leaderboard so the
    on-commit eager refresh (see record_finished_game) can populate the
    cache with the exact same query instead of duplicating it."""
    if not is_configured():
        return None
    ensure_schema()
    if not _schema_ready:
        return None
    conn = None
    try:
        conn = _connect()
        with conn, conn.cursor() as cur:
            # limit + 1 to detect whether another page exists, same
            # lookahead trick get_recent_games already uses -- no separate
            # COUNT(*) query needed.
            cur.execute(
                """
                SELECT username, elo, games_played, games_won
                FROM players
                WHERE google_id IS NOT NULL AND id NOT IN (SELECT player_id FROM bots)
                ORDER BY elo DESC, username ASC
                LIMIT %s OFFSET %s
                """,
                (limit + 1, offset),
            )
            all_rows = cur.fetchall()
            has_more = len(all_rows) > limit
            rows = [
                {"username": u, "elo": e, "games_played": gp, "games_won": gw}
                for u, e, gp, gw in all_rows[:limit]
            ]
            return {"rows": rows, "has_more": has_more}
    except Exception as e:  # noqa: BLE001
        LoggingManager.warning(f"game_history._fetch_leaderboard_page failed: {e}")
        return None
    finally:
        if conn is not None:
            _release_connection(conn)


def get_leaderboard(limit: int = 20, offset: int = 0) -> dict:
    """
    Page of players ranked by elo -- {"rows": [{"username", "elo",
    "games_played", "games_won"}, ...], "has_more": bool}. Restricted to
    Google-linked accounts (a guest's elo never moves off the 1000
    default, so including them would just be a meaningless tie-heavy
    list) and explicitly excluding the 3 reserved bot identities (see the
    `bots` table): bots are real rated participants now (see record_
    finished_game's own docstring) so their elo genuinely moves, but it
    must never be shown to players. {"rows": [], "has_more": False} on
    any failure or no database.

    The leaderboard is the exact same data for every single visitor --
    unlike everything else in this module, there's no per-user identity
    involved at all, which makes it the cleanest possible caching target:
    each (limit, offset) page is cached in memory for
    _LEADERBOARD_CACHE_TTL_SECONDS and served with zero database
    round-trips to every request that lands within that window. That TTL
    is just a safety net now, not the real staleness bound, though: the
    moment a rated game actually finishes, record_finished_game clears
    (and, for the default first page, immediately repopulates) this same
    cache -- see _warm_leaderboard_cache's own docstring -- so in
    practice almost nobody ever waits on either the TTL or a real query.
    """
    cache_key = (limit, offset)
    now = time.monotonic()
    with _leaderboard_cache_lock:
        cached = _leaderboard_cache.get(cache_key)
    if cached is not None and now - cached[0] < _LEADERBOARD_CACHE_TTL_SECONDS:
        return cached[1]

    result = _fetch_leaderboard_page(limit, offset)
    if result is None:
        return {"rows": [], "has_more": False}

    with _leaderboard_cache_lock:
        _leaderboard_cache[cache_key] = (now, result)
    return result


def _warm_leaderboard_cache() -> None:
    """Called from record_finished_game's own background write thread the
    instant a rated game commits, right after that same call already
    cleared the cache (see there) -- re-runs the query immediately and
    reinstalls the result, so the clear above doesn't just leave the next
    real visitor to pay for a fresh fetch themselves. Only ever refills
    the (20, 0) key -- the frontend's own default first page (see
    leaderboard.js's PAGE_SIZE) and by far the overwhelming majority of
    real views, since almost nobody actually pages past a small
    leaderboard -- rather than trying to guess every (limit, offset) some
    caller might have had cached; anything else just falls back to a
    normal lazy fetch-on-next-request like before. Runs on a background
    thread already, and this table is tiny, so the extra query here is
    free in every way that matters to an actual player."""
    result = _fetch_leaderboard_page(20, 0)
    if result is None:
        return  # leave the cache clear -- see _fetch_leaderboard_page's own docstring on why not to overwrite with a worse guess
    with _leaderboard_cache_lock:
        _leaderboard_cache[(20, 0)] = (time.monotonic(), result)


def get_rating_history(username: str) -> list:
    """This player's full ratings history, oldest first -- {"old_rating",
    "new_rating", "created_at"} per game they were rated in. Always []
    for a guest/bot (see `ratings`' own write path in record_finished_
    game) or a Google-linked human who's simply never been rated yet."""
    if not is_configured():
        return []
    ensure_schema()
    if not _schema_ready:
        return []
    conn = None
    try:
        conn = _connect()
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.old_rating, r.new_rating, r.created_at
                FROM ratings r
                JOIN players p ON p.id = r.user_id
                WHERE p.username = %s
                ORDER BY r.created_at ASC
                """,
                (username,),
            )
            return [
                {"old_rating": old, "new_rating": new, "created_at": created_at.isoformat()}
                for old, new, created_at in cur.fetchall()
            ]
    except Exception as e:  # noqa: BLE001
        LoggingManager.warning(f"game_history.get_rating_history failed: {e}")
        return []
    finally:
        if conn is not None:
            _release_connection(conn)


def create_google_player(google_id: str, email: str, username: str, display_name: str) -> bool:
    """
    Creates a new players row for a first-time Google sign-in -- see
    web_server.py's /api/auth/google/claim_username, which already
    re-checked username_is_taken moments before calling this. Still
    relies on the table's own UNIQUE constraints (username, google_id,
    email) to catch the same race at the database level (two tabs
    claiming the same username within the same instant) rather than
    trusting that earlier check alone: returns False on any failure,
    including a uniqueness violation, so the caller can just re-prompt
    for a different username without needing to tell "the database is
    down" apart from "someone else just took that name".
    """
    if not is_configured():
        return False
    ensure_schema()
    if not _schema_ready:
        return False
    conn = None
    try:
        conn = _connect()
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO players (username, display_name, google_id, email)
                VALUES (%s, %s, %s, %s)
                """,
                (username, display_name, google_id, email),
            )
        return True
    except Exception as e:  # noqa: BLE001 -- includes a uniqueness-constraint race, deliberately not special-cased
        LoggingManager.warning(f"game_history.create_google_player failed: {e}")
        return False
    finally:
        if conn is not None:
            _release_connection(conn)


def create_guest_player(username: str) -> bool:
    """
    Reserves a username for a guest (no google_id/email) -- see
    web_server.py's /api/auth/guest/claim. Same shape and error handling
    as create_google_player, just without a linked account behind it.
    """
    if not is_configured():
        return False
    ensure_schema()
    if not _schema_ready:
        return False
    conn = None
    try:
        conn = _connect()
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO players (username, display_name) VALUES (%s, %s)",
                (username, username),
            )
        return True
    except Exception as e:  # noqa: BLE001 -- includes a uniqueness-constraint race, deliberately not special-cased
        LoggingManager.warning(f"game_history.create_guest_player failed: {e}")
        return False
    finally:
        if conn is not None:
            _release_connection(conn)


def rename_player(old_username: str, new_username: str) -> bool:
    """
    Renames an existing players row from old_username to new_username
    (guest or Google-linked alike) -- see web_server.py's
    /api/auth/username/change. If old_username was never actually
    reserved (a profile that predates this reservation system, or one
    created while the database was unconfigured), falls back to just
    inserting new_username fresh instead of failing outright. No
    server-side proof of ownership beyond the caller already knowing the
    current username -- consistent with the rest of this app's stateless,
    sessionless identity model.
    """
    if not is_configured():
        return False
    ensure_schema()
    if not _schema_ready:
        return False
    if username_is_taken(new_username):
        return False
    conn = None
    try:
        conn = _connect()
        with conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE players SET username = %s, display_name = %s, last_seen_at = now() WHERE username = %s",
                (new_username, new_username, old_username),
            )
            if cur.rowcount == 0:
                cur.execute(
                    "INSERT INTO players (username, display_name) VALUES (%s, %s)",
                    (new_username, new_username),
                )
        return True
    except Exception as e:  # noqa: BLE001 -- includes a uniqueness-constraint race, deliberately not special-cased
        LoggingManager.warning(f"game_history.rename_player failed: {e}")
        return False
    finally:
        if conn is not None:
            _release_connection(conn)


def _record_auction_rounds(cur, game_id: int, auction_rounds: list, player_id_by_username: dict) -> None:
    """
    Populates game_rounds/round_players/game_actions from `auction_rounds`
    (the same AuctionRecord.to_dict()-shaped list PlayGame.get_auction_
    history() returns) -- called once, at game-end, from data the engine
    already built for BOT_API.md's AUCTION_RESULT feed. No live-path DB
    writes anywhere; this only ever runs after the whole game is over.

    `player_id_by_username`: real per-game username -> player_id (see
    record_finished_game's own docstring on `game_username`) -- an entry
    missing or mapping to None (a bot with no known difficulty) means that
    round/action simply can't be attributed to anyone, so it's skipped
    rather than written with a dangling/NULL player_id.

    Batched into a handful of multi-row statements rather than one query
    per round/action -- confirmed live (see the post-game Elo reveal's own
    investigation) that the naive one-row-at-a-time version made this
    function alone take ~11 seconds for an ordinary short game (100+
    sequential round trips to Supabase, each paying full network latency),
    which blew straight through record_finished_game_async's caller's
    ~5.6s reveal-polling budget even though the actual rating write next
    to it finishes in ~1s. Built by hand (a "(%s,%s,...),(%s,%s,...)"
    placeholder string plus one flat params list) rather than psycopg2.
    extras.execute_values, deliberately: execute_values builds its batched
    SQL via cur.mogrify(), which needs a real psycopg2 cursor (mogrify
    needs the connection's encoding, among other things) -- this repo's
    whole test suite exercises this module against a plain mocked cursor
    (see test_game_history.py's _fake_connection), so a hand-built
    placeholder string that only ever calls the already-mocked cur.execute/
    cur.fetchone keeps this testable without a real database.
    """
    if not auction_rounds:
        return

    # This SELECT stays one-per-round (not batched) -- there are only ever
    # as many rounds as cards in the deck (a couple dozen at most), so this
    # was never the source of the real slowness; the round_players/
    # game_actions writes below, one of which fires per player per round
    # and the other per logged action per round, are what multiply into
    # 100+ round trips for an ordinary game.
    round_rows = []
    for record in auction_rounds:
        card = record["card"]
        # Matched by (type, value), not a stored card identity -- for a
        # repeated card (the 3 identical Prestige cards) any matching row
        # is equally correct, since the game's own data never tags which
        # *specific* physical card a given auction drew.
        cur.execute("SELECT card_id FROM cards WHERE type = %s AND value = %s LIMIT 1",
                    (card["type"], card["value"]))
        row = cur.fetchone()
        card_id = row[0] if row is not None else None

        recipient = record.get("recipient")
        winner_id = player_id_by_username.get(recipient) if recipient else None
        # Null for a disgrace card by design (per the original request) --
        # a disgrace "recipient" is whoever passed first and got stuck
        # with it, not a highest bidder; their own money_spent is always 0
        # (AuctionRecord's own docstring), which isn't a real winning bid
        # to record, just the accident of how a disgrace auction settles.
        winning_bid = None
        if recipient and record.get("auction_type") == "normal":
            winning_bid = (record.get("money_spent") or {}).get(recipient)
        round_rows.append((game_id, record["round_number"], card_id, winner_id, winning_bid,
                            record.get("started_at"), record.get("ended_at")))

    # One INSERT for every round's own row, in one round trip. Postgres
    # returns RETURNING rows in the same order as the VALUES list for a
    # plain multi-row INSERT like this, so reading them back with one
    # fetchone() per row (in input order, below) safely zips round_ids
    # back against auction_rounds by position -- and, unlike fetchall(),
    # matches exactly how _fake_connection's mocked cursor already
    # behaves (a fresh incrementing id per fetchone() call), so no test
    # infrastructure needs to change for this.
    cur.execute(
        f"""
        INSERT INTO game_rounds (game_id, round_number, card_id, winner_id, winning_bid, started_at, ended_at)
        VALUES {_values_placeholder(len(round_rows), 7)}
        RETURNING id
        """,
        _flatten(round_rows),
    )
    round_ids = [cur.fetchone()[0] for _ in round_rows]

    round_player_rows = []
    action_rows = []
    for record, round_id in zip(auction_rounds, round_ids):
        recipient = record.get("recipient")
        starting_money = record.get("starting_money") or {}
        ending_money = record.get("ending_money") or {}
        money_spent = record.get("money_spent") or {}
        for username in set(starting_money) | set(ending_money) | set(money_spent):
            player_id = player_id_by_username.get(username)
            if player_id is None:
                continue
            round_player_rows.append((game_id, round_id, player_id, starting_money.get(username),
                                       ending_money.get(username), money_spent.get(username),
                                       "won" if username == recipient else "lost"))

        for event in record.get("events") or []:
            player_id = player_id_by_username.get(event["player"])
            if player_id is None:
                continue
            action_rows.append((game_id, player_id, event["action"].upper(), event.get("amount"),
                                 record["round_number"], event.get("timestamp")))

    if round_player_rows:
        # Two bot seats of the same difficulty share one player_id (the
        # bots table's whole design -- see record_finished_game's own
        # docstring), so this table's (round_id, player_id) primary key
        # can collide within a single round -- confirmed live: any real
        # game with 2+ same-difficulty bots crashed this whole function
        # (and rolled back the games/player_games row along with it,
        # since it's all one transaction) the instant a round included
        # both seats. DO NOTHING keeps whichever seat's row landed first
        # within this batch; losing the second seat's per-round money
        # snapshot here is far cheaper than losing the entire game's
        # history.
        cur.execute(
            f"""
            INSERT INTO round_players
                (game_id, round_id, player_id, starting_money, ending_money, amount_paid, result)
            VALUES {_values_placeholder(len(round_player_rows), 7)}
            ON CONFLICT (round_id, player_id) DO NOTHING
            """,
            _flatten(round_player_rows),
        )

    if action_rows:
        cur.execute(
            f"""
            INSERT INTO game_actions (game_id, player_id, action_type, amount, round, timestamp)
            VALUES {_values_placeholder(len(action_rows), 6)}
            """,
            _flatten(action_rows),
        )


def _values_placeholder(num_rows: int, num_columns: int) -> str:
    """"(%s,%s,...),(%s,%s,...)" -- num_rows groups of num_columns
    placeholders each, for a hand-batched multi-row INSERT ... VALUES.
    See _record_auction_rounds's own docstring on why this is built by
    hand instead of psycopg2.extras.execute_values."""
    row = "(" + ",".join(["%s"] * num_columns) + ")"
    return ",".join([row] * num_rows)


def _flatten(rows: list) -> list:
    """[(a, b), (c, d)] -> [a, b, c, d] -- the flat params list a
    _values_placeholder-built query's positional %s placeholders expect."""
    return [value for row in rows for value in row]


def record_finished_game(*, room_code: str, seats: int, bot_mix: list,
                          started_at: datetime.datetime, finished_at: datetime.datetime,
                          participants: list, achievement_unlocks: Optional[dict] = None,
                          host_username: Optional[str] = None, time_control: Optional[int] = None,
                          is_finished_successfully: bool = True,
                          auction_rounds: Optional[list] = None) -> dict:
    """
    Returns {rating_key: {"old_rating", "new_rating", "rating_change"}} for
    every participant whose rating actually changed this game (a human's
    rating_key is their real username -- see game_username's own docstring
    below) -- empty on any early-return/no-op/failure path. Used by
    web_server.py to surface the finished screen's post-game Elo reveal
    without the caller needing its own copy of the Elo math or a second
    DB round-trip; every other side effect below still happens regardless
    of whether the caller reads this.

    `participants`: one dict per seat, already reduced to exactly what this
    module needs — see web_server.py's call site for how it's built from
    PlayGame.final_standings/GameRoom.players. Keeping that translation in
    the caller (rather than importing NetworkPlayer/PlayGame here) is what
    lets this stay a plain, independently-testable persistence layer with no
    dependency on the game engine's own classes. Empty for a game that
    crashed before final_standings ever existed (see
    is_finished_successfully below) — every per-participant write below is
    simply skipped in that case, leaving just the bare `games` row.

    Each participant: {"is_bot": bool, "username": str | None, "name": str,
                        "points": int, "money_left": int, "is_winner": bool,
                        "eliminated": bool, "difficulty": str | None,
                        "game_username": str}
    `game_username` is always the real per-game username, bot or human
    (unlike `username`, which stays None for a bot) -- used only inside
    this function to attribute `auction_rounds` events/recipients (which
    always name the real username) back to a resolved player_id; never
    itself written anywhere. Optional for backward compatibility: a caller
    that omits it (or omits `auction_rounds` entirely) just can't have its
    bot seats' actions/rounds attributed -- everything else is unaffected.
    Exactly one of (is_bot False + username set) or (is_bot True + username
    None) holds per participant. `difficulty` ("easy"/"medium"/"hard") is
    only meaningful when is_bot is True — see the `bots` table: a bot
    participant with a known difficulty gets a real player_id (the shared
    identity for that whole difficulty tier) alongside its existing
    bot_name flavor label; omitted or unrecognized, it falls back to the
    original bot_name-only behavior (player_id stays NULL), so older
    callers/tests that don't pass it are unaffected.

    `achievement_unlocks`: {username: {achievement_id, ...}} from
    achievements.detect_per_game_achievements — everything earned in this
    one game, for every human participant regardless of account type.
    Only actually written for a participant whose players row has a
    google_id (see achievements.py's own module docstring for why guests
    are excluded), and only after also checking WIN_COUNT_MILESTONES
    against that player's total win count, which by this point already
    includes the row this same call just inserted.

    Elo ratings update the same way, for the same reason: only players
    with a google_id get a rating change (see elo.py's own docstring) --
    everyone else's `elo` column simply sits at whatever it already was
    (1000 by default), which matchmaking.py still reads and pairs by as
    normal, just never diverging for an identity with nothing persistent
    behind it. Every rating change is also appended to `ratings` (the
    audit trail `elo` alone doesn't keep).

    `games_played`/`games_won` on `players`, by contrast, are NOT gated to
    google_id -- they increment for every human participant, matching
    get_player_profile_stats' own (already ungated) COUNT query, which
    these columns exist to duplicate as a cheap denormalization rather
    than replace.

    `host_username`: whoever created the room (see web_server.py's
    GameRoom.host_username), resolved to a player_id the same way any
    other participant would be -- independent of whether they're also
    seated (the common case) or even human.

    `is_finished_successfully`: False only for the crash/abort path (see
    web_server.py's run_game) -- True is the default because every
    pre-existing call site is a clean finish.

    `auction_rounds`: the same list PlayGame.get_auction_history() returns
    (each entry AuctionRecord.to_dict()-shaped) -- populates game_rounds/
    round_players/game_actions. None/empty (the default, for any caller
    that predates this or a crashed game with no history) just skips that
    entirely; nothing else here depends on it.
    """
    elo_changes: dict = {}
    if not is_configured():
        return elo_changes
    ensure_schema()
    if not _schema_ready:
        return elo_changes  # schema setup already failed and logged a warning above
    achievement_unlocks = achievement_unlocks or {}
    conn = None
    try:
        conn = _connect()
        with conn, conn.cursor() as cur:
            host_player_id = None
            if host_username:
                host_player_id, _, _ = _upsert_player(cur, host_username, host_username)

            # `bot_mix` (the parameter) is still accepted for backward
            # compatibility with existing callers/tests, but deliberately
            # not written here any more -- superseded by player_1..
            # player_5 + the bots table, which carry the same information
            # (and more) per-seat rather than as one game-wide list. The
            # column itself stays (old rows still have it; new rows just
            # get the schema's own DEFAULT '[]').
            cur.execute(
                """
                INSERT INTO games (room_code, seats, started_at, finished_at,
                                    host_player_id, time_control, is_finished_successfully)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (room_code, seats, started_at, finished_at,
                 host_player_id, time_control, is_finished_successfully),
            )
            game_id = cur.fetchone()[0]

            # Mirrors PlayGame's own win rule (gameplay.py's
            # final_standings/winners computation), NOT a plain points
            # sort: a player eliminated for having the least money loses
            # outright regardless of points (they can easily have the
            # *highest* points and still lose this way -- confirmed live),
            # so eliminated participants always rank behind everyone else
            # here. Within each tier, higher points ranks better. Ties
            # (including multiple simultaneous winners) are broken by
            # participants' original order -- a documented simplification,
            # since the game itself doesn't produce a single strict
            # ranking on a genuine tie.
            def _placement_tier(participant: dict) -> int:
                if participant["is_winner"]:
                    return 0
                if participant["eliminated"]:
                    return 2
                return 1

            placement_by_index = {
                original_index: rank + 1
                for rank, original_index in enumerate(
                    sorted(range(len(participants)),
                           key=lambda i: (_placement_tier(participants[i]), -participants[i]["points"]))
                )
            }

            rated_standings = []  # {"username", "points", "rating"} -- see elo.compute_elo_deltas
            rated_player_ids = {}  # username -> player_id, for writing the delta back below
            # player_id (not rating_key) -- every rating_key sharing a
            # player_id (see the bot-seat-sharing comment below) reads the
            # exact same elo at this point anyway, so one dict keyed by the
            # real identity is enough for the ratings audit row.
            elo_before_by_player_id = {}
            seat_player_ids = []  # in participant order -- becomes player_1..player_5 below
            winner_player_id = None  # first winner only -- see this function's own docstring on ties
            player_id_by_game_username = {}  # real per-game username (bot or human) -> player_id
            # Real, stable DB usernames only (never a bot's per-game display
            # name, which isn't a stable identity -- see the PlayerSession
            # cache refresh below, right before this function returns).
            real_username_by_player_id = {}
            for index, p in enumerate(participants):
                player_id = None
                google_id = None
                elo_before = None
                bot_name = None
                if p["is_bot"]:
                    bot_name = p["name"]
                    difficulty = p.get("difficulty")
                    if difficulty:
                        # Also fetches the bot's *current* elo (unlike the
                        # player_id-only lookup this used to be) -- bots
                        # are now real rated participants (see the elo
                        # gating below), so this needs elo_before the same
                        # way _upsert_player already gives a human.
                        cur.execute(
                            "SELECT b.player_id, p.elo FROM bots b JOIN players p ON p.id = b.player_id "
                            "WHERE b.difficulty = %s",
                            (difficulty,),
                        )
                        row = cur.fetchone()
                        if row is not None:
                            player_id, elo_before = row
                else:
                    player_id, google_id, elo_before = _upsert_player(cur, p["username"], p["name"])
                    real_username_by_player_id[player_id] = p["username"]
                    cur.execute(
                        "UPDATE players SET games_played = games_played + 1, games_won = games_won + %s WHERE id = %s",
                        (1 if p["is_winner"] else 0, player_id),
                    )
                seat_player_ids.append(player_id)
                if p.get("game_username"):
                    player_id_by_game_username[p["game_username"]] = player_id

                cur.execute(
                    """
                    INSERT INTO player_games
                        (game_id, player_id, bot_name, points, money_left, is_winner, eliminated, placement)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (game_id, player_id, bot_name, p["points"], p["money_left"],
                     p["is_winner"], p["eliminated"], placement_by_index[index]),
                )

                if player_id is not None:
                    if p["is_winner"] and winner_player_id is None:
                        winner_player_id = player_id
                    cur.execute(
                        """
                        INSERT INTO game_results (game_id, user_id, num_players, final_money, final_score, placement)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (game_id, user_id) DO NOTHING
                        """,
                        (game_id, player_id, len(participants), p["money_left"], p["points"],
                         placement_by_index[index]),
                    )

                # Elo eligibility now includes bots with a known difficulty
                # (they have a real, evolving rating of their own -- see
                # this function's own docstring) alongside Google-linked
                # humans; a bot's real per-game username (never a stable
                # DB `username`, see `game_username`'s own doc) is what
                # keys rated_standings here, since two bots in the same
                # game would otherwise collide on `None`.
                is_rated = player_id is not None and (google_id is not None or p["is_bot"])
                if is_rated:
                    rating_key = p.get("game_username") or p["username"]
                    rated_standings.append({"username": rating_key, "points": p["points"], "rating": elo_before})
                    rated_player_ids[rating_key] = player_id
                    elo_before_by_player_id[player_id] = elo_before

                # Achievements stay strictly human-only -- a bot must never
                # unlock one, regardless of the elo change above.
                if player_id is not None and google_id is not None:
                    ids_to_unlock = set(achievement_unlocks.get(p["username"]) or ())
                    if p["is_winner"]:
                        cur.execute("SELECT count(*) FROM player_games WHERE player_id = %s AND is_winner",
                                    (player_id,))
                        win_count = cur.fetchone()[0]
                        for threshold, achievement_id in WIN_COUNT_MILESTONES.items():
                            if win_count >= threshold:
                                ids_to_unlock.add(achievement_id)
                    for achievement_id in ids_to_unlock:
                        cur.execute(
                            """
                            INSERT INTO player_achievements (player_id, achievement_id)
                            VALUES (%s, %s)
                            ON CONFLICT (player_id, achievement_id) DO NOTHING
                            """,
                            (player_id, achievement_id),
                        )

            # Two or more bot seats of the same difficulty share one
            # player_id (the bots table's whole design), so rated_standings
            # can carry multiple distinct rating_keys that all resolve to
            # the same real identity here. compute_elo_deltas has no idea
            # they're the same player -- it computes each seat's delta as
            # if it were a genuinely separate participant, which would
            # double- (or triple-, ...) count that identity's rating swing
            # if applied as-is: confirmed live, a game with 2 shared-
            # identity bot seats moved the bot's elo by the SUM of both
            # seats' independent deltas, and wrote two ratings rows that
            # both claimed the same stale "before" value despite the first
            # update having already landed. Grouped by player_id and
            # averaged (not summed) below so one identity occupying N
            # seats in one game gets exactly one, roughly-N-times-smaller-
            # per-seat, correctly-audited rating change -- not N times the
            # swing a single seat would have earned alone.
            deltas_by_player_id: dict[int, list[int]] = {}
            rating_keys_by_player_id: dict[int, list[str]] = {}
            for rating_key, delta in elo.compute_elo_deltas(rated_standings).items():
                player_id = rated_player_ids[rating_key]
                deltas_by_player_id.setdefault(player_id, []).append(delta)
                rating_keys_by_player_id.setdefault(player_id, []).append(rating_key)
            # (username, new_rating) pairs to refresh in the PlayerSession
            # cache -- collected here but deliberately not applied until
            # after this whole transaction actually commits (see below):
            # refreshing the in-memory cache mid-transaction would leave it
            # showing a change that a later failure in this same block (e.g.
            # _record_auction_rounds) could still roll back, which is
            # exactly the kind of cache/database disagreement this cache
            # can't afford to have. Bots are skipped -- their "username" is
            # an ephemeral per-game display name, not a stable identity
            # anything would ever look up again.
            cache_refreshes = []
            for player_id, seat_deltas in deltas_by_player_id.items():
                combined_delta = round(sum(seat_deltas) / len(seat_deltas))
                if combined_delta:
                    old_rating = elo_before_by_player_id[player_id]
                    new_rating = old_rating + combined_delta
                    cur.execute("UPDATE players SET elo = elo + %s WHERE id = %s", (combined_delta, player_id))
                    cur.execute(
                        """
                        INSERT INTO ratings (user_id, game_id, old_rating, new_rating, rating_change)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (player_id, game_id, old_rating, new_rating, combined_delta),
                    )
                    real_username = real_username_by_player_id.get(player_id)
                    if real_username is not None:
                        cache_refreshes.append((real_username, new_rating))
                    # Keyed by rating_key (a human's real username, see
                    # game_username's own docstring) so the caller
                    # (web_server.py's post-game reveal) can look a
                    # specific player up directly by the same username it
                    # already knows client-side -- see this function's own
                    # docstring for the full return-value shape.
                    for rating_key in rating_keys_by_player_id[player_id]:
                        elo_changes[rating_key] = {
                            "old_rating": old_rating, "new_rating": new_rating, "rating_change": combined_delta,
                        }

            seat_columns = (seat_player_ids + [None] * 5)[:5]
            cur.execute(
                """
                UPDATE games SET winner_id = %s, player_1 = %s, player_2 = %s,
                                  player_3 = %s, player_4 = %s, player_5 = %s
                WHERE id = %s
                """,
                (winner_player_id, *seat_columns, game_id),
            )

            if auction_rounds:
                _record_auction_rounds(cur, game_id, auction_rounds, player_id_by_game_username)
    except Exception as e:  # noqa: BLE001 — see record_finished_game_async's docstring
        LoggingManager.warning(f"game_history.record_finished_game failed: {e}")
        return {}
    finally:
        if conn is not None:
            _release_connection(conn)
    # Only reached once the transaction above has actually committed
    # (the `with conn` block's own clean exit) -- see cache_refreshes'
    # own comment on why this can't happen any earlier.
    for username, new_rating in cache_refreshes:
        _refresh_cached_elo(username, new_rating)
    if cache_refreshes:
        # A rated game just changed someone's standing -- don't make
        # everyone wait out the rest of the leaderboard's TTL to see it.
        # Clearing (not just the default page) covers every paginated
        # offset someone might have cached, since a rank shift can move
        # players across page boundaries too. _warm_leaderboard_cache
        # then immediately re-fetches just the default first page, so
        # the very next real visitor -- the overwhelmingly common case --
        # gets a warm cache hit instead of paying for the query this
        # clear would otherwise have pushed onto them. Runs on this same
        # background write thread (see record_finished_game_async), so
        # it costs a real player nothing either way.
        with _leaderboard_cache_lock:
            _leaderboard_cache.clear()
        _warm_leaderboard_cache()
    return elo_changes


def record_finished_game_async(on_complete: Optional[Callable[[dict], None]] = None, **kwargs) -> None:
    """
    Fire-and-forget wrapper — the only entry point web_server.py's actual
    game-end path calls, since it must never wait on (or fail because of) a
    slow/unreachable database. A finished game already has everything it'll
    ever have by this point, so nothing time-sensitive is being raced here:
    worst case, this game's row shows up a moment late or not at all
    (logged), while every player-facing message still goes out on schedule.

    `on_complete`, if given, is called from the background thread with
    record_finished_game's own return value (or `{}` if there's no
    database configured at all, matching what record_finished_game itself
    returns for every other no-op path) once the write actually finishes
    -- a notification, not something this function waits on. Lets a caller
    (the post-game Elo reveal) find out the real Elo change without
    duplicating the Elo math or adding a second DB round-trip of its own.
    """
    if not is_configured():
        if on_complete:
            on_complete({})
        return

    def _run():
        result = record_finished_game(**kwargs)
        if on_complete:
            on_complete(result)

    threading.Thread(target=_run, daemon=True, name="GameHistoryWrite").start()
