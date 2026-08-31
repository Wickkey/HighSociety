import itertools
import json
import threading
import time
from unittest.mock import MagicMock

import pytest

from highsociety.code.common import matchmaking
from highsociety.code.common.db import game_history
from highsociety.code.gamecore.card_manager.money_card_manager import MoneyCardManager

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


def test_compute_disconnect_grace_seconds_defaults_to_20s_when_untimed():
    assert web_server._compute_disconnect_grace_seconds(None) == 20.0


def test_compute_disconnect_grace_seconds_is_timer_over_5_when_timed():
    assert web_server._compute_disconnect_grace_seconds(50) == 10.0


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


def test_create_game_stores_host_username_for_game_history(running_web_server):
    client = web_server.app.test_client()

    resp = client.post(
        "/api/create_game", json={"seats": 2, "bot_mix": ["pass"], "host_username": "alice"}
    )
    assert resp.status_code == 200
    room_code = resp.get_json()["room_code"]
    assert web_server._rooms[room_code].host_username == "alice"

    # Omitted entirely -- purely informational, never required.
    no_host = client.post("/api/create_game", json={"seats": 2, "bot_mix": ["pass"]})
    assert no_host.status_code == 200
    assert web_server._rooms[no_host.get_json()["room_code"]].host_username is None

    bad_type = client.post(
        "/api/create_game", json={"seats": 2, "bot_mix": ["pass"], "host_username": 123}
    )
    assert bad_type.status_code == 400


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


def test_seed_is_reported_explicit_or_random(running_web_server):
    """The seed is shown in-game (see app.js's seed-display) so a game can
    be reported/reproduced precisely -- confirm both an explicitly chosen
    seed and an auto-generated one round-trip correctly through
    /api/create_game and /api/status. manual_seed distinguishes the two
    cases so the client only displays a seed someone actually typed in
    (see gameState.js's applyRoomDisplaySettings), not one nobody chose."""
    client = web_server.app.test_client()

    explicit = client.post(
        "/api/create_game", json={"seats": 2, "bot_mix": ["pass"], "seed": 424242}
    ).get_json()
    assert explicit["seed"] == 424242
    assert explicit["manual_seed"] is True
    status = client.get(f"/api/status?room={explicit['room_code']}").get_json()
    assert status["seed"] == 424242
    assert status["manual_seed"] is True

    random_seed = client.post(
        "/api/create_game", json={"seats": 2, "bot_mix": ["pass"]}
    ).get_json()
    assert isinstance(random_seed["seed"], int)  # some real seed was picked, not null/omitted
    assert random_seed["manual_seed"] is False


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

    # The lobby form only ever offers a fixed set of presets (see
    # _TURN_TIME_PRESETS) -- anything else, even a plausible-looking custom
    # value, is rejected rather than silently accepted.
    non_preset = client.post(
        "/api/create_game", json={"seats": 2, "bot_mix": ["pass"], "turn_time_limit": 45}
    )
    assert non_preset.status_code == 400


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


def test_finished_game_is_recorded_when_a_database_is_configured(running_web_server, monkeypatch):
    """Exercises the real _record_game_history translation (web_server.py)
    against a real finished game, with only the database connection itself
    faked out — see tests/common/test_game_history.py for game_history's own
    unit tests, which this deliberately doesn't re-duplicate."""
    port = running_web_server
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/testdb")
    game_history._schema_ready = True  # skip the real DDL round-trip for this test
    conn = MagicMock()
    cursor = MagicMock()
    ids = iter(range(1, 1000))
    # Second/third columns stand in for google_id/elo -- see game_history's
    # own _upsert_player (RETURNING id, google_id, elo); None means "guest,
    # no achievements/elo change", matching this test's own alice, who
    # isn't Google-linked.
    cursor.fetchone.side_effect = lambda: (next(ids), None, 1000)
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.__enter__.return_value = conn
    monkeypatch.setattr(game_history, "_connect", lambda: conn)
    monkeypatch.setattr(game_history, "_release_connection", lambda c: None)
    # Run synchronously so the test doesn't have to race a background thread
    # for a database write that, in production, is deliberately fire-and-forget.
    def _synchronous_record(on_complete=None, **kwargs):
        result = game_history.record_finished_game(**kwargs)
        if on_complete:
            on_complete(result)
    monkeypatch.setattr(game_history, "record_finished_game_async", _synchronous_record)

    resp = web_server.app.test_client().post(
        "/api/create_game", json={"seats": 2, "bot_mix": ["pass"], "seed": 42, "bot_think_time": 0}
    )
    room_code = resp.get_json()["room_code"]
    player = ScriptedWSClient(_ws_url(port, f"/ws?room={room_code}"), "alice")
    player.handshake()
    player.start()

    room = web_server._rooms[room_code]
    deadline = time.time() + 30
    while time.time() < deadline and room.state != "finished":
        threading.Event().wait(0.2)
    assert room.state == "finished"

    queries = [call.args[0] for call in cursor.execute.call_args_list]
    assert any("INSERT INTO games" in q for q in queries)
    player_games_calls = [c for c in cursor.execute.call_args_list if "INSERT INTO player_games" in c.args[0]]
    assert len(player_games_calls) == 2  # alice + the pass bot

    # One row per participant: the bot has no player_id (params[1]), the
    # human does -- see the player_xor_bot schema constraint.
    player_ids = sorted((c.args[1][1] is None) for c in player_games_calls)
    assert player_ids == [False, True]

    player.close()


