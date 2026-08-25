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
import datetime
import json
import os
import random
import secrets
import signal
import sys
import threading
import time
import uuid
from typing import Optional

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request
from flask_sock import Sock

from highsociety.code.ai import BOT_TYPES, create_bot_players
from highsociety.code.ai.mcts import decision_service
from highsociety.code.ai.mcts.worker_pool_decision_service import WorkerPoolBotDecisionService
from highsociety.code.common import achievements, matchmaking
from highsociety.code.common.db import game_history
from highsociety.code.common.guest_username import generate_guest_username
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager, LogType
from highsociety.code.common.utils.network_utility import get_local_ip
from highsociety.code.common.utils.utility import (
    generate_game_id,
    get_all_configurations,
    get_game_setting_configurations,
    validate_player_count,
)
from highsociety.code.gamecore.game_manager.auction_history import AuctionHistory
from highsociety.code.gamecore.game_manager.auction_information import summarize_card
from highsociety.code.gamecore.game_manager.gameplay import PlayGame
from highsociety.code.gamecore.network.transport import WebSocketTransport
from highsociety.code.gamecore.player.networkplayer import NetworkPlayer
from highsociety.code.gamecore.player.networkspectator import NetworkSpectator

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

# Pulls DATABASE_URL (and anything else in a local .env file) into the
# environment before ensure_schema() below reads it — a no-op if no .env
# file exists, which is exactly the case on a real host that sets env vars
# its own way (Render/Railway/etc.), so this only ever matters for local dev.
load_dotenv()

# No-op unless DATABASE_URL is set (see game_history's module docstring) —
# safe to call unconditionally on every process start, including once per
# gunicorn worker in production.
game_history.ensure_schema()

# Optional, same opt-in-via-env-var pattern as DATABASE_URL above: unset (the
# default, e.g. every local dev run) means index.html/404.html render with no
# gtag.js snippet at all, so local testing never pollutes real GA4 traffic.
GA_MEASUREMENT_ID = os.environ.get("GA_MEASUREMENT_ID")

# Same opt-in-via-env-var pattern again: unset (the default -- no real
# Client ID exists yet) means index.html renders with no "Continue with
# Google" button at all, guest-only, exactly today's behavior. Further
# gated on a configured database (see GOOGLE_SIGN_IN_ENABLED) -- an
# account that can't be persisted anywhere defeats the entire point of
# choosing Google over Guest, so the button simply doesn't offer that
# broken promise rather than appearing and then failing.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_SIGN_IN_ENABLED = bool(GOOGLE_CLIENT_ID) and game_history.is_configured()

# Same opt-in-via-env-var pattern again: unset/"0" (the default -- every
# local dev run and the whole pytest suite) keeps every MCTSBot decision
# computed in-process, exactly as before. A deployment sets BOT_POOL_SIZE to
# route decide_bid() to N separate worker processes per difficulty instead
# (see worker_pool_decision_service.py for why that can help even on a
# fractional CPU quota) -- tune N from here, no code change needed.
_BOT_POOL_SIZE = int(os.environ.get("BOT_POOL_SIZE", "0"))
if _BOT_POOL_SIZE > 0:
    decision_service.default_decision_service = WorkerPoolBotDecisionService(pool_size=_BOT_POOL_SIZE)
    print(f"BOT_POOL_SIZE={_BOT_POOL_SIZE} -- MCTS bot decisions run in worker processes, "
          f"{_BOT_POOL_SIZE} per difficulty.")
    # concurrent.futures.process registers its own atexit hook to shut down
    # every ProcessPoolExecutor's workers -- but atexit hooks only run on a
    # *normal* interpreter exit (sys.exit(), an uncaught exception, or the
    # main thread finishing), not a bare SIGTERM with no handler installed,
    # which is the OS's default action and skips Python cleanup entirely.
    # Confirmed empirically: a plain `kill -TERM` on this process left its
    # worker subprocesses running as orphans. Turning SIGTERM into a normal
    # sys.exit() here doesn't change gunicorn's own shutdown behavior (it
    # still just wants this process to exit) -- it only makes that exit
    # actually clean up the worker processes instead of orphaning them.
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))

# The lobby form only ever offers these as a preset <select> (see
# index.html's #host-turn-time) — no free-text entry — so the server
# rejects anything else here too rather than silently accepting whatever a
# modified/non-browser client sends.
_TURN_TIME_PRESETS = frozenset({15.0, 30.0, 60.0, 90.0, 120.0})


def _compute_disconnect_grace_seconds(turn_time_limit: Optional[float]) -> float:
    """
    How long NetworkPlayer.get_bid()/choose_painting_to_discard() should wait
    for a dropped connection to reconnect before falling back to an
    auto-quit for that one decision (see NetworkPlayer._wait_for_reconnect).
    20s flat for an untimed room; a fifth of the configured per-move timer
    otherwise, since a long grace on a short-timer room would let a
    disconnect eat most or all of everyone else's patience for that turn.
    """
    return 20.0 if turn_time_limit is None else turn_time_limit / 5


