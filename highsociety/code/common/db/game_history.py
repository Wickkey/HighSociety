"""
Persists finished games to a Postgres database (see README's "Game history
database" section) so a player could eventually be shown their own past
games. Entirely optional: unless the DATABASE_URL environment variable is
set, every public function here is a no-op, so this can never affect an
existing local/dev/test setup that hasn't configured a database. A game
whose write fails or is slow also never blocks the actual game-over message
reaching players — see record_finished_game_async.

Schema (3 tables, see _SCHEMA_STATEMENTS for the exact DDL):
  players       One row per distinct human username ever seen. `username`
                is the identity key today, since there's no login system
                yet — the nullable google_id/email columns exist so a future
                Google Sign-In can attach a *real* identity to an existing
                players.id later without changing any foreign key that
                already points at it.
  games         One row per finished game, including each rematch (a
                rematch is just another call to record_finished_game).
  player_games  The "key that maps players to their past games": one row
                per (game, participant). Bots have no persistent identity to
                attach a row to, so they're recorded by name only
                (player_id NULL, bot_name set) rather than being forced into
                the players table.
"""
import datetime
import json
import os
import threading

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
    """
    global _schema_ready
    if not is_configured() or _schema_ready:
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
        except Exception as e:  # noqa: BLE001 — a DB hiccup at startup must never crash the app
            LoggingManager.warning(f"game_history.ensure_schema failed, game history disabled: {e}")
        finally:
            if conn is not None:
                conn.close()


def _upsert_player(cur, username: str, display_name: str) -> int:
    cur.execute(
        """
        INSERT INTO players (username, display_name)
        VALUES (%s, %s)
        ON CONFLICT (username) DO UPDATE SET display_name = EXCLUDED.display_name, last_seen_at = now()
        RETURNING id
        """,
        (username, display_name),
    )
    return cur.fetchone()[0]


def record_finished_game(*, room_code: str, seats: int, bot_mix: list,
                          started_at: datetime.datetime, finished_at: datetime.datetime,
                          participants: list) -> None:
    """
    `participants`: one dict per seat, already reduced to exactly what this
    module needs — see web_server.py's call site for how it's built from
    PlayGame.final_standings/GameRoom.players. Keeping that translation in
    the caller (rather than importing NetworkPlayer/PlayGame here) is what
    lets this stay a plain, independently-testable persistence layer with no
    dependency on the game engine's own classes.

    Each participant: {"is_bot": bool, "username": str | None, "name": str,
                        "points": int, "money_left": int, "is_winner": bool,
                        "eliminated": bool}
    Exactly one of (is_bot False + username set) or (is_bot True + username
    None) holds per participant — see the player_xor_bot constraint.
    """
    if not is_configured():
        return
    ensure_schema()
    if not _schema_ready:
        return  # schema setup already failed and logged a warning above
    conn = None
    try:
        conn = _connect()
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO games (room_code, seats, bot_mix, started_at, finished_at)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (room_code, seats, json.dumps(bot_mix), started_at, finished_at),
            )
            game_id = cur.fetchone()[0]
            for p in participants:
                player_id = None
                bot_name = None
                if p["is_bot"]:
                    bot_name = p["name"]
                else:
                    player_id = _upsert_player(cur, p["username"], p["name"])
                cur.execute(
                    """
                    INSERT INTO player_games
                        (game_id, player_id, bot_name, points, money_left, is_winner, eliminated)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (game_id, player_id, bot_name, p["points"], p["money_left"],
                     p["is_winner"], p["eliminated"]),
                )
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