def test_status_payload_exposes_elo_changes_once_the_async_write_completes(running_web_server, monkeypatch):
    """The post-game Elo reveal (rematch.js's revealEloChange) polls
    /api/status until elo_changes stops being null -- confirms the field
    exists, starts null the instant the room is marked finished (the
    real async write is still in flight), and becomes a real dict once
    GameRoom's on_complete callback (_store_elo_changes) has run. Doesn't
    re-duplicate game_history's own Elo-math unit tests -- alice here is
    a guest (no rating change), so `{}` is the expected settled value;
    what this test exists to prove is the wiring itself, not the math."""
    port = running_web_server
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/testdb")
    game_history._schema_ready = True
    conn = MagicMock()
    cursor = MagicMock()
    ids = iter(range(1, 1000))
    cursor.fetchone.side_effect = lambda: (next(ids), None, 1000)
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.__enter__.return_value = conn
    monkeypatch.setattr(game_history, "_connect", lambda: conn)
    monkeypatch.setattr(game_history, "_release_connection", lambda c: None)
    # Deliberately NOT monkeypatched to run synchronously here (unlike the
    # test above) -- the whole point is to exercise the real async
    # on_complete wiring, including the brief window where it's still null.

    resp = web_server.app.test_client().post(
        "/api/create_game", json={"seats": 2, "bot_mix": ["pass"], "seed": 42, "bot_think_time": 0}
    )
    room_code = resp.get_json()["room_code"]
    player = ScriptedWSClient(_ws_url(port, f"/ws?room={room_code}"), "alice")
    player.handshake()
    player.start()

    room = web_server._rooms[room_code]
    deadline = time.time() + 30
    while time.time() < deadline and room.state != "finished":
        threading.Event().wait(0.2)
    assert room.state == "finished"

    status = web_server.app.test_client().get(f"/api/status?room={room_code}").get_json()
    assert "elo_changes" in status  # present even while still null/pending

    deadline = time.time() + 5
    while time.time() < deadline and room.elo_changes is None:
        threading.Event().wait(0.1)
    assert room.elo_changes == {}  # settled: alice is a guest, nothing rated

    status = web_server.app.test_client().get(f"/api/status?room={room_code}").get_json()
    assert status["elo_changes"] == {}

    player.close()


