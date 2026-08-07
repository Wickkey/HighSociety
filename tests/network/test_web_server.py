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

# test_end_to_end_socket.py's fixtures scatter across roughly 19100-21299
# (19100/19600/20700/20800 + id(thread) % 500, plus a 21000-step-2 counter) —
# starting well clear of that avoids an intermittent "port already in use"
# collision between the two files when the whole suite runs in one process.
_port_counter = itertools.count(25000, 1)


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
    web_server._rooms.clear()  # each test starts from a clean slate
    port = next(_port_counter)
    thread = threading.Thread(
        target=web_server.app.run,
        kwargs={"host": "127.0.0.1", "port": port, "threaded": True, "use_reloader": False},
        daemon=True,
    )
    thread.start()
    threading.Event().wait(0.3)  # let the dev server actually start listening
    yield port
    web_server._rooms.clear()


def _ws_url(port, path):
    return f"ws://127.0.0.1:{port}{path}"


def test_create_status_and_config_endpoints(running_web_server):
    port = running_web_server
    client = web_server.app.test_client()

    # No room param (or an unknown one) means "no such room" — not a crash.
    assert client.get("/api/status").get_json() == {"exists": False}
    assert client.get("/api/status?room=NOPE").get_json() == {"exists": False}

    config = client.get("/api/config").get_json()
    assert config["min_players"] == 2

    resp = client.post("/api/create_game", json={"seats": 2, "bot_mix": ["pass"]})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["state"] == "lobby"
    assert body["visibility"] == "public"  # default
    assert body["human_seats"] == 1
    assert body["joined"] == [{"username": "pass1", "name": "Pass1", "is_bot": True}]
    room_code = body["room_code"]
    assert room_code

    # Fetching that room's status by code returns the same room.
    status = client.get(f"/api/status?room={room_code}").get_json()
    assert status["room_code"] == room_code

    # Multiple rooms can exist at once now — creating another game doesn't
    # conflict with the first, and each gets its own room code.
    second = client.post("/api/create_game", json={"seats": 2, "bot_mix": ["pass"]})
    assert second.status_code == 200
    second_code = second.get_json()["room_code"]
    assert second_code != room_code

    bad = client.post("/api/create_game", json={"seats": 2, "bot_mix": ["not-a-bot"]})
    assert bad.status_code == 400

    too_many_bots = client.post("/api/create_game", json={"seats": 2, "bot_mix": ["pass", "greedy"]})
    assert too_many_bots.status_code == 400

    bad_visibility = client.post(
        "/api/create_game", json={"seats": 2, "bot_mix": ["pass"], "visibility": "hidden"}
    )
    assert bad_visibility.status_code == 400


def test_rooms_listing_shows_only_open_public_rooms(running_web_server):
    client = web_server.app.test_client()

    public_room = client.post(
        "/api/create_game", json={"seats": 2, "bot_mix": ["pass"], "visibility": "public"}
    ).get_json()
    private_room = client.post(
        "/api/create_game", json={"seats": 2, "bot_mix": ["pass"], "visibility": "private"}
    ).get_json()

    rooms = client.get("/api/rooms").get_json()["rooms"]
    codes = {r["room_code"] for r in rooms}
    assert public_room["room_code"] in codes
    assert private_room["room_code"] not in codes  # private rooms aren't listed


def test_full_game_over_websockets_against_a_bot(running_web_server):
    port = running_web_server
    resp = web_server.app.test_client().post(
        "/api/create_game", json={"seats": 2, "bot_mix": ["pass"], "seed": 42, "bot_think_time": 0}
    )
    assert resp.status_code == 200
    room_code = resp.get_json()["room_code"]

    player = ScriptedWSClient(_ws_url(port, f"/ws?room={room_code}"), "alice")
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
    status = web_server.app.test_client().get(f"/api/status?room={room_code}").get_json()
    assert status["state"] == "finished"
    assert status["winners"] is not None
    assert len(status["final_standings"]) == 2

    player.close()


def test_spectator_sees_the_game_live(running_web_server):
    port = running_web_server
    room_code = web_server.app.test_client().post(
        "/api/create_game", json={"seats": 2, "bot_mix": ["pass"], "seed": 7, "bot_think_time": 0}
    ).get_json()["room_code"]

    spectator = Client(_ws_url(port, f"/ws_spectate?room={room_code}"))
    spectator.receive(timeout=5)  # "Enter your name"
    spectator.send(json.dumps({"message_type": "IDENTIFY_ACK", "prompt": "watcher"}))
    spectator.receive(timeout=5)  # "Enter your username"
    spectator.send(json.dumps({"message_type": "IDENTIFY_ACK", "prompt": "watcher-user"}))
    welcome = json.loads(spectator.receive(timeout=5))
    assert welcome["message_type"] == "IDENTIFY_SUCCESS"

    player = ScriptedWSClient(_ws_url(port, f"/ws?room={room_code}"), "bob")
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


