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
from typing import Optional

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


def _connect():
    # Imported lazily so merely importing this module (which web_server.py
    # does unconditionally) never fails for a dev/test setup that hasn't
    # installed psycopg2 — only actually needed once DATABASE_URL is set.
    import psycopg2
    return psycopg2.connect(os.environ[_DATABASE_URL_ENV])


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
                conn.close()


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
            conn.close()


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
            conn.close()


_DEFAULT_ELO = 1000


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
    """
    if not is_configured():
        return _DEFAULT_ELO
    ensure_schema()
    if not _schema_ready:
        return _DEFAULT_ELO
    conn = None
    try:
        conn = _connect()
        with conn, conn.cursor() as cur:
            cur.execute("SELECT elo FROM players WHERE username = %s", (username,))
            row = cur.fetchone()
            return row[0] if row is not None else _DEFAULT_ELO
    except Exception as e:  # noqa: BLE001
        LoggingManager.warning(f"game_history.get_player_elo failed: {e}")
        return _DEFAULT_ELO
    finally:
        if conn is not None:
            conn.close()


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
            conn.close()


def get_player_profile_stats(username: str) -> Optional[dict]:
    """
    {"games_played", "wins", "win_rate", "avg_placement", "avg_points",
    "avg_money_remaining"} for any known username, guest or Google-linked
    -- unlike achievements, profile stats aren't gated to linked accounts,
    since this is just a factual record of games already played under
    that exact username, nothing tied to a persistent identity guarantee.
    None if the username has no players row at all (never played, or no
    database) -- distinguishes "never played" from "played zero games"
    for the caller, though today's UI renders both as an empty profile
    either way.

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
    conn = None
    try:
        conn = _connect()
        with conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM players WHERE username = %s", (username,))
            row = cur.fetchone()
            if row is None:
                return None
            player_id = row[0]
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
            return {
                "games_played": games_played, "wins": wins, "win_rate": win_rate,
                "avg_placement": float(avg_placement) if avg_placement is not None else None,
                "avg_points": float(avg_points) if avg_points is not None else None,
                "avg_money_remaining": float(avg_money_remaining) if avg_money_remaining is not None else None,
            }
    except Exception as e:  # noqa: BLE001
        LoggingManager.warning(f"game_history.get_player_profile_stats failed: {e}")
        return None
    finally:
        if conn is not None:
            conn.close()


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
            conn.close()


def get_recent_games(username: str, limit: int = 20) -> list:
    """
    This player's most recent games, newest first -- {"game_id",
    "finished_at", "placement", "opponents": [{"name", "is_bot",
    "is_winner"}, ...]} per game. `opponents` includes every seat at the
    table (not just non-`username` ones) since the caller (the "My Games"
    list, and the home screen's Recent Games widget) wants to show who
    was actually at the table, this player included. [] on any failure or
    no database -- an empty list renders as "no games yet", never an
    error, for what's a purely supplementary view.
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
            cur.execute("SELECT id FROM players WHERE username = %s", (username,))
            row = cur.fetchone()
            if row is None:
                return []
            player_id = row[0]
            cur.execute(
                """
                SELECT g.id, g.finished_at, gr.placement
                FROM game_results gr
                JOIN games g ON g.id = gr.game_id
                WHERE gr.user_id = %s
                ORDER BY g.finished_at DESC
                LIMIT %s
                """,
                (player_id, limit),
            )
            games = [{"game_id": r[0], "finished_at": r[1].isoformat(), "placement": r[2]} for r in cur.fetchall()]
            if not games:
                return []
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
            return games
    except Exception as e:  # noqa: BLE001
        LoggingManager.warning(f"game_history.get_recent_games failed: {e}")
        return []
    finally:
        if conn is not None:
            conn.close()


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
            cur.execute(
                """
                SELECT COALESCE(p.username, pg.bot_name) AS name, p.id IS NULL AS is_bot,
                       pg.points, pg.money_left, pg.is_winner, pg.eliminated, gr.placement
                FROM player_games pg
                LEFT JOIN players p ON p.id = pg.player_id AND p.id NOT IN (SELECT player_id FROM bots)
                LEFT JOIN game_results gr ON gr.game_id = pg.game_id AND gr.user_id = pg.player_id
                WHERE pg.game_id = %s
                ORDER BY gr.placement NULLS LAST
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
            conn.close()