def test_malformed_response_does_not_strand_the_room_in_progress(running_web_server):
    """
    Regression test for a crash found by driving the protocol by hand: a player
    connection answering a bid prompt with a RESPONSE that has no `prompt` field
    used to raise KeyError inside NetworkPlayer.get_bid(), killing the daemon
    game thread. The room stayed "in_progress" forever (the reaper only reclaims
    "lobby"/"finished" rooms), leaking the room and hanging every player and
    spectator. The parser now treats a missing/non-string prompt as invalid input
    and re-prompts, and the game thread guards against any other crash by closing
    connections and marking the room finished either way.
    """
    port = running_web_server
    room_code = web_server.app.test_client().post(
        "/api/create_game", json={"seats": 2, "bot_mix": ["pass"], "seed": 42, "bot_think_time": 0}
    ).get_json()["room_code"]

    class MalformedOnceClient(ScriptedWSClient):
        """Answers like ScriptedWSClient, but the very first bid answer is a
        RESPONSE with no `prompt` field — the exact malformed message that used
        to crash the game thread. Falls back to normal pass answers after."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._malformed_sent = False
            self._malformed_seen = threading.Event()

        def _answer(self, payload):
            if not self._malformed_sent:
                self._malformed_sent = True
                self._malformed_seen.set()
                return None  # None means "send the malformed RESPONSE", see _loop
            return super()._answer(payload)

    # _loop sends {"message_type": "RESPONSE", "prompt": self._answer(payload)}
    # for every PLAYER_MOVE. Patch the send to drop the prompt when the answer
    # is None, reproducing the malformed payload exactly.
    original_send = ScriptedWSClient._send

    def _send_maybe_malformed(self, payload):
        if payload.get("prompt") is None:
            payload = {"message_type": "RESPONSE"}
        original_send(self, payload)

    ScriptedWSClient._send = _send_maybe_malformed
    try:
        player = MalformedOnceClient(_ws_url(port, f"/ws?room={room_code}"), "alice")
        player.handshake()
        player.start()

        assert player._malformed_seen.wait(timeout=15), "test setup failed: the malformed message was never sent"

        # The game must still run to completion despite the malformed message,
        # and the room must reach "finished" (never left stranded "in_progress").
        deadline = time.time() + 30
        room_obj = web_server._rooms[room_code]
        while time.time() < deadline and room_obj.state != "finished":
            threading.Event().wait(0.2)
        assert room_obj.state == "finished"

        status = web_server.app.test_client().get(f"/api/status?room={room_code}").get_json()
        assert status["state"] == "finished"
        assert len(status["final_standings"]) == 2

        player.close()
    finally:
        ScriptedWSClient._send = original_send


def test_game_thread_crash_marks_the_room_finished_and_closes_connections(running_web_server, monkeypatch):
    """
    Covers the *other* half of the malformed-RESPONSE fix: NetworkPlayer now
    turns that specific malformed input into a re-prompt instead of a crash
    (see test_malformed_response_does_not_strand_the_room_in_progress above),
    but GameRoom.run_game also gained a broader safety net for any other
    unexpected exception in the game thread — without it, the room would
    stay "in_progress" forever (the reaper only reclaims "lobby"/"finished"
    rooms). Unlike a clean finish, a crashed game has no sane
    final_standings/winners to offer a rematch from, so connections are
    closed outright rather than left open.
    """
    port = running_web_server
    monkeypatch.setattr(web_server.PlayGame, "play_game",
                         lambda self: (_ for _ in ()).throw(RuntimeError("simulated game-thread crash")))

    room_code = web_server.app.test_client().post(
        "/api/create_game", json={"seats": 2, "bot_mix": ["pass"], "seed": 42, "bot_think_time": 0}
    ).get_json()["room_code"]

    player = ScriptedWSClient(_ws_url(port, f"/ws?room={room_code}"), "alice")
    player.handshake()
    player.start()

    room = web_server._rooms[room_code]
    deadline = time.time() + 10
    while time.time() < deadline and room.state != "finished":
        threading.Event().wait(0.1)
    assert room.state == "finished"

    alice = next(p for p in room.players if isinstance(p, web_server.NetworkPlayer))
    deadline = time.time() + 5
    while time.time() < deadline and alice.active:
        threading.Event().wait(0.1)
    assert alice.active is False

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

    An untimed room gets a ~20s disconnect grace period (see web_server.py's
    _compute_disconnect_grace_seconds) — reconnecting promptly, as this test
    does, means the disconnect never actually resolves as a quit at all (see
    NetworkPlayer._wait_for_reconnect): `active` never flips False, and no
    AUCTION_UPDATE("quit") is ever broadcast. See
    test_reconnect_after_grace_period_still_quits_then_recovers for the slow
    path where the grace period actually runs out.
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
    assert mover_player in room.players, "mid-game disconnect must keep the seat, not remove it"
    assert room.state == "in_progress", "the other two haven't answered anything yet — nothing should have resolved"

    # Reconnect with the same token/username, well inside the default grace
    # period — confirm the server kept them active the whole time (the
    # disconnect never resolved as a quit at all), with catch-up state
    # waiting for them.
    reconnected = ReconnectingWSClient(
        _ws_url(port, f"/ws?room={room_code}&rejoin_token={token}"), mover_name,
    )
    reconnected.handshake()
    # active only flips True in finish_reconnect(), called *after* the
    # catch-up state is fully sent (see networkplayer.py) -- deliberately
    # after IDENTIFY_SUCCESS, which this client just received, so a brief
    # gap here is correct, not a bug (closes a real race where the game
    # thread could otherwise start writing fresh messages to this player's
    # transport concurrently with the still-in-flight catch-up sequence).
    _wait_until_active(mover_player, "should have stayed active throughout — this reconnect is well within the grace period")
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

    # A fast reconnect should be fully invisible to the rest of the table —
    # no quit was ever broadcast for the disconnected player. WS delivery is
    # ordered/buffered at the OS/library level, so this still holds even
    # though these two clients only started actively draining their queue
    # (via .start(), above) after the reconnect already happened.
    for name, client in clients.items():
        if name != mover_name:
            quits = [m for m in client.messages_of_type("AUCTION_UPDATE")
                     if (m.get("data") or {}).get("kind") == "quit"
                     and (m.get("data") or {}).get("player") == mover_name]
            assert not quits, f"{name} saw a quit broadcast for {mover_name} despite reconnecting within the grace period"

    reconnected.close()
    for name, client in clients.items():
        if name != mover_name:
            client.close()


def test_reconnect_within_grace_period_on_a_timed_room_produces_no_quit_broadcast(running_web_server):
    """
    Pins the timed-room grace formula (turn_time_limit / 5, see
    _compute_disconnect_grace_seconds) end-to-end, not just the untimed
    default already covered by test_player_can_reconnect_after_disconnecting_mid_game
    above. turn_time_limit=15 (the shortest preset the lobby form actually
    offers -- see _TURN_TIME_PRESETS) -> grace=3.0s; reconnecting well
    inside that should behave identically to the untimed case: active never
    flips False, no quit is ever broadcast.
    """
    port = running_web_server
    room_code = web_server.app.test_client().post(
        "/api/create_game", json={"seats": 3, "bot_mix": [], "seed": 1, "turn_time_limit": 15}
    ).get_json()["room_code"]

    clients = {
        name: ScriptedWSClient(_ws_url(port, f"/ws?room={room_code}"), name)
        for name in ("alice", "bob", "carol")
    }
    for client in clients.values():
        client.handshake()

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
    mover.client.close()  # simulate a dropped connection

    room = web_server._rooms[room_code]
    mover_player = next(p for p in room.players if p.username == mover_name)

    # Reconnect well inside the 3.0s grace window.
    threading.Event().wait(0.3)
    reconnected = ReconnectingWSClient(
        _ws_url(port, f"/ws?room={room_code}&rejoin_token={token}"), mover_name,
    )
    reconnected.handshake()
    _wait_until_active(mover_player, "should have stayed active -- well within the timed room's grace window")

    for name, client in clients.items():
        if name != mover_name:
            client.start()
    threading.Event().wait(1.0)  # give any (unwanted) quit broadcast a chance to arrive

    for name, client in clients.items():
        if name != mover_name:
            quits = [m for m in client.messages_of_type("AUCTION_UPDATE")
                     if (m.get("data") or {}).get("kind") == "quit"
                     and (m.get("data") or {}).get("player") == mover_name]
            assert not quits, f"{name} saw a quit broadcast for {mover_name} despite reconnecting within the grace period"

    reconnected.close()
    for name, client in clients.items():
        if name != mover_name:
            client.close()


def _wait_until_active(player, message, timeout=2):
    """
    NetworkPlayer.finish_reconnect() (see networkplayer.py) only flips
    active=True once the reconnect handler is done sending catch-up state --
    strictly after the client-visible IDENTIFY_SUCCESS a test's own
    .handshake() call already returned from. Polls briefly rather than
    asserting instantaneously, since that small gap is now the correct,
    intentional behavior (it's what closes a real race between the
    reconnect handler's own sends and the game thread's).
    """
    deadline = time.time() + timeout
    while time.time() < deadline and not player.active:
        threading.Event().wait(0.02)
    assert player.active, message


def _drain_once(client):
    """Pulls one waiting message (if any) straight into client.received,
    without auto-answering anything the way ScriptedWSClient.start()'s
    background responder would. Deliberately non-responsive: answering a
    PLAYER_MOVE prompt would let the game keep advancing through everyone
    else's turns for real, which (with pacing mocked to no-ops suite-wide)
    can race clear through to the end of the deck in milliseconds --
    exactly what test_reconnect_after_grace_period_still_quits_then_recovers_via_player_reconnected
    needs to avoid, since it depends on the room still being "in_progress"
    by the time it attempts the late reconnect."""
    try:
        raw = client.client.receive(timeout=0.2)
    except ConnectionClosed:
        return
    if raw is None:
        return
    with client._lock:
        client.received.append(json.loads(raw))


def test_reconnect_after_grace_period_still_quits_then_recovers_via_player_reconnected(running_web_server):
    """
    The slow-reconnect path: once the grace window (3.0s here) genuinely
    runs out with no reconnect, behavior must fall back to exactly today's
    pre-feature shape -- a real quit fires, `active` goes False -- and only
    then does the earlier-shipped player_reconnected broadcast (see
    _handle_player_reconnect) become the thing that recovers the
    disconnected player's UI tile for everyone else once they do eventually
    reconnect.

    Deliberately never calls .start() on alice/carol (unlike the happy-path
    test above) -- its auto-responder would answer "pass" to literally
    everything, and with this suite's pacing mocked to no-ops, that races
    the game clear through to completion in milliseconds the instant
    mover's quit clears their turn, finishing the room before this test
    ever gets to attempt its own late reconnect. Draining passively via
    _drain_once keeps the game frozen at whoever's turn comes right after
    mover -- unanswered, but that's fine, this test doesn't need the game
    to actually finish.
    """
    port = running_web_server
    room_code = web_server.app.test_client().post(
        "/api/create_game", json={"seats": 3, "bot_mix": [], "seed": 1, "turn_time_limit": 15}
    ).get_json()["room_code"]

    clients = {
        name: ScriptedWSClient(_ws_url(port, f"/ws?room={room_code}"), name)
        for name in ("alice", "bob", "carol")
    }
    for client in clients.values():
        client.handshake()

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
    mover.client.close()

    room = web_server._rooms[room_code]
    mover_player = next(p for p in room.players if p.username == mover_name)
    others = [c for name, c in clients.items() if name != mover_name]

    # Wait out the 3.0s grace window with no reconnect, passively draining
    # the other two the whole time so nothing they receive gets lost.
    deadline = time.time() + 5
    while time.time() < deadline and mover_player.active:
        for c in others:
            _drain_once(c)
        threading.Event().wait(0.1)
    assert not mover_player.active, "grace period should have expired and quit them by now"
    assert room.state == "in_progress", "the room must still be in progress for the late-reconnect check below"

    deadline = time.time() + 5
    quits_found = {name: [] for name in clients if name != mover_name}
    while time.time() < deadline and not all(quits_found.values()):
        for name, client in clients.items():
            if name != mover_name:
                _drain_once(client)
                quits_found[name] = [m for m in client.messages_of_type("AUCTION_UPDATE")
                                      if (m.get("data") or {}).get("kind") == "quit"
                                      and (m.get("data") or {}).get("player") == mover_name]
        threading.Event().wait(0.1)
    for name, quits in quits_found.items():
        assert quits, f"{name} never saw the expected quit broadcast for {mover_name} after grace expired"

    # Reconnect (late) and confirm the player_reconnected recovery broadcast
    # reaches the other clients.
    reconnected = ReconnectingWSClient(
        _ws_url(port, f"/ws?room={room_code}&rejoin_token={token}"), mover_name,
    )
    reconnected.handshake()
    _wait_until_active(mover_player, "finish_reconnect() should mark the player active again")

    deadline = time.time() + 5
    found = False
    while time.time() < deadline and not found:
        for name, client in clients.items():
            if name != mover_name:
                _drain_once(client)
                events = [m for m in client.messages_of_type("GLOBAL_EVENT")
                          if (m.get("data") or {}).get("event") == "player_reconnected"
                          and (m.get("data") or {}).get("player") == mover_name]
                if events:
                    found = True
        threading.Event().wait(0.1)
    assert found, "no player_reconnected broadcast reached the other clients after the late reconnect"

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


def test_out_of_turn_resign_does_not_hang_the_game(running_web_server):
    """
    End-to-end confirmation of the "Resign works anytime" feature over the
    real WebSocket protocol: unlike the in-turn "RESPONSE: quit" path (see
    the test above, which answers a live PLAYER_MOVE prompt), the web UI's
    Resign button now sends an out-of-band RESIGN message that must take
    effect even when it's some *other* player's turn (see
    WebSocketTransport's RESIGN handling and web_server.py's on_resign).

    The actual hang this was built to prevent — a resigning player being
    skipped forever without ever being subtracted from an auction's
    remaining-player count, leaving the real remaining bidder re-prompted
    indefinitely — is precisely (and deterministically) covered by
    TestOutOfTurnDeparture in tests/game_manager/test_gameplay.py, not here:
    a real end-to-end scenario can't reliably reproduce that specific race,
    since a scripted/human client's own eventual response to a redundant
    re-prompt would just look like an (incorrect) game outcome rather than a
    literal hang. This test instead confirms the wire mechanism itself
    works correctly end-to-end: the game reaches "finished" without
    erroring, the resigning player ends up correctly excluded, and other
    connected players are told about it live.
    """
    port = running_web_server
    room_code = web_server.app.test_client().post(
        "/api/create_game", json={"seats": 3, "bot_mix": ["pass"], "seed": 1, "bot_think_time": 0}
    ).get_json()["room_code"]

    alice = ScriptedWSClient(_ws_url(port, f"/ws?room={room_code}"), "alice")
    alice.handshake()
    alice.start()
    bob = ScriptedWSClient(_ws_url(port, f"/ws?room={room_code}"), "bob")
    bob.handshake()
    # Deliberately not bob.start() -- ScriptedWSClient's background _loop()
    # would auto-answer any PLAYER_MOVE bob receives (e.g. if bob happens to
    # be next in turn order), racing against the explicit RESIGN sent below
    # from this thread. A real human doesn't compete against their own
    # in-flight bid this way; this keeps the test deterministic without
    # exercising a client-harness-only race that has nothing to do with the
    # actual fix under test.

    # Wait for the first auction to genuinely be underway (alice actually
    # prompted for a move) before bob resigns -- this specifically targets
    # the "goes inactive *mid*-auction" transition (bob was still counted as
    # active when this auction's player count was taken), not just "already
    # inactive before any auction ever started" (a much easier case that
    # doesn't need the counted_player_ids fix at all).
    deadline = time.time() + 10
    while time.time() < deadline and not alice.messages_of_type("PLAYER_MOVE"):
        threading.Event().wait(0.1)
    assert alice.messages_of_type("PLAYER_MOVE"), "test setup failed: alice was never prompted"

    # Resign -- regardless of whose turn it happens to be when this lands,
    # which is exactly the point: it must work either way.
    bob._send({"message_type": "RESIGN"})

    room = web_server._rooms[room_code]
    deadline = time.time() + 30
    while time.time() < deadline and room.state != "finished":
        threading.Event().wait(0.2)
    assert room.state == "finished", "game hung instead of finishing after an out-of-turn resign"

    status = web_server.app.test_client().get(f"/api/status?room={room_code}").get_json()
    bob_standing = next(s for s in status["final_standings"] if s["username"] == "bob")
    assert bob_standing["active"] is False

    # Other players are told about it live (not just left to infer it from
    # bob quietly never taking another turn) -- see web_server.py's
    # _handle_out_of_turn_resign.
    resign_events = [m for m in alice.messages_of_type("GLOBAL_EVENT")
                      if (m.get("data") or {}).get("event") == "player_resigned"]
    assert any(e["data"]["player"] == "bob" for e in resign_events)

    alice.close()
    bob.close()


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
    alice_player = next(p for p in first_game.players if p.username == "alice")
    # Both sides always pass, so whoever isn't first to pass in each auction
    # wins it by default (for free — neither side ever places a real bid, so
    # this can't also exercise a spent-money-cards reset) — alice should
    # have picked up at least one card (and its points) by the time a whole
    # game against a pass-only bot finishes. If this ever assert-fails, the
    # rest of this test can't tell a real reset apart from "there was
    # nothing to reset in the first place" — see the regression this guards
    # below.
    assert alice_player.points != 0 or alice_player.status_cards, \
        "test setup assumption broken: alice won nothing in game 1"

    # Player connections are kept open past game-end specifically for this
    # (see GameRoom.run_game) — the connection must still be usable here.
    assert alice.client.connected

    alice._send({"message_type": "REMATCH_REQUEST", "data": {"bot_mix": ["greedy"]}})

    deadline = time.time() + 10
    while time.time() < deadline and not alice.messages_of_type("REMATCH_STARTING"):
        threading.Event().wait(0.1)
    assert alice.messages_of_type("REMATCH_STARTING"), "alice never got REMATCH_STARTING"
    assert room.bot_mix == ["greedy"], "the requested bot mix should replace the room's old one"

    # Regression test for a real bug: the rematch used to reuse alice's exact
    # NetworkPlayer object (deliberately — see reset_for_new_game's
    # docstring for why a fresh object isn't the fix) but never actually
    # reset its game state, so game 2 silently started with game 1's final
    # points/status cards/spent money still attached.
    deadline = time.time() + 10
    while time.time() < deadline and room.game is first_game:
        threading.Event().wait(0.1)
    new_game_alice = next(p for p in room.game.players if p.username == "alice")
    assert new_game_alice is alice_player, "rematch should reuse the same player object, not a fresh one"
    assert alice_player.points == 0, "points must reset for a rematch, not carry over from the last game"
    assert alice_player.status_cards == (), "status cards must reset for a rematch"
    full_hand = sorted(c.value for c in MoneyCardManager().cards)
    assert sorted(c.value for c in alice_player.money_cards) == full_hand, \
        "money cards must reset to a fresh full hand for a rematch"

    deadline = time.time() + 30
    while time.time() < deadline and room.state != "finished":
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


# ------------------------------------------------------ /api/auth/google --

def test_auth_google_requires_an_id_token():
    resp = web_server.app.test_client().post("/api/auth/google", json={})
    assert resp.status_code == 400


def test_auth_google_rejects_an_invalid_token(monkeypatch):
    monkeypatch.setattr(web_server, "_verify_google_id_token", lambda token: None)
    resp = web_server.app.test_client().post("/api/auth/google", json={"id_token": "garbage"})
    assert resp.status_code == 401


def test_auth_google_reports_needs_username_for_a_first_time_account(monkeypatch):
    monkeypatch.setattr(web_server, "_verify_google_id_token",
                         lambda token: {"sub": "g-123", "email": "a@example.com", "name": "Alice A"})
    monkeypatch.setattr(game_history, "find_player_by_google_id", lambda google_id: None)

    resp = web_server.app.test_client().post("/api/auth/google", json={"id_token": "valid"})
    body = resp.get_json()
    assert resp.status_code == 200
    assert body == {"needs_username": True, "suggested_display_name": "Alice A"}


def test_auth_google_returns_the_existing_account_when_already_linked(monkeypatch):
    monkeypatch.setattr(web_server, "_verify_google_id_token",
                         lambda token: {"sub": "g-123", "email": "a@example.com", "name": "Alice A"})
    monkeypatch.setattr(game_history, "find_player_by_google_id",
                         lambda google_id: {"username": "alice", "display_name": "Alice"})

    resp = web_server.app.test_client().post("/api/auth/google", json={"id_token": "valid"})
    assert resp.status_code == 200
    assert resp.get_json() == {"username": "alice", "display_name": "Alice"}


# --------------------------------------- /api/auth/google/claim_username --

def test_claim_username_requires_an_id_token():
    resp = web_server.app.test_client().post("/api/auth/google/claim_username", json={"username": "alice"})
    assert resp.status_code == 400


def test_claim_username_requires_a_non_empty_username(monkeypatch):
    monkeypatch.setattr(web_server, "_verify_google_id_token",
                         lambda token: {"sub": "g-123", "email": "a@example.com", "name": "Alice A"})
    resp = web_server.app.test_client().post(
        "/api/auth/google/claim_username", json={"id_token": "valid", "username": "   "}
    )
    assert resp.status_code == 400


def test_claim_username_rejects_an_invalid_token(monkeypatch):
    monkeypatch.setattr(web_server, "_verify_google_id_token", lambda token: None)
    resp = web_server.app.test_client().post(
        "/api/auth/google/claim_username", json={"id_token": "garbage", "username": "alice"}
    )
    assert resp.status_code == 401


def test_claim_username_rejects_an_already_taken_name(monkeypatch):
    monkeypatch.setattr(web_server, "_verify_google_id_token",
                         lambda token: {"sub": "g-123", "email": "a@example.com", "name": "Alice A"})
    monkeypatch.setattr(game_history, "find_player_by_google_id", lambda google_id: None)
    monkeypatch.setattr(game_history, "username_is_taken", lambda username: True)

    resp = web_server.app.test_client().post(
        "/api/auth/google/claim_username", json={"id_token": "valid", "username": "alice"}
    )
    assert resp.status_code == 409


def test_claim_username_creates_the_account_and_defaults_display_name_to_username(monkeypatch):
    """Regression for the user's explicit ask: display name must not be a
    required step -- omitting it entirely still succeeds, defaulting to
    the username itself."""
    monkeypatch.setattr(web_server, "_verify_google_id_token",
                         lambda token: {"sub": "g-123", "email": "a@example.com", "name": "Alice A"})
    monkeypatch.setattr(game_history, "find_player_by_google_id", lambda google_id: None)
    monkeypatch.setattr(game_history, "username_is_taken", lambda username: False)
    created = {}

    def fake_create(google_id, email, username, display_name):
        created.update(google_id=google_id, email=email, username=username, display_name=display_name)
        return True

    monkeypatch.setattr(game_history, "create_google_player", fake_create)

    resp = web_server.app.test_client().post(
        "/api/auth/google/claim_username", json={"id_token": "valid", "username": "alice"}
    )
    assert resp.status_code == 200
    assert resp.get_json() == {"username": "alice", "display_name": "alice"}
    assert created == {"google_id": "g-123", "email": "a@example.com", "username": "alice", "display_name": "alice"}


def test_claim_username_returns_the_existing_row_on_a_retry_by_the_same_account(monkeypatch):
    """A client retrying after a flaky connection ate the first success
    response must not see a spurious "username taken" for a name this
    exact Google account already owns."""
    monkeypatch.setattr(web_server, "_verify_google_id_token",
                         lambda token: {"sub": "g-123", "email": "a@example.com", "name": "Alice A"})
    monkeypatch.setattr(game_history, "find_player_by_google_id",
                         lambda google_id: {"username": "alice", "display_name": "Alice"})
    monkeypatch.setattr(game_history, "username_is_taken", lambda username: (_ for _ in ()).throw(
        AssertionError("should not re-check uniqueness for an already-linked account")))

    resp = web_server.app.test_client().post(
        "/api/auth/google/claim_username", json={"id_token": "valid", "username": "alice"}
    )
    assert resp.status_code == 200
    assert resp.get_json() == {"username": "alice", "display_name": "Alice"}


def test_claim_username_handles_a_uniqueness_race_at_the_database_level(monkeypatch):
    """username_is_taken said it was free, but create_google_player's own
    INSERT still lost a race (two tabs claiming it at once) -- must
    surface as the same clean 409, not a raw 500."""
    monkeypatch.setattr(web_server, "_verify_google_id_token",
                         lambda token: {"sub": "g-123", "email": "a@example.com", "name": "Alice A"})
    monkeypatch.setattr(game_history, "find_player_by_google_id", lambda google_id: None)
    monkeypatch.setattr(game_history, "username_is_taken", lambda username: False)
    monkeypatch.setattr(game_history, "create_google_player", lambda *a, **k: False)

    resp = web_server.app.test_client().post(
        "/api/auth/google/claim_username", json={"id_token": "valid", "username": "alice"}
    )
    assert resp.status_code == 409


# ------------------------------------------------------- /api/auth/guest --

def test_guest_suggest_returns_a_username_without_a_database(monkeypatch):
    monkeypatch.setattr(game_history, "is_configured", lambda: False)
    resp = web_server.app.test_client().get("/api/auth/guest/suggest")
    assert resp.status_code == 200
    assert resp.get_json()["username"]  # non-empty, no DB round-trip needed


def test_guest_suggest_retries_past_a_taken_candidate(monkeypatch):
    monkeypatch.setattr(game_history, "is_configured", lambda: True)
    taken = {"AzureNaruto111"}
    monkeypatch.setattr(game_history, "username_is_taken", lambda username: username in taken)
    monkeypatch.setattr(web_server, "generate_guest_username",
                         iter(["AzureNaruto111", "CoralGoku222"]).__next__)

    resp = web_server.app.test_client().get("/api/auth/guest/suggest")
    assert resp.status_code == 200
    assert resp.get_json() == {"username": "CoralGoku222"}


def test_guest_suggest_retries_past_a_username_live_in_a_room(monkeypatch, running_web_server):
    """username_is_taken() alone only knows about `players` rows, which
    don't exist for a guest mid-game -- a candidate matching someone
    currently seated in a live room must also be retried past (see
    _active_usernames). Constructs the NetworkPlayer directly (transport
    mocked out) rather than a real websocket handshake, since only
    room.players' membership matters here."""
    from highsociety.code.gamecore.player.networkplayer import NetworkPlayer

    monkeypatch.setattr(game_history, "is_configured", lambda: True)
    monkeypatch.setattr(game_history, "username_is_taken", lambda username: False)
    monkeypatch.setattr(web_server, "generate_guest_username",
                         iter(["AzureNaruto111", "CoralGoku222"]).__next__)

    client = web_server.app.test_client()
    room = client.post("/api/create_game", json={"seats": 2, "bot_mix": []}).get_json()
    live_player = NetworkPlayer(name="AzureNaruto111", username="AzureNaruto111",
                                 transport=MagicMock(), game_id=room["room_code"])
    web_server._rooms[room["room_code"]].players.append(live_player)

    resp = client.get("/api/auth/guest/suggest")
    assert resp.status_code == 200
    assert resp.get_json() == {"username": "CoralGoku222"}