class GameRoom:
    """
    All the state for one hosted game. Many of these can exist at once, kept
    in the module-level `_rooms` dict below, keyed by `room_code` — this is
    what makes real multi-room website hosting possible (public/private rooms
    users create and join themselves, per the room-based-matchmaking design)
    instead of the one-game-per-process limit this class used to encode.
    """

    def __init__(self, room_code: str, seats: int, bot_mix: list[str], seed: Optional[int],
                 bot_think_time: float, visibility: str, turn_time_limit: Optional[float] = None,
                 reveal_cards: bool = True, show_logs: bool = True, host_username: Optional[str] = None):
        self.room_code = room_code
        self.game_id = generate_game_id()
        self.seats = seats
        self.bot_mix = bot_mix
        # Whoever's browser called /api/create_game -- purely informational
        # (game_history.py's games.host_player_id), resolved to a player_id
        # at game-history-write time the same way any other participant is.
        # None for the rare case a caller omits it; no gameplay logic
        # depends on this being set.
        self.host_username = host_username
        self.seed = seed if seed is not None else random.randint(0, 2 ** 31 - 1)
        self.bot_think_time = bot_think_time
        self.visibility = visibility  # "public" | "private"
        self.turn_time_limit = turn_time_limit  # seconds per move, or None for no limit
        self.disconnect_grace_seconds = _compute_disconnect_grace_seconds(turn_time_limit)
        # Fixed for the whole table at creation time — the frontend has no
        # runtime toggle for either of these anymore (see app.js's
        # resetGameState), just a read-only status label reflecting whatever
        # the host picked.
        self.reveal_cards = reveal_cards
        self.show_logs = show_logs
        # The "universal source of truth" for this room's current game state
        # (each player's money cards/paintings/Faux Pas status), refreshed
        # after every turn by PlayGame — see auction_history.py. One per
        # room, not per rematch: _maybe_start_rematch reuses it as-is, so a
        # rematch's early turns aren't missing the context of who just won.
        self.auction_history = AuctionHistory()

        self.players = create_bot_players(bot_mix, bot_think_time) if bot_mix else []
        self.human_seats = seats - len(self.players)
        self.spectators = []

        self.state = "lobby"  # lobby -> starting -> in_progress -> finished
        self.game: Optional[PlayGame] = None
        self.lock = threading.Lock()
        self.last_active_at = time.time()
        # token -> username, so a disconnected human player's browser can
        # reattach to their existing seat instead of the game treating a
        # refresh/dropped connection as a permanent quit. Issued once at
        # IDENTIFY_SUCCESS (see ws_player), never for bots.
        self.rejoin_tokens: dict[str, str] = {}
        # None, or {"requested_by": username, "bot_mix": [...], "votes": {username: True|None}}
        # while a rematch is being voted on — see _start_rematch_request/
        # _handle_rematch_vote/_maybe_start_rematch. Only set while
        # state == "finished"; cleared the moment it's declined or the
        # rematch actually starts.
        self.rematch: Optional[dict] = None

    def touch(self) -> None:
        """Marks the room as recently active, so the reaper thread's idle
        timeout (see _reap_stale_rooms) doesn't count time spent actually
        being used against it."""
        self.last_active_at = time.time()

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
            started_at = datetime.datetime.now(datetime.timezone.utc)
            game = PlayGame(players=self.players, spectators=self.spectators,
                             mode='network', game_id=self.game_id, seed=self.seed,
                             turn_duration=self.turn_time_limit,
                             auction_history=self.auction_history)
            self.game = game
            # A player who joined before someone else otherwise has no way
            # to know that other seat exists until some in-auction event
            # happens to name them by chance (see _send_opponent_roster) —
            # tell everyone about the full, already-seated table up front.
            for p in self.players:
                if isinstance(p, NetworkPlayer):
                    _send_opponent_roster(p, self)
            _broadcast_spectator_count(self)
            self.state = "in_progress"
            try:
                game.play_game()
            except Exception:
                # A crash in the game thread must not strand the room
                # "in_progress" forever: _reap_stale_rooms only reaps "lobby"
                # and "finished" rooms, so an unhandled exception here would
                # leak the room and hang every connected player/spectator with
                # no way out (see the RESPONSE-parsing hardening in
                # NetworkPlayer.get_bid/choose_painting_to_discard, added
                # alongside this guard as the first known way to trigger it).
                # Unlike a clean finish below, final_standings/winners may
                # never have been populated at all, so there's no sane state
                # left to offer a rematch from — close every connection
                # instead of leaving them open.
                LoggingManager.exception("Game thread crashed; closing the room's connections")
                self.state = "finished"
                self.touch()
                # Previously this left *no* games row at all -- "aborted"
                # and "never happened" were indistinguishable in history.
                # No participants (final_standings may never have been
                # populated) -- is_finished_successfully=False is the
                # entire signal this write exists to record.
                if game_history.is_configured():
                    game_history.record_finished_game_async(
                        room_code=self.room_code,
                        seats=self.seats,
                        bot_mix=self.bot_mix,
                        started_at=started_at,
                        finished_at=datetime.datetime.now(datetime.timezone.utc),
                        participants=[],
                        host_username=self.host_username,
                        time_control=int(self.turn_time_limit) if self.turn_time_limit else None,
                        is_finished_successfully=False,
                    )
                for p in self.players:
                    if isinstance(p, NetworkPlayer):
                        p.close()
                for s in self.spectators:
                    s.close()
                return
            self.state = "finished"
            self.touch()  # start the reaper's finished-room retention window from now
            _record_game_history(self, game, started_at)
            # Unlike before, human players' connections are deliberately left
            # open here instead of closed — a rematch (see
            # _start_rematch_request/_maybe_start_rematch) reuses the same
            # WebSocket rather than making everyone rejoin from scratch. Each
            # connected player's own per-connection loop (_run_player_session)
            # picks up rematch request/vote messages from here on; this
            # broadcast is what tells their *client* to stop watching the
            # live game panel and show the results screen instead — the job
            # closing the connection used to do implicitly (see app.js's
            # GLOBAL_EVENT "game_over" handler).
            for p in self.players:
                if isinstance(p, NetworkPlayer) and p.active:
                    p.send_message("", message_type="GLOBAL_EVENT", data={"event": "game_over"})
            for s in self.spectators:
                s.close()

        threading.Thread(target=_run, daemon=True, name=f"Game-{self.game_id}").start()


def _record_game_history(room: "GameRoom", game: PlayGame, started_at: datetime.datetime) -> None:
    """Translates this finished game into the plain-dict shape game_history
    expects (see its own docstring for the schema/rationale), then hands off
    to its fire-and-forget writer — cheap to call even when no database is
    configured, since is_configured() short-circuits before touching a
    network connection."""
    if not game_history.is_configured():
        return
    winner_usernames = {w.username for w in (game.winners or [])}
    players_by_username = {p.username: p for p in room.players}
    participants = []
    for standing in game.final_standings:
        player = players_by_username.get(standing["username"])
        if player is None:
            continue
        is_bot = not isinstance(player, NetworkPlayer)
        participants.append({
            "is_bot": is_bot,
            "username": None if is_bot else player.username,
            "name": player.name,
            "points": standing["points"],
            "money_left": standing["money_left"],
            "is_winner": standing["username"] in winner_usernames,
            "eliminated": standing["eliminated"],
            # Only MCTSBot instances (the web lobby's easy/medium/hard
            # choices) have this — see game_history.py's bots table, which
            # uses it to attribute this seat to the right shared
            # per-difficulty player_id. None for a human or for any of the
            # other bot classes (greedy/pass/capped, not offered here).
            "difficulty": getattr(player, "difficulty", None),
            # Always the real per-game username (bot or human) -- unlike
            # "username" above, which is deliberately None for a bot. Only
            # used inside record_finished_game to attribute auction_rounds
            # events/recipients (which always name the real username) back
            # to a resolved player_id; never itself persisted.
            "game_username": player.username,
        })
    auction_rounds = game.get_auction_history()
    achievement_unlocks = achievements.detect_per_game_achievements(
        final_standings=game.final_standings,
        winner_usernames=winner_usernames,
        auction_rounds=auction_rounds,
        bot_mix=room.bot_mix,
    )
    game_history.record_finished_game_async(
        room_code=room.room_code,
        seats=room.seats,
        bot_mix=room.bot_mix,
        started_at=started_at,
        achievement_unlocks=achievement_unlocks,
        auction_rounds=auction_rounds,
        finished_at=datetime.datetime.now(datetime.timezone.utc),
        participants=participants,
        host_username=room.host_username,
        time_control=int(room.turn_time_limit) if room.turn_time_limit else None,
    )


