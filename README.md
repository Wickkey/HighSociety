# HighSociety

A Python implementation of the High Society card game backend.

## Requirements

Python 3.9+, no third-party packages needed to play. For running tests:

```
pip install -r highsociety/code/gamecore/unittest/requirements.txt
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
`unittest/game_manager/test_scenario_faux_pas_branches.py`.

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
`highsociety/code/gamecore/unittest/` (including a real-socket end-to-end game in
`unittest/network/test_end_to_end_socket.py`).

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