def get_leaderboard(limit: int = 50) -> list:
    """
    Top `limit` players by elo -- {"username", "elo", "games_played",
    "games_won"} -- restricted to Google-linked accounts (a guest's elo
    never moves off the 1000 default, so including them would just be a
    meaningless tie-heavy list) and explicitly excluding the 3 reserved
    bot identities (see the `bots` table): bots are real rated
    participants now (see record_finished_game's own docstring) so their
    elo genuinely moves, but it must never be shown to players. []
    on any failure or no database.
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
                SELECT username, elo, games_played, games_won
                FROM players
                WHERE google_id IS NOT NULL AND id NOT IN (SELECT player_id FROM bots)
                ORDER BY elo DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [
                {"username": username, "elo": elo, "games_played": games_played, "games_won": games_won}
                for username, elo, games_played, games_won in cur.fetchall()
            ]
    except Exception as e:  # noqa: BLE001
        LoggingManager.warning(f"game_history.get_leaderboard failed: {e}")
        return []
    finally:
        if conn is not None:
            conn.close()


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
            conn.close()


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
            conn.close()


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
            conn.close()


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
            conn.close()


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
    """
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

        cur.execute(
            """
            INSERT INTO game_rounds (game_id, round_number, card_id, winner_id, winning_bid, started_at, ended_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (game_id, record["round_number"], card_id, winner_id, winning_bid,
             record.get("started_at"), record.get("ended_at")),
        )
        round_id = cur.fetchone()[0]

        starting_money = record.get("starting_money") or {}
        ending_money = record.get("ending_money") or {}
        money_spent = record.get("money_spent") or {}
        for username in set(starting_money) | set(ending_money) | set(money_spent):
            player_id = player_id_by_username.get(username)
            if player_id is None:
                continue
            cur.execute(
                """
                INSERT INTO round_players
                    (game_id, round_id, player_id, starting_money, ending_money, amount_paid, result)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (game_id, round_id, player_id, starting_money.get(username), ending_money.get(username),
                 money_spent.get(username), "won" if username == recipient else "lost"),
            )

        for event in record.get("events") or []:
            player_id = player_id_by_username.get(event["player"])
            if player_id is None:
                continue
            cur.execute(
                """
                INSERT INTO game_actions (game_id, player_id, action_type, amount, round, timestamp)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (game_id, player_id, event["action"].upper(), event.get("amount"),
                 record["round_number"], event.get("timestamp")),
            )


def record_finished_game(*, room_code: str, seats: int, bot_mix: list,
                          started_at: datetime.datetime, finished_at: datetime.datetime,
                          participants: list, achievement_unlocks: Optional[dict] = None,
                          host_username: Optional[str] = None, time_control: Optional[int] = None,
                          is_finished_successfully: bool = True,
                          auction_rounds: Optional[list] = None) -> None:
    """
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
    if not is_configured():
        return
    ensure_schema()
    if not _schema_ready:
        return  # schema setup already failed and logged a warning above
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
            elo_before_by_username = {}  # username -> rating before this game, for the ratings audit row
            seat_player_ids = []  # in participant order -- becomes player_1..player_5 below
            winner_player_id = None  # first winner only -- see this function's own docstring on ties
            player_id_by_game_username = {}  # real per-game username (bot or human) -> player_id
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
                        (game_id, player_id, bot_name, points, money_left, is_winner, eliminated)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (game_id, player_id, bot_name, p["points"], p["money_left"],
                     p["is_winner"], p["eliminated"]),
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
                    elo_before_by_username[rating_key] = elo_before

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

            for username, delta in elo.compute_elo_deltas(rated_standings).items():
                if delta:
                    player_id = rated_player_ids[username]
                    old_rating = elo_before_by_username[username]
                    cur.execute("UPDATE players SET elo = elo + %s WHERE id = %s", (delta, player_id))
                    cur.execute(
                        """
                        INSERT INTO ratings (user_id, game_id, old_rating, new_rating, rating_change)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (player_id, game_id, old_rating, old_rating + delta, delta),
                    )

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
    finally:
        if conn is not None:
            conn.close()


def record_finished_game_async(**kwargs) -> None:
    """
    Fire-and-forget wrapper — the only entry point web_server.py's actual
    game-end path calls, since it must never wait on (or fail because of) a
    slow/unreachable database. A finished game already has everything it'll
    ever have by this point, so nothing time-sensitive is being raced here:
    worst case, this game's row shows up a moment late or not at all
    (logged), while every player-facing message still goes out on schedule.
    """
    if not is_configured():
        return
    threading.Thread(target=record_finished_game, kwargs=kwargs, daemon=True,
                      name="GameHistoryWrite").start()