# Excludes 0/O and 1/I — a room code is meant to be read aloud or typed by a
# friend, and those pairs are the ones people actually misread/mistype.
_ROOM_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_ROOM_CODE_LENGTH = 5

# How long an idle/finished room is kept around before the reaper drops it.
# Generous on purpose — this is memory hygiene for abandoned rooms, not a
# tight resource limit; a real player should never be able to "run out the
# clock" on a room they're actively using (every join/start/finish calls
# GameRoom.touch()).
_ROOM_LOBBY_IDLE_TIMEOUT_SECONDS = 30 * 60
_ROOM_FINISHED_RETENTION_SECONDS = 10 * 60
_ROOM_REAPER_INTERVAL_SECONDS = 60

_rooms: dict[str, GameRoom] = {}
_rooms_lock = threading.Lock()


def _generate_room_code() -> str:
    return "".join(random.choices(_ROOM_CODE_ALPHABET, k=_ROOM_CODE_LENGTH))


def _create_room(seats: int, bot_mix: list[str], seed: Optional[int], bot_think_time: float,
                  visibility: str, turn_time_limit: Optional[float] = None,
                  reveal_cards: bool = True, show_logs: bool = True,
                  host_username: Optional[str] = None) -> GameRoom:
    with _rooms_lock:
        for _ in range(20):
            code = _generate_room_code()
            if code not in _rooms:
                break
        else:
            # Astronomically unlikely at this scale of concurrent rooms, but
            # a longer code guarantees termination rather than looping forever.
            code = "".join(random.choices(_ROOM_CODE_ALPHABET, k=_ROOM_CODE_LENGTH * 2))
        room = GameRoom(room_code=code, seats=seats, bot_mix=bot_mix, seed=seed,
                         bot_think_time=bot_think_time, visibility=visibility,
                         turn_time_limit=turn_time_limit, reveal_cards=reveal_cards,
                         show_logs=show_logs, host_username=host_username)
        _rooms[code] = room
        return room


def _get_room(room_code: Optional[str]) -> Optional[GameRoom]:
    if not room_code:
        return None
    with _rooms_lock:
        return _rooms.get(room_code)


def _reap_stale_rooms() -> None:
    """
    Background hygiene for rooms nobody's using anymore: a lobby that never
    filled up, or a finished game nobody's still looking at its standings
    for. Without this, `_rooms` only ever grows for the lifetime of the
    process. Runs forever as a daemon thread — see its start call near the
    bottom of this module.

    Also reaps idle bot worker pools (see BOT_POOL_SIZE above) on the same
    cadence -- an unrelated kind of staleness, but sharing this loop's
    existing periodic wakeup avoids a whole second background thread just
    for it.
    """
    while True:
        threading.Event().wait(_ROOM_REAPER_INTERVAL_SECONDS)
        if isinstance(decision_service.default_decision_service, WorkerPoolBotDecisionService):
            decision_service.default_decision_service.reap_idle_pools()
        now = time.time()
        with _rooms_lock:
            stale = [
                (code, room) for code, room in _rooms.items()
                if (room.state == "lobby" and now - room.last_active_at > _ROOM_LOBBY_IDLE_TIMEOUT_SECONDS)
                or (room.state == "finished" and now - room.last_active_at > _ROOM_FINISHED_RETENTION_SECONDS)
            ]
            for code, _room in stale:
                del _rooms[code]
        # A finished room's human connections are deliberately kept open past
        # game-end for rematches (see GameRoom.run_game) — once the room
        # itself is gone, nobody's still-open tab should linger forever;
        # close them here instead. Outside the lock: NetworkPlayer.close()
        # can block briefly on the socket, and nothing else touches these
        # rooms once they're out of `_rooms`.
        for _code, room in stale:
            for p in room.players:
                if isinstance(p, NetworkPlayer) and p.active:
                    p.close()


threading.Thread(target=_reap_stale_rooms, daemon=True, name="RoomReaper").start()


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


def _send(ws, game_id: str, message_type: str, prompt: str, requires_response: bool = False,
          data: Optional[dict] = None) -> None:
    payload = {
        "game_id": game_id,
        "message_type": message_type,
        "prompt": prompt,
        "requires_response": requires_response,
    }
    if data is not None:
        payload["data"] = data
    ws.send(json.dumps(payload))


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


_MAX_USERNAME_LENGTH = 24


def _verify_google_id_token(token: str) -> Optional[dict]:
    """
    Verifies a "Sign In With Google" ID token's signature, audience, and
    expiry against Google's own public keys, returning the decoded claims
    (sub/email/name -- see https://developers.google.com/identity/openid-connect/openid-connect#obtainuserinfo)
    on success, or None on any failure at all (expired, wrong audience,
    malformed, a network hiccup fetching Google's keys, GOOGLE_CLIENT_ID
    unset). Every failure mode collapses to the same "reject this token"
    response for the caller -- none are actionable differently from a
    client's perspective, and none should ever crash the request.

    Imports google-auth lazily, same reasoning as game_history.py's own
    lazy `import psycopg2` inside _connect(): merely importing this
    module must never fail for an environment that hasn't installed it,
    only actually calling this (which only happens once GOOGLE_CLIENT_ID
    is set) needs it.
    """
    if not GOOGLE_CLIENT_ID:
        return None
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token
    try:
        return google_id_token.verify_oauth2_token(token, google_requests.Request(), audience=GOOGLE_CLIENT_ID)
    except Exception as e:  # noqa: BLE001 -- any verification failure means "reject this token"
        LoggingManager.warning(f"Google ID token verification failed: {e}", log_type=LogType.SECURITY)
        return None


@app.route("/api/auth/google", methods=["POST"])
def api_auth_google():
    """
    First step of Google sign-in: the client already ran Google's own
    "Sign In With Google" button flow and got back a signed ID token --
    this verifies it and resolves whichever players row (if any) belongs
    to that Google account, keyed on the token's "sub" claim (Google's
    own stable per-user id, not the email, which a user could in
    principle change). A first-time Google account gets
    needs_username=True instead of an error -- see
    /api/auth/google/claim_username, the very next call the client makes
    in that case.
    """
    body = request.get_json(silent=True) or {}
    token = body.get("id_token")
    if not isinstance(token, str) or not token:
        return jsonify({"error": "id_token is required"}), 400

    claims = _verify_google_id_token(token)
    if claims is None:
        return jsonify({"error": "Invalid or expired Google sign-in. Please try again."}), 401

    existing = game_history.find_player_by_google_id(claims["sub"])
    if existing:
        return jsonify(existing)
    return jsonify({"needs_username": True, "suggested_display_name": claims.get("name") or ""})