def test_players_can_chat_live_without_it_being_mistaken_for_a_bid(running_web_server):
    """
    Players can chat with each other (and spectators) mid-game. This has to
    be delivered the instant it's sent, not just whenever the sender's next
    turn happens to come up — and it must never be handed to
    NetworkPlayer.get_bid()/choose_painting_to_discard() as if it were the
    real move response. See WebSocketTransport's on_chat filtering (a
    background reader thread that intercepts CHAT before it ever reaches the
    queue those methods read from) and web_server.py's _relay_player_chat.
    """
    port = running_web_server
    room_code = web_server.app.test_client().post(
        "/api/create_game", json={"seats": 2, "bot_mix": [], "seed": 11, "bot_think_time": 0}
    ).get_json()["room_code"]

    alice = ScriptedWSClient(_ws_url(port, f"/ws?room={room_code}"), "alice")
    bob = ScriptedWSClient(_ws_url(port, f"/ws?room={room_code}"), "bob")
    alice.handshake()
    bob.handshake()

    spectator = Client(_ws_url(port, f"/ws_spectate?room={room_code}"))
    spectator.receive(timeout=5)  # "Enter your name"
    spectator.send(json.dumps({"message_type": "IDENTIFY_ACK", "prompt": "watcher"}))
    spectator.receive(timeout=5)  # "Enter your username"
    spectator.send(json.dumps({"message_type": "IDENTIFY_ACK", "prompt": "watcher-user"}))
    spectator.receive(timeout=5)  # IDENTIFY_SUCCESS

    alice.start()
    bob.start()

    # Sent immediately, before either player has taken a single game action —
    # delivery must not wait for alice's own next turn.
    alice._send({"message_type": "CHAT", "prompt": "gl everyone!"})

    deadline = time.time() + 10
    bob_saw_it = False
    while time.time() < deadline and not bob_saw_it:
        bob_saw_it = any(
            m.get("prompt") == "💬 alice: gl everyone!" for m in bob.messages_of_type("CHAT")
        )
        threading.Event().wait(0.1)
    assert bob_saw_it, "bob never received alice's chat message"

    spec_saw_it = False
    spec_deadline = time.time() + 5
    while time.time() < spec_deadline and not spec_saw_it:
        raw = spectator.receive(timeout=0.5)
        if raw is None:
            continue
        msg = json.loads(raw)
        if msg.get("message_type") == "CHAT" and msg.get("prompt") == "💬 alice: gl everyone!":
            spec_saw_it = True
    assert spec_saw_it, "spectator never received the player's chat message"

    # The chat message must not have been consumed as alice's bid/pass —
    # play the game out normally afterward and confirm it completes cleanly.
    room = web_server._rooms[room_code]
    deadline = time.time() + 15
    while time.time() < deadline and room.state != "finished":
        threading.Event().wait(0.2)
    assert room.state == "finished"

    alice.close()
    bob.close()
    # By now the game has finished and GameRoom.run_game()'s own cleanup
    # (see web_server.py) has already closed every spectator's connection
    # server-side — closing it again here is redundant, and
    # simple_websocket.Client.close() raises ConnectionClosed on an
    # already-closed connection rather than being a harmless no-op.
    try:
        spectator.close()
    except ConnectionClosed:
        pass


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
    room_code = web_server.app.test_client().post(
        "/api/create_game", json={"seats": 2, "bot_mix": [], "seed": 1, "bot_think_time": 0}
    ).get_json()["room_code"]
    bob = ScriptedWSClient(_ws_url(port, f"/ws?room={room_code}"), "bob")
    bob.handshake()
    bob.client.close()

    bob_player = next(p for p in web_server._rooms[room_code].players if p.username == "bob")
    deadline = time.time() + 10
    while time.time() < deadline and bob_player.transport.is_connected:
        threading.Event().wait(0.1)
    assert not bob_player.transport.is_connected, "server never noticed bob's connection close"

    bob_player.active = True  # force the exact "still marked active, but the transport is already dead" state
    bob_player.send_message("test broadcast", message_type="GLOBAL_EVENT")  # must not raise
    assert bob_player.active is False
