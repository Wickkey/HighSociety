import itertools
import json
import threading
import time

import pytest

# CLI/socket play has zero third-party dependencies (see README.md); only the
# web path needs flask/flask-sock installed (requirements.txt). Skip this
# whole module rather than failing collection for anyone running the suite
# without them.
simple_websocket = pytest.importorskip("simple_websocket")
Client, ConnectionClosed = simple_websocket.Client, simple_websocket.ConnectionClosed
web_server = pytest.importorskip("web_server")

_port_counter = itertools.count(19500, 1)


class ScriptedWSClient:
    """
    Drives the same JSON-over-WebSocket protocol app.js speaks, via a real
    WebSocket connection — the browser-side counterpart to
    tests/network/test_end_to_end_socket.py's ScriptedSocketClient. Always
    answers "pass" to a bid and the first allowed painting to a discard
    prompt, so a whole game completes deterministically and quickly.
    """

    def __init__(self, url, username):
        self.username = username
        self.client = Client(url)
        self.received = []
        self._lock = threading.Lock()
        self._running = True
        self._thread = None

    def handshake(self):
        self._recv()  # "Enter your username"
        self._send({"message_type": "IDENTIFY_ACK", "prompt": self.username})
        self._recv()  # "Enter your display name"
        self._send({"message_type": "IDENTIFY_ACK", "prompt": f"{self.username}-display"})
        welcome = self._recv()
        assert welcome.get("message_type") == "IDENTIFY_SUCCESS", welcome

    def _send(self, payload):
        self.client.send(json.dumps(payload))

    def _recv(self, timeout=5):
        raw = self.client.receive(timeout=timeout)
        return json.loads(raw) if raw is not None else {}

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while self._running:
            try:
                raw = self.client.receive(timeout=1.0)
            except ConnectionClosed:
                self._running = False
                break
            if raw is None:
                continue
            payload = json.loads(raw)
            with self._lock:
                self.received.append(payload)
            if payload.get("message_type") == "PLAYER_MOVE":
                try:
                    self._send({"message_type": "RESPONSE", "prompt": self._answer(payload)})
                except (OSError, ConnectionClosed):
                    # The game may have already ended and closed our socket
                    # between receiving this prompt and answering it (a real
                    # race, not a bug — see ScriptedSocketClient's identical
                    # guard in test_end_to_end_socket.py).
                    pass

    def _answer(self, payload):
        if payload.get("move_type") == "discard_painting":
            allowed = (payload.get("constraints") or {}).get("allowed_paintings") or []
            if allowed:
                return str(allowed[0])
        return "pass"

    def prompts(self):
        with self._lock:
            return [p.get("prompt", "") for p in self.received]

    def messages_of_type(self, message_type):
        with self._lock:
            return [p for p in self.received if p.get("message_type") == message_type]

    def close(self):
        self._running = False
        try:
            self.client.close()
        except Exception:
            pass


@pytest.fixture
def running_web_server():
    web_server._room = None  # each test starts from a clean slate
    port = next(_port_counter)
    thread = threading.Thread(
        target=web_server.app.run,
        kwargs={"host": "127.0.0.1", "port": port, "threaded": True, "use_reloader": False},
        daemon=True,
    )
    thread.start()
    threading.Event().wait(0.3)  # let the dev server actually start listening
    yield port
    web_server._room = None


def _ws_url(port, path):
    return f"ws://127.0.0.1:{port}{path}"


def test_create_status_and_config_endpoints(running_web_server):
    port = running_web_server
    client = web_server.app.test_client()

    assert client.get("/api/status").get_json() == {"exists": False}

    config = client.get("/api/config").get_json()
    assert config["min_players"] == 2

    resp = client.post("/api/create_game", json={"seats": 2, "bot_mix": ["pass"]})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["state"] == "lobby"
    assert body["human_seats"] == 1
    assert body["joined"] == [{"username": "pass1", "name": "Pass1", "is_bot": True}]

    # Can't reconfigure a room that's already accepting players.
    conflict = client.post("/api/create_game", json={"seats": 2, "bot_mix": ["pass"]})
    assert conflict.status_code == 409

    bad = client.post("/api/create_game", json={"seats": 2, "bot_mix": ["not-a-bot"]})
    assert bad.status_code == 400

    too_many_bots = client.post("/api/create_game", json={"seats": 2, "bot_mix": ["pass", "greedy"]})
    assert too_many_bots.status_code == 400


