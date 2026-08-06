#!/usr/bin/env python3
"""
HighSociety Web Server

Serves the browser frontend (highsociety/web/) and hosts one game "room" per
running process, played over WebSockets instead of raw sockets. Unlike
network_server.py, nothing about the game (seat count, bot mix, seed) is
configured on the command line: the first browser to open the page configures
it, and everyone else just opens the same URL and types their name.

Architecture note: this reuses the exact same engine/protocol layers
network_server.py does — PlayGame, NetworkPlayer, NetworkSpectator,
network/protocol.py — completely unchanged. The only new piece is
WebSocketTransport (network/transport.py), a second implementation of the
existing Transport interface. See README.md's "Architecture: adding a new
frontend" section, which called this out as the intended extension point.
"""
import argparse
import json
import os
import random
import sys
import threading
import time
import uuid
from typing import Optional

from flask import Flask, jsonify, render_template, request
from flask_sock import Sock

from highsociety.code.ai import BOT_TYPES
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager, LogType
from highsociety.code.common.utils.network_utility import get_local_ip
from highsociety.code.common.utils.utility import (
    generate_game_id,
    get_all_configurations,
    get_game_setting_configurations,
    validate_player_count,
)
from highsociety.code.gamecore.game_manager.gameplay import PlayGame
from highsociety.code.gamecore.network.transport import WebSocketTransport
from highsociety.code.gamecore.player.networkplayer import NetworkPlayer
from highsociety.code.gamecore.player.networkspectator import NetworkSpectator
from main import create_bot_players

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "highsociety", "web")

app = Flask(__name__, template_folder=os.path.join(WEB_DIR, "templates"),
            static_folder=os.path.join(WEB_DIR, "static"))
# Every sock.route() connection gets these options (see flask_sock.Sock.route's
# use of current_app.config['SOCK_SERVER_OPTIONS']) — ping_interval gives us
# dead-connection detection for free (a missed pong flips ws.connected to
# False), so unlike network_server.py's SocketTransport there's no need for a
# custom PING-message convention or a heartbeat-monitor thread here.
app.config['SOCK_SERVER_OPTIONS'] = {"ping_interval": 20}
sock = Sock(app)


class GameRoom:
    """
    All the state for the one game this process is hosting. Deliberately a
    single instance (see _room/_room_lock below) rather than a dict keyed by
    id — real multi-room hosting (the "later extend it by hosting on a
    website" goal) is a mechanical change to those two module-level names
    later, not a rewrite of this class.
    """

    def __init__(self, seats: int, bot_mix: list[str], seed: Optional[int], bot_think_time: float):
        self.game_id = generate_game_id()
        self.seats = seats
        self.bot_mix = bot_mix
        self.seed = seed if seed is not None else random.randint(0, 2 ** 31 - 1)
        self.bot_think_time = bot_think_time

        self.players = create_bot_players(bot_mix, bot_think_time) if bot_mix else []
        self.human_seats = seats - len(self.players)
        self.spectators = []

        self.state = "lobby"  # lobby -> starting -> in_progress -> finished
        self.game: Optional[PlayGame] = None
        self.lock = threading.Lock()

    def joined_summary(self) -> list[dict]:
        return [
            {"username": p.username, "name": p.name, "is_bot": not isinstance(p, NetworkPlayer)}
            for p in self.players
        ]

    def try_start(self) -> bool:
        """
        Called right after a human player is appended. Returns True exactly
        once — for whichever call observes the room as newly full — since
        every mutation of `self.players` happens under `self.lock`, so two
        connections can never both see themselves as "the one that filled it".
        """
        with self.lock:
            if self.state != "lobby":
                return False
            if len(self.players) < self.seats:
                return False
            self.state = "starting"
            return True

    def run_game(self) -> None:
        def _run():
            game = PlayGame(players=self.players, spectators=self.spectators,
                             mode='network', game_id=self.game_id, seed=self.seed)
            self.game = game
            self.state = "in_progress"
            game.play_game()
            self.state = "finished"
            for p in self.players:
                if isinstance(p, NetworkPlayer):
                    p.close()
            for s in self.spectators:
                s.close()

        threading.Thread(target=_run, daemon=True, name=f"Game-{self.game_id}").start()


_room: Optional[GameRoom] = None
_room_lock = threading.Lock()


def _get_room() -> Optional[GameRoom]:
    with _room_lock:
        return _room


def _is_valid_identify_ack(data: dict, game_id: str) -> bool:
    """Same rule network_server.py's accept_players/accept_spectators use:
    permissive on a missing game_id, strict on one that's present but wrong."""
    if not isinstance(data, dict) or data.get("message_type") != "IDENTIFY_ACK":
        return False
    incoming_game_id = data.get("game_id")
    if incoming_game_id is not None and incoming_game_id != game_id:
        return False
    return True