@app.route("/api/auth/google/claim_username", methods=["POST"])
def api_auth_google_claim_username():
    """
    Second step, only reached when /api/auth/google above returned
    needs_username=True: the client is submitting a username for a
    first-time Google account. Re-verifies the ID token from scratch
    (deliberately not trusting a bare google_id supplied by the client
    alone) -- otherwise anyone could POST an arbitrary made-up google_id
    here and claim a username under an identity they don't actually
    control.
    """
    body = request.get_json(silent=True) or {}
    token = body.get("id_token")
    if not isinstance(token, str) or not token:
        return jsonify({"error": "id_token is required"}), 400

    username = (body.get("username") or "").strip()
    if not username or len(username) > _MAX_USERNAME_LENGTH:
        return jsonify({"error": f"Username must be 1-{_MAX_USERNAME_LENGTH} characters"}), 400
    display_name = (body.get("display_name") or "").strip() or username

    claims = _verify_google_id_token(token)
    if claims is None:
        return jsonify({"error": "Invalid or expired Google sign-in. Please try again."}), 401

    # Already claimed by this exact Google account -- e.g. the client
    # retried after a flaky connection ate the first response. Return the
    # existing row rather than a spurious "username taken" for a name
    # this same account already owns.
    existing = game_history.find_player_by_google_id(claims["sub"])
    if existing:
        return jsonify(existing)

    if game_history.username_is_taken(username):
        return jsonify({"error": "That username is already taken."}), 409

    ok = game_history.create_google_player(claims["sub"], claims.get("email"), username, display_name)
    if not ok:
        return jsonify({"error": "That username is already taken."}), 409

    return jsonify({"username": username, "display_name": display_name})


def _generate_unique_guest_username() -> Optional[str]:
    """
    No database means nothing to check a candidate against -- just hand
    back a fresh one so the guest flow keeps working under this app's
    established DATABASE_URL="" local-testing convention. With a real
    database, retries a handful of times against username_is_taken;
    with ~30 colors * ~50 names * 900 numbers, a collision on every one
    of 20 random tries is effectively impossible.
    """
    if not game_history.is_configured():
        return generate_guest_username()
    for _ in range(20):
        candidate = generate_guest_username()
        if not game_history.username_is_taken(candidate):
            return candidate
    return None


@app.route("/api/auth/guest/suggest")
def api_auth_guest_suggest():
    username = _generate_unique_guest_username()
    if username is None:
        return jsonify({"error": "Couldn't generate a username right now. Please try again."}), 503
    return jsonify({"username": username})


@app.route("/api/auth/guest/claim", methods=["POST"])
def api_auth_guest_claim():
    """
    Reserves a guest username -- either the one /api/auth/guest/suggest
    handed back, or one the visitor typed themselves (the field is
    freely editable, see the login screen). No database means nothing to
    reserve against, so this just echoes the username back unchecked,
    same as the suggest endpoint above.
    """
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    if not username or len(username) > _MAX_USERNAME_LENGTH:
        return jsonify({"error": f"Username must be 1-{_MAX_USERNAME_LENGTH} characters"}), 400

    if not game_history.is_configured():
        return jsonify({"username": username})

    if game_history.username_is_taken(username):
        return jsonify({"error": "That username is already taken."}), 409

    ok = game_history.create_guest_player(username)
    if not ok:
        return jsonify({"error": "That username is already taken."}), 409

    return jsonify({"username": username})


@app.route("/api/auth/username/change", methods=["POST"])
def api_auth_username_change():
    """
    Renames an existing profile's username -- guest or Google-linked
    alike (see game_history.rename_player). Used by the profile
    popover's Save button, and only reached when the new username
    actually differs from the current one (app.js skips the round-trip
    otherwise).
    """
    body = request.get_json(silent=True) or {}
    old_username = (body.get("old_username") or "").strip()
    new_username = (body.get("new_username") or "").strip()
    if not new_username or len(new_username) > _MAX_USERNAME_LENGTH:
        return jsonify({"error": f"Username must be 1-{_MAX_USERNAME_LENGTH} characters"}), 400

    if not game_history.is_configured():
        return jsonify({"username": new_username})

    ok = game_history.rename_player(old_username, new_username)
    if not ok:
        return jsonify({"error": "That username is already taken."}), 409

    return jsonify({"username": new_username})


# --------------------------------------------------------- matchmaking --

def _create_matchmaking_room(usernames: list[str]) -> str:
    """
    The create_room_fn matchmaking.py's status()/_try_match() call once
    enough similarly-rated players are waiting -- this is the one place
    that actually knows what a "room" is; matchmaking.py itself never
    imports GameRoom or anything game-specific. Private (not listed in
    /api/rooms) since only the matched players ever receive this room's
    code, no bots (every seat is a real matched human), no turn timer by
    default -- same as a plain hosted game.
    """
    room = _create_room(seats=len(usernames), bot_mix=[], seed=None, bot_think_time=1.5,
                         visibility="private")
    return room.room_code


@app.route("/api/matchmaking/join", methods=["POST"])
def api_matchmaking_join():
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    if not username:
        return jsonify({"error": "username is required"}), 400

    try:
        seats = int(body.get("seats"))
    except (TypeError, ValueError):
        return jsonify({"error": "seats must be an integer"}), 400
    error = validate_player_count(seats)
    if error:
        return jsonify({"error": error}), 400

    elo = game_history.get_player_elo(username)
    ticket_id = matchmaking.join(username, elo, seats)
    return jsonify({"ticket_id": ticket_id})


@app.route("/api/matchmaking/status")
def api_matchmaking_status():
    ticket_id = request.args.get("ticket")
    result = matchmaking.status(ticket_id, _create_matchmaking_room)
    if result is None:
        return jsonify({"error": "Unknown or cancelled matchmaking ticket."}), 404
    return jsonify(result)


@app.route("/api/matchmaking/cancel", methods=["POST"])
def api_matchmaking_cancel():
    body = request.get_json(silent=True) or {}
    matchmaking.cancel(body.get("ticket_id"))
    return jsonify({})


# ------------------------------------------------------- achievements/profile --

@app.route("/api/achievements")
def api_achievements():
    """Unlocked achievement ids for the given username -- always [] for a
    guest account, see game_history.get_player_achievements's own
    docstring for why. No auth/session to prove "this is really you"
    requesting your own achievements, same trust model as every other
    read in this app (e.g. /api/auth/username/change) -- the frontend
    only ever calls this with the browser's own saved username."""
    username = request.args.get("username") or ""
    return jsonify({"achievements": game_history.get_player_achievements(username)})


@app.route("/api/profile/<username>")
def api_profile(username):
    """Public profile stats -- games played, win rate, current Elo.
    Unlike achievements this isn't gated to Google-linked accounts (see
    get_player_profile_stats's own docstring): it's just a factual record
    of games already played under this exact username, visible to anyone,
    matching the user's own "should be publicly visible" ask."""
    stats = game_history.get_player_profile_stats(username)
    if stats is None:
        return jsonify({"error": "No games recorded for that username yet."}), 404
    return jsonify({
        "username": username,
        "games_played": stats["games_played"],
        "wins": stats["wins"],
        "win_rate": stats["win_rate"],
        "avg_placement": stats["avg_placement"],
        "avg_points": stats["avg_points"],
        "avg_money_remaining": stats["avg_money_remaining"],
        "elo": game_history.get_player_elo(username),
    })


