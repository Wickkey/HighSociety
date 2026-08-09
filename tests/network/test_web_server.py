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
        # _recv() doesn't go through _loop(), so it never lands in
        # self.received on its own — record it so rejoin_token() (and any
        # other post-handshake inspection) can actually find it.
        with self._lock:
            self.received.append(welcome)

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

    def rejoin_token(self):
        """The token issued at IDENTIFY_SUCCESS (see web_server.py's
        ws_player) — None if this client's handshake hasn't completed yet."""
        for msg in self.messages_of_type("IDENTIFY_SUCCESS"):
            token = (msg.get("data") or {}).get("rejoin_token")
            if token:
                return token
        return None


class ReconnectingWSClient(ScriptedWSClient):
    """Connects straight to a rejoin-token URL — no IDENTIFY/IDENTIFY_ACK
    exchange, since the server skips that entirely for a valid reconnect
    (see web_server.py's _handle_player_reconnect)."""

    def handshake(self):
        welcome = self._recv()
        assert welcome.get("message_type") == "IDENTIFY_SUCCESS", welcome
        with self._lock:
            self.received.append(welcome)


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
    # Bot names are randomly assigned (see highsociety/code/ai/bot_names.py),
    # not a fixed "pass1" pattern — just check the shape.
    assert len(body["joined"]) == 1
    assert body["joined"][0]["is_bot"] is True
    assert body["joined"][0]["name"].lower() == body["joined"][0]["username"]
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


def test_create_game_validates_and_normalizes_turn_time_limit(running_web_server):
    client = web_server.app.test_client()

    with_limit = client.post(
        "/api/create_game", json={"seats": 2, "bot_mix": ["pass"], "turn_time_limit": 30}
    ).get_json()
    assert with_limit["turn_time_limit"] == 30.0

    no_limit_specified = client.post(
        "/api/create_game", json={"seats": 2, "bot_mix": ["pass"]}
    ).get_json()
    assert no_limit_specified["turn_time_limit"] is None


