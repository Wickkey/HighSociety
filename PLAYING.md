# Playing HighSociety

Four ways to play, plus how to capture and replay a session.

## 1. Multiplayer CLI (one terminal, hot-seat)

All players share the same terminal, taking turns.

```bash
source .venv/bin/activate
python3 main.py
```

You'll be prompted for:
- Number of players (2+)
- Each player's username and display name (Enter defaults the display name to the username)

Then a 5-second countdown and the game begins. On your turn, enter:
- A number (`10`) to bid that money card
- A list (`[1,2,3]`) to bid multiple money cards at once (their values sum)
- `pass` or `fold` to withdraw from the current auction
- `quit` to leave the game entirely

Per-turn time limit comes from `highsociety/HSConfig.json` → `game_settings.rules.time_per_move`
(seconds, or `null` for no limit — waits indefinitely for input).

Optional: `python3 main.py --seed N` makes the whole game (deck order, player order, starting
player) reproducible — see the recording section below for why that matters.

## 2. Multiplayer networking CLI (one terminal per player)

One machine hosts the server; each player connects from their own terminal (same machine or
different machines on the same network).

**Host**, in one terminal:
```bash
python3 network_server.py --players 2
```
Prints the host's IP and port, then waits for that many players to connect.

**Each player**, in their own terminal:
```bash
python3 network_client.py --host <ip-from-server-output> --port 8888
```

**Spectators** (optional) connect to `<port + 1>`:
```bash
python3 network_spectator_client.py --host <ip-from-server-output> --port 8889
```
Spectators watch the game live and can chat: type a message + Enter to reach everyone (players and
other spectators), or prefix with `/spectators ` to reach spectators only. A chat message is never
echoed back to whoever sent it.

Same bid syntax as CLI mode (numbers, lists, `pass`/`fold`/`quit`).

Optional: `python3 network_server.py --seed N` for a reproducible game, same as the CLI.

## 3. Play in a browser

One machine hosts the server; everyone else — including the host — just opens a page in their
browser. No terminal use needed except to start the server itself, and nothing is configured on
the command line: the first browser to open the page sets up the game.

**Requires** `flask` and `flask-sock` (`pip install -r requirements.txt`) — CLI and socket play
above need no third-party packages, only this path does.

**Host**, in one terminal:
```bash
python3 web_server.py
```
Prints the host's LAN IP and port.

**Everyone** (the host included) opens a browser to:
- `http://localhost:8000` on the host's own machine, or
- `http://<ip-from-server-output>:8000` from any other device on the same LAN.

The page opens on a "Join a game" screen listing every open public game (room code and seats
filled) plus a room-code box for joining a private one, and a "Host a new game" form below it:
total seats, how many of those seats to fill with bots (`pass`/`greedy`/`capped` — see "Playing
against bots" below), an optional seed for a reproducible game, and whether the room is **public**
(listed for anyone to join) or **private** (joinable only by sharing its room code). Submitting the
host form opens that specific room to everyone else — either they pick it from the public list, or
the host shares the room code directly for a private one. Everyone joining sees a "Join this game"
form (username + display name), the room code, and a live "seats filled" count. The game starts
automatically the moment that room's last human seat fills — no separate "start" step. Any number
of rooms can run at once on one server process; each is entirely independent.

**Solo play against bots**: host with, say, 3 seats and 2 bots — you fill the one remaining human
seat yourself from the same machine at `http://localhost:8000`.

**Spectating**: anyone can click "Watch as a spectator instead" on the join screen (or open
`http://<host>:8000/?room=<code>` directly) to get a read-only view of the table (auction history,
running log, and a chat box), the same `NetworkSpectator` support networked play already has.

**Limitations**: no reconnection — closing or refreshing the tab mid-game drops you, same as a
`NetworkPlayer` losing its connection anywhere else in this project. Recording/replay and `--bots`
bot-mix think-time tuning are CLI-only for now; see `web_server.py --help` for what it does take.

**Hosting it as a real website** (not just LAN play): `web_server.py` is deployable behind a
production WSGI server — see the repo-root `Procfile` (`gunicorn -k eventlet -w 1 web_server:app`),
built for managed hosting platforms (Render/Railway/Fly.io-style) that run it directly. Set the
`PORT` env var (most such platforms do this automatically) instead of `--port`. Kept to a single
worker/process since room state lives in memory, not a database — fine at the scale a room-based
(not auto-matchmaking) game expects.

## 4. Record and replay a session

Every game — CLI or networked — can be recorded and replayed exactly, decision for decision,
with no input needed the second time.

**Record while playing (CLI):**
```bash
python3 main.py --record my_session.json
```
Plays completely normally; every bid/pass/discard you make is logged to `my_session.json` as you
make it, alongside the seed used for that game (auto-generated if you didn't pass `--seed`).

**Record while playing (networked):**
```bash
python3 network_server.py --players 2 --record my_session.json
```
Same idea — the server logs every connected player's decisions. Players connect with the normal
`network_client.py` command; nothing changes on their end.

**Replay any recording (always via `main.py`, regardless of how it was recorded):**
```bash
python3 main.py --replay my_session.json
```
Reconstructs the players from the recording and feeds back the exact same decisions in the exact
same order — no typing needed, and it reproduces the identical final outcome (points, money,
winner) every time.

### Does replay work across both CLI and networked games?

**Yes.** Replay never touches the network layer at all, no matter which mode originally produced
the recording — it only needs a `CLIPlayer`-like object to hold state (money, status cards,
points) while the recorded decisions are fed back into it. So a session recorded from a real
multi-machine networked game replays through the exact same `python3 main.py --replay` command as
a session recorded from local hot-seat CLI play. This has been verified with an automated test
that records a real game over actual sockets and replays it with zero networking involved,
producing the identical outcome (`tests/network/test_end_to_end_socket.py::test_recorded_network_game_replays_identically_via_a_plain_cli_replay`).

### What this is useful for

- **Debugging an edge case you hit while playing**: play normally with `--record`, and if
  something looks wrong, the recording is already saved — no need to reproduce it by hand.
- **Regression tests**: drop a recording anywhere and write a small pytest that loads it, replays
  it, and asserts on the outcome — it'll keep verifying that exact scenario forever as the code
  changes.
- **Bot training data**: recordings are plain, readable JSON — a seed, a list of players, and
  each player's ordered decisions — straightforward to use as a dataset.

### Planning specific scenarios ahead of time

If you want to *engineer* a specific situation (rather than record one you stumbled into), inspect
what a seed deals before playing it:

```bash
python3 -m highsociety.code.gamecore.dev_tools.inspect_seed --seed 5
```

This prints the exact card order that seed produces (and where the green-card limit will end the
game), so you can plan scripted decisions around a known sequence of auctions. See
`tests/game_manager/test_scenario_faux_pas_branches.py` for an
example of one seed driving two different, deliberately-scripted outcomes.