@app.route("/api/global_stats")
def api_global_stats():
    """Home-page footer: total games played / total players site-wide.
    204 (not a zeroed JSON body) when unavailable, so the frontend can
    just skip rendering the section instead of showing a misleading 0."""
    stats = game_history.get_global_stats()
    if stats is None:
        return "", 204
    return jsonify(stats)


@app.route("/api/games/<username>")
def api_recent_games(username):
    """'My Games' list + the home screen's Recent Games widget -- same
    data, different caller. Always 200 with a (possibly empty) list, no
    404: an empty list already means "nothing to show" to both callers."""
    return jsonify({"games": game_history.get_recent_games(username)})


@app.route("/api/games/detail/<int:game_id>")
def api_game_detail(game_id):
    """Full per-participant breakdown for one game -- the "click a game
    to see full results" view. No username in this path (unlike /api/
    games/<username> above) since a game's own id is all this needs -- see
    get_game_detail's own docstring on why no access check is tied to it."""
    detail = game_history.get_game_detail(game_id)
    if detail is None:
        return jsonify({"error": "No such game."}), 404
    return jsonify(detail)


@app.route("/api/leaderboard")
def api_leaderboard():
    """Top players by Elo -- Google-linked accounts only (see
    get_leaderboard's own docstring for why guests and bots are both
    excluded)."""
    return jsonify({"leaderboard": game_history.get_leaderboard()})


@app.route("/api/profile/<username>/rating_history")
def api_rating_history(username):
    """This player's Elo over time, for the leaderboard screen's
    sparkline -- always 200 with a (possibly empty) list."""
    return jsonify({"history": game_history.get_rating_history(username)})


@app.route("/")
def index():
    return render_template(
        "index.html", ga_measurement_id=GA_MEASUREMENT_ID,
        google_client_id=GOOGLE_CLIENT_ID if GOOGLE_SIGN_IN_ENABLED else None,
    )


@app.route("/robots.txt")
def robots_txt():
    # /?room=<code> pages are ephemeral game sessions, not indexable
    # content -- the room may not even exist anymore by the time a crawler
    # visits. Everything else (just the homepage, in practice) is fine to
    # crawl, so nothing else needs an explicit Disallow.
    return Response("User-agent: *\nDisallow: /?room=\n", mimetype="text/plain")


@app.errorhandler(404)
def not_found(_error):
    return render_template("404.html", ga_measurement_id=GA_MEASUREMENT_ID), 404


@app.route("/api/config")
def api_config():
    return jsonify(get_game_setting_configurations() or {})


@app.route("/api/create_game", methods=["POST"])
def api_create_game():
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
        bot_think_time = float(body.get("bot_think_time", 1.5))
    except (TypeError, ValueError):
        return jsonify({"error": "bot_think_time must be a number"}), 400

    visibility = body.get("visibility", "public")
    if visibility not in ("public", "private"):
        return jsonify({"error": "visibility must be 'public' or 'private'"}), 400

    turn_time_limit = body.get("turn_time_limit")
    if turn_time_limit is not None:
        try:
            turn_time_limit = float(turn_time_limit)
        except (TypeError, ValueError):
            return jsonify({"error": "turn_time_limit must be a number"}), 400
        if turn_time_limit <= 0:
            turn_time_limit = None  # 0/blank means "no limit", not "instant timeout"
        elif turn_time_limit not in _TURN_TIME_PRESETS:
            return jsonify({"error": f"turn_time_limit must be one of {sorted(_TURN_TIME_PRESETS)} or omitted"}), 400

    reveal_cards = body.get("reveal_cards", True)
    show_logs = body.get("show_logs", True)
    if not isinstance(reveal_cards, bool) or not isinstance(show_logs, bool):
        return jsonify({"error": "reveal_cards and show_logs must be booleans"}), 400

    host_username = body.get("host_username")
    if host_username is not None and not isinstance(host_username, str):
        return jsonify({"error": "host_username must be a string"}), 400

    room = _create_room(seats=seats, bot_mix=bot_mix, seed=seed, bot_think_time=bot_think_time,
                         visibility=visibility, turn_time_limit=turn_time_limit,
                         reveal_cards=reveal_cards, show_logs=show_logs,
                         host_username=host_username)
    return jsonify(_status_payload(room))


def _status_payload(room: Optional[GameRoom]) -> dict:
    if room is None:
        return {"exists": False}
    payload = {
        "exists": True,
        "room_code": room.room_code,
        "visibility": room.visibility,
        "state": room.state,
        "game_id": room.game_id,
        "seats": room.seats,
        "human_seats": room.human_seats,
        "bot_mix": room.bot_mix,
        "seed": room.seed,
        "turn_time_limit": room.turn_time_limit,
        "reveal_cards": room.reveal_cards,
        "show_logs": room.show_logs,
        "joined": room.joined_summary(),
    }
    if room.state == "finished" and room.game is not None:
        # game.winners is a list of player objects (determine_winner()'s
        # return value, unchanged) — not JSON-serializable as-is.
        winners = room.game.winners or []
        payload["winners"] = [w.username for w in winners]
        payload["final_standings"] = room.game.final_standings

        # Everything a finished-screen client needs to render the rematch
        # panel from a plain page load/status poll, not just from the live
        # REMATCH_UPDATE pushes (see _broadcast_rematch_update) — e.g. a
        # browser that was mid-refresh when a vote was already underway.
        eligible = _rematch_eligible_players(room)
        bot_seats = max(room.seats - len(eligible), 0)
        payload["rematch"] = room.rematch
        payload["rematch_bot_seats"] = bot_seats
        payload["rematch_default_bot_mix"] = _default_rematch_bot_mix(room, bot_seats)
    return payload


@app.route("/api/status")
def api_status():
    return jsonify(_status_payload(_get_room(request.args.get("room"))))


@app.route("/api/rooms")
def api_rooms():
    """Public lobby listing — rooms anyone can browse and join without
    knowing a code, as opposed to private rooms (reachable only by sharing
    the room code out of band). Only rooms still accepting players are worth
    showing here; an in-progress/finished room has nothing to join."""
    with _rooms_lock:
        rooms = [
            {
                "room_code": room.room_code,
                "seats": room.seats,
                "human_seats": room.human_seats,
                "joined": len(room.players),
                "bot_mix": room.bot_mix,
            }
            for room in _rooms.values()
            if room.visibility == "public" and room.state == "lobby"
        ]
    rooms.sort(key=lambda r: r["room_code"])
    return jsonify({"rooms": rooms})