def test_full_game_over_websockets_against_a_bot(running_web_server):
    port = running_web_server
    resp = web_server.app.test_client().post(
        "/api/create_game", json={"seats": 2, "bot_mix": ["pass"], "seed": 42, "bot_think_time": 0}
    )
    assert resp.status_code == 200

    player = ScriptedWSClient(_ws_url(port, "/ws"), "alice")
    player.handshake()
    player.start()

    deadline = time.time() + 30
    while time.time() < deadline and player._running:
        threading.Event().wait(0.2)

    all_prompts = " ".join(player.prompts())
    assert "Game Started" in all_prompts or "🚀" in all_prompts
    assert "Game Concluded" in all_prompts
    assert player.messages_of_type("AUCTION_RESULT")
    assert player.messages_of_type("AUCTION_UPDATE")
    assert player.messages_of_type("PLAYER_STATE")

    # The room's own status should now reflect a finished game with standings.
    status = web_server.app.test_client().get("/api/status").get_json()
    assert status["state"] == "finished"
    assert status["winners"] is not None
    assert len(status["final_standings"]) == 2

    player.close()


def test_spectator_sees_the_game_live(running_web_server):
    port = running_web_server
    web_server.app.test_client().post(
        "/api/create_game", json={"seats": 2, "bot_mix": ["pass"], "seed": 7, "bot_think_time": 0}
    )

    spectator = Client(_ws_url(port, "/ws_spectate"))
    spectator.receive(timeout=5)  # "Enter your name"
    spectator.send(json.dumps({"message_type": "IDENTIFY_ACK", "prompt": "watcher"}))
    spectator.receive(timeout=5)  # "Enter your username"
    spectator.send(json.dumps({"message_type": "IDENTIFY_ACK", "prompt": "watcher-user"}))
    welcome = json.loads(spectator.receive(timeout=5))
    assert welcome["message_type"] == "IDENTIFY_SUCCESS"

    player = ScriptedWSClient(_ws_url(port, "/ws"), "bob")
    player.handshake()
    player.start()

    seen_auction_result = False
    deadline = time.time() + 15
    while time.time() < deadline and not seen_auction_result:
        raw = spectator.receive(timeout=1.0)
        if raw is None:
            continue
        msg = json.loads(raw)
        if msg.get("message_type") == "AUCTION_RESULT":
            seen_auction_result = True

    assert seen_auction_result, "spectator never received a live AUCTION_RESULT"

    player.close()
    spectator.close()


def test_sending_to_a_dead_websocket_marks_the_player_inactive_instead_of_crashing(running_web_server):
    """
    Regression test for a crash found by manually driving a real browser
    against web_server.py: a browser tab closing mid-game used to crash the
    whole game thread the next time NetworkPlayer tried to broadcast to it.
    simple_websocket.Server.send() raises its own ConnectionClosed on a dead
    connection — NetworkPlayer.send_message() didn't know to catch that (it
    only expected BrokenPipeError/ConnectionResetError/socket.error, the
    exceptions a dead raw TCP socket raises). See WebSocketTransport.send()
    in network/transport.py, which now translates ConnectionClosed into
    BrokenPipeError so NetworkPlayer's existing handling covers it unchanged.

    Deliberately bypasses PlayGame entirely and forces `active = True` right
    before sending: driving this through a real game is a race
    (web_server.py's own per-connection loop also reacts to a dead transport
    by setting `active = False`, and — fatally for a timing-based test — the
    whole suite monkeypatches time.sleep to a no-op, so that reactive loop
    busy-spins and usually wins the race before the game engine ever
    broadcasts again, hiding the bug). Asserting directly on send_message()
    is what actually pins the fix regardless of scheduling.
    """
    port = running_web_server
    web_server.app.test_client().post(
        "/api/create_game", json={"seats": 2, "bot_mix": [], "seed": 1, "bot_think_time": 0}
    )
    bob = ScriptedWSClient(_ws_url(port, "/ws"), "bob")
    bob.handshake()
    bob.client.close()

    bob_player = next(p for p in web_server._room.players if p.username == "bob")
    deadline = time.time() + 10
    while time.time() < deadline and bob_player.transport.is_connected:
        threading.Event().wait(0.1)
    assert not bob_player.transport.is_connected, "server never noticed bob's connection close"

    bob_player.active = True  # force the exact "still marked active, but the transport is already dead" state
    bob_player.send_message("test broadcast", message_type="GLOBAL_EVENT")  # must not raise
    assert bob_player.active is False
