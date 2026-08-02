import json
import socket
import threading
import time

import pytest

from network_server import start_server, accept_players
from highsociety.code.common.utils.network_utility import send_json, receive_json


class ScriptedSocketClient:
    """
    Drives the real wire protocol over a real TCP socket, without needing a
    terminal/stdin — used to exercise network_server.py + NetworkPlayer
    end-to-end. Always responds "pass" to every PLAYER_MOVE, so a whole game
    completes deterministically and quickly.
    """

    def __init__(self, host, port, username):
        self.username = username
        self.sock = socket.create_connection((host, port), timeout=5)
        self.sock.settimeout(5)
        self.received = []
        self._buffer = ""
        self._lock = threading.Lock()
        self._running = True
        self._thread = None

    def handshake(self):
        prompt = receive_json(self.sock)
        send_json(self.sock, {"message_type": "IDENTIFY_ACK", "prompt": self.username})
        prompt = receive_json(self.sock)
        send_json(self.sock, {"message_type": "IDENTIFY_ACK", "prompt": f"{self.username}-display"})
        welcome = receive_json(self.sock)
        assert welcome.get("message_type") == "IDENTIFY_SUCCESS", welcome

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while self._running:
            try:
                self.sock.settimeout(1.0)
                chunk = self.sock.recv(4096).decode("utf-8", errors="ignore")
                if not chunk:
                    self._running = False
                    break
                self._buffer += chunk
                while "\n" in self._buffer:
                    line, self._buffer = self._buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    payload = json.loads(line)
                    with self._lock:
                        self.received.append(payload)
                    if payload.get("message_type") == "PLAYER_MOVE":
                        response = self._answer(payload)
                        try:
                            send_json(self.sock, {"message_type": "RESPONSE", "prompt": response})
                        except OSError:
                            pass
            except socket.timeout:
                continue
            except OSError:
                self._running = False
                break

    def _answer(self, payload):
        """
        Always pass on bids (simplest way to drive a game to completion
        deterministically), but a discard-painting prompt requires a valid
        painting value, not a command — answer it correctly using the
        constraints the server already sent us.
        """
        if "discard" in payload.get("prompt", "").lower():
            allowed_paintings = (payload.get("constraints") or {}).get("allowed_paintings") or []
            if allowed_paintings:
                return str(allowed_paintings[0])
        return "pass"

    def prompts(self):
        with self._lock:
            return [p.get("prompt", "") for p in self.received]

    def close(self):
        self._running = False
        try:
            self.sock.close()
        except OSError:
            pass


@pytest.fixture
def running_server():
    port = 19100 + (id(threading.current_thread()) % 500)
    t = threading.Thread(
        target=start_server,
        kwargs={"host": "127.0.0.1", "port": port, "num_players": 2},
        daemon=True,
    )
    t.start()
    # NOTE: can't readiness-probe with a real connect() here — the server's
    # accept_players() loop counts every accepted connection toward
    # num_players, so a throwaway probe connection would consume one of the
    # two expected player slots and the real second client would hang
    # forever. A short real-time wait is the simplest safe option; uses
    # threading.Event().wait() since the session's autouse fixture
    # monkeypatches time.sleep to a no-op (to skip the CLI's countdown delay).
    threading.Event().wait(0.5)
    yield port


def test_two_players_complete_a_full_game_over_real_sockets(running_server):
    port = running_server
    c1 = ScriptedSocketClient("127.0.0.1", port, "alice")
    c2 = ScriptedSocketClient("127.0.0.1", port, "bob")

    c1.handshake()
    c2.handshake()
    c1.start()
    c2.start()

    deadline = time.time() + 30
    while time.time() < deadline:
        if not c1._running and not c2._running:
            break
        threading.Event().wait(0.2)

    all_prompts = " ".join(c1.prompts() + c2.prompts())
    assert "Game Started" in all_prompts or "🚀" in all_prompts
    assert "Game Concluded" in all_prompts

    c1.close()
    c2.close()


def test_recorded_network_game_replays_identically_via_a_plain_cli_replay(tmp_path):
    """
    Cross-mode record/replay: a game actually played over real sockets is
    recorded with network_server.py's --record, then replayed purely through
    PlayGame + ReplayPlayer (the same mechanism main.py --replay uses) with
    no networking involved at all — replay never needs a live connection,
    since ReplayPlayer's wrapped state-holder is a plain CLIPlayer regardless
    of how the original game was played.
    """
    from highsociety.code.gamecore.game_manager.gameplay import PlayGame
    from highsociety.code.gamecore.player.cliplayer import CLIPlayer
    from highsociety.code.gamecore.recording.session_recorder import SessionRecorder
    from highsociety.code.gamecore.recording.replay_player import ReplayPlayer

    port = 19600 + (id(threading.current_thread()) % 500)
    record_path = tmp_path / "network_session.json"
    t = threading.Thread(
        target=start_server,
        kwargs={"host": "127.0.0.1", "port": port, "num_players": 2, "seed": 5, "record_path": str(record_path)},
        daemon=True,
    )
    t.start()
    threading.Event().wait(0.5)

    c1 = ScriptedSocketClient("127.0.0.1", port, "alice")
    c2 = ScriptedSocketClient("127.0.0.1", port, "bob")
    c1.handshake()
    c2.handshake()
    c1.start()
    c2.start()

    deadline = time.time() + 30
    while time.time() < deadline:
        if not c1._running and not c2._running:
            break
        threading.Event().wait(0.2)
    c1.close()
    c2.close()

    assert record_path.exists()
    recording = SessionRecorder.load(record_path)
    assert recording["seed"] == 5
    assert len(recording["players"]) == 2

    replay_players = [
        ReplayPlayer(CLIPlayer(name=p["name"], username=p["username"]), recording["actions"][p["username"]])
        for p in recording["players"]
    ]
    replay_game = PlayGame(players=replay_players, mode="cli", seed=recording["seed"])
    replay_game.play_game()  # must complete without raising ReplayMismatch/ReplayReachedEndOfRecording

    assert any(p.points for p in replay_game.players)  # sanity: game actually progressed