def test_guest_claim_requires_a_non_empty_username():
    resp = web_server.app.test_client().post("/api/auth/guest/claim", json={"username": "   "})
    assert resp.status_code == 400


def test_guest_claim_accepts_unchecked_without_a_database(monkeypatch):
    monkeypatch.setattr(game_history, "is_configured", lambda: False)
    resp = web_server.app.test_client().post("/api/auth/guest/claim", json={"username": "alice"})
    assert resp.status_code == 200
    assert resp.get_json() == {"username": "alice"}


def test_guest_claim_rejects_an_already_taken_name(monkeypatch):
    monkeypatch.setattr(game_history, "is_configured", lambda: True)
    monkeypatch.setattr(game_history, "username_is_taken", lambda username: True)
    resp = web_server.app.test_client().post("/api/auth/guest/claim", json={"username": "alice"})
    assert resp.status_code == 409


def test_guest_claim_creates_the_account(monkeypatch):
    monkeypatch.setattr(game_history, "is_configured", lambda: True)
    monkeypatch.setattr(game_history, "username_is_taken", lambda username: False)
    created = {}
    monkeypatch.setattr(game_history, "create_guest_player",
                         lambda username: created.setdefault("username", username) or True)

    resp = web_server.app.test_client().post("/api/auth/guest/claim", json={"username": "alice"})
    assert resp.status_code == 200
    assert resp.get_json() == {"username": "alice"}
    assert created == {"username": "alice"}


