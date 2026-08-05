"""
Transport-agnostic channel for exchanging JSON messages with one remote
party. Everything above this layer — message shapes (network/protocol.py),
NetworkPlayer/NetworkSpectator, and the game engine — depends only on this
interface, never on sockets directly. That's the seam a future transport
(e.g. a WebSocketTransport for a browser client) plugs into without any of
those layers changing.
"""
import json
import queue
import socket
import threading
import time
from abc import ABC, abstractmethod
from typing import Optional

from highsociety.code.common.utils.network_utility import send_json
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager

try:
    # Only needed by WebSocketTransport (web_server.py's browser path) —
    # CLI/socket play (main.py, network_server.py) never imports this module's
    # WebSocketTransport class, so keep it optional here rather than making
    # flask-sock/simple-websocket a hard dependency for every player.
    from simple_websocket import ConnectionClosed
except ImportError:
    class ConnectionClosed(Exception):
        pass


class Transport(ABC):
    @abstractmethod
    def start(self) -> None:
        """Begin receiving messages in the background, if applicable."""
        ...

    @abstractmethod
    def send(self, payload: dict) -> None:
        """Send one JSON-serializable message. May raise on a dead connection."""
        ...

    @abstractmethod
    def receive(self, timeout: Optional[float] = None) -> Optional[dict]:
        """
        Returns the next received message, waiting up to `timeout` seconds
        (None = wait indefinitely). Returns None if the timeout elapses
        first, or once the connection has closed for good.
        """
        ...

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        ...

    @abstractmethod
    def close(self) -> None:
        ...


class SocketTransport(Transport):
    """
    Transport implementation over a raw TCP socket — the only Transport that
    exists today (see network_server.py / network_client.py). Owns the
    receiver thread, message queue, and PING-based heartbeat tracking that
    used to live directly inside NetworkPlayer.

    A different transport (e.g. WebSockets) isn't obligated to reproduce the
    PING-message heartbeat convention — many transports have native
    liveness/ping-pong support instead; this is transport-specific, not part
    of the shared player-facing contract.
    """

    def __init__(self, conn: socket.socket, label: str = "transport"):
        self._conn = conn
        self._label = label
        self._active = True
        self._last_heartbeat = time.time()
        self._heartbeat_lock = threading.Lock()
        self._queue = queue.Queue()
        self._receiver_thread = None
        self._receiver_running = False

    def start(self) -> None:
        if self._receiver_thread is not None and self._receiver_thread.is_alive():
            return  # already running
        self._receiver_running = True
        self._receiver_thread = threading.Thread(
            target=self._receiver_loop, daemon=True, name=f"Receiver-{self._label}"
        )
        self._receiver_thread.start()

    def _receiver_loop(self) -> None:
        buffer = ""
        while self._receiver_running and self._active:
            try:
                self._conn.settimeout(1.0)
                chunk = self._conn.recv(4096).decode('utf-8', errors='ignore')

                if not chunk:
                    print(f"⚠️ Connection closed for {self._label}")
                    self._active = False
                    break

                buffer += chunk
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    line = line.strip()
                    if not line:
                        continue

                    msg = json.loads(line)
                    if msg.get("message_type") == "PING":
                        with self._heartbeat_lock:
                            self._last_heartbeat = time.time()
                        continue

                    self._queue.put(msg)

            except socket.timeout:
                continue
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                print(f"⚠️ Connection error for {self._label}: {e}")
                self._active = False
                break
            except Exception as e:
                LoggingManager.error(f"Error in transport receiver for {self._label}: {e}")
                if self._active:
                    continue
                else:
                    break

        try:
            self._queue.put(None)  # signal receiver has stopped
        except Exception:
            pass

    def send(self, payload: dict) -> None:
        send_json(self._conn, payload)

    def receive(self, timeout: Optional[float] = None) -> Optional[dict]:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def get_last_heartbeat(self) -> float:
        with self._heartbeat_lock:
            return self._last_heartbeat

    @property
    def is_connected(self) -> bool:
        return self._active

    def stop(self) -> None:
        self._receiver_running = False
        if self._receiver_thread is not None:
            self._receiver_thread.join(timeout=2.0)

    def close(self) -> None:
        self._active = False
        self.stop()
        try:
            self._conn.close()
        except Exception:
            pass


class WebSocketTransport(Transport):
    """
    Transport implementation over a browser WebSocket connection, via
    flask-sock/simple_websocket (see web_server.py). The seam the "Architecture:
    adding a new frontend" section of README.md predicted — NetworkPlayer,
    network/protocol.py, and the entire game engine are unchanged; this is the
    only new piece a browser client needed.

    Unlike SocketTransport, this owns no receiver thread of its own:
    simple_websocket.Server already runs its own background thread that reads
    the underlying socket and buffers incoming frames the moment it's
    constructed, so calling ws.receive(timeout=...) directly from whichever
    thread wants the next message already blocks/wakes correctly with no
    extra queue needed. It also has native WebSocket ping/pong (configured via
    ping_interval where web_server.py constructs the Server), so — unlike
    SocketTransport — there's no PING-message convention or explicit
    heartbeat tracking here; a dead connection surfaces as `is_connected`
    turning False / `receive()` raising ConnectionClosed.
    """

    def __init__(self, ws, label: str = "ws-transport"):
        self._ws = ws
        self._label = label

    def start(self) -> None:
        pass  # simple_websocket.Server already started its own reader thread at construction

    def send(self, payload: dict) -> None:
        self._ws.send(json.dumps(payload))

    def receive(self, timeout: Optional[float] = None) -> Optional[dict]:
        try:
            raw = self._ws.receive(timeout=timeout)
        except ConnectionClosed:
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            LoggingManager.error(f"Malformed JSON from {self._label}: {raw!r}")
            return None

    @property
    def is_connected(self) -> bool:
        return self._ws.connected

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass
