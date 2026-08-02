from socket import error as SocketError
from typing import Optional

from highsociety.code.gamecore.network.transport import Transport
from highsociety.code.gamecore.network.protocol import build_spectator_payload


class NetworkSpectator:
    """
    Remote spectator. Talks entirely through a Transport (today always a
    SocketTransport) using the shared spectator message protocol
    (network/protocol.py) — no socket knowledge of its own.

    Mostly send-only (the server drives game broadcasts to spectators
    without ever reading anything back), but does receive CHAT messages —
    see network_server.py's per-spectator chat-relay thread, which is the
    only code that calls transport.receive() on a spectator's connection.
    """
    def __init__(self, transport: Transport, username: str, name: str, game_id: str):
        self.transport = transport
        self.name = name
        self.username = username
        self.active = True
        self.game_id = game_id

    def start_receiver_thread(self) -> None:
        self.transport.start()

    def stop_receiver_thread(self) -> None:
        self.transport.stop()

    def send_message(
        self,
        msg: str,
        message_type: str,
        created_at: Optional[float] = None,
        from_user: Optional[str] = None,
        to_users: Optional[str] = None,
    ):
        payload = build_spectator_payload(
            game_id=self.game_id,
            message_type=message_type,
            prompt=msg,
            created_at=created_at,
            from_user=from_user,
            to_users=to_users,
        )
        try:
            self.transport.send(payload)
        except (BrokenPipeError, ConnectionResetError, SocketError):
            if self.active:
                self.active = False
                print(f"⚠️ Connection lost while sending to {self.name}")

    def close(self):
        """Close the connection."""
        try:
            self.transport.close()
            self.active = False
        except Exception:
            pass
