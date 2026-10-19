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

    The same goes for every other credential a developer's .env may hold
    (see _BLANKED_FOR_TESTS) -- a real, reported incident: once the
    self-play job learned to email progress (NOTIFY_SMTP_*), the test
    suite's own tiny practice batches sent real "Self-play done" emails to
    the developer's inbox, because importing web_server.py loads .env.
    """
    originals = {name: os.environ.get(name) for name in _BLANKED_FOR_TESTS}
    for name in _BLANKED_FOR_TESTS:
        os.environ[name] = ""
    yield
    for name, original in originals.items():
        if original is not None:
            os.environ[name] = original
        else:
            os.environ.pop(name, None)


# Real credentials that may sit in a developer's .env -- blanked (not
# deleted, see no_real_database above) for the whole test session.
_BLANKED_FOR_TESTS = (
    "DATABASE_URL",
    "NOTIFY_SMTP_USER", "NOTIFY_SMTP_PASSWORD", "NOTIFY_EMAIL_TO",
    "RENDER_API_KEY",
)


@pytest.fixture(autouse=True)
def no_real_email(monkeypatch):
    """Belt and braces on top of blanking NOTIFY_SMTP_*: no test may open a
    real mail connection. A test that exercises the sending path patches
    smtplib.SMTP_SSL itself (see tests/common/test_notify.py), which takes
    precedence over this."""
    import smtplib

    def _refuse(*args, **kwargs):
        raise RuntimeError("tests must never send real email")

    monkeypatch.setattr(smtplib, "SMTP_SSL", _refuse)
    monkeypatch.setattr(smtplib, "SMTP", _refuse)


@pytest.fixture(autouse=True)
def close_leftover_websocket_clients(monkeypatch):
    """Closes every simple_websocket.Client a test opened and left open.
    Each client reads on its own non-daemon thread, and the test servers
    run on daemon threads that never shut down, so one forgotten client
    kept the whole pytest process alive after "N passed" -- holding its
    ports, which then broke the next run's web-server tests."""
    try:
        import simple_websocket
    except ImportError:
        yield
        return
    opened = []
    original_init = simple_websocket.Client.__init__

    def tracking_init(self, *args, **kwargs):
        opened.append(self)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(simple_websocket.Client, "__init__", tracking_init)
    yield
    for client in opened:
        if client.connected:
            try:
                client.close()
            except Exception:
                pass  # already closing/closed from the other side


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
        # Full record of every send_message() call (message_type/data
        # included, not just the string) -- .messages above only ever kept
        # the plain-text prompt, which can't tell apart e.g. two different
        # AUCTION_UPDATE kinds sent with the same empty prompt string.
        self.sent_messages = []

    def get_bid(self, timeout=None):
        if self._actions:
            return self._actions.pop(0)
        return self._default_action

    def send_message(self, message, message_type=None, created_at=None, **kwargs):
        self.messages.append(message)
        self.sent_messages.append({"message": message, "message_type": message_type, **kwargs})

    def choose_painting_to_discard(self):
        paintings = [c for c in self.status_cards if isinstance(c, Painting)]
        return paintings[0] if paintings else None


@pytest.fixture
def make_player():
    def _make(name="P", username=None, actions=None, default_action="pass"):
        return ScriptedPlayer(name=name, username=username or name.lower(), actions=actions, default_action=default_action)
    return _make
