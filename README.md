# HighSociety

A Python implementation of the High Society card game backend.

## Requirements

Python 3.9+, no third-party packages needed to play. For running tests:

```
pip install -r highsociety/code/gamecore/unittest/requirements.txt
```

## Play locally (CLI)

```
python3 main.py
```

Prompts for number of players, then each player's username/display name, then runs the game
in the current terminal (all players share the same terminal, taking turns).

## Play over the network

On the host machine, start the server (waits for the given number of players to connect):

```
python3 network_server.py --players 2
```

It prints the host's IP and port. Each player then runs, on their own machine/terminal:

```
python3 network_client.py --host <server-ip> --port 8888
```

Optionally, spectators can watch (read-only) by connecting to `<port + 1>`:

```
python3 network_spectator_client.py --host <server-ip> --port 8889
```

## Deterministic games, recording, and replay

`PlayGame(..., seed=N)` (or `python3 main.py --seed N`) makes an entire game — deck order,
player order, starting player — 100% reproducible.

To inspect what a seed deals before playing it:

```
python3 -m highsociety.code.gamecore.dev_tools.inspect_seed --seed 5
python3 -m highsociety.code.gamecore.dev_tools.inspect_seed --seed 5 --save my_scenario --description "..."
python3 -m highsociety.code.gamecore.dev_tools.inspect_seed --list
```

Saved scenarios land in `highsociety/code/gamecore/unittest/scenarios/` and only record the
seed + resulting card order — they're a planning aid for writing `ScriptedPlayer`-based tests
(see `unittest/game_manager/test_scenario_faux_pas_branches.py`) that explore different
outcomes from the same underlying deck.

To capture and replay a *real* play session (e.g. to build a regression test from something you
hit while actually playing, or as training data for a bot):

```
python3 main.py --seed 5 --record my_session.json   # play normally; every decision gets logged
python3 main.py --replay my_session.json             # re-plays it exactly, no input needed
```

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
