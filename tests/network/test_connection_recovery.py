"""
Dropping and regaining a connection at awkward moments: a reconnect that beats
the game thread to noticing the drop, an old socket that still looks open, and
what the rest of the table gets told.
"""
import json
import queue
import threading
import time

import pytest

simple_websocket = pytest.importorskip("simple_websocket")
web_server = pytest.importorskip("web_server")

from highsociety.code.gamecore.network.transport import ConnectionClosed, WebSocketTransport  # noqa: E402
from highsociety.code.gamecore.player.networkplayer import NetworkPlayer  # noqa: E402


class FakeWS:
    """The bit of simple_websocket.Server that WebSocketTransport uses."""

    def __init__(self):
        self.connected = True
        self.sent = []
        self.incoming = queue.Queue()

    def receive(self, timeout=None):
        deadline = time.monotonic() + (timeout if timeout is not None else 3600)
        while time.monotonic() < deadline:
            if not self.connected:
                raise ConnectionClosed()
            try:
                return self.incoming.get(timeout=0.01)
            except queue.Empty:
                pass
        return None

    def send(self, data):
        if not self.connected:
            raise ConnectionClosed()
        self.sent.append(json.loads(data))

    def close(self):
        self.connected = False

    def message_types(self):
        return [m["message_type"] for m in self.sent]


def make_transport(ws=None):
    return WebSocketTransport(ws or FakeWS(), label="test", on_chat=lambda msg: None)


def make_player(transport=None, grace=5.0, username="alice"):
    transport = transport or make_transport()
    player = NetworkPlayer(name=username, username=username, transport=transport, game_id="g",
                           disconnect_grace_seconds=grace)
    transport.start()
    return player


def in_thread(fn):
    """Runs fn on a thread; returns a getter that waits for its result."""
    box = {}
    thread = threading.Thread(target=lambda: box.update(result=fn()), daemon=True)
    thread.start()

    def result(timeout):
        thread.join(timeout)
        assert not thread.is_alive(), f"still blocked after {timeout}s"
        return box["result"]

    return result


def test_a_reconnect_that_beats_the_game_thread_does_not_cost_the_player_their_seat():
    """The reconnect completes in the gap between get_bid noticing the old
    connection is gone and it starting to wait. It used to sit out the whole
    grace, then report "quit" for a player who was connected and waiting."""
    new_transport = make_transport()

    class DiesThenReconnects(WebSocketTransport):
        @property
        def is_connected(self):
            if player.transport is self:
                player.reattach(new_transport)
                player.finish_reconnect()
            return False

        def receive(self, timeout=None):
            return None

    player = make_player(DiesThenReconnects(FakeWS(), label="old", on_chat=lambda msg: None), grace=0.3)

    assert player.get_bid(timeout=None) is None  # "ask again", not "quit"
    assert player.active is True


def test_reconnecting_over_a_socket_that_still_looks_open_re_prompts_at_once():
    """A phone changing networks reconnects before its old socket is noticed
    as dead. The game thread was blocked on that socket: forever-ish in an
    untimed room, and in a timed one it moved on without ever sending the new
    connection a prompt, so the player couldn't act and timed out."""
    old_ws, new_ws = FakeWS(), FakeWS()
    player = make_player(make_transport(old_ws))

    result = in_thread(lambda: player.get_bid(timeout=None))
    threading.Event().wait(0.1)

    player.reattach(make_transport(new_ws))
    player.finish_reconnect()

    assert result(timeout=1.5) is None

    player.get_bid(timeout=0.05)
    assert "PLAYER_MOVE" in new_ws.message_types()


def test_nothing_is_written_to_a_new_connection_until_catch_up_is_done():
    """reattach leaves the seat inactive even if the old socket never looked
    dead, or the game's broadcasts would race the catch-up messages."""
    player = make_player()
    assert player.active is True
    player.reattach(make_transport())
    assert player.active is False
    player.finish_reconnect()
    assert player.active is True


def test_grace_is_capped_by_what_is_left_of_the_turn_not_what_it_started_with():
    ws = FakeWS()
    player = make_player(make_transport(ws), grace=5.0)
    threading.Timer(0.5, ws.close).start()

    started = time.monotonic()
    assert player.get_bid(timeout=1.0) == "quit"

    # Used to wait a further min(5, 1.0) after dropping: about 1.5s in all.
    assert time.monotonic() - started < 1.3


def test_a_dead_connection_that_was_already_replaced_is_ignored():
    old = make_transport()
    player = make_player(old)
    player.reattach(make_transport())
    player.finish_reconnect()

    assert player.connection_lost(old) is False
    assert player.active is True


def test_a_failed_send_on_a_replaced_connection_does_not_mark_the_player_gone():
    old_ws = FakeWS()
    old = make_transport(old_ws)
    player = make_player(old)
    new = make_transport()
    old_ws.connected = False
    original_send = old.send

    def reconnect_mid_send(payload):
        player.reattach(new)
        player.finish_reconnect()
        original_send(payload)

    old.send = reconnect_mid_send
    player.send_message("x", message_type="PLAYER_INFO")

    assert player.active is True


class TestWaitForReturn:
    def test_waits_out_only_what_is_left_of_the_grace_since_the_drop(self):
        player = make_player(grace=0.4)
        player.active = False
        threading.Event().wait(0.2)

        started = time.monotonic()
        assert player.wait_for_return() is False
        assert time.monotonic() - started < 0.35

    def test_wakes_as_soon_as_they_return(self):
        player = make_player(grace=5.0)
        player.active = False

        def come_back():
            player.reattach(make_transport())
            player.finish_reconnect()

        threading.Timer(0.1, come_back).start()
        started = time.monotonic()
        assert player.wait_for_return() is True
        assert time.monotonic() - started < 1.0

    def test_one_drop_only_holds_the_table_up_once(self):
        player = make_player(grace=0.1)
        player.active = False
        assert player.wait_for_return() is False

        started = time.monotonic()
        assert player.wait_for_return() is False
        assert time.monotonic() - started < 0.05

    def test_never_waits_for_a_resignation(self):
        player = make_player(grace=5.0)
        player.resigned = True
        player.active = False

        started = time.monotonic()
        assert player.wait_for_return() is False
        assert time.monotonic() - started < 0.05

    def test_never_waits_without_a_reconnect_mechanism(self):
        player = make_player(grace=None)
        player.active = False
        assert player.wait_for_return() is False


def make_room(state, *usernames):
    room = web_server.GameRoom("TEST", len(usernames), [], 1, 0, "public")
    room.state = state
    room.players = [make_player(username=name) for name in usernames]
    for player in room.players:
        player.transport._ws.sent.clear()
    return room, room.players


def events(player):
    return [m.get("data", {}).get("event") for m in player.transport._ws.sent]


class TestTheTableIsTold:
    def test_a_dropped_player_is_announced_to_everyone_else(self):
        room, (alice, bob) = make_room("in_progress", "alice", "bob")
        alice.transport._ws.connected = False

        web_server._run_player_session(alice, alice.transport, room)

        assert alice.active is False
        assert "player_disconnected" in events(bob)

    def test_a_resignation_is_not_announced_again_as_a_drop(self):
        room, (alice, bob) = make_room("in_progress", "alice", "bob")
        alice.resigned = True
        alice.active = False

        web_server._run_player_session(alice, alice.transport, room)

        assert "player_disconnected" not in events(bob)

    def test_a_replaced_connection_dying_announces_nothing(self):
        room, (alice, bob) = make_room("in_progress", "alice", "bob")
        old = alice.transport
        alice.reattach(make_transport())
        alice.finish_reconnect()
        old._ws.connected = False

        web_server._run_player_session(alice, old, room)

        assert alice.active is True
        assert "player_disconnected" not in events(bob)


def test_a_replaced_connection_stays_open_until_its_socket_dies():
    """Returning from the session closes the socket. If a second tab owns it,
    that tab would reconnect and take the seat back, over and over."""
    room, (alice, _bob) = make_room("in_progress", "alice", "bob")
    old = alice.transport
    alice.reattach(make_transport())
    alice.finish_reconnect()

    session = threading.Thread(target=web_server._run_player_session, args=(alice, old, room), daemon=True)
    session.start()
    session.join(0.8)
    assert session.is_alive()

    old._ws.connected = False
    session.join(2.0)
    assert not session.is_alive()


def test_a_voter_who_leaves_cancels_the_rematch_instead_of_hanging_it():
    room, (alice, bob) = make_room("finished", "alice", "bob")
    room.rematch = {"requested_by": "alice", "bot_mix": [], "votes": {"alice": True, "bob": None}}
    bob.transport._ws.connected = False

    web_server._run_player_session(bob, bob.transport, room)

    assert room.rematch is None
    assert "REMATCH_DECLINED" in alice.transport._ws.message_types()


def test_a_reconnecting_client_learns_where_everyone_stands_in_the_auction():
    room, (alice, bob) = make_room("in_progress", "alice", "bob")
    bob.place_bid([6])

    web_server._send_opponent_roster(alice, room)

    sync = next(m["data"] for m in alice.transport._ws.sent
                if m.get("data", {}).get("username") == "bob")
    assert sync["in_auction"] is True
    assert sync["current_bid"] == 6


def test_a_rematch_is_a_new_game_with_nothing_left_over_from_the_last(monkeypatch):
    """The finished screen tells games apart by game_id. With the same id, a
    rematch's finish looked like the game already revealed, so the rating
    change on screen stayed the previous game's."""
    room, (alice, _bob) = make_room("finished", "alice", "bob")
    monkeypatch.setattr(room, "run_game", lambda: None)
    first_id = room.game_id
    room.elo_changes = {"alice": {"old_rating": 1000, "new_rating": 1016, "rating_change": 16}}
    room.game = object()
    room.rematch = {"requested_by": "alice", "bot_mix": [], "votes": {"alice": True, "bob": True}}

    web_server._maybe_start_rematch(room)

    assert room.state == "starting"
    assert room.game_id != first_id
    assert alice.game_id == room.game_id  # or the player would reject their own answers
    assert room.elo_changes is None
    assert room.game is None


def test_the_bot_board_endpoint_returns_every_bot():
    response = web_server.app.test_client().get("/api/leaderboard/bots")
    assert response.status_code == 200
    bots = response.get_json()["bots"]
    assert {b["name"] for b in bots} >= {"Ziggy Bot", "Magnus Carlsen Bot"}
    assert {"username", "name", "difficulty", "elo", "games_played", "games_won"} <= set(bots[0])


class TestBioEndpoint:
    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setattr(web_server.game_history, "is_configured", lambda: True)
        saved = {}
        monkeypatch.setattr(web_server.game_history, "set_player_bio",
                            lambda username, bio: saved.update({username: bio}) or True)
        client = web_server.app.test_client()
        client.saved = saved
        return client

    def _post(self, client, monkeypatch, match, **body):
        monkeypatch.setattr(web_server.game_history, "login_token_matches", lambda u, t: match)
        return client.post("/api/profile/bio", json={"username": "alice", "login_token": "t", **body})

    def test_saves_it_on_one_line(self, client, monkeypatch):
        response = self._post(client, monkeypatch, True, bio="  loves\n  auctions ")
        assert response.get_json() == {"bio": "loves auctions"}
        assert client.saved == {"alice": "loves auctions"}

    def test_an_empty_bio_clears_it(self, client, monkeypatch):
        assert self._post(client, monkeypatch, True, bio="   ").status_code == 200
        assert client.saved == {"alice": None}

    def test_someone_elses_token_is_refused(self, client, monkeypatch):
        assert self._post(client, monkeypatch, False, bio="hacked").status_code == 403
        assert client.saved == {}

    def test_a_failed_token_lookup_is_refused_too(self, client, monkeypatch):
        assert self._post(client, monkeypatch, None, bio="hacked").status_code == 403
        assert client.saved == {}

    def test_a_bot_bio_cannot_be_overwritten(self, client, monkeypatch):
        assert self._post(client, monkeypatch, True, username="__bot_ziggy__", bio="hacked").status_code == 403
        assert client.saved == {}

    def test_too_long_is_refused(self, client, monkeypatch):
        assert self._post(client, monkeypatch, True, bio="x" * 161).status_code == 400

    def test_non_text_is_refused(self, client, monkeypatch):
        assert self._post(client, monkeypatch, True, bio=["x"]).status_code == 400


def test_bot_pictures_are_found_by_roster_key_and_missing_ones_stay_absent(tmp_path):
    (tmp_path / "ziggy.png").write_bytes(b"x")
    (tmp_path / "not_a_bot.png").write_bytes(b"x")
    assert web_server._find_bot_avatars(str(tmp_path)) == {"__bot_ziggy__": "/static/avatars/bots/ziggy.png"}


def test_a_named_bot_seat_links_to_its_profile_and_a_human_does_not():
    room = web_server.GameRoom("TEST", 2, ["hard"], 1, 0, "public")
    room.players.append(make_player(username="alice"))
    seats = {s["username"]: s for s in room.joined_summary()}
    assert seats["alice"]["profile_username"] is None
    bot_seat = next(s for s in seats.values() if s["is_bot"])
    assert bot_seat["profile_username"].startswith("__bot_")
