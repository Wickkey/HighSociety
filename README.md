# HighSociety

A Python implementation of the High Society card game backend.

## Requirements

Python 3.9+. No third-party packages needed for CLI or socket play. Playing in a browser needs
`flask`/`flask-sock`:

```
pip install -r requirements.txt
```

For running tests:

```
pip install -r tests/requirements.txt
```

## Playing

See [PLAYING.md](PLAYING.md) for full instructions: local hot-seat CLI, networked multiplayer
(server + per-player clients + spectators), a browser-based lobby + web client, and how to
record/replay a session (works identically regardless of which mode produced the recording).

Quick start:

```
python3 main.py                              # local, one terminal, hot-seat
python3 network_server.py --players 2        # networked: host
python3 network_client.py --host <ip> --port 8888   # networked: each player
python3 web_server.py                        # browser: host, then everyone (including you) opens a page
```

For planning specific test scenarios ahead of time (rather than recording a real session), see
`highsociety/code/gamecore/dev_tools/inspect_seed.py` and
`tests/game_manager/test_scenario_faux_pas_branches.py`.

## Playing against bots

`highsociety/code/ai/` has three ready-made bots — `PassBot` (always passes), `GreedyBot` (always
raises with the single cheapest card that beats the current bid), and `CappedGreedyBot` (like
`GreedyBot`, but refuses to spend past a budget that depends on the card up for auction). Both
`main.py` and `network_server.py` take a `--bots` flag: a comma-separated list of bot types
(`pass`, `greedy`, `capped`) that fills that many seats automatically, one bot per entry.

**CLI:**

```
python3 main.py --bots greedy,pass          # you fill the remaining seat(s) interactively
```

You're only prompted for whichever seats `--bots` didn't fill — if `--bots` names as many bots as
you asked for players, nobody is prompted at all and it's a bots-only game.

**Networked:**

```
python3 network_server.py --players 3 --bots greedy,pass    # server pre-fills 2 of 3 seats
python3 network_client.py --host <ip> --port 8888            # the remaining human connects as usual
```

`--players` is the *total* seat count, bots included — the server only waits for
`--players` minus however many `--bots` named to actually connect over the network. Bots aren't
sockets, so this works with no client-side changes.

Both `--bots` flags default to a 1-second pause before each bot's decision (`--bot-think-time`,
e.g. `--bot-think-time 0.3`) — a real game is meant to be watched, so an instant decision is easy
to miss entirely. Set it to `0` for full speed.

**Watching bots play each other live**, without any human or network setup at all:

```
python3 -m highsociety.code.gamecore.dev_tools.simulate_bots --bots greedy,greedy,pass,capped,capped
```

See `highsociety/code/gamecore/dev_tools/simulate_bots.py --help` for `--seed` (reproducible
games) and `--think-time` (pause between each bot's decision, so you can actually follow along).

Writing your own bot: see [BOT_API.md](BOT_API.md)'s "Embedded" section — subclass `BasePlayer`,
implement `get_bid`/`choose_painting_to_discard`/`send_message`, and it plugs into `--bots` the
same way by adding it to the `BOT_TYPES` registry in `highsociety/code/ai/__init__.py`.

## Run tests

```
pytest
```

## Configuration

Game rules (starting cash, painting values, disgrace card types, player limits, per-turn time
limit, etc.) live in `highsociety/HSConfig.json` — see `highsociety/code/HSConfig.md` for a
field-by-field description.

## Game history database (optional)

`web_server.py` can persist every finished game (room, seats, bot mix, and each participant's
final points/money/win-or-loss — see `highsociety/code/common/db/game_history.py`'s docstring for
the exact schema) to a Postgres database. This is entirely optional and off by default: unless the
`DATABASE_URL` environment variable is set, nothing here is ever touched (no import, no connection
attempt), so the app runs exactly as before without a database configured.

To turn it on, using [Supabase](https://supabase.com)'s free tier (chosen because it's Postgres —
a natural fit for "map players to their past games" — and its optional Google-sign-in auth product
means the same free project can cover a later real-accounts feature too, without switching
providers):

1. Create a free Supabase account and a new project.
2. In the project dashboard, go to **Project Settings → Database → Connection string** and copy
   the URI (the "Session pooler" or direct connection string both work).
3. Set it as an environment variable before starting the server:
   `export DATABASE_URL="postgresql://..."` (locally), or as a secret/environment variable in
   whatever hosting service ends up running this (Render, Railway, etc.).
4. Start the app as usual. The three tables (`players`, `games`, `player_games`) are created
   automatically on first startup if they don't already exist — no manual migration step.

A player's identity is keyed by their `username` today (there's no login system yet), but the
schema already has nullable `google_id`/`email` columns on `players` so a future Google Sign-In
can attach a real identity to an existing player's row without any schema change or data migration.
A database write happening slowly or failing outright never affects gameplay — it happens
fire-and-forget on a background thread after a game has already fully finished (see
`record_finished_game_async`).

## Status

CLI, networked, and browser play are all functional, covered by the test suite under `tests/`
(including a real-socket end-to-end game in `tests/network/test_end_to_end_socket.py` and a
real-WebSocket one in `tests/network/test_web_server.py`).

## Architecture: how the web client was added without touching the engine

`PlayGame` (`highsociety/code/gamecore/game_manager/gameplay.py`) never knows or cares whether a
player is local or remote — it only calls `get_bid()` / `choose_painting_to_discard()` /
`send_message()` / reads `.active` on whatever player objects it's given. `CLIPlayer` and
`NetworkPlayer` are just two implementations of that same informal interface.

Remote play is itself layered so a new transport doesn't require touching player logic or the
engine:

- **`highsociety/code/gamecore/network/transport.py`** — `Transport`, an ABC for "send one JSON
  message" / "receive the next one". `SocketTransport` (raw TCP, used by `network_server.py`) and
  `WebSocketTransport` (a browser WebSocket via flask-sock/simple-websocket, used by
  `web_server.py`) are its two implementations.
- **`highsociety/code/gamecore/network/protocol.py`** — the JSON message *shapes* (`PLAYER_MOVE`,
  `PLAYER_INFO`, `AUCTION_UPDATE`, etc.), independent of how those bytes move.
- **`NetworkPlayer` / `NetworkSpectator`** — thin adapters gluing a `Transport` + the protocol
  builders into the `BasePlayer`-shaped interface `PlayGame` expects. No socket/threading code of
  their own, and no idea whether they're backed by a raw socket or a browser tab.

`web_server.py` is the proof this layering works: it's a Flask app with an in-browser lobby
(`GameRoom`) on top, but the only genuinely new engine-adjacent code is `WebSocketTransport` — 
`NetworkPlayer`, `network/protocol.py`, and `PlayGame` itself are used completely unchanged from
`network_server.py`'s socket-based path. The same seam is what a future multi-room/hosted-website
version would extend (a dict of `GameRoom`s instead of the current single global one), not a
rewrite.
