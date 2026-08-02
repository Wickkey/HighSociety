from socket import error as SocketError
from typing import Optional

from highsociety.code.gamecore.network.transport import Transport
from highsociety.code.gamecore.network.protocol import build_spectator_payload


class NetworkSpectator:
    """
    Remote, read-only spectator. Talks entirely through a Transport (today
    always a SocketTransport) using the shared spectator message protocol
    (network/protocol.py) — no socket knowledge of its own. Spectators are
    send-only in the current design (the server never reads anything back
    from one), so unlike NetworkPlayer this never calls transport.start().
    """
    def __init__(self, transport: Transport, username: str, name: str, game_id: str):
        self.transport = transport
        self.name = name
        self.username = username
        self.active = True
        self.game_id = game_id

    def send_message(self, msg: str, message_type: str, created_at: Optional[float] = None):
        payload = build_spectator_payload(
            game_id=self.game_id,
            message_type=message_type,
            prompt=msg,
            created_at=created_at,
        )
        try:
            self.transport.send(payload)
        except (BrokenPipeError, ConnectionResetError, SocketError):
            self.active = False
            print(f"⚠️ Connection lost while sending to {self.name}")

    def close(self):
        """Close the connection."""
        try:
            self.transport.close()
            self.active = False
        except Exception:
            pass
