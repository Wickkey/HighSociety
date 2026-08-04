# HighSociety

A Python implementation of the High Society card game backend.

## Requirements

Python 3.9+, no third-party packages needed to play. For running tests:

```
pip install -r tests/requirements.txt
```

## Playing

See [PLAYING.md](PLAYING.md) for full instructions: local hot-seat CLI, networked multiplayer
(server + per-player clients + spectators), and how to record/replay a session (works
identically whether the recording came from CLI or networked play).

Quick start:

```
python3 main.py                              # local, one terminal, hot-seat
python3 network_server.py --players 2        # networked: host
python3 network_client.py --host <ip> --port 8888   # networked: each player
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

## Status

CLI and networked play are both functional, covered by the test suite under
`tests/` (including a real-socket end-to-end game in
`tests/network/test_end_to_end_socket.py`).

## Architecture: adding a new frontend (e.g. a web client)

`PlayGame` (`highsociety/code/gamecore/game_manager/gameplay.py`) never knows or cares whether a
player is local or remote — it only calls `get_bid()` / `choose_painting_to_discard()` /
`send_message()` / reads `.active` on whatever player objects it's given. `CLIPlayer` and
`NetworkPlayer` are just two implementations of that same informal interface.

Remote play is itself layered so a new transport doesn't require touching player logic or the
engine:

- **`highsociety/code/gamecore/network/transport.py`** — `Transport`, an ABC for "send one JSON
  message" / "receive the next one". `SocketTransport` is the only implementation today (raw TCP).
- **`highsociety/code/gamecore/network/protocol.py`** — the JSON message *shapes* (`PLAYER_MOVE`,
  `PLAYER_INFO`, etc.), independent of how those bytes move.
- **`NetworkPlayer` / `NetworkSpectator`** — thin adapters gluing a `Transport` + the protocol
  builders into the `BasePlayer`-shaped interface `PlayGame` expects. No socket/threading code of
  their own.

A browser-based client means adding a `WebSocketTransport` (or similar) implementing the same
`Transport` interface — `NetworkPlayer`, `network/protocol.py`, and the entire game engine stay
unchanged. `network_server.py` would just construct that transport instead of `SocketTransport`
when accepting a web connection.