def _recv_json(ws, timeout: float = 30.0) -> dict:
    try:
        raw = ws.receive(timeout=timeout)
    except Exception:
        return {}
    if raw is None:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def _send(ws, game_id: str, message_type: str, prompt: str, requires_response: bool = False) -> None:
    ws.send(json.dumps({
        "game_id": game_id,
        "message_type": message_type,
        "prompt": prompt,
        "requires_response": requires_response,
    }))


def _identify(ws, game_id: str, first_prompt: str, second_prompt: str):
    """
    Runs the two-step IDENTIFY -> IDENTIFY_ACK handshake network_server.py
    already uses (see accept_players/accept_spectators), just reusable for
    either prompt ordering (players: username then name; spectators: name
    then username). Returns (first_answer, second_answer), or None if the
    handshake failed/disconnected.
    """
    _send(ws, game_id, "IDENTIFY", first_prompt, requires_response=True)
    data = _recv_json(ws)
    if not _is_valid_identify_ack(data, game_id) or not data.get("prompt"):
        _send(ws, game_id, "IDENTIFY_ERROR", f"Expected: {first_prompt}")
        return None
    first = data["prompt"]

    _send(ws, game_id, "IDENTIFY", second_prompt, requires_response=True)
    data = _recv_json(ws)
    if not _is_valid_identify_ack(data, game_id) or not data.get("prompt"):
        _send(ws, game_id, "IDENTIFY_ERROR", f"Expected: {second_prompt}")
        return None
    second = data["prompt"]

    return first, second


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config")
def api_config():
    return jsonify(get_game_setting_configurations() or {})


@app.route("/api/create_game", methods=["POST"])
def api_create_game():
    global _room
    body = request.get_json(silent=True) or {}

    try:
        seats = int(body.get("seats"))
    except (TypeError, ValueError):
        return jsonify({"error": "seats must be an integer"}), 400

    error = validate_player_count(seats)
    if error:
        return jsonify({"error": error}), 400

    bot_mix = body.get("bot_mix") or []
    if not isinstance(bot_mix, list) or any(not isinstance(b, str) for b in bot_mix):
        return jsonify({"error": "bot_mix must be a list of strings"}), 400
    unknown = set(bot_mix) - set(BOT_TYPES)
    if unknown:
        return jsonify({"error": f"Unknown bot type(s) {sorted(unknown)}; choose from {list(BOT_TYPES)}"}), 400
    if len(bot_mix) >= seats:
        return jsonify({"error": "At least one seat must be left for a human player"}), 400

    seed = body.get("seed")
    if seed is not None:
        try:
            seed = int(seed)
        except (TypeError, ValueError):
            return jsonify({"error": "seed must be an integer"}), 400

    try:
        bot_think_time = float(body.get("bot_think_time", 1.0))
    except (TypeError, ValueError):
        return jsonify({"error": "bot_think_time must be a number"}), 400

    with _room_lock:
        if _room is not None and _room.state in ("lobby", "starting", "in_progress"):
            return jsonify({"error": "A game is already in progress or being set up."}), 409
        _room = GameRoom(seats=seats, bot_mix=bot_mix, seed=seed, bot_think_time=bot_think_time)
        new_room = _room

    return jsonify(_status_payload(new_room))


def _status_payload(room: Optional[GameRoom]) -> dict:
    if room is None:
        return {"exists": False}
    payload = {
        "exists": True,
        "state": room.state,
        "game_id": room.game_id,
        "seats": room.seats,
        "human_seats": room.human_seats,
        "bot_mix": room.bot_mix,
        "joined": room.joined_summary(),
    }
    if room.state == "finished" and room.game is not None:
        # game.winners is a list of player objects (determine_winner()'s
        # return value, unchanged) — not JSON-serializable as-is.
        winners = room.game.winners or []
        payload["winners"] = [w.username for w in winners]
        payload["final_standings"] = room.game.final_standings
    return payload


@app.route("/api/status")
def api_status():
    return jsonify(_status_payload(_get_room()))


def _relay_player_chat(username: str, room: GameRoom, msg: dict) -> None:
    """
    WebSocketTransport's on_chat callback for a player connection (see its
    docstring for why this can't just be a background listener thread like
    spectators get) — reaches every other active human player plus every
    spectator. Players don't get a "target" selector the way spectators do
    (spectators-only chat doesn't make sense from a player's seat); this
    always reaches everyone at the table.
    """
    incoming_game_id = msg.get("game_id")
    if incoming_game_id is not None and incoming_game_id != room.game_id:
        return
    text = msg.get("prompt", "")
    if not text:
        return
    formatted = f"💬 {username}: {text}"
    for p in list(room.players):
        if isinstance(p, NetworkPlayer) and p.username != username and p.active:
            p.send_message(formatted, message_type="CHAT", from_user=username)
    for s in list(room.spectators):
        if s.active:
            s.send_message(formatted, message_type="CHAT", from_user=username)


