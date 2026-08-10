import json
import queue
import threading
import time

import pytest

from highsociety.code.gamecore.network.transport import WebSocketTransport

# NOTE: real delays here, not time.sleep() — the session's autouse fixture
# monkeypatches time.sleep to a no-op (see tests/conftest.py), which would
# turn these polling waits into busy-spins instead of real waits.


class FakeWebSocket:
    """Stands in for simple_websocket.Server: a queue of raw JSON strings
    fed in by the test (simulating the client), consumed by
    WebSocketTransport's background reader thread via receive(timeout=...)."""

    def __init__(self):
        self._incoming = queue.Queue()
        self.sent = []
        self.connected = True

    def push(self, payload: dict) -> None:
        self._incoming.put(json.dumps(payload))

    def receive(self, timeout=None):
        try:
            return self._incoming.get(timeout=timeout)
        except queue.Empty:
            return None

    def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def close(self):
        self.connected = False


@pytest.fixture
def transport_with_callbacks():
    ws = FakeWebSocket()
    chats = []
    resigns = []
    transport = WebSocketTransport(ws, label="test", on_chat=chats.append, on_resign=resigns.append)
    transport.start()
    yield transport, ws, chats, resigns
    transport.close()


def _wait_until(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        threading.Event().wait(0.02)
    return False


def test_chat_messages_are_relayed_not_queued(transport_with_callbacks):
    transport, ws, chats, resigns = transport_with_callbacks
    ws.push({"message_type": "CHAT", "prompt": "hello"})

    assert _wait_until(lambda: len(chats) == 1)
    assert chats[0]["prompt"] == "hello"
    assert transport.receive(timeout=0.3) is None  # never queued for the normal receive() path


def test_ordinary_messages_are_queued_for_receive(transport_with_callbacks):
    transport, ws, chats, resigns = transport_with_callbacks
    ws.push({"message_type": "RESPONSE", "prompt": "5"})

    result = transport.receive(timeout=2.0)
    assert result == {"message_type": "RESPONSE", "prompt": "5"}
    assert chats == []
    assert resigns == []


def test_resign_message_calls_on_resign_immediately(transport_with_callbacks):
    """The out-of-turn resign path (see web_server.py's on_resign) must fire
    the moment a RESIGN message arrives -- it can't wait for anyone to call
    receive(), since that might not happen again for a long time if it's not
    this player's turn."""
    transport, ws, chats, resigns = transport_with_callbacks
    ws.push({"message_type": "RESIGN"})

    assert _wait_until(lambda: len(resigns) == 1)


def test_resign_also_queues_a_synthetic_quit_response(transport_with_callbacks):
    """In case get_bid()/choose_painting_to_discard() *is* genuinely blocked
    waiting on this exact player right now (it's actually their live turn),
    this is what lets that call return promptly through the normal
    quit-handling path instead of hanging until a timeout (if any)."""
    transport, ws, chats, resigns = transport_with_callbacks
    ws.push({"message_type": "RESIGN"})

    result = transport.receive(timeout=2.0)
    assert result == {"message_type": "RESPONSE", "prompt": "quit"}


def test_resign_works_with_no_on_resign_callback():
    """A spectator's WebSocketTransport (or any other caller) that doesn't
    pass on_resign at all must not crash if a RESIGN somehow arrives -- the
    synthetic quit should still be queued regardless."""
    ws = FakeWebSocket()
    chats = []
    transport = WebSocketTransport(ws, label="test", on_chat=chats.append)
    transport.start()
    try:
        ws.push({"message_type": "RESIGN"})
        result = transport.receive(timeout=2.0)
        assert result == {"message_type": "RESPONSE", "prompt": "quit"}
    finally:
        transport.close()
