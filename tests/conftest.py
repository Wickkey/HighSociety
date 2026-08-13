import os
import time

import pytest

from highsociety.code.gamecore.player.cliplayer import CLIPlayer
from highsociety.code.gamecore.components_module.painting import Painting


@pytest.fixture(autouse=True)
def no_countdown_sleep(monkeypatch):
    """Game countdowns/turn pacing use time.sleep; skip it so tests run instantly."""
    monkeypatch.setattr(time, "sleep", lambda *a, **kw: None)


@pytest.fixture(autouse=True, scope="session")
def no_real_database():
    """
    Strips DATABASE_URL out of the environment for the ENTIRE test session
    (not just per-test), regardless of what a developer's local .env happens
    to have configured for real local development.

    This has to be session-scoped, not a per-test monkeypatch: a game's
    completion calls game_history.record_finished_game_async, which spawns a
    background daemon thread and returns immediately -- the test function
    that triggered it typically returns (and a per-test monkeypatch fixture
    reverts, restoring the real DATABASE_URL) before that thread gets
    scheduled. A per-test fixture was tried first and still let real rows
    through for exactly this reason: is_configured() re-reads os.environ
    fresh whenever the thread actually runs, which can be *after* teardown
    already put the real URL back. Stripping it once for the whole session
    closes that race, since there's no per-test boundary for a stray thread
    to outlive.

    Also has to set DATABASE_URL to "" rather than deleting it outright:
    tests/network/test_web_server.py's running_web_server fixture calls
    web_server.app.run(), and Flask's dev server re-triggers python-dotenv's
    load_dotenv() internally -- which only fills in variables that are
    completely ABSENT from os.environ. A deleted key is absent, so it gets
    silently refilled with the real value from .env on the very first test
    that starts the dev server; an explicitly empty string still counts as
    "present" and is left alone. Confirmed via direct instrumentation: with
    a plain pop(), is_configured() saw the real Supabase URL again the
    moment a game-playing background thread checked it, despite this
    fixture having already "removed" it at session start -- and real rows
    (fake players, fake games) kept appearing in production even after this
    fixture's first (pop-based) version was added.

    The one test that actually wants to exercise the DB-write path
    (test_finished_game_is_recorded_when_a_database_is_configured) sets its
    own fake DATABASE_URL via monkeypatch and mocks game_history._connect
    itself, so it's unaffected by this stripping it back out for everyone
    else -- and being function-scoped, that monkeypatch reverts before this
    session-scoped fixture's own restore, so ordering is safe either way.
    """
    original = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = ""
    yield
    if original is not None:
        os.environ["DATABASE_URL"] = original
    else:
        os.environ.pop("DATABASE_URL", None)


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

    def send_message(self, message, message_type=None, created_at=None, **kwargs):
        self.messages.append(message)

    def choose_painting_to_discard(self):
        paintings = [c for c in self.status_cards if isinstance(c, Painting)]
        return paintings[0] if paintings else None


@pytest.fixture
def make_player():
    def _make(name="P", username=None, actions=None, default_action="pass"):
        return ScriptedPlayer(name=name, username=username or name.lower(), actions=actions, default_action=default_action)
    return _make