@app.route("/api/add_bot", methods=["POST"])
def api_add_bot():
    """
    Lets anyone already waiting in a room's lobby fill an empty seat with a
    bot — e.g. one friend backs out at the last minute and nobody wants to
    wait around for a replacement. Same effect as configuring more bots at
    creation time, just usable after the room already exists.
    """
    body = request.get_json(silent=True) or {}
    room = _get_room(body.get("room"))
    if room is None:
        return jsonify({"error": "No such room."}), 404

    bot_type = body.get("bot_type")
    if bot_type not in BOT_TYPES:
        return jsonify({"error": f"Unknown bot type; choose from {list(BOT_TYPES)}"}), 400

    with room.lock:
        if room.state != "lobby":
            return jsonify({"error": "This game has already started."}), 409
        if len(room.players) >= room.seats:
            return jsonify({"error": "The room is already full."}), 409

        taken_usernames = {p.username for p in room.players}
        bot = create_bot_players([bot_type], think_time=room.bot_think_time, taken_usernames=taken_usernames)[0]
        room.players.append(bot)
        room.bot_mix.append(bot_type)
        room.human_seats -= 1  # this seat is no longer reserved for a human
        room.touch()

    if room.try_start():
        room.run_game()

    return jsonify(_status_payload(room))


QUICK_REACTION_EMOJI = {"🗣️", "😂", "💀", "🔥", "🤔"}


def _relay_player_chat(username: str, room: GameRoom, msg: dict) -> None:
    """
    WebSocketTransport's on_chat callback for a player connection (see its
    docstring for why this can't just be a background listener thread like
    spectators get) — reaches every other active human player plus every
    spectator. Players don't get a "target" selector the way spectators do
    (spectators-only chat doesn't make sense from a player's seat); this
    always reaches everyone at the table. Also carries REACTION messages
    (a quick emoji), which share the same live-relay path as CHAT.
    """
    incoming_game_id = msg.get("game_id")
    if incoming_game_id is not None and incoming_game_id != room.game_id:
        return
    if msg.get("message_type") == "REACTION":
        emoji = msg.get("emoji", "")
        if emoji not in QUICK_REACTION_EMOJI:
            return  # ignore anything not from the fixed reaction set
        for p in list(room.players):
            if isinstance(p, NetworkPlayer) and p.username != username and p.active:
                p.send_message("", message_type="REACTION", from_user=username, data={"emoji": emoji})
        for s in list(room.spectators):
            if s.active:
                s.send_message("", message_type="REACTION", from_user=username, data={"emoji": emoji})
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


def _handle_out_of_turn_resign(username: str, room: GameRoom) -> None:
    """
    WebSocketTransport's on_resign callback (see its docstring) — fires the
    instant a RESIGN message arrives, regardless of whose turn it currently
    is. If it happens to already be this player's live turn, the transport
    also queues a synthetic "quit" RESPONSE, so the normal in-turn quit
    handling in gameplay.py runs as usual (setting these same flags again is
    a harmless no-op by the time it gets there) — this covers the far more
    common case: nothing in the engine would otherwise notice a resignation
    until this player's turn *would* naturally have come up again, which
    could be many other players' turns away. Mirrors _relay_player_chat's
    shape (same background-reader-thread call site, same "loop over
    room.players/spectators directly" pattern) rather than reaching into
    PlayGame's own broadcast machinery, which expects a live StatusCard
    object this out-of-band path has no access to.
    """
    player = next((p for p in room.players if isinstance(p, NetworkPlayer) and p.username == username), None)
    if player is None or player.resigned:
        return
    player.resigned = True
    player.active = False
    formatted = f"❌ {username} resigned."
    data = {"event": "player_resigned", "player": username}
    for p in list(room.players):
        if isinstance(p, NetworkPlayer) and p.username != username and p.active:
            p.send_message(formatted, message_type="GLOBAL_EVENT", data=data)
    for s in list(room.spectators):
        if s.active:
            s.send_message(formatted, message_type="GLOBAL_EVENT", data=data)


def _rematch_eligible_players(room: "GameRoom") -> list[NetworkPlayer]:
    """
    The humans a rematch can actually reuse: still marked active (so neither
    resigned nor disconnected-and-never-reconnected — see NetworkPlayer.resigned
    and get_bid()'s disconnect fallback, both of which clear `active`) and
    still actually connected right now. A seat that doesn't qualify simply
    becomes an available bot seat for the rematch instead of blocking it.
    """
    return [p for p in room.players if isinstance(p, NetworkPlayer) and p.active and p.transport.is_connected]


def _default_rematch_bot_mix(room: "GameRoom", bot_seats: int) -> list[str]:
    """Defaults a rematch's bot mix to whatever the just-finished game used,
    trimmed/padded to however many bot seats are actually available this
    time (eligibility can shrink between games — see
    _rematch_eligible_players — though it can never grow)."""
    mix = list(room.bot_mix)[:bot_seats]
    if mix:
        fallback = mix[-1]
    else:
        fallback = next(iter(BOT_TYPES))
    while len(mix) < bot_seats:
        mix.append(fallback)
    return mix


def _broadcast_rematch_update(room: "GameRoom") -> None:
    r = room.rematch
    if r is None:
        return
    for p in _rematch_eligible_players(room):
        p.send_message("", message_type="REMATCH_UPDATE", data={
            "requested_by": r["requested_by"],
            "bot_mix": r["bot_mix"],
            "votes": r["votes"],
        })


def _maybe_start_rematch(room: "GameRoom") -> None:
    """Called after every vote; actually starts the rematch once everyone
    eligible has accepted (including the requester, who auto-accepts their
    own request — see _start_rematch_request)."""
    with room.lock:
        r = room.rematch
        if r is None or any(v is not True for v in r["votes"].values()):
            return
        eligible = _rematch_eligible_players(room)
        # Re-validated fresh rather than trusting r["votes"]'s keys are still
        # exactly right: nobody can resign/disconnect mid-vote (the game
        # engine that used to react to that is long gone once state ==
        # "finished"), so this only guards the reaper deleting the room out
        # from under an abandoned vote (see _reap_stale_rooms).
        if room.state != "finished" or {p.username for p in eligible} != set(r["votes"]):
            room.rematch = None
            return
        bot_mix = r["bot_mix"]
        room.rematch = None
        room.bot_mix = bot_mix
        # A fresh shuffle for a fresh game -- self.seed was otherwise fixed
        # once in __init__ and run_game() always passes seed=self.seed, so
        # every rematch replayed the exact same deck order as the room's
        # very first game, forever. Re-rolled here regardless of whether
        # the original was an explicit host-chosen seed or an auto-random
        # one: that seed was for reproducing *that* game, not for pinning
        # every rematch to it too.
        room.seed = random.randint(0, 2 ** 31 - 1)
        bots = create_bot_players(
            bot_mix, think_time=room.bot_think_time,
            taken_usernames={p.username for p in eligible},
        ) if bot_mix else []
        # Same NetworkPlayer objects as the just-finished game, deliberately
        # not fresh ones (see reset_for_new_game's docstring for why) —
        # reset in place so the new PlayGame starts everyone with a clean
        # hand/0 points instead of carrying over the last game's final
        # money cards and score.
        for p in eligible:
            p.reset_for_new_game()
        room.players = eligible + bots
        room.human_seats = len(eligible)
        room.state = "starting"
        room.touch()
    for p in eligible:
        p.send_message("", message_type="REMATCH_STARTING",
                        data={"bot_mix": bot_mix, "seats": room.seats, "seed": room.seed})
    room.run_game()


