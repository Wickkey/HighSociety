import json
import socket
import threading
import time

import pytest

from network_server import start_server
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
