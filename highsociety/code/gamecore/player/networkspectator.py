import socket
from socket import error as SocketError
import queue
import time
from typing import Union, Optional
from highsociety.code.common.utils.network_utility import send_json


class NetworkSpectator:
    """Remote Spectator using sockets"""
    def __init__(self, conn: socket.socket, username: str, name: str, game_id: str):
        self.conn = conn
        self.name = name
        self.username = username
        self.active = True
        self.game_id = game_id

    def send_message(self, msg: str, message_type: str, created_at: Optional[float] = None):
        """
        Sends message to the client socket.
        """
        if created_at is None:
            created_at = time.time()

        if message_type not in ["GLOBAL_EVENT", "GLOBAL_MOVE_INFO", "CHAT"]:
            raise ValueError(f"Invalid Message type: {message_type}")

        if message_type == 'GLOBAL_EVENT':
            payload = {
                "game_id": self.game_id,
                "message_type": message_type,
                "prompt": msg,
                "requires_response": False,
                "created_at": created_at
            }

        elif message_type == "CHAT":
            payload = {
                "game_id": self.game_id,
                "message_type": message_type,
                "prompt": msg,
                "from_user": "nan",
                "to_user(s)": "nan",
                "created_at": created_at
            }

        elif message_type == "GLOBAL_MOVE_INFO":
            payload = {
                "game_id": self.game_id,
                "message_type": message_type,
                "prompt": msg,
                "created_at": created_at,
                "requires_response": False
            }
        try:
            # Add newline for better formatting
            send_json(self.conn, payload)
        except (BrokenPipeError, ConnectionResetError, SocketError):
            self.active = False
            print(f"⚠️ Connection lost while sending to {self.name}")


    def close(self):
        """Close the connection and stop the receiver thread."""
        try:
            self.conn.close()
            self.active = False
        except:
            pass