def _start_rematch_request(player: NetworkPlayer, room: "GameRoom", data: dict) -> None:
    with room.lock:
        if room.state != "finished" or room.rematch is not None:
            return  # a vote's already underway, or the room's moved on — ignore a stray/duplicate request
        eligible = _rematch_eligible_players(room)
        bot_seats = max(room.seats - len(eligible), 0)
        bot_mix = data.get("bot_mix")
        if (not isinstance(bot_mix, list) or len(bot_mix) != bot_seats
                or any(b not in BOT_TYPES for b in bot_mix)):
            bot_mix = _default_rematch_bot_mix(room, bot_seats)
        room.rematch = {
            "requested_by": player.username,
            "bot_mix": bot_mix,
            "votes": {p.username: (True if p is player else None) for p in eligible},
        }
        room.touch()
    _broadcast_rematch_update(room)
    _maybe_start_rematch(room)  # covers the single-human-at-the-table case


def _handle_rematch_vote(player: NetworkPlayer, room: "GameRoom", data: dict) -> None:
    declined_by = None
    with room.lock:
        r = room.rematch
        if r is None or player.username not in r["votes"]:
            return
        if data.get("accept"):
            r["votes"][player.username] = True
        else:
            room.rematch = None
            declined_by = player.username
    if declined_by:
        for p in _rematch_eligible_players(room):
            p.send_message("", message_type="REMATCH_DECLINED", data={"declined_by": declined_by})
        return
    _broadcast_rematch_update(room)
    _maybe_start_rematch(room)


def _handle_post_game_message(player: NetworkPlayer, room: "GameRoom", msg: dict) -> None:
    incoming_game_id = msg.get("game_id")
    if incoming_game_id is not None and incoming_game_id != room.game_id:
        return
    message_type = msg.get("message_type")
    data = msg.get("data") or {}
    if message_type == "REMATCH_REQUEST":
        _start_rematch_request(player, room, data)
    elif message_type == "REMATCH_VOTE":
        _handle_rematch_vote(player, room, data)


def _run_player_session(player: NetworkPlayer, transport: WebSocketTransport, room: "GameRoom") -> None:
    """
    Owns this connection for as long as it stays alive — which, since a
    finished room can rematch (see _maybe_start_rematch), may span more than
    one game. While a game is starting/in progress, NetworkPlayer.get_bid()/
    choose_painting_to_discard() are the *only* allowed readers of this
    transport's queued messages (see WebSocketTransport's on_chat docstring
    on why two concurrent readers can silently steal each other's messages);
    this loop just idles, watching for a real disconnect, same as before.
    Once the game finishes, there's nothing left to steal from, so this
    becomes the reader instead, handling rematch request/vote messages until
    either a rematch actually starts (back to idling) or the connection dies.
    threading.Event().wait() rather than time.sleep(): the test suite's
    autouse fixture monkeypatches time.sleep to a no-op (see
    tests/network/test_transport.py's note on this exact gotcha), which would
    turn the idle branch into a real busy-spin instead of an idle wait.
    """
    while player.active and transport.is_connected:
        if room.state == "finished":
            msg = transport.receive(timeout=0.5)
            if msg is not None:
                _handle_post_game_message(player, room, msg)
        else:
            threading.Event().wait(0.5)
    # Only clear `active` if this transport is still the one attached to the
    # player. A concurrent reconnect (see NetworkPlayer.reattach) may have
    # already swapped in a fresh transport and marked them active again by
    # the time this loop notices the *old* transport died — without this
    # check, that reconnect gets silently clobbered back to inactive right
    # after succeeding, which then tears the new connection down too (its
    # own copy of this same loop reads active=False and exits immediately).
    if player.transport is transport:
        player.active = False


def _broadcast_spectator_count(room: "GameRoom") -> None:
    """
    Tells every connected player how many people are currently watching.
    Spectators already see the full player roster, but players previously
    had zero visibility into spectators at all -- not even a count (see
    UX_AUDIT.md #2). Called on every spectator join/leave for live updates,
    and once per player at game start (run_game) to cover anyone who was
    already watching from the lobby before this player's own connection
    existed to receive those live join broadcasts.

    No-ops during "lobby" on purpose: a player sitting in the waiting room
    has no game-message channel yet in any meaningful sense, and
    app.js's applyGameMessage() unconditionally forces the game screen
    visible the instant *any* GLOBAL_EVENT arrives (ensureGameScreenVisible)
    -- sending this while still in lobby was yanking a still-waiting host
    straight to the (not yet real) game screen the moment a spectator
    joined, well before the table was actually full. The game-start
    broadcast in run_game() already covers the initial count correctly once
    play actually begins.
    """
    if room.state == "lobby":
        return
    count = sum(1 for s in room.spectators if s.active)
    data = {"event": "spectator_count", "count": count}
    for p in list(room.players):
        if isinstance(p, NetworkPlayer) and p.active:
            p.send_message("", message_type="GLOBAL_EVENT", data=data)


def _send_opponent_roster(player: NetworkPlayer, room: "GameRoom") -> None:
    """
    Tells `player` about every other seat at the table right now — status
    cards, active state, bot-ness — as a batch of synthetic
    opponent_state_sync events (see app.js's GLOBAL_EVENT handler). Used
    both for a reconnect catch-up (see _send_reconnect_catchup) and, just as
    importantly, right as a fresh game starts: without this, a player who
    joined *before* someone else had no way to find out that other seat was
    filled until some in-auction event happened to name them by chance
    (being the random starting player, or reaching their first turn) —
    leaving an already-seated, real opponent looking like they didn't exist
    yet for however long that took.
    """
    for other in room.players:
        if other is player:
            continue
        player.send_message(
            "", message_type="GLOBAL_EVENT",
            data={
                "event": "opponent_state_sync",
                "username": other.username,
                "name": other.name,
                "is_bot": not isinstance(other, NetworkPlayer),
                "active": other.active,
                "status_cards": [summarize_card(c) for c in other.status_cards],
            },
        )


