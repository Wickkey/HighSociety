"""A fake database connection for game_history.record_finished_game, shared
by tests/common/test_game_history.py and tests/network/test_web_server.py.
It answers each statement by what it is rather than by call order, since
the save batches one statement across every seat."""
from unittest.mock import MagicMock


_CARD_ROWS = [("Painting", v, 100 + v) for v in range(1, 11)] + [
    ("Prestige", 0, 111), ("FauxPas", 0, 112), ("Passe", 0, 113), ("Scandale", 0, 114)]


def save_connection(players=None, bots=None, win_counts=None, leaderboard_rows=()):
    """A fake connection for record_finished_game that answers each
    statement by what it is, not by call order (its writes are batched, so
    one statement covers every seat). `players`: username -> (player_id,
    google_id, elo) for the players upsert (unlisted usernames get fresh
    guest ids from 10 at 1000 elo); `bots`: bot_key -> (player_id, elo) for
    the bots lookup (unlisted keys are simply not found); `win_counts`:
    player_id -> wins for the win-count milestones (default 1);
    `leaderboard_rows` answers _warm_leaderboard_cache's query."""
    players = dict(players or {})
    bots = bots or {}
    win_counts = win_counts or {}
    next_player_id = iter(range(10, 1000))
    next_round_id = iter(range(700, 1000))
    pending = {"one": [], "all": []}

    def execute(query, params=None):
        q = " ".join(query.split())
        one, rows = [], []
        if q.startswith("INSERT INTO players"):
            for username in params[0::4]:
                if username not in players:
                    players[username] = (next(next_player_id), None, 1000)
                rows.append((username, *players[username]))
        elif "FROM bots b JOIN" in q:
            rows = [(key, *bots[key]) for key in params[0] if key in bots]
        elif q.startswith("INSERT INTO games"):
            one = [(1,)]
        elif "count(*) FROM player_games" in q:
            rows = [(player_id, win_counts.get(player_id, 1)) for player_id in dict.fromkeys(params[0])]
        elif "FROM cards GROUP BY" in q:
            rows = list(_CARD_ROWS)
        elif q.startswith("INSERT INTO game_rounds"):
            one = [(next(next_round_id),) for _ in range(len(params) // 8)]
        elif "FROM players" in q and "google_id IS NOT NULL" in q:
            rows = list(leaderboard_rows)
        pending["one"], pending["all"] = one, rows

    conn = MagicMock()
    cursor = MagicMock()
    cursor.execute.side_effect = execute
    cursor.fetchone.side_effect = lambda: pending["one"].pop(0)
    cursor.fetchall.side_effect = lambda: pending["all"]
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.__enter__.return_value = conn
    return conn, cursor


def rows(cursor, statement, width):
    """Every row a batched `statement` wrote, unflattened from its flat
    params into `width`-column tuples, across all matching calls."""
    found = []
    for call in cursor.execute.call_args_list:
        if statement in call.args[0]:
            params = call.args[1]
            found += [tuple(params[i:i + width]) for i in range(0, len(params), width)]
    return found


def queries(cursor, fragment):
    return [call for call in cursor.execute.call_args_list if fragment in call.args[0]]
