import socket
from socket import error as SocketError
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager
import json

def send_json(conn: socket.socket, payload: dict[str, any]):
    msg = json.dumps(payload) + "\n"
    conn.sendall(msg.encode("utf-8"))

def receive_json(conn: socket.socket) -> dict[str, any]:
    """
    Reads a single newline-delimited JSON message from conn, buffering across
    multiple recv() calls since a message is not guaranteed to arrive in one
    TCP segment. Intended for simple one-shot request/response exchanges
    (e.g. the connect-time handshake) — a long-lived connection with an
    ongoing message stream should use its own persistent buffer instead.
    """
    buffer = ""
    while "\n" not in buffer:
        chunk = conn.recv(4096).decode("utf-8", errors="ignore")
        if not chunk:
            raise ConnectionError("Connection closed before a complete message was received")
        buffer += chunk

    line, _, _ = buffer.partition("\n")
    return json.loads(line)