@sock.route("/ws")
def ws_player(ws):
    room = _get_room()
    if room is None or room.state != "lobby":
        _send(ws, "", "IDENTIFY_ERROR", "No game is accepting players right now.")
        return

    game_id = room.game_id
    identity = _identify(ws, game_id, "Enter your username", "Enter your display name")
    if identity is None:
        return
    username, name = identity

    with room.lock:
        if room.state != "lobby":
            _send(ws, game_id, "IDENTIFY_ERROR", "Sorry, this game has already started.")
            return
        if len(room.players) >= room.seats:
            _send(ws, game_id, "IDENTIFY_ERROR", "Sorry, the game is already full.")
            return
        if any(p.username == username for p in room.players):
            _send(ws, game_id, "IDENTIFY_ERROR", "That username is already taken in this game.")
            return

        transport = WebSocketTransport(
            ws, label=f"{username}@web",
            on_chat=lambda msg: _relay_player_chat(username, room, msg),
        )
        transport.start()
        player = NetworkPlayer(name=name, username=username, transport=transport, game_id=game_id)
        room.players.append(player)

    _send(ws, game_id, "IDENTIFY_SUCCESS", f"Welcome {username}! Waiting for other players...")

    if room.try_start():
        room.run_game()

    # Keep this HTTP upgrade alive for the lifetime of the connection —
    # PlayGame calls transport.receive()/send() directly from the game
    # thread once it's this player's turn; this thread's only job is to let
    # flask-sock know when the socket has actually gone away (dead
    # connection detection is ping/pong-based — see SOCK_SERVER_OPTIONS).
    # threading.Event().wait() here, not time.sleep(): the test suite's
    # autouse fixture monkeypatches time.sleep to a no-op (see
    # tests/network/test_transport.py's note on this exact gotcha), which
    # would turn this into a real busy-spin — one per connection, for its
    # entire lifetime — instead of an idle wait.
    while player.active and transport.is_connected:
        threading.Event().wait(0.5)
    player.active = False


def _spectator_chat_listener(spectator: NetworkSpectator, room: GameRoom) -> None:
    """Mirrors network_server.py's _spectator_chat_listener: relays a
    spectator's CHAT to everyone (default) or spectators only."""
    while spectator.active:
        msg = spectator.transport.receive(timeout=1.0)
        if msg is None:
            continue
        if msg.get("message_type") != "CHAT":
            continue
        incoming_game_id = msg.get("game_id")
        if incoming_game_id is not None and incoming_game_id != room.game_id:
            continue
        text = msg.get("prompt", "")
        if not text:
            continue
        target = "spectators" if msg.get("target") == "spectators" else "all"
        # The message's own JSON carries `to_user(s)` structurally (see
        # protocol.py's _chat_payload), but no client renders that field —
        # they just print `prompt` — so a spectators-only message needs its
        # own tag baked into the text itself, or a receiving spectator has no
        # way to tell it apart from a message that also reached the players.
        tag = " (spectators only)" if target == "spectators" else ""
        formatted = f"💬 {spectator.username}{tag}: {text}"
        for other in list(room.spectators):
            if other is spectator or not other.active:
                continue
            other.send_message(formatted, message_type="CHAT", from_user=spectator.username, to_users=target)
        if target != "spectators":
            for player in list(room.players):
                if isinstance(player, NetworkPlayer) and player.active:
                    player.send_message(formatted, message_type="CHAT", from_user=spectator.username, to_users=target)


@sock.route("/ws_spectate")
def ws_spectate(ws):
    room = _get_room()
    if room is None:
        _send(ws, "", "IDENTIFY_ERROR", "No game exists yet.")
        return

    game_id = room.game_id
    # Same prompt order network_server.py's accept_spectators uses (name, then username).
    identity = _identify(ws, game_id, "You are connected as a spectator. Enter your name:", "Enter your username:")
    if identity is None:
        return
    name, username = identity

    transport = WebSocketTransport(ws, label=f"{username}@web-spectator")
    spectator = NetworkSpectator(transport=transport, name=name, username=username, game_id=game_id)
    room.spectators.append(spectator)

    _send(ws, game_id, "IDENTIFY_SUCCESS", f"Welcome {name}! You are now watching the game live.")

    chat_thread = threading.Thread(target=_spectator_chat_listener, args=(spectator, room),
                                    daemon=True, name=f"Chat-{username}")
    chat_thread.start()

    while spectator.active and transport.is_connected:
        threading.Event().wait(0.5)
    spectator.active = False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HighSociety Web Server")
    parser.add_argument("--host", type=str, default="0.0.0.0",
                        help="Host address to bind to (default: 0.0.0.0 for all interfaces)")
    parser.add_argument("--port", type=int, default=8000,
                        help="Port number to listen on (default: 8000)")
    args = parser.parse_args()

    config = get_all_configurations()
    LoggingManager(config)

    local_ip = get_local_ip()
    print(f"\n{'=' * 60}")
    print("🎮 HighSociety Web Server Started!")
    print(f"{'=' * 60}")
    print(f"On this machine: http://localhost:{args.port}")
    print(f"For friends on your LAN: http://{local_ip}:{args.port}")
    print(f"{'=' * 60}\n")

    app.run(host=args.host, port=args.port, threaded=True, debug=False, use_reloader=False)