def _send_reconnect_catchup(player: NetworkPlayer, room: "GameRoom") -> None:
    """
    A reconnecting browser starts from a completely blank client-side game
    state (see app.js's resetGameState) — without this, it would just sit
    there showing nothing until the next live event happens to arrive.
    Pushes: the player's own hand/points/cards, a synthetic "sync" auction
    update (current round/card/highest bid/whose turn — see PlayGame's
    get_live_auction_state), and every other player's currently-visible
    status cards (built up over the whole game via individual AUCTION_RESULT
    broadcasts they missed while disconnected). Deliberately not routed
    through the normal toast-generating message shapes (AUCTION_RESULT's
    real path, enqueueEvent) — this is a silent state catch-up, not a
    replay of past events.
    """
    if room.game is None:
        return
    room.game._send_player_state(player)

    # Restores the true post-shuffle seat order a reconnecting client would
    # otherwise have lost (see PlayGame.play_game's own player_order
    # broadcast, sent once at game start and missed by anyone who wasn't
    # connected yet) -- room.game.players is the same list shuffle_players()
    # mutated in place, so this is always the real turn order, not lobby
    # join order.
    player.send_message(
        "", message_type="GLOBAL_EVENT",
        data={"event": "player_order", "usernames": [p.username for p in room.game.players]},
    )

    live_state = room.game.get_live_auction_state()
    if live_state.get("card") is not None:
        player.send_message(
            "", message_type="AUCTION_UPDATE",
            data={
                "round_number": live_state["round_number"],
                "kind": "sync",
                "card": live_state["card"],
                "max_bid": live_state["max_bid"],
                "turn_player": live_state["turn_player"],
            },
        )

    _send_opponent_roster(player, room)
    player.send_message(
        "", message_type="GLOBAL_EVENT",
        data={"event": "spectator_count", "count": sum(1 for s in room.spectators if s.active)},
    )


def _handle_player_reconnect(ws, room: "GameRoom", rejoin_token: str) -> None:
    username = room.rejoin_tokens.get(rejoin_token)
    player = None
    if username:
        player = next(
            (p for p in room.players if isinstance(p, NetworkPlayer) and p.username == username),
            None,
        )
    if player is not None and player.resigned:
        # An explicit resignation is permanent — unlike an ordinary dropped
        # connection, there's no seat left to come back to.
        _send(ws, room.game_id, "IDENTIFY_ERROR", "You resigned from this game and can't rejoin.")
        return
    if player is None or room.state not in ("starting", "in_progress"):
        _send(ws, room.game_id, "IDENTIFY_ERROR", "This reconnect link is no longer valid.")
        return

    transport = WebSocketTransport(
        ws, label=f"{username}@web-reconnect",
        on_chat=lambda msg: _relay_player_chat(username, room, msg),
        on_resign=lambda msg: _handle_out_of_turn_resign(username, room),
    )
    player.reattach(transport)
    room.touch()

    # reattach() above flips this player back to active on the server, but
    # everyone else's browser still has them frozen at whatever state a
    # dropped connection left them in (see app.js's opponent-tile "(out)"/
    # greyed styling) -- without telling the rest of the table, that tile
    # never recovers even though the seat is genuinely back. Mirrors
    # _handle_out_of_turn_resign's broadcast shape, just the opposite event.
    formatted = f"🔌 {username} reconnected."
    data = {"event": "player_reconnected", "player": username}
    for p in list(room.players):
        if isinstance(p, NetworkPlayer) and p.username != username and p.active:
            p.send_message(formatted, message_type="GLOBAL_EVENT", data=data)
    for s in list(room.spectators):
        if s.active:
            s.send_message(formatted, message_type="GLOBAL_EVENT", data=data)

    _send(ws, room.game_id, "IDENTIFY_SUCCESS", f"Welcome back, {username}!",
          data={"rejoin_token": rejoin_token, "reconnected": True})
    _send_reconnect_catchup(player, room)

    # Only now release a get_bid()/choose_painting_to_discard() that's been
    # waiting in _wait_for_reconnect() since the old transport died -- see
    # NetworkPlayer.finish_reconnect(). Doing this any earlier risks the
    # game thread's own fresh re-prompt (a new "Enter your bid") racing
    # ahead of IDENTIFY_SUCCESS/the catch-up messages above on this same
    # transport, since it can start sending the instant something wakes it.
    player.finish_reconnect()

    _run_player_session(player, transport, room)


@sock.route("/ws")
def ws_player(ws):
    room = _get_room(request.args.get("room"))
    if room is None:
        _send(ws, "", "IDENTIFY_ERROR", "No game found for this room.")
        return

    rejoin_token = request.args.get("rejoin_token")
    if rejoin_token:
        _handle_player_reconnect(ws, room, rejoin_token)
        return

    if room.state != "lobby":
        _send(ws, room.game_id, "IDENTIFY_ERROR", "No game is accepting players right now.")
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
            on_resign=lambda msg: _handle_out_of_turn_resign(username, room),
        )
        transport.start()
        player = NetworkPlayer(name=name, username=username, transport=transport, game_id=game_id,
                                disconnect_grace_seconds=room.disconnect_grace_seconds)
        room.players.append(player)
        rejoin_token = secrets.token_urlsafe(16)
        room.rejoin_tokens[rejoin_token] = username
        room.touch()

    _send(ws, game_id, "IDENTIFY_SUCCESS", f"Welcome {username}! Waiting for other players...",
          data={"rejoin_token": rejoin_token})

    if room.try_start():
        room.run_game()

    _run_player_session(player, transport, room)

    # A disconnect during the lobby is fully recoverable (nothing about the
    # game has started, nothing to reconnect to) — free the seat entirely
    # rather than leaving a permanent ghost that occupies a spot forever and
    # can prevent the room from ever filling. Once the game has actually
    # started, leave them in room.players: that's exactly what lets a later
    # reconnect (see _handle_player_reconnect) resume their seat.
    if room.state == "lobby":
        with room.lock:
            if player in room.players:
                room.players.remove(player)
            room.rejoin_tokens = {t: u for t, u in room.rejoin_tokens.items() if u != username}
        room.touch()


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
    room = _get_room(request.args.get("room"))
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
    _broadcast_spectator_count(room)

    _send(ws, game_id, "IDENTIFY_SUCCESS", f"Welcome {name}! You are now watching the game live.")

    chat_thread = threading.Thread(target=_spectator_chat_listener, args=(spectator, room),
                                    daemon=True, name=f"Chat-{username}")
    chat_thread.start()

    while spectator.active and transport.is_connected:
        threading.Event().wait(0.5)
    spectator.active = False
    _broadcast_spectator_count(room)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HighSociety Web Server")
    parser.add_argument("--host", type=str, default="0.0.0.0",
                        help="Host address to bind to (default: 0.0.0.0 for all interfaces)")
    # Hosting platforms (Render/Railway/Fly.io-style) assign a port at deploy
    # time via the $PORT env var and expect the app to listen on it — the
    # --port flag stays the default for local/LAN use.
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)),
                        help="Port number to listen on (default: $PORT env var, or 8000)")
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