def test_add_bot_fills_an_empty_seat_and_can_start_the_game(running_web_server):
    client = web_server.app.test_client()

    room = client.post("/api/create_game", json={"seats": 2, "bot_mix": []}).get_json()
    room_code = room["room_code"]
    assert room["human_seats"] == 2
    assert room["joined"] == []

    resp = client.post("/api/add_bot", json={"room": room_code, "bot_type": "greedy"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["human_seats"] == 1  # one seat converted from human to bot
    assert len(body["joined"]) == 1
    assert body["joined"][0]["is_bot"] is True
    # Bot names are randomly assigned, not hardcoded — just check the shape.
    assert body["joined"][0]["name"].lower() == body["joined"][0]["username"]
    assert web_server._rooms[room_code].state == "lobby"  # still one seat open

    # An unknown bot type is rejected.
    bad = client.post("/api/add_bot", json={"room": room_code, "bot_type": "nope"})
    assert bad.status_code == 400

    # A bad room code is rejected.
    missing = client.post("/api/add_bot", json={"room": "NOPE", "bot_type": "greedy"})
    assert missing.status_code == 404

    # Filling the last seat with a bot starts the game immediately, same as
    # a human filling it would.
    resp2 = client.post("/api/add_bot", json={"room": room_code, "bot_type": "pass"})
    assert resp2.status_code == 200
    deadline = time.time() + 10
    room_obj = web_server._rooms[room_code]
    while time.time() < deadline and room_obj.state == "lobby":
        threading.Event().wait(0.1)
    assert room_obj.state in ("starting", "in_progress", "finished")

    # No seats left — the next add_bot attempt is rejected.
    full = client.post("/api/add_bot", json={"room": room_code, "bot_type": "greedy"})
    assert full.status_code == 409

    # 0/blank means "no limit", not "instant timeout" — the lobby form's
    # placeholder is "no limit" for an empty field, which the frontend sends
    # through as 0 or omits entirely; either way it must not create a game
    # nobody can ever actually move in.
    zero_limit = client.post(
        "/api/create_game", json={"seats": 2, "bot_mix": ["pass"], "turn_time_limit": 0}
    ).get_json()
    assert zero_limit["turn_time_limit"] is None

    bad_limit = client.post(
        "/api/create_game", json={"seats": 2, "bot_mix": ["pass"], "turn_time_limit": "soon"}
    )
    assert bad_limit.status_code == 400


def test_player_receives_a_live_countdown_when_the_host_sets_a_turn_time_limit(running_web_server):
    port = running_web_server
    room_code = web_server.app.test_client().post(
        "/api/create_game",
        json={"seats": 2, "bot_mix": ["pass"], "seed": 3, "bot_think_time": 0, "turn_time_limit": 30},
    ).get_json()["room_code"]

    player = ScriptedWSClient(_ws_url(port, f"/ws?room={room_code}"), "alice")
    player.handshake()
    player.start()

    deadline = time.time() + 15
    timer_messages = []
    while time.time() < deadline and not timer_messages:
        timer_messages = player.messages_of_type("PLAYER_MOVE_TIMER")
        threading.Event().wait(0.1)

    assert timer_messages, "player never received a PLAYER_MOVE_TIMER message"
    seconds_remaining = timer_messages[0]["data"]["seconds_remaining"]
    assert 0 < seconds_remaining <= 30

    player.close()


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

    # Player connections are deliberately kept open past game-end now (see
    # GameRoom.run_game — rematches reuse the same WebSocket), so
    # player._running no longer flips False on its own here; wait on the
    # room's own state instead, same as the rematch tests below.
    room = web_server._rooms[room_code]
    deadline = time.time() + 30
    while time.time() < deadline and room.state != "finished":
        threading.Event().wait(0.2)
    assert room.state == "finished"

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


def test_player_can_reconnect_after_disconnecting_mid_game(running_web_server):
    """
    A dropped connection (e.g. an accidental tab refresh) shouldn't
    permanently lock a player out of a game still in progress.

    Uses 3 real WS clients (no bots) with no --bot-think-time/turn-timer
    pacing to race against: with pytest's autouse time.sleep mock, a
    bot-only game resolves in well under a second (bots never sleep for
    real, and this suite's pacing/think-time sleeps are all no-ops), which
    left no reliable window to actually perform a reconnect before the game
    had already finished. With three real sockets, whoever's turn it is
    next simply blocks until *something* answers it (no turn_time_limit is
    configured), giving this test full deterministic control over pacing.
    """
    port = running_web_server
    room_code = web_server.app.test_client().post(
        "/api/create_game", json={"seats": 3, "bot_mix": [], "seed": 1}
    ).get_json()["room_code"]

    clients = {
        name: ScriptedWSClient(_ws_url(port, f"/ws?room={room_code}"), name)
        for name in ("alice", "bob", "carol")
    }
    for client in clients.values():
        client.handshake()  # game auto-starts once the 3rd seat completes its handshake

    # Find out whose turn actually comes up first (shuffled server-side) by
    # polling all three raw sockets round-robin, rather than assuming it's
    # alice — none of them have their auto-responder thread running yet, so
    # whoever doesn't get picked here just sits with an unanswered prompt,
    # which is fine (nothing times out with no turn_time_limit configured).
    mover_name = None
    deadline = time.time() + 10
    while time.time() < deadline and mover_name is None:
        for name, client in clients.items():
            try:
                raw = client.client.receive(timeout=0.2)
            except ConnectionClosed:
                continue
            if raw is None:
                continue
            with client._lock:
                client.received.append(json.loads(raw))
            if json.loads(raw).get("message_type") == "PLAYER_MOVE":
                mover_name = name
                break
    assert mover_name is not None, "nobody got a turn"

    mover = clients[mover_name]
    token = mover.rejoin_token()
    assert token, "no rejoin_token issued at IDENTIFY_SUCCESS"
    mover.client.close()  # simulate a refresh/dropped connection, no response sent

    room = web_server._rooms[room_code]
    mover_player = next(p for p in room.players if p.username == mover_name)
    deadline = time.time() + 10
    while time.time() < deadline and mover_player.active:
        threading.Event().wait(0.1)
    assert not mover_player.active, "server never noticed the disconnect"
    assert mover_player in room.players, "mid-game disconnect must keep the seat, not remove it"
    assert room.state == "in_progress", "the other two haven't answered anything yet — nothing should have resolved"

    # Reconnect with the same token/username and confirm the server marks
    # them active again immediately, with catch-up state waiting for them.
    reconnected = ReconnectingWSClient(
        _ws_url(port, f"/ws?room={room_code}&rejoin_token={token}"), mover_name,
    )
    reconnected.handshake()
    assert mover_player.active, "reattach() should mark the player active again"
    reconnected.start()  # start collecting: the catch-up messages arrive right after IDENTIFY_SUCCESS

    deadline = time.time() + 5
    while time.time() < deadline and not reconnected.messages_of_type("PLAYER_STATE"):
        threading.Event().wait(0.1)
    assert reconnected.messages_of_type("PLAYER_STATE"), "no reconnect catch-up state was sent"

    # Let everyone (the two original clients plus the reconnected one)
    # finish the game normally from here.
    for name, client in clients.items():
        if name != mover_name:
            client.start()

    deadline = time.time() + 20
    while time.time() < deadline and room.state != "finished":
        threading.Event().wait(0.2)
    assert room.state == "finished"

    # Confirm the reconnected player genuinely got to act again post-reconnect
    # — not just marked active with no real turn ever coming their way.
    assert reconnected.messages_of_type("PLAYER_MOVE"), "never got another turn after reconnecting"

    reconnected.close()
    for name, client in clients.items():
        if name != mover_name:
            client.close()


def test_reconnect_with_an_invalid_token_is_rejected(running_web_server):
    port = running_web_server
    room_code = web_server.app.test_client().post(
        "/api/create_game", json={"seats": 2, "bot_mix": ["pass"], "seed": 1, "bot_think_time": 0}
    ).get_json()["room_code"]

    bad = simple_websocket.Client(_ws_url(port, f"/ws?room={room_code}&rejoin_token=not-a-real-token"))
    msg = json.loads(bad.receive(timeout=5))
    assert msg["message_type"] == "IDENTIFY_ERROR"


def test_disconnecting_during_lobby_frees_the_seat(running_web_server):
    """
    Disconnecting before the game has even started is fully recoverable —
    nothing is at stake yet — so the seat should be freed entirely rather
    than left as a permanent ghost that blocks the room from ever filling
    (and blocks the same username from ever joining again).
    """
    port = running_web_server
    room_code = web_server.app.test_client().post(
        "/api/create_game", json={"seats": 2, "bot_mix": [], "seed": 1}
    ).get_json()["room_code"]

    alice = ScriptedWSClient(_ws_url(port, f"/ws?room={room_code}"), "alice")
    alice.handshake()
    alice.client.close()

    room = web_server._rooms[room_code]
    deadline = time.time() + 10
    while time.time() < deadline and any(p.username == "alice" for p in room.players):
        threading.Event().wait(0.1)
    assert not any(p.username == "alice" for p in room.players), \
        "a lobby-phase disconnect should free the seat, not leave a ghost"

    # The same username can now join fresh.
    alice2 = ScriptedWSClient(_ws_url(port, f"/ws?room={room_code}"), "alice")
    alice2.handshake()
    assert any(p.username == "alice" for p in room.players)
    alice2.close()


def test_resigning_permanently_forfeits_the_seat_unlike_a_dropped_connection(running_web_server):
    """
    The web UI's "Resign" button sends the same wire command a disconnect
    falls back to ("quit" — see NetworkPlayer.get_bid), but the two must not
    be treated the same for reconnection purposes: a dropped connection is
    recoverable (see the mid-game reconnect test above), an explicit
    resignation is not.
    """
    port = running_web_server
    room_code = web_server.app.test_client().post(
        "/api/create_game", json={"seats": 2, "bot_mix": ["pass"], "seed": 1, "bot_think_time": 0}
    ).get_json()["room_code"]

    alice = ScriptedWSClient(_ws_url(port, f"/ws?room={room_code}"), "alice")
    alice.handshake()
    token = alice.rejoin_token()
    assert token

    deadline = time.time() + 10
    got_move = False
    while time.time() < deadline and not got_move:
        raw = alice.client.receive(timeout=1.0)
        if raw is None:
            continue
        if json.loads(raw).get("message_type") == "PLAYER_MOVE":
            got_move = True
    assert got_move, "alice never got a turn"

    # Explicitly resign, same as clicking the web UI's Resign button.
    alice._send({"message_type": "RESPONSE", "prompt": "quit"})

    room = web_server._rooms[room_code]
    alice_player = next(p for p in room.players if p.username == "alice")
    deadline = time.time() + 10
    while time.time() < deadline and not alice_player.resigned:
        threading.Event().wait(0.1)
    assert alice_player.resigned, "explicit quit should mark the player as resigned"
    assert not alice_player.active

    reconnect_attempt = simple_websocket.Client(_ws_url(port, f"/ws?room={room_code}&rejoin_token={token}"))
    msg = json.loads(reconnect_attempt.receive(timeout=5))
    assert msg["message_type"] == "IDENTIFY_ERROR"
    assert "resigned" in msg["prompt"].lower()

    alice.close()


def test_single_player_rematch_reuses_the_connection_for_a_fresh_game(running_web_server):
    """
    With only one human at the table, requesting a rematch auto-accepts (the
    requester's own vote is enough — see _start_rematch_request) and should
    start a brand new game over the *same* WebSocket, no rejoin needed.
    """
    port = running_web_server
    room_code = web_server.app.test_client().post(
        "/api/create_game", json={"seats": 2, "bot_mix": ["pass"], "seed": 42, "bot_think_time": 0}
    ).get_json()["room_code"]

    alice = ScriptedWSClient(_ws_url(port, f"/ws?room={room_code}"), "alice")
    alice.handshake()
    alice.start()

    room = web_server._rooms[room_code]
    deadline = time.time() + 30
    while time.time() < deadline and room.state != "finished":
        threading.Event().wait(0.2)
    assert room.state == "finished"
    first_game = room.game

    # Player connections are kept open past game-end specifically for this
    # (see GameRoom.run_game) — the connection must still be usable here.
    assert alice.client.connected

    alice._send({"message_type": "REMATCH_REQUEST", "data": {"bot_mix": ["greedy"]}})

    deadline = time.time() + 10
    while time.time() < deadline and not alice.messages_of_type("REMATCH_STARTING"):
        threading.Event().wait(0.1)
    assert alice.messages_of_type("REMATCH_STARTING"), "alice never got REMATCH_STARTING"
    assert room.bot_mix == ["greedy"], "the requested bot mix should replace the room's old one"

    deadline = time.time() + 30
    while time.time() < deadline and (room.state != "finished" or room.game is first_game):
        threading.Event().wait(0.2)
    assert room.state == "finished"
    assert room.game is not None and room.game is not first_game, "rematch should run a brand new PlayGame"

    alice.close()


def test_rematch_needs_every_players_acceptance_and_a_decline_cancels_it(running_web_server):
    port = running_web_server
    room_code = web_server.app.test_client().post(
        "/api/create_game", json={"seats": 2, "bot_mix": [], "seed": 11, "bot_think_time": 0}
    ).get_json()["room_code"]

    alice = ScriptedWSClient(_ws_url(port, f"/ws?room={room_code}"), "alice")
    bob = ScriptedWSClient(_ws_url(port, f"/ws?room={room_code}"), "bob")
    alice.handshake()
    bob.handshake()
    alice.start()
    bob.start()

    room = web_server._rooms[room_code]
    deadline = time.time() + 30
    while time.time() < deadline and room.state != "finished":
        threading.Event().wait(0.2)
    assert room.state == "finished"

    alice._send({"message_type": "REMATCH_REQUEST", "data": {"bot_mix": []}})

    def _latest_update(client):
        updates = client.messages_of_type("REMATCH_UPDATE")
        return updates[-1] if updates else None

    deadline = time.time() + 10
    while time.time() < deadline and _latest_update(bob) is None:
        threading.Event().wait(0.1)
    update = _latest_update(bob)
    assert update is not None, "bob never got the rematch request"
    assert update["data"]["requested_by"] == "alice"
    assert update["data"]["votes"] == {"alice": True, "bob": None}
    assert room.rematch is not None

    # Bob declines — the whole thing is cancelled, not just his own seat.
    bob._send({"message_type": "REMATCH_VOTE", "data": {"accept": False}})

    deadline = time.time() + 10
    declined = False
    while time.time() < deadline and not declined:
        declined = any(
            m["data"]["declined_by"] == "bob" for m in alice.messages_of_type("REMATCH_DECLINED")
        )
        threading.Event().wait(0.1)
    assert declined, "alice never learned bob declined"
    assert room.rematch is None
    assert room.state == "finished", "a declined rematch must not start a new game"

    # A second request, this time accepted by everyone, does start one. With
    # bot_think_time=0 and both sides auto-passing, a rematch can run to
    # completion (finished -> starting -> in_progress -> finished again)
    # faster than a poll interval here could ever catch it mid-flight — so
    # rather than racing to observe a transient state, just confirm a *new*
    # PlayGame instance actually ran (room.game's identity changes).
    first_game = room.game
    updates_before = len(bob.messages_of_type("REMATCH_UPDATE"))
    alice._send({"message_type": "REMATCH_REQUEST", "data": {"bot_mix": []}})
    deadline = time.time() + 10
    while time.time() < deadline and len(bob.messages_of_type("REMATCH_UPDATE")) <= updates_before:
        threading.Event().wait(0.05)
    assert len(bob.messages_of_type("REMATCH_UPDATE")) > updates_before, "bob never got the second rematch request"
    bob._send({"message_type": "REMATCH_VOTE", "data": {"accept": True}})

    deadline = time.time() + 10
    while time.time() < deadline and room.game is first_game:
        threading.Event().wait(0.05)
    assert room.game is not None and room.game is not first_game, "unanimous acceptance should start a new game"

    alice.close()
    bob.close()
