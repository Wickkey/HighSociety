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