def test_guest_claim_handles_a_uniqueness_race_at_the_database_level(monkeypatch):
    monkeypatch.setattr(game_history, "is_configured", lambda: True)
    monkeypatch.setattr(game_history, "username_is_taken", lambda username: False)
    monkeypatch.setattr(game_history, "create_guest_player", lambda username: False)

    resp = web_server.app.test_client().post("/api/auth/guest/claim", json={"username": "alice"})
    assert resp.status_code == 409


# --------------------------------------------------- /api/auth/username/change --

def test_username_change_requires_a_non_empty_new_username():
    resp = web_server.app.test_client().post(
        "/api/auth/username/change", json={"old_username": "alice", "new_username": "  "}
    )
    assert resp.status_code == 400


def test_username_change_accepts_unchecked_without_a_database(monkeypatch):
    monkeypatch.setattr(game_history, "is_configured", lambda: False)
    resp = web_server.app.test_client().post(
        "/api/auth/username/change", json={"old_username": "alice", "new_username": "bob"}
    )
    assert resp.status_code == 200
    assert resp.get_json() == {"username": "bob"}


def test_username_change_rejects_an_already_taken_name(monkeypatch):
    monkeypatch.setattr(game_history, "is_configured", lambda: True)
    monkeypatch.setattr(game_history, "rename_player", lambda old, new: False)
    resp = web_server.app.test_client().post(
        "/api/auth/username/change", json={"old_username": "alice", "new_username": "bob"}
    )
    assert resp.status_code == 409


