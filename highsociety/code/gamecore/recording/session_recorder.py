import json
from pathlib import Path
from typing import Union


class SessionRecorder:
    """
    Collects every decision RecordingPlayer wrappers report during a game and
    persists them, keyed by username, alongside the seed the game was played
    with — so the whole session can be reproduced exactly via ReplayPlayer.

    File format:
        {
            "seed": 12345,
            "players": [{"username": "alice", "name": "Alice"}, ...],
            "actions": {
                "alice": [
                    {"type": "get_bid", "result": [1], "active_after": true},
                    {"type": "get_bid", "result": "pass", "active_after": true},
                    ...
                ],
                "bob": [...]
            }
        }

    `active_after` captures the player's `.active` flag immediately after the
    call — some player implementations mutate it as a side effect invisible
    in the return value alone (e.g. CLIPlayer marking itself inactive on an
    EOF while retrying an invalid discard choice). Replaying just the return
    values without this would let a player who should have gone inactive keep
    getting asked for further turns, eventually running the recording dry.
    """

    def __init__(self, path: Union[str, Path], seed: int):
        self.path = Path(path)
        self.seed = seed
        self.players: list[dict] = []
        self.actions: dict[str, list[dict]] = {}

    def register_player(self, username: str, name: str) -> None:
        self.players.append({"username": username, "name": name})
        self.actions.setdefault(username, [])

    def log(self, username: str, action_type: str, result, active_after: bool) -> None:
        self.actions.setdefault(username, []).append(
            {"type": action_type, "result": result, "active_after": active_after}
        )
        self._flush()

    def _flush(self) -> None:
        payload = {
            "seed": self.seed,
            "players": self.players,
            "actions": self.actions,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(payload, f, indent=2)

    @staticmethod
    def load(path: Union[str, Path]) -> dict:
        with open(path, "r") as f:
            return json.load(f)
