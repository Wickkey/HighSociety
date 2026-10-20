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
  players       One row per distinct human username ever seen, plus one
                reserved row per named bot (see `bots` and
                common/bot_roster.py) and 3 legacy per-difficulty bot rows. `username` is the identity
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
                seat name (bot_name) same as always, and — when it's one of
                the named roster bots — also by that bot's own player_id
                from `bots`. Games from before named identities point at
                the legacy per-difficulty player_id instead.
"""
import datetime
import hashlib
import json
import os
import secrets
import threading
import time
from typing import Callable, Optional

from highsociety.code.common import bot_roster, elo
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

    # One row per bot identity, pointing at a dedicated players row (see
    # the seed INSERTs below). Originally one row per difficulty tier, when
    # bot names were random flavor; now each named bot (bot_key, added
    # below) has its own row with a fixed difficulty, and the 3 tier rows
    # stay only as the identity older games were recorded under.
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
        ('bots', 'One row per bot identity: each named bot (bot_key, fixed difficulty), plus the 3 legacy per-difficulty rows (bot_key NULL) older games were recorded under.'),
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

    # The named bots (see common/bot_roster.py) -- one players row each, so
    # "Ziggy bot" has its own Elo/stats/profile across every game, and two
    # same-difficulty bots at one table are two different players. bot_key
    # is NULL for the 3 legacy per-difficulty rows above, which no new game
    # is recorded under any more: they're kept as-is for the history that
    # predates named identities. DO UPDATE (not NOTHING) on the bots row so
    # moving a name to another difficulty in the roster takes effect on the
    # next startup.
    "ALTER TABLE bots ADD COLUMN IF NOT EXISTS bot_key TEXT UNIQUE",
    "INSERT INTO players (username, display_name) VALUES "
    + ", ".join(f"('{bot_roster.roster_username(b)}', '{b.name}')" for b in bot_roster.BOT_ROSTER)
    + " ON CONFLICT (username) DO UPDATE SET display_name = EXCLUDED.display_name",
    "INSERT INTO bots (player_id, difficulty, bot_key) "
    "SELECT p.id, v.difficulty, v.bot_key FROM players p JOIN (VALUES "
    + ", ".join(f"('{bot_roster.roster_username(b)}', '{b.difficulty}', '{b.key}')" for b in bot_roster.BOT_ROSTER)
    + ") AS v(username, difficulty, bot_key) ON p.username = v.username "
    "ON CONFLICT (player_id) DO UPDATE SET difficulty = EXCLUDED.difficulty, bot_key = EXCLUDED.bot_key",

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

    # What makes a finished game replayable from these tables alone (see
    # recording/game_replay.py): the engine's seed (deck order and starting
    # player), each seat's turn order, and game_actions as the complete,
    # ordered action log -- every bid with the money cards behind it, every
    # pass/quit (and whether a timeout or disconnect forced it), every Faux
    # Pas discard, and every disconnect/reconnect. `seq` is the action's
    # position in the game; `think_ms` how long the player took to decide.
    # player_id is nullable now so a seat with no players row (a bot
    # without a roster identity) still has its actions recorded, by seat.
    "ALTER TABLE games ADD COLUMN IF NOT EXISTS seed BIGINT",
    # Final points aren't always whole -- a Scandale halves them (1.5) --
    # but these two columns were INTEGER, so Postgres silently rounded
    # every halved score (caught by replaying a stored game). Widened once;
    # the guard keeps every later startup from re-taking the table lock.
    """
    DO $$ BEGIN
        IF (SELECT data_type FROM information_schema.columns
            WHERE table_name = 'player_games' AND column_name = 'points') = 'integer' THEN
            ALTER TABLE player_games ALTER COLUMN points TYPE DOUBLE PRECISION;
        END IF;
        IF (SELECT data_type FROM information_schema.columns
            WHERE table_name = 'game_results' AND column_name = 'final_score') = 'integer' THEN
            ALTER TABLE game_results ALTER COLUMN final_score TYPE DOUBLE PRECISION;
        END IF;
    END $$
    """,
    "ALTER TABLE player_games ADD COLUMN IF NOT EXISTS seat INTEGER",
    "ALTER TABLE game_rounds ADD COLUMN IF NOT EXISTS winner_seat INTEGER",
    "ALTER TABLE game_actions ADD COLUMN IF NOT EXISTS seq INTEGER",
    "ALTER TABLE game_actions ADD COLUMN IF NOT EXISTS seat INTEGER",
    "ALTER TABLE game_actions ADD COLUMN IF NOT EXISTS cards INTEGER[]",
    "ALTER TABLE game_actions ADD COLUMN IF NOT EXISTS think_ms INTEGER",
    "ALTER TABLE game_actions ADD COLUMN IF NOT EXISTS reason TEXT",
    "ALTER TABLE game_actions ALTER COLUMN player_id DROP NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_game_actions_game_seq ON game_actions (game_id, seq)",
    """
    UPDATE meta SET description = 'Every action of a game in order (seq): bid (amount + money cards), pass/fold/quit (reason: timeout/disconnected when forced), discard (Faux Pas, amount = painting value), disconnect/resign/reconnect (amount = engine liveness check), with seat and think_ms. With games.seed, the complete record a game is replayed from -- see recording/game_replay.py.'
    WHERE table_name = 'game_actions'
    """,

    # Bot-vs-bot self-play episodes for RL training (see scripts/selfplay_job.py),
    # deliberately NOT written into games/player_games/ratings: thousands of
    # bot-only games there would swing the bots' own Elo, flood the
    # leaderboard's/global stats' counts, and buy nothing an episode row
    # doesn't already carry. `replay` holds the game record
    # (recording/game_replay.py) -- its "actions" are exactly game_actions'
    # rows, so website and self-play games share one format; num_decisions
    # counts those actions. `batch` names one generation run (bot pool,
    # engine version, target count) so a future run with different settings
    # never gets mixed into an earlier one's dataset.
    """
    CREATE TABLE IF NOT EXISTS selfplay_games (
        id BIGSERIAL PRIMARY KEY,
        batch TEXT NOT NULL,
        seed BIGINT NOT NULL,
        seat_kinds JSONB NOT NULL,
        winner_seats JSONB NOT NULL,
        num_rounds INTEGER NOT NULL,
        num_decisions INTEGER NOT NULL,
        duration_ms INTEGER NOT NULL,
        engine_version TEXT,
        format_version INTEGER NOT NULL,
        replay JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_selfplay_games_batch ON selfplay_games (batch)",
    """
    INSERT INTO meta (table_name, description) VALUES
        ('selfplay_games', 'Bot-vs-bot self-play episodes for RL training, grouped by generation batch -- kept out of games/ratings on purpose. `replay` is the same game record (seed, seats, actions, rounds, standings) game_history.fetch_game_record assembles from game_actions; see recording/game_replay.py and scripts/selfplay_job.py.')
    ON CONFLICT (table_name) DO NOTHING
    """,

    # Proof that a browser really is the player it claims to be. Identity
    # used to be just a username in localStorage, so anyone could play as
    # (or rename) any account, Google-linked leaderboard players included.
    # Each sign-in now issues a random token to that device; only its
    # SHA-256 is stored. Keyed to player_id, so a rename keeps it working.
    """
    CREATE TABLE IF NOT EXISTS player_logins (
        token_hash TEXT PRIMARY KEY,
        player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_player_logins_player ON player_logins (player_id)",
    """
    INSERT INTO meta (table_name, description) VALUES
        ('player_logins', 'One row per signed-in device: SHA-256 of the login token it was issued, and whose it is. Required to play as, or rename, that player.')
    ON CONFLICT (table_name) DO NOTHING
    """,
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
# (not "as many as this process could use") -- DATABASE_URL already
# points at Supabase's own connection pooler (not a direct connection),
# which still has its own real ceiling on live sessions this process
# shouldn't try to hog.
_MIN_POOL_CONNECTIONS = 1
_MAX_POOL_CONNECTIONS = 10
_pool = None
_pool_lock = threading.Lock()

# One slot per pooled connection. psycopg2's pool doesn't queue: once all
# _MAX_POOL_CONNECTIONS are lent out, getconn() raises "connection pool
# exhausted" straight away, so a burst of visitors (each home-page load
# fires ~5 API calls at once) turned into instant failures -- a false
# "username taken" on guest sign-up, an empty leaderboard, even a
# finished game's save giving up. _connect() waits here for a free slot
# instead, and _release_connection() hands it back.
_pool_slots = threading.Semaphore(_MAX_POOL_CONNECTIONS)
_POOL_WAIT_SECONDS = 10


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
                    # A connection the pooler quietly dropped while idle
                    # goes "half open" -- TCP still looks up from this side,
                    # but the server is gone. Without these, both a fresh
                    # connect and any query on such a connection block
                    # forever (that's the failure mode that took the whole
                    # site down after ~5 idle days -- see _connect's
                    # docstring). connect_timeout bounds establishment; TCP
                    # keepalives make the OS notice a dead peer within ~1
                    # minute and fail the socket instead of hanging, so
                    # _connect's own SELECT 1 probe below actually gets an
                    # error it can retry past rather than stalling on it;
                    # statement_timeout is a last-resort ceiling on any
                    # single query.
                    connect_timeout=10,
                    keepalives=1,
                    keepalives_idle=30,
                    keepalives_interval=10,
                    keepalives_count=3,
                    # Caps how long a *sent* packet waits for an ACK before
                    # the socket errors out -- the piece connect_timeout and
                    # keepalives don't cover, and the one that actually
                    # bounds a query stuck writing to a peer that's silently
                    # gone. Linux-only (Render's host); a no-op elsewhere.
                    tcp_user_timeout=15000,
                    options="-c statement_timeout=15000",
                )
    return _pool


_CONNECT_RETRY_ATTEMPTS = 3


def _connect():
    """
    Hands back a pooled connection that's actually known-good, not just
    "the pool still has a reference to it" -- a real production incident
    this exact gap caused: after several idle days with no traffic,
    Supabase's own pooler silently dropped every one of this pool's
    idle connections. psycopg2's pool has no way to notice that on its
    own (it only ever checks conn.closed, which stays 0 for a connection
    that was cleanly dropped out from under it while idle -- that only
    flips once something actually tries to use it and fails), so it kept
    handing out those dead connections to every single request from then
    on. Every one of this module's callers has its own broad `except
    Exception` around the query itself, so each failure quietly mapped to
    whatever fail-safe response that call site already had for "the
    database is unreachable" -- get_leaderboard/get_rating_history/etc.
    silently returning empty, username_is_taken() failing safe toward
    "taken" (see its own docstring) -- with nothing to ever recover until
    a manual process restart happened to hand out a fresh pool.

    A cheap SELECT 1 here (much cheaper than the TCP+TLS handshake this
    pool exists to avoid in the first place) catches that before the
    caller ever sees a dead connection: a connection that fails it gets
    discarded from the pool outright (never recycled again) and a
    replacement is fetched instead. Capped at _CONNECT_RETRY_ATTEMPTS so
    a genuine outage (not just some stale idle connections) still fails
    fast, in roughly the same number of attempts as before, rather than
    retrying indefinitely against a database that's actually down.
    """
    # Imported lazily so merely importing this module (which web_server.py
    # does unconditionally) never fails for a dev/test setup that hasn't
    # installed psycopg2 — only actually needed once DATABASE_URL is set.
    import psycopg2  # noqa: F401 -- see _get_pool's own lazy psycopg2.pool import
    pool = _get_pool()
    if not _pool_slots.acquire(timeout=_POOL_WAIT_SECONDS):
        raise RuntimeError(f"no free database connection within {_POOL_WAIT_SECONDS}s")
    last_error = None
    for _ in range(_CONNECT_RETRY_ATTEMPTS):
        try:
            conn = pool.getconn()
        except Exception as e:  # noqa: BLE001 -- the pool itself couldn't produce a connection (exhausted, or the database is genuinely unreachable)
            last_error = e
            continue
        try:
            # `with conn` (not just the cursor) so the probe's own implicit
            # transaction is committed/closed straight away -- the caller
            # gets a genuinely idle connection, not one sitting "idle in
            # transaction" from an un-finalized SELECT.
            with conn, conn.cursor() as cur:
                cur.execute("SELECT 1")
            return conn
        except Exception as e:  # noqa: BLE001 -- this specific connection is dead; discard it and try another
            last_error = e
            pool.putconn(conn, close=True)
    _pool_slots.release()  # nothing was handed out, so no _release_connection() will follow
    raise last_error


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
    try:
        if conn.closed:
            # Already dead (e.g. the network dropped mid-query) -- tell the
            # pool to discard it outright rather than recycling a connection
            # nothing could actually use.
            pool.putconn(conn, close=True)
        else:
            pool.putconn(conn)
    finally:
        _pool_slots.release()


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
    -- see record_finished_game's player upsert). Checked before create_google_player commits
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


def get_display_ratings(usernames: list) -> dict:
    """{username: elo} for whichever of `usernames` are actually rated --
    Google-linked humans and bot identities (see bot_roster) -- for showing
    ratings at the table. Guests are left out, matching how the rest of the
    app treats them as unrated. {} with no database or on any error."""
    if not usernames or not is_configured():
        return {}
    ensure_schema()
    if not _schema_ready:
        return {}
    conn = None
    try:
        conn = _connect()
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT p.username, p.elo FROM players p WHERE p.username = ANY(%s) "
                "AND (p.google_id IS NOT NULL OR EXISTS (SELECT 1 FROM bots b WHERE b.player_id = p.id))",
                (sorted(set(usernames)),),
            )
            return {username: elo for username, elo in cur.fetchall()}
    except Exception as e:  # noqa: BLE001
        LoggingManager.warning(f"game_history.get_display_ratings failed: {e}")
        return {}
    finally:
        if conn is not None:
            _release_connection(conn)


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
            # finishes (record_finished_game's player upsert), so serving it from that cache
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
                # record_finished_game's player upsert), not on every page load/heartbeat, so
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
    "finished_at", "participants": [{"name", "is_bot",
    "bot_profile_username", "points", "money_left", "is_winner",
    "eliminated", "placement"}, ...]}, ordered by placement. No access
    check tied to any particular username -- consistent with this app's
    existing stateless, sessionless trust model (e.g.
    /api/auth/username/change's own docstring), and a finished game's
    results aren't sensitive. None if the game_id doesn't exist or on any
    failure.

    `bot_profile_username` is a second, deliberately separate lookup from
    `name`: `name` stays this seat's own per-game flavor label (e.g.
    "Ziggy Bot", from `pg.bot_name`) so the row itself never changes, but
    a bot recorded under an identity also has a real players row (see the
    `bots` table) with genuine aggregate stats -- this is that row's
    username, for the caller to link the name to a real profile: the named
    bot's own `__bot_ziggy__`, or for an older game one of the legacy
    `__bot_easy__`/`__bot_medium__`/`__bot_hard__`. None for a human
    participant, or for a bot recorded with no identity at all.
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
                       bot_p.username AS bot_profile_username,
                       pg.points, pg.money_left, pg.is_winner, pg.eliminated, pg.placement
                FROM player_games pg
                LEFT JOIN players p ON p.id = pg.player_id AND p.id NOT IN (SELECT player_id FROM bots)
                LEFT JOIN players bot_p ON bot_p.id = pg.player_id AND bot_p.id IN (SELECT player_id FROM bots)
                WHERE pg.game_id = %s
                ORDER BY pg.placement NULLS LAST
                """,
                (game_id,),
            )
            participants = [
                {"name": name, "is_bot": is_bot, "bot_profile_username": bot_profile_username,
                 "points": points, "money_left": money_left,
                 "is_winner": is_winner, "eliminated": eliminated, "placement": placement}
                for name, is_bot, bot_profile_username, points, money_left, is_winner, eliminated, placement
                in cur.fetchall()
            ]
            return {"game_id": game_id, "finished_at": finished_at.isoformat(), "participants": participants}
    except Exception as e:  # noqa: BLE001
        LoggingManager.warning(f"game_history.get_game_detail failed: {e}")
        return None
    finally:
        if conn is not None:
            _release_connection(conn)


# Three months -- effectively "until something changes it". Every write
# that can change what the leaderboard shows clears it on the spot (see
# _invalidate_leaderboard_cache: each finished game, which moves Elo and
# games_played/games_won, and every rename), and a deploy restarts the
# process with an empty cache anyway. So the TTL only bounds an
# out-of-band change nothing here knows about (a manual DB edit), and that
# is rare enough to fix with a restart rather than a short TTL that makes
# the first visitor after every expiry pay for the query. If this app ever
# runs more than the single gunicorn worker it does today, the other
# workers' copies wouldn't hear about a write -- revisit this then.
_LEADERBOARD_CACHE_TTL_SECONDS = 90 * 24 * 60 * 60
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
                WHERE google_id IS NOT NULL
                  AND games_played > 0
                  AND id NOT IN (SELECT player_id FROM bots)
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
    Google-linked accounts who've actually finished at least one game
    (a guest's elo never moves off the 1000 default, and a linked account
    that signed in but never played is just noise on the board -- neither
    is a real ranking) and explicitly excluding the 3 reserved bot
    identities (see the `bots` table): bots are real rated participants
    now (see record_finished_game's own docstring) so their elo genuinely
    moves, but it must never be shown to players. {"rows": [], "has_more":
    False} on any failure or no database.

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


def _invalidate_leaderboard_cache(rewarm: bool = False) -> None:
    """
    Drops every cached leaderboard page after a write that can change it
    (see _LEADERBOARD_CACHE_TTL_SECONDS on why that's what keeps it fresh,
    not the TTL). Clearing every page, not just the first, covers any
    offset someone might have cached, since a rank shift can move players
    across page boundaries.

    rewarm: also re-fetch the default first page right away, so the next
    visitor -- almost always on page one -- gets a warm hit instead of
    paying for the query. Worth it after a rating change (people check the
    board right after a game); otherwise the next visit simply refills it.
    """
    with _leaderboard_cache_lock:
        _leaderboard_cache.clear()
    if rewarm:
        _warm_leaderboard_cache()


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
        # The leaderboard lists players by username -- a renamed player
        # must not keep showing under the old name until the TTL.
        _invalidate_leaderboard_cache()
        # Same for the per-username id/elo cache: left alone, the old name
        # kept pointing at this account, so whoever claimed it next got
        # this account's stats on their profile.
        with _player_sessions_lock:
            _player_sessions.pop(old_username, None)
            _player_sessions.pop(new_username, None)
        return True
    except Exception as e:  # noqa: BLE001 -- includes a uniqueness-constraint race, deliberately not special-cased
        LoggingManager.warning(f"game_history.rename_player failed: {e}")
        return False
    finally:
        if conn is not None:
            _release_connection(conn)


def _hash_login_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_login_token(username: str) -> Optional[str]:
    """A fresh login token for `username`'s existing players row (see the
    player_logins table), or None if there's no such row, no database, or
    the write fails. The caller hands it to the browser once; only its hash
    is kept."""
    if not is_configured():
        return None
    ensure_schema()
    if not _schema_ready:
        return None
    token = secrets.token_urlsafe(32)
    conn = None
    try:
        conn = _connect()
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO player_logins (token_hash, player_id) "
                "SELECT %s, id FROM players WHERE username = %s",
                (_hash_login_token(token), username),
            )
            if cur.rowcount == 0:
                return None
        return token
    except Exception as e:  # noqa: BLE001
        LoggingManager.warning(f"game_history.issue_login_token failed: {e}")
        return None
    finally:
        if conn is not None:
            _release_connection(conn)


def login_token_matches(username: str, token: Optional[str]) -> Optional[bool]:
    """Whether `token` was issued to `username`'s account. None when that
    can't be determined (no database, or the lookup errors) -- callers let
    the request through then, rather than locking everyone out of playing
    during a database blip."""
    if not is_configured():
        return None
    ensure_schema()
    if not _schema_ready:
        return None
    if not isinstance(token, str) or not token:
        return False
    conn = None
    try:
        conn = _connect()
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM player_logins l JOIN players p ON p.id = l.player_id "
                "WHERE l.token_hash = %s AND p.username = %s",
                (_hash_login_token(token), username),
            )
            return cur.fetchone() is not None
    except Exception as e:  # noqa: BLE001
        LoggingManager.warning(f"game_history.login_token_matches failed: {e}")
        return None
    finally:
        if conn is not None:
            _release_connection(conn)


def issue_first_login_token(username: str) -> Optional[str]:
    """One-time upgrade for a guest profile saved before login tokens
    existed: issues a token only if `username` is a guest (no google_id)
    that has never been issued one -- once any device holds a token, the
    name is claimed and this refuses. A Google-linked account never
    qualifies; it signs in with Google again instead. A username with no
    players row at all (reserved while the database was unreachable) is
    reserved now. None means refused or failed."""
    if not is_configured():
        return None
    ensure_schema()
    if not _schema_ready:
        return None
    conn = None
    try:
        conn = _connect()
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO players (username, display_name) VALUES (%s, %s) ON CONFLICT (username) DO NOTHING",
                (username, username),
            )
            cur.execute(
                "SELECT p.id FROM players p WHERE p.username = %s AND p.google_id IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM player_logins l WHERE l.player_id = p.id) FOR UPDATE",
                (username,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            token = secrets.token_urlsafe(32)
            cur.execute("INSERT INTO player_logins (token_hash, player_id) VALUES (%s, %s)",
                        (_hash_login_token(token), row[0]))
        return token
    except Exception as e:  # noqa: BLE001
        LoggingManager.warning(f"game_history.issue_first_login_token failed: {e}")
        return None
    finally:
        if conn is not None:
            _release_connection(conn)


# (type, value) -> card_id, read once per process: `cards` is a fixed table
# seeded by the schema and never written afterwards, so looking each round's
# card up separately cost one round trip per auction for an answer that
# never changes.
_card_id_cache: Optional[dict] = None


def _card_ids(cur) -> dict:
    global _card_id_cache
    if _card_id_cache is None:
        cur.execute("SELECT type, value, min(card_id) FROM cards GROUP BY type, value")
        _card_id_cache = {(t, v): card_id for t, v, card_id in cur.fetchall()}
    return _card_id_cache


def _record_auction_rounds(cur, game_id: int, auction_rounds: list, player_id_by_username: dict,
                           seat_by_username: Optional[dict] = None) -> None:
    """
    Populates game_rounds/round_players from `auction_rounds`
    (the same AuctionRecord.to_dict()-shaped list PlayGame.get_auction_
    history() returns) -- called once, at game-end, from data the engine
    already built for docs/BOT_API.md's AUCTION_RESULT feed. No live-path DB
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

    card_ids = _card_ids(cur)
    round_rows = []
    for record in auction_rounds:
        card = record["card"]
        # Matched by (type, value), not a stored card identity -- for a
        # repeated card (the 3 identical Prestige cards) any matching row
        # is equally correct, since the game's own data never tags which
        # *specific* physical card a given auction drew.
        card_id = card_ids.get((card["type"], card["value"]))

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
        winner_seat = (seat_by_username or {}).get(recipient) if recipient else None
        round_rows.append((game_id, record["round_number"], card_id, winner_id, winning_bid,
                            record.get("started_at"), record.get("ended_at"), winner_seat))

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
        INSERT INTO game_rounds (game_id, round_number, card_id, winner_id, winning_bid, started_at, ended_at,
                                 winner_seat)
        VALUES {_values_placeholder(len(round_rows), 8)}
        RETURNING id
        """,
        _flatten(round_rows),
    )
    round_ids = [cur.fetchone()[0] for _ in round_rows]

    round_player_rows = []
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



def _record_actions(cur, game_id: int, actions: list, player_id_by_seat: dict) -> None:
    """
    One game_actions row per entry of PlayGame.action_log, in one batched
    INSERT (see _record_auction_rounds' docstring on why batching matters
    against Supabase). Keyed by seat; player_id rides along for joins and
    is NULL for a seat with no players row.
    """
    rows = [
        (game_id, a["seq"], a["seat"], player_id_by_seat.get(a["seat"]), a["action"].upper(), a.get("amount"),
         a.get("cards"), a.get("think_ms"), a.get("reason"), a["round"], a["timestamp"])
        for a in actions
    ]
    cur.execute(
        f"""
        INSERT INTO game_actions (game_id, seq, seat, player_id, action_type, amount, cards, think_ms, reason,
                                  round, timestamp)
        VALUES {_values_placeholder(len(rows), 11)}
        """,
        _flatten(rows),
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
                          auction_rounds: Optional[list] = None,
                          seed: Optional[int] = None, actions: Optional[list] = None,
                          rewarm_leaderboard: bool = True) -> dict:
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
    only meaningful when is_bot is True — see the `bots` table: a bot whose
    `name` is a roster bot's seat name (common/bot_roster.py) *and* whose
    difficulty matches the one the roster pins that name to gets that
    bot's own player_id alongside its bot_name; anything else (no
    difficulty, a non-roster name) stays bot_name-only (player_id NULL).

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
    round_players. None/empty (the default, for any caller
    that predates this or a crashed game with no history) just skips that
    entirely; nothing else here depends on it.

    `seed` (PlayGame.seed) and `actions` (PlayGame.action_log): what makes
    the game replayable from the database -- seed goes on the games row,
    actions become game_actions rows (one per action, in order). Each
    participant's "seat" (its index in PlayGame.players) lands on
    player_games and keys every action. Without `actions`, no game_actions
    rows are written.
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
            # Every write below is batched into one multi-row statement per
            # table: the database is a long way from the app server, and
            # this whole transaction is what the finished screen's Elo
            # reveal waits on, so each extra statement is a full network
            # round trip the player sits through.

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

            # Every human seat plus the host, in one upsert. A host who
            # also plays ends with their seat's display name (the later
            # entry wins, as when they were two separate upserts), and
            # only seated humans get games_played/games_won bumped.
            # Returns each player's id, google_id (whether achievements
            # and Elo apply -- see achievements.py's module docstring) and
            # elo *before* this game, for elo.compute_elo_deltas.
            human_rows: dict = {}  # username -> [display_name, games_played increment, games_won increment]
            if host_username:
                human_rows[host_username] = [host_username, 0, 0]
            for p in participants:
                if not p["is_bot"]:
                    row = human_rows.setdefault(p["username"], [p["name"], 0, 0])
                    row[0] = p["name"]
                    row[1] += 1
                    row[2] += 1 if p["is_winner"] else 0
            human_by_username = {}  # username -> (player_id, google_id, elo)
            if human_rows:
                rows = [(username, *row) for username, row in human_rows.items()]
                cur.execute(
                    f"""
                    INSERT INTO players (username, display_name, games_played, games_won)
                    VALUES {_values_placeholder(len(rows), 4)}
                    ON CONFLICT (username) DO UPDATE SET
                        display_name = EXCLUDED.display_name, last_seen_at = now(),
                        games_played = players.games_played + EXCLUDED.games_played,
                        games_won = players.games_won + EXCLUDED.games_won
                    RETURNING username, id, google_id, elo
                    """,
                    _flatten(rows),
                )
                human_by_username = {username: (player_id, google_id, elo)
                                     for username, player_id, google_id, elo in cur.fetchall()}
            host_player_id = human_by_username[host_username][0] if host_username else None

            # Each bot seat's own named identity (see common/bot_roster.py),
            # only when the name really is that roster bot -- same name
            # *and* the difficulty the roster pins it to. A heuristic bot
            # that drew a roster name for flavor (CLI/scripts, difficulty
            # None) or an overflow "Bot3" stays bot_name-only. Never the
            # legacy per-difficulty rows: those are frozen history now.
            # Bots are rated too, so this also reads each one's current elo.
            roster_bot_by_index = {}
            for index, p in enumerate(participants):
                if p["is_bot"]:
                    roster_bot = bot_roster.find_by_name(p["name"])
                    if roster_bot is not None and roster_bot.difficulty == p.get("difficulty"):
                        roster_bot_by_index[index] = roster_bot
            bot_by_key = {}  # bot_key -> (player_id, elo)
            if roster_bot_by_index:
                cur.execute(
                    "SELECT b.bot_key, b.player_id, p.elo FROM bots b JOIN players p ON p.id = b.player_id "
                    "WHERE b.bot_key = ANY(%s)",
                    (sorted({bot.key for bot in roster_bot_by_index.values()}),),
                )
                bot_by_key = {key: (player_id, elo) for key, player_id, elo in cur.fetchall()}

            rated_standings = []  # {"username", "placement", "rating"} -- see elo.compute_elo_deltas
            rated_player_ids = {}  # rating_key -> player_id, for writing the delta back below
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
            player_games_rows = []
            game_results_rows = []
            game_results_seen = set()
            winner_ids_to_count = []  # Google-linked winners, for the win-count milestones
            achievement_rows = []
            for index, p in enumerate(participants):
                player_id = google_id = elo_before = bot_name = None
                if p["is_bot"]:
                    bot_name = p["name"]
                    roster_bot = roster_bot_by_index.get(index)
                    if roster_bot is not None and roster_bot.key in bot_by_key:
                        player_id, elo_before = bot_by_key[roster_bot.key]
                        # A named bot's username is stable now, so its
                        # cached profile Elo must refresh like a human's.
                        real_username_by_player_id[player_id] = bot_roster.roster_username(roster_bot)
                else:
                    player_id, google_id, elo_before = human_by_username[p["username"]]
                    real_username_by_player_id[player_id] = p["username"]
                seat_player_ids.append(player_id)
                if p.get("game_username"):
                    player_id_by_game_username[p["game_username"]] = player_id

                player_games_rows.append((None, player_id, bot_name, p["points"], p["money_left"],
                                          p["is_winner"], p["eliminated"], placement_by_index[index], p.get("seat")))
                if player_id is not None:
                    if p["is_winner"] and winner_player_id is None:
                        winner_player_id = player_id
                    if player_id not in game_results_seen:  # first seat wins, as ON CONFLICT DO NOTHING did
                        game_results_seen.add(player_id)
                        game_results_rows.append((None, player_id, len(participants), p["money_left"], p["points"],
                                                  placement_by_index[index]))

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
                    # placement, not raw points -- see elo.compute_elo_deltas'
                    # own docstring on why: this game's real winner is
                    # whoever scored highest *among players not eliminated*
                    # for having the least money, so the points leader can
                    # genuinely finish last (placement_by_index above
                    # already accounts for this correctly; points alone
                    # never did).
                    rated_standings.append({
                        "username": rating_key, "placement": placement_by_index[index], "rating": elo_before,
                    })
                    rated_player_ids[rating_key] = player_id
                    elo_before_by_player_id[player_id] = elo_before

                # Achievements stay strictly human-only -- a bot must never
                # unlock one, regardless of the elo change above.
                if player_id is not None and google_id is not None:
                    for achievement_id in sorted(set(achievement_unlocks.get(p["username"]) or ())):
                        achievement_rows.append((player_id, achievement_id))
                    if p["is_winner"]:
                        winner_ids_to_count.append(player_id)

            # The games row goes in last of the "who" writes, once the
            # winner and every seat's player_id are already known.
            seat_columns = (seat_player_ids + [None] * 5)[:5]
            cur.execute(
                """
                INSERT INTO games (room_code, seats, started_at, finished_at, host_player_id, time_control,
                                   is_finished_successfully, seed, winner_id,
                                   player_1, player_2, player_3, player_4, player_5)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (room_code, seats, started_at, finished_at, host_player_id, time_control,
                 is_finished_successfully, seed, winner_player_id, *seat_columns),
            )
            game_id = cur.fetchone()[0]

            if player_games_rows:
                cur.execute(
                    f"""
                    INSERT INTO player_games
                        (game_id, player_id, bot_name, points, money_left, is_winner, eliminated, placement, seat)
                    VALUES {_values_placeholder(len(player_games_rows), 9)}
                    """,
                    _flatten([(game_id, *row[1:]) for row in player_games_rows]),
                )
            if game_results_rows:
                cur.execute(
                    f"""
                    INSERT INTO game_results (game_id, user_id, num_players, final_money, final_score, placement)
                    VALUES {_values_placeholder(len(game_results_rows), 6)}
                    ON CONFLICT (game_id, user_id) DO NOTHING
                    """,
                    _flatten([(game_id, *row[1:]) for row in game_results_rows]),
                )

            # Win-count milestones read player_games *after* this game's own
            # rows are in, so this win counts toward them.
            if winner_ids_to_count:
                cur.execute(
                    "SELECT player_id, count(*) FROM player_games "
                    "WHERE player_id = ANY(%s) AND is_winner GROUP BY player_id",
                    (winner_ids_to_count,),
                )
                for player_id, win_count in cur.fetchall():
                    for threshold, achievement_id in WIN_COUNT_MILESTONES.items():
                        if win_count >= threshold:
                            achievement_rows.append((player_id, achievement_id))
            achievement_rows = list(dict.fromkeys(achievement_rows))
            if achievement_rows:
                cur.execute(
                    f"""
                    INSERT INTO player_achievements (player_id, achievement_id)
                    VALUES {_values_placeholder(len(achievement_rows), 2)}
                    ON CONFLICT (player_id, achievement_id) DO NOTHING
                    """,
                    _flatten(achievement_rows),
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
            # can't afford to have. A named bot is included (its
            # __bot_<key>__ username is a stable profile identity); a bot
            # with no roster identity never gets this far.
            cache_refreshes = []
            rating_rows = []  # (player_id, game_id, old_rating, new_rating, rating_change)
            for player_id, seat_deltas in deltas_by_player_id.items():
                combined_delta = round(sum(seat_deltas) / len(seat_deltas))
                if combined_delta:
                    old_rating = elo_before_by_player_id[player_id]
                    new_rating = old_rating + combined_delta
                    rating_rows.append((player_id, game_id, old_rating, new_rating, combined_delta))
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
            if rating_rows:
                cur.execute(
                    f"""
                    UPDATE players SET elo = players.elo + delta.change
                    FROM (VALUES {_values_placeholder(len(rating_rows), 2)}) AS delta (player_id, change)
                    WHERE players.id = delta.player_id
                    """,
                    _flatten([(row[0], row[4]) for row in rating_rows]),
                )
                cur.execute(
                    f"""
                    INSERT INTO ratings (user_id, game_id, old_rating, new_rating, rating_change)
                    VALUES {_values_placeholder(len(rating_rows), 5)}
                    """,
                    _flatten(rating_rows),
                )

            seat_by_game_username = {p["game_username"]: p["seat"] for p in participants
                                     if p.get("game_username") is not None and p.get("seat") is not None}
            if auction_rounds:
                _record_auction_rounds(cur, game_id, auction_rounds, player_id_by_game_username,
                                       seat_by_game_username)
            if actions:
                player_id_by_seat = {seat: player_id_by_game_username.get(u)
                                     for u, seat in seat_by_game_username.items()}
                _record_actions(cur, game_id, actions, player_id_by_seat)
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
    if participants:
        # Any finished game can change the leaderboard -- not just Elo
        # (cache_refreshes), but games_played/games_won too, which move
        # even when a rating doesn't. Runs on this same background write
        # thread (see record_finished_game_async), so it costs a real
        # player nothing. rewarm_leaderboard=False leaves the re-fetch to
        # the caller (record_finished_game_async does it after on_complete,
        # so the Elo reveal doesn't wait on a leaderboard query).
        _invalidate_leaderboard_cache(rewarm=rewarm_leaderboard and bool(cache_refreshes))
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
        result = record_finished_game(rewarm_leaderboard=False, **kwargs)
        if on_complete:
            on_complete(result)
        # After on_complete: the finished screen is polling for those Elo
        # numbers, and the leaderboard re-fetch is one more trip to the
        # database. A non-empty result is exactly "a rating changed".
        if result:
            _warm_leaderboard_cache()

    threading.Thread(target=_run, daemon=True, name="GameHistoryWrite").start()


# ---------------------------------------------------------------------------
# Self-play episodes (scripts/selfplay_job.py) and replay lookups (scripts/verify_replay.py).
# Unlike everything above, these RAISE on failure instead of logging and
# returning a fail-safe default: their callers are batch jobs, where a
# silently-swallowed write error would quietly lose data (or make a job
# believe it's further along than it is) -- exactly the failure mode this
# module's live-game writers accept on purpose so a player never waits on
# the database.
# ---------------------------------------------------------------------------

def _require_schema() -> None:
    if not is_configured():
        raise RuntimeError("DATABASE_URL is not set")
    ensure_schema()
    if not _schema_ready:
        raise RuntimeError("game_history schema setup failed -- see the log line above")


def count_selfplay_games(batch: str) -> int:
    _require_schema()
    conn = _connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM selfplay_games WHERE batch = %s", (batch,))
            return cur.fetchone()[0]
    finally:
        _release_connection(conn)


def insert_selfplay_games(batch: str, episodes: list, engine_version: Optional[str] = None) -> None:
    """One multi-row INSERT for a whole flush of finished episodes (each
    {"record": recording/game_replay.game_record(...), "duration_ms"}) --
    see _record_auction_rounds' docstring on why per-row round trips to
    Supabase are worth avoiding."""
    if not episodes:
        return
    _require_schema()
    rows = []
    for ep in episodes:
        record = ep["record"]
        seats = sorted(record["seats"], key=lambda s: s["seat"])
        rows.append((batch, record["seed"], json.dumps([s["kind"] for s in seats]),
                     json.dumps(sorted(s["seat"] for s in record["standings"] if s["is_winner"])),
                     len(record["rounds"]), len(record["actions"]), ep["duration_ms"], engine_version,
                     record["version"], json.dumps(record)))
    row_placeholder = "(%s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s::jsonb)"
    conn = _connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO selfplay_games (batch, seed, seat_kinds, winner_seats, num_rounds, num_decisions, "
                "duration_ms, engine_version, format_version, replay) VALUES "
                + ",".join([row_placeholder] * len(rows)),
                _flatten(rows),
            )
    finally:
        _release_connection(conn)


# Rows per query when fetch_selfplay_records reads a whole batch. Each row
# carries a full game record (~3KB of JSON); asking for all 10,000 rl-v1
# games in one query ran past the database's statement timeout.
_SELFPLAY_FETCH_PAGE_SIZE = 500


def fetch_selfplay_records(ids: Optional[list] = None, batch: Optional[str] = None,
                           sample: Optional[int] = None) -> list:
    """[(selfplay_games.id, game record), ...] -- explicit `ids`, or a random
    `sample` of that many (optionally within one `batch`), else all.
    Everything but a sample is read in id order, _SELFPLAY_FETCH_PAGE_SIZE
    rows per query (keyset paging on id), so no single query is big enough
    to hit the statement timeout."""
    _require_schema()
    where, params = [], []
    if ids:
        where.append("id = ANY(%s)")
        params.append(list(ids))
    if batch is not None:
        where.append("batch = %s")
        params.append(batch)
    conn = _connect()
    try:
        with conn, conn.cursor() as cur:
            if sample:
                query = "SELECT id, replay FROM selfplay_games"
                if where:
                    query += " WHERE " + " AND ".join(where)
                cur.execute(query + " ORDER BY random() LIMIT %s", params + [sample])
                return cur.fetchall()
            rows, last_id = [], 0
            while True:
                cur.execute(
                    "SELECT id, replay FROM selfplay_games WHERE " + " AND ".join(where + ["id > %s"])
                    + " ORDER BY id LIMIT %s",
                    params + [last_id, _SELFPLAY_FETCH_PAGE_SIZE],
                )
                page = cur.fetchall()
                rows.extend(page)
                if len(page) < _SELFPLAY_FETCH_PAGE_SIZE:
                    return rows
                last_id = page[-1][0]
    finally:
        _release_connection(conn)


def assemble_game_record(seed: Optional[int], seat_rows: list, round_rows: list, action_rows: list) -> dict:
    """
    The replayable game record (recording/game_replay.py) built from the
    history tables' own rows -- shared by fetch_game_records and tests, so
    "what the database holds" and "what gets replayed" are one code path.

    seat_rows:   (seat, username, name, is_bot, difficulty, points, money_left, is_winner, eliminated)
                 -- player_games, difficulty from the bot's `bots` row (None for a human or no identity)
    round_rows:  (round_number, card_type, card_value, winner_seat, winning_bid)             -- game_rounds
    action_rows: (seq, round, seat, action_type, amount, cards, think_ms, reason, timestamp) -- game_actions
    """
    from highsociety.code.gamecore.recording.game_replay import GAME_RECORD_VERSION

    seats, standings = [], []
    for seat, username, name, is_bot, difficulty, points, money_left, is_winner, eliminated in sorted(
            seat_rows, key=lambda r: (r[0] is None, r[0])):
        kind = difficulty or ("bot" if is_bot else "human")
        seats.append({"seat": seat, "username": username, "name": name, "kind": kind})
        standings.append({"seat": seat, "points": points, "money_left": money_left,
                          "is_winner": is_winner, "eliminated": eliminated})
    rounds = [{"round": n, "card": {"type": t, "value": v}, "recipient_seat": ws, "price": price}
              for n, t, v, ws, price in round_rows]
    actions = [{"seq": seq, "round": rnd, "seat": seat, "action": action_type.lower(), "amount": amount,
                "cards": list(cards) if cards is not None else None, "think_ms": think_ms, "reason": reason,
                "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else ts}
               for seq, rnd, seat, action_type, amount, cards, think_ms, reason, ts in action_rows]
    return {"version": GAME_RECORD_VERSION, "seed": seed, "settlement": "ForfeitSettlement",
            "seats": seats, "actions": actions, "rounds": rounds, "standings": standings}


def fetch_game_records(ids: Optional[list] = None, sample: Optional[int] = None) -> list:
    """
    [(game_id, game record), ...] assembled from the regular history tables
    alone -- games.seed, player_games (seat, final standing), game_rounds
    (card, winner_seat, winning_bid) and game_actions (every action, by
    seq). This is the record recording/game_replay.replay_record rebuilds
    and verifies, so it proves those tables are a complete source of truth.
    Explicit `ids`, or a random `sample` of games that have a seed.
    """
    _require_schema()
    conn = _connect()
    try:
        with conn, conn.cursor() as cur:
            if ids:
                game_ids = list(ids)
            else:
                cur.execute("SELECT id FROM games WHERE seed IS NOT NULL ORDER BY random() LIMIT %s",
                            (sample or 20,))
                game_ids = [r[0] for r in cur.fetchall()]
            records = []
            for game_id in game_ids:
                cur.execute("SELECT seed FROM games WHERE id = %s", (game_id,))
                row = cur.fetchone()
                if row is None:
                    continue
                cur.execute(
                    """
                    SELECT pg.seat, COALESCE(p.username, pg.bot_name), COALESCE(pg.bot_name, p.display_name),
                           pg.bot_name IS NOT NULL, b.difficulty,
                           pg.points, pg.money_left, pg.is_winner, pg.eliminated
                    FROM player_games pg
                    LEFT JOIN players p ON p.id = pg.player_id
                    LEFT JOIN bots b ON b.player_id = pg.player_id
                    WHERE pg.game_id = %s
                    """,
                    (game_id,),
                )
                seat_rows = cur.fetchall()
                cur.execute(
                    """
                    SELECT gr.round_number, c.type, c.value, gr.winner_seat, gr.winning_bid
                    FROM game_rounds gr LEFT JOIN cards c ON c.card_id = gr.card_id
                    WHERE gr.game_id = %s ORDER BY gr.round_number
                    """,
                    (game_id,),
                )
                round_rows = cur.fetchall()
                cur.execute(
                    """
                    SELECT seq, round, seat, action_type, amount, cards, think_ms, reason, timestamp
                    FROM game_actions WHERE game_id = %s ORDER BY seq
                    """,
                    (game_id,),
                )
                records.append((game_id, assemble_game_record(row[0], seat_rows, round_rows, cur.fetchall())))
            return records
    finally:
        _release_connection(conn)
