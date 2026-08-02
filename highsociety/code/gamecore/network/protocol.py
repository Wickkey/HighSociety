"""
Shared, transport-agnostic message-envelope builders for the JSON wire
protocol remote players and spectators speak. Factored out of
NetworkPlayer/NetworkSpectator so any Transport (today: SocketTransport;
later: e.g. a WebSocketTransport for a browser client) carries the exact
same message shapes — this module has no knowledge of sockets, threads, or
any other transport detail.
"""
import time
from typing import Optional

PLAYER_MESSAGE_TYPES = {
    "INFO",
    "PLAYER_INFO",
    "PLAYER_MOVE_TIMER",
    "PLAYER_MOVE",
    "INPUT_ERROR",
    "GLOBAL_EVENT",
    "GLOBAL_MOVE_INFO",
}

SPECTATOR_MESSAGE_TYPES = {"GLOBAL_EVENT", "GLOBAL_MOVE_INFO", "CHAT"}


def build_player_payload(
    *,
    game_id,
    username: str,
    message_type: str,
    prompt: str,
    created_at: Optional[float] = None,
    constraints: Optional[dict] = None,
) -> dict:
    """Builds the payload a NetworkPlayer sends. Raises ValueError on an unknown message_type."""
    if message_type not in PLAYER_MESSAGE_TYPES:
        raise ValueError(f"Invalid message type: {message_type}")

    created_at = created_at if created_at is not None else time.time()

    if message_type == "GLOBAL_EVENT":
        return {
            "game_id": game_id,
            "message_type": message_type,
            "prompt": prompt,
            "requires_response": False,
            "created_at": created_at,
        }

    if message_type == "GLOBAL_MOVE_INFO":
        return {
            "game_id": game_id,
            "message_type": message_type,
            "prompt": prompt,
            "created_at": created_at,
            "requires_response": False,
        }

    if message_type == "INFO":
        return {
            "game_id": game_id,
            "message_type": message_type,
            "prompt": prompt,
            "requires_response": False,
            "created_at": created_at,
        }

    # PLAYER_INFO, PLAYER_MOVE_TIMER, PLAYER_MOVE, INPUT_ERROR all share this shape,
    # differing only in requires_response and (for PLAYER_MOVE) constraints.
    payload = {
        "game_id": game_id,
        "message_type": message_type,
        "player_id": [username],
        "prompt": prompt,
        "requires_response": message_type == "PLAYER_MOVE",
        "created_at": created_at,
    }
    if message_type == "PLAYER_MOVE" and constraints is not None:
        payload["constraints"] = constraints
    return payload


def build_spectator_payload(
    *,
    game_id,
    message_type: str,
    prompt: str,
    created_at: Optional[float] = None,
) -> dict:
    """Builds the payload a NetworkSpectator sends. Raises ValueError on an unknown message_type."""
    if message_type not in SPECTATOR_MESSAGE_TYPES:
        raise ValueError(f"Invalid Message type: {message_type}")

    created_at = created_at if created_at is not None else time.time()

    if message_type == "CHAT":
        return {
            "game_id": game_id,
            "message_type": message_type,
            "prompt": prompt,
            "from_user": "nan",
            "to_user(s)": "nan",
            "created_at": created_at,
        }

    if message_type == "GLOBAL_MOVE_INFO":
        return {
            "game_id": game_id,
            "message_type": message_type,
            "prompt": prompt,
            "created_at": created_at,
            "requires_response": False,
        }

    # GLOBAL_EVENT
    return {
        "game_id": game_id,
        "message_type": message_type,
        "prompt": prompt,
        "requires_response": False,
        "created_at": created_at,
    }