def test_username_change_renames_the_account(monkeypatch):
    monkeypatch.setattr(game_history, "is_configured", lambda: True)
    renamed = {}
    monkeypatch.setattr(game_history, "rename_player",
                         lambda old, new: renamed.update(old=old, new=new) or True)

    resp = web_server.app.test_client().post(
        "/api/auth/username/change", json={"old_username": "alice", "new_username": "bob"}
    )
    assert resp.status_code == 200
    assert resp.get_json() == {"username": "bob"}
    assert renamed == {"old": "alice", "new": "bob"}


# --------------------------------------------------------- matchmaking --

@pytest.fixture
def clean_matchmaking_queue():
    """matchmaking._tickets is module-level, shared, in-memory state --
    reset it around every test so one test's queued players can't leak
    into the next."""
    matchmaking._tickets.clear()
    yield
    matchmaking._tickets.clear()


def test_matchmaking_join_requires_a_username(clean_matchmaking_queue):
    resp = web_server.app.test_client().post("/api/matchmaking/join", json={"username": "", "seats": 3})
    assert resp.status_code == 400


def test_matchmaking_join_validates_seats(clean_matchmaking_queue):
    resp = web_server.app.test_client().post(
        "/api/matchmaking/join", json={"username": "alice", "seats": 99}
    )
    assert resp.status_code == 400


def test_matchmaking_join_returns_a_ticket_id(clean_matchmaking_queue, monkeypatch):
    monkeypatch.setattr(game_history, "get_player_elo", lambda username: 1000)
    resp = web_server.app.test_client().post(
        "/api/matchmaking/join", json={"username": "alice", "seats": 2}
    )
    assert resp.status_code == 200
    assert resp.get_json()["ticket_id"]


def test_matchmaking_status_404s_for_an_unknown_ticket(clean_matchmaking_queue):
    resp = web_server.app.test_client().get("/api/matchmaking/status?ticket=nope")
    assert resp.status_code == 404


def test_matchmaking_status_reports_waiting_below_the_seat_count(clean_matchmaking_queue, monkeypatch):
    monkeypatch.setattr(game_history, "get_player_elo", lambda username: 1000)
    client = web_server.app.test_client()
    ticket_id = client.post("/api/matchmaking/join", json={"username": "alice", "seats": 2}).get_json()["ticket_id"]

    resp = client.get(f"/api/matchmaking/status?ticket={ticket_id}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["matched"] is False
    assert body["room_code"] is None
    assert body["waiting_count"] == 1


def test_matchmaking_matches_two_waiting_players_into_a_real_private_room(clean_matchmaking_queue, monkeypatch):
    web_server._rooms.clear()
    monkeypatch.setattr(game_history, "get_player_elo", lambda username: 1000)
    client = web_server.app.test_client()
    ticket_a = client.post("/api/matchmaking/join", json={"username": "alice", "seats": 2}).get_json()["ticket_id"]
    ticket_b = client.post("/api/matchmaking/join", json={"username": "bob", "seats": 2}).get_json()["ticket_id"]

    status_a = client.get(f"/api/matchmaking/status?ticket={ticket_a}").get_json()
    status_b = client.get(f"/api/matchmaking/status?ticket={ticket_b}").get_json()

    assert status_a["matched"] is True
    assert status_b["matched"] is True
    assert status_a["room_code"] == status_b["room_code"]

    room = web_server._get_room(status_a["room_code"])
    assert room is not None
    assert room.seats == 2
    assert room.visibility == "private"
    assert room.players == []  # no bots -- both seats wait for the real matched humans to connect


def test_matchmaking_cancel_is_idempotent_and_removes_the_ticket(clean_matchmaking_queue, monkeypatch):
    monkeypatch.setattr(game_history, "get_player_elo", lambda username: 1000)
    client = web_server.app.test_client()
    ticket_id = client.post("/api/matchmaking/join", json={"username": "alice", "seats": 2}).get_json()["ticket_id"]

    resp = client.post("/api/matchmaking/cancel", json={"ticket_id": ticket_id})
    assert resp.status_code == 200
    assert client.get(f"/api/matchmaking/status?ticket={ticket_id}").status_code == 404

    # Cancelling again, or a ticket that never existed, must not error.
    resp = client.post("/api/matchmaking/cancel", json={"ticket_id": ticket_id})
    assert resp.status_code == 200
    resp = client.post("/api/matchmaking/cancel", json={"ticket_id": "never-existed"})
    assert resp.status_code == 200


# ------------------------------------------------- achievements/profile --

def test_achievements_endpoint_returns_the_unlocked_list(monkeypatch):
    monkeypatch.setattr(game_history, "get_player_achievements", lambda username: ["first_win", "sniper"])
    resp = web_server.app.test_client().get("/api/achievements?username=alice")
    assert resp.status_code == 200
    assert resp.get_json() == {"achievements": ["first_win", "sniper"]}


def test_achievements_endpoint_defaults_to_empty_for_a_missing_username():
    resp = web_server.app.test_client().get("/api/achievements")
    assert resp.status_code == 200
    assert resp.get_json() == {"achievements": []}


def test_profile_endpoint_404s_for_an_unknown_username(monkeypatch):
    monkeypatch.setattr(game_history, "get_player_profile_stats", lambda username: None)
    resp = web_server.app.test_client().get("/api/profile/nobody")
    assert resp.status_code == 404


def test_profile_endpoint_returns_stats_and_elo(monkeypatch):
    """elo now comes from get_player_profile_stats' own result (one DB
    connection instead of two -- see that function's docstring), not a
    separate get_player_elo() call."""
    monkeypatch.setattr(game_history, "get_player_profile_stats", lambda username: {
        "games_played": 4, "wins": 3, "win_rate": 0.75, "elo": 1032,
        "avg_placement": 1.5, "avg_points": 12.0, "avg_money_remaining": 6.0,
        "created_at": "2025-06-01T00:00:00+00:00", "last_played_at": "2026-03-15T00:00:00+00:00",
    })
    resp = web_server.app.test_client().get("/api/profile/alice")
    assert resp.status_code == 200
    assert resp.get_json() == {
        "username": "alice", "games_played": 4, "wins": 3, "win_rate": 0.75,
        "avg_placement": 1.5, "avg_points": 12.0, "avg_money_remaining": 6.0, "elo": 1032,
        "created_at": "2025-06-01T00:00:00+00:00", "last_played_at": "2026-03-15T00:00:00+00:00",
    }


def test_global_stats_endpoint_returns_204_when_unavailable(monkeypatch):
    monkeypatch.setattr(game_history, "get_global_stats", lambda: None)
    resp = web_server.app.test_client().get("/api/global_stats")
    assert resp.status_code == 204


def test_global_stats_endpoint_returns_counts(monkeypatch):
    monkeypatch.setattr(game_history, "get_global_stats",
                         lambda: {"total_games": 433, "total_players": 49})
    resp = web_server.app.test_client().get("/api/global_stats")
    assert resp.status_code == 200
    assert resp.get_json() == {"total_games": 433, "total_players": 49}


def test_recent_games_endpoint_always_200s_with_a_list(monkeypatch):
    monkeypatch.setattr(game_history, "get_recent_games", lambda username, **kw: {"games": [], "has_more": False})
    resp = web_server.app.test_client().get("/api/games/nobody")
    assert resp.status_code == 200
    assert resp.get_json() == {"games": [], "has_more": False}


def test_recent_games_endpoint_passes_through_limit_and_offset(monkeypatch):
    seen = {}

    def fake_get_recent_games(username, limit=20, offset=0):
        seen["args"] = (username, limit, offset)
        return {"games": [], "has_more": False}

    monkeypatch.setattr(game_history, "get_recent_games", fake_get_recent_games)
    resp = web_server.app.test_client().get("/api/games/alice?limit=10&offset=10")
    assert resp.status_code == 200
    assert seen["args"] == ("alice", 10, 10)


def test_recent_games_endpoint_rejects_non_integer_pagination_params(monkeypatch):
    resp = web_server.app.test_client().get("/api/games/alice?limit=abc")
    assert resp.status_code == 400


def test_game_detail_endpoint_404s_for_an_unknown_game(monkeypatch):
    monkeypatch.setattr(game_history, "get_game_detail", lambda game_id: None)
    resp = web_server.app.test_client().get("/api/games/detail/999")
    assert resp.status_code == 404


def test_game_detail_endpoint_returns_the_detail(monkeypatch):
    monkeypatch.setattr(game_history, "get_game_detail",
                         lambda game_id: {"game_id": game_id, "finished_at": "2026-01-01T00:00:00", "participants": []})
    resp = web_server.app.test_client().get("/api/games/detail/42")
    assert resp.status_code == 200
    assert resp.get_json()["game_id"] == 42


def test_leaderboard_endpoint_returns_a_list(monkeypatch):
    monkeypatch.setattr(game_history, "get_leaderboard",
                         lambda **kw: {"rows": [{"username": "alice", "elo": 1200, "games_played": 10, "games_won": 6}],
                                       "has_more": False})
    resp = web_server.app.test_client().get("/api/leaderboard")
    assert resp.status_code == 200
    assert resp.get_json() == {
        "leaderboard": [{"username": "alice", "elo": 1200, "games_played": 10, "games_won": 6}],
        "has_more": False,
    }


def test_leaderboard_endpoint_passes_through_limit_and_offset(monkeypatch):
    seen = {}

    def fake_get_leaderboard(limit=20, offset=0):
        seen["args"] = (limit, offset)
        return {"rows": [], "has_more": False}

    monkeypatch.setattr(game_history, "get_leaderboard", fake_get_leaderboard)
    resp = web_server.app.test_client().get("/api/leaderboard?limit=20&offset=20")
    assert resp.status_code == 200
    assert seen["args"] == (20, 20)


def test_leaderboard_endpoint_rejects_non_integer_pagination_params(monkeypatch):
    resp = web_server.app.test_client().get("/api/leaderboard?offset=abc")
    assert resp.status_code == 400


def test_rating_history_endpoint_returns_a_list(monkeypatch):
    monkeypatch.setattr(game_history, "get_rating_history", lambda username: [
        {"old_rating": 1000, "new_rating": 1016, "created_at": "2026-01-01T00:00:00"},
    ])
    resp = web_server.app.test_client().get("/api/profile/alice/rating_history")
    assert resp.status_code == 200
    assert resp.get_json()["history"] == [
        {"old_rating": 1000, "new_rating": 1016, "created_at": "2026-01-01T00:00:00"},
    ]


# ---------------------------------------- static per-screen URLs --------

@pytest.mark.parametrize("path", ["/", "/play", "/join", "/host", "/leaderboard", "/rules", "/account", "/achievements"])
def test_static_screen_paths_all_serve_the_same_app_shell(path):
    """Each of the 7 top-level sidebar screens gets a real, refreshable/
    shareable URL (see app.js's SCREEN_PATHS/setScreenPath) -- all served
    by the same index() view as '/', since this is a single-page app that
    figures out which screen to show client-side."""
    resp = web_server.app.test_client().get(path)
    assert resp.status_code == 200
    assert b"High Society" in resp.data
