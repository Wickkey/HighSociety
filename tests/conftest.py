import time

import pytest

from highsociety.code.gamecore.player.cliplayer import CLIPlayer
from highsociety.code.gamecore.components_module.painting import Painting


@pytest.fixture(autouse=True)
def no_countdown_sleep(monkeypatch):
    """Game countdowns/turn pacing use time.sleep; skip it so tests run instantly."""
    monkeypatch.setattr(time, "sleep", lambda *a, **kw: None)


class ScriptedPlayer(CLIPlayer):
    """
    A CLIPlayer whose bids/discards come from a pre-programmed queue instead
    of stdin, for deterministic auction/game tests. Falls back to `default_action`
    (pass, by default) once the script is exhausted.
    """

    def __init__(self, name: str, username: str, actions=None, default_action="pass"):
        super().__init__(name, username)
        self._actions = list(actions or [])
        self._default_action = default_action
        self.messages = []

    def get_bid(self, timeout=None):
        if self._actions:
            return self._actions.pop(0)
        return self._default_action

    def send_message(self, message, message_type=None, created_at=None):
        self.messages.append(message)

    def choose_painting_to_discard(self):
        paintings = [c for c in self.status_cards if isinstance(c, Painting)]
        return paintings[0] if paintings else None


@pytest.fixture
def make_player():
    def _make(name="P", username=None, actions=None, default_action="pass"):
        return ScriptedPlayer(name=name, username=username or name.lower(), actions=actions, default_action=default_action)
    return _make
