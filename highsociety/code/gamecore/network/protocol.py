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
    "CHAT",
    "AUCTION_RESULT",
    # Structured, machine-parseable companions to the plain-text broadcasts
    # above, added for the web frontend (see gameplay.py) so a UI doesn't have
    # to regex-parse human-readable strings. Purely additive: CLI/existing
    # socket clients that don't look for these fields are unaffected.
    "AUCTION_UPDATE",  # live in-auction narration (turn start/bid/pass/fold/quit) — GLOBAL_EVENT-shaped
    "PLAYER_STATE",  # a snapshot of one player's own hand/points/status cards — PLAYER_INFO-shaped
    # Rematch voting, sent only after a game finishes (see web_server.py's
    # _broadcast_rematch_update/_handle_rematch_vote/_maybe_start_rematch) —
    # GLOBAL_EVENT-shaped, since none of them need a player_id/response.
    "REMATCH_UPDATE",  # a request was just made, or someone just voted on one
    "REMATCH_DECLINED",  # cancels a pending request
    "REMATCH_STARTING",  # unanimous accept — a fresh game is starting now
}

SPECTATOR_MESSAGE_TYPES = {
    "GLOBAL_EVENT",
    "GLOBAL_MOVE_INFO",
    "CHAT",
    "AUCTION_RESULT",
    "AUCTION_UPDATE",
}


def _chat_payload(*, game_id, prompt, created_at, from_user, to_users) -> dict:
    return {
        "game_id": game_id,
        "message_type": "CHAT",
        "prompt": prompt,
        "from_user": from_user or "nan",
        "to_user(s)": to_users or "all",
        "requires_response": False,
        "created_at": created_at,
    }


def build_player_payload(
    *,
    game_id,
    username: str,
    message_type: str,
    prompt: str,
    created_at: Optional[float] = None,
    constraints: Optional[dict] = None,
    from_user: Optional[str] = None,
    to_users: Optional[str] = None,
    data: Optional[dict] = None,
    move_type: Optional[str] = None,
) -> dict:
    """
    Builds the payload a NetworkPlayer sends. Raises ValueError on an unknown
    message_type. `data`, if given, is attached verbatim as an extra "data"
    field on top of whatever shape the message_type normally has — this is
    how structured, machine-parseable content (e.g. AUCTION_RESULT's full
    AuctionRecord) rides alongside a message's regular human-readable prompt.

    `move_type` (only meaningful when message_type == "PLAYER_MOVE") tells a
    bot which *kind* of move is being asked for — "bid" (the usual case) or
    "discard_painting" (a FauxPas discard choice) — without needing to parse
    the human-readable `prompt` text to tell them apart. Note that
    `constraints.allowed_paintings` is populated on every PLAYER_MOVE
    regardless of move_type (it just reflects whatever paintings the player
    currently owns), so it is NOT a reliable signal for which kind of move
    this is — always check `move_type` instead.
    """
    if message_type not in PLAYER_MESSAGE_TYPES:
        raise ValueError(f"Invalid message type: {message_type}")

    created_at = created_at if created_at is not None else time.time()

    if message_type == "CHAT":
        payload = _chat_payload(game_id=game_id, prompt=prompt, created_at=created_at, from_user=from_user, to_users=to_users)
    elif message_type in ("GLOBAL_EVENT", "AUCTION_RESULT", "AUCTION_UPDATE",
                          "REMATCH_UPDATE", "REMATCH_DECLINED", "REMATCH_STARTING"):
        payload = {
            "game_id": game_id,
            "message_type": message_type,
            "prompt": prompt,
            "requires_response": False,
            "created_at": created_at,
        }
    elif message_type == "GLOBAL_MOVE_INFO":
        payload = {
            "game_id": game_id,
            "message_type": message_type,
            "prompt": prompt,
            "created_at": created_at,
            "requires_response": False,
        }
    elif message_type == "INFO":
        payload = {
            "game_id": game_id,
            "message_type": message_type,
            "prompt": prompt,
            "requires_response": False,
            "created_at": created_at,
        }
    else:
        # PLAYER_INFO, PLAYER_MOVE_TIMER, PLAYER_MOVE, INPUT_ERROR all share this
        # shape, differing only in requires_response and (for PLAYER_MOVE) constraints.
        payload = {
            "game_id": game_id,
            "message_type": message_type,
            "player_id": [username],
            "prompt": prompt,
            "requires_response": message_type == "PLAYER_MOVE",
            "created_at": created_at,
        }
        if message_type == "PLAYER_MOVE":
            if constraints is not None:
                payload["constraints"] = constraints
            payload["move_type"] = move_type or "bid"

    if data is not None:
        payload["data"] = data
    return payload


def build_spectator_payload(
    *,
    game_id,
    message_type: str,
    prompt: str,
    created_at: Optional[float] = None,
    from_user: Optional[str] = None,
    to_users: Optional[str] = None,
    data: Optional[dict] = None,
) -> dict:
    """Builds the payload a NetworkSpectator sends. Raises ValueError on an unknown message_type.
    See build_player_payload for what `data` is."""
    if message_type not in SPECTATOR_MESSAGE_TYPES:
        raise ValueError(f"Invalid Message type: {message_type}")

    created_at = created_at if created_at is not None else time.time()

    if message_type == "CHAT":
        payload = _chat_payload(game_id=game_id, prompt=prompt, created_at=created_at, from_user=from_user, to_users=to_users)
    elif message_type == "GLOBAL_MOVE_INFO":
        payload = {
            "game_id": game_id,
            "message_type": message_type,
            "prompt": prompt,
            "created_at": created_at,
            "requires_response": False,
        }
    else:
        # GLOBAL_EVENT, AUCTION_RESULT
        payload = {
            "game_id": game_id,
            "message_type": message_type,
            "prompt": prompt,
            "requires_response": False,
            "created_at": created_at,
        }

    if data is not None:
        payload["data"] = data
    return payload