def test_a_failed_handshake_does_not_permanently_steal_a_player_slot():
    """
    Regression test: accept_players() used to count accepted connections,
    not successful handshakes, so a client that failed the IDENTIFY
    handshake would permanently occupy one of the `expected_players` slots
    — the server proceeded with fewer real players than requested instead
    of waiting for a replacement connection.
    """
    port = 20700 + (id(threading.current_thread()) % 500)
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("127.0.0.1", port))
    server_socket.listen(5)

    result = {}

    def run_accept():
        result["players"] = accept_players(server_socket, expected_players=2, game_id="g1")

    t = threading.Thread(target=run_accept, daemon=True)
    t.start()
    threading.Event().wait(0.3)  # let accept_players start listening

    # Bad client: connects, then sends a bogus first response instead of IDENTIFY_ACK.
    bad = socket.create_connection(("127.0.0.1", port), timeout=5)
    receive_json(bad)  # username prompt
    send_json(bad, {"message_type": "NOT_IDENTIFY_ACK", "prompt": "oops"})
    receive_json(bad)  # IDENTIFY_ERROR
    bad.close()

    def good_client(username):
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        receive_json(sock)  # username prompt
        send_json(sock, {"message_type": "IDENTIFY_ACK", "prompt": username})
        receive_json(sock)  # display name prompt
        send_json(sock, {"message_type": "IDENTIFY_ACK", "prompt": f"{username}-display"})
        welcome = receive_json(sock)
        return sock, welcome

    good1_sock, welcome1 = good_client("alice")
    good2_sock, welcome2 = good_client("bob")

    t.join(timeout=10)
    assert not t.is_alive()

    players = result["players"]
    assert len(players) == 2
    assert {p.username for p in players} == {"alice", "bob"}
    assert welcome1["message_type"] == "IDENTIFY_SUCCESS"
    assert welcome2["message_type"] == "IDENTIFY_SUCCESS"

    for p in players:
        p.close()
    good1_sock.close()
    good2_sock.close()
    server_socket.close()


def test_handshake_rejects_a_mismatched_game_id_and_waits_for_a_replacement():
    """
    A client whose IDENTIFY_ACK carries a *different, present* game_id (e.g.
    stale data from a previous connection attempt, or a confused client
    talking to the wrong server) must be rejected rather than accepted into
    this game — same "wait for a real replacement" behavior as a malformed
    handshake.
    """
    port = 20800 + (id(threading.current_thread()) % 500)
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("127.0.0.1", port))
    server_socket.listen(5)

    result = {}

    def run_accept():
        result["players"] = accept_players(server_socket, expected_players=1, game_id="the-real-game")

    t = threading.Thread(target=run_accept, daemon=True)
    t.start()
    threading.Event().wait(0.3)

    # Wrong game_id: should be rejected with IDENTIFY_ERROR, not joined.
    wrong = socket.create_connection(("127.0.0.1", port), timeout=5)
    receive_json(wrong)  # username prompt (carries the real game_id, which this client ignores)
    send_json(wrong, {"game_id": "a-stale-or-different-game", "message_type": "IDENTIFY_ACK", "prompt": "mallory"})
    rejection = receive_json(wrong)
    assert rejection["message_type"] == "IDENTIFY_ERROR"
    wrong.close()

    # A correctly-tagged client should still be accepted to fill the slot.
    good = socket.create_connection(("127.0.0.1", port), timeout=5)
    prompt = receive_json(good)
    send_json(good, {"game_id": prompt["game_id"], "message_type": "IDENTIFY_ACK", "prompt": "alice"})
    receive_json(good)  # display name prompt
    send_json(good, {"game_id": prompt["game_id"], "message_type": "IDENTIFY_ACK", "prompt": "Alice"})
    welcome = receive_json(good)

    t.join(timeout=10)
    assert not t.is_alive()

    players = result["players"]
    assert len(players) == 1
    assert players[0].username == "alice"
    assert welcome["message_type"] == "IDENTIFY_SUCCESS"

    players[0].close()
    good.close()
    server_socket.close()
