#!/usr/bin/env python3
"""
HighSociety Game Server
This script runs the game server that accepts network connections from players.
Run this on the host machine (the one that will run the game).
"""

import logging
import random
import socket
import threading
from socket import error as SocketError
import sys
from tabnanny import check
import time
from highsociety.code.gamecore.player.networkplayer import NetworkPlayer
from highsociety.code.gamecore.game_manager.gameplay import PlayGame
from highsociety.code.common.utils.utility import get_all_configurations, validate_player_count, generate_game_id
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager, LogType
from highsociety.code.common.utils.network_utility import send_json, receive_json, get_local_ip
from highsociety.code.gamecore.player.networkspectator import NetworkSpectator
from highsociety.code.gamecore.player.player import BasePlayer
from highsociety.code.gamecore.network.transport import SocketTransport
from highsociety.code.gamecore.recording.session_recorder import SessionRecorder
from highsociety.code.gamecore.recording.recording_player import RecordingPlayer

def _is_valid_identify_ack(data: dict, game_id: str) -> bool:
    """
    Permissive on a missing game_id (lightweight clients/tests that don't
    bother setting it), strict on one that's present but wrong — that means
    the response belongs to a different game (a confused client, or stale
    data from a previous connection attempt), not this handshake.
    """
    if data.get("message_type") != "IDENTIFY_ACK":
        return False
    incoming_game_id = data.get("game_id")
    if incoming_game_id is not None and incoming_game_id != game_id:
        return False
    return True

def set_keepalive(sock, after_idle_sec=60, interval_sec=30, max_fails=3):
    """
    Sets aggressive TCP KeepAlive options to prevent idle disconnects.
    Works on Linux, macOS, and Windows.
    """
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    
    # Linux / macOS
    if hasattr(socket, "TCP_KEEPIDLE"):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, after_idle_sec)
    elif hasattr(socket, "TCP_KEEPALIVE"):  # macOS specific
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPALIVE, after_idle_sec)
        
    if hasattr(socket, "TCP_KEEPINTVL"):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, interval_sec)
        
    if hasattr(socket, "TCP_KEEPCNT"):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, max_fails)

    # Windows (requires specific IOCTL)
    if sys.platform == 'win32':
        # (on, time_ms, interval_ms)
        sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, after_idle_sec * 1000, interval_sec * 1000))

def players_heartbeat_monitor_thread(players: list[NetworkPlayer], timeout_seconds:float = 10, check_interval: float = 5.0):
    """
    Periodically checks whether the player has received heartbeats.
    Disconnects the client if no heartbeat is received for timeout seconds.

    Args:
        timeout (float): Max duration allowed without heartbeat.
        check_interval (float): How often to check heartbeat.
    """
    while True:
        now = time.time()

        for player in list(players):
            if not isinstance(player, NetworkPlayer):
                continue  # e.g. a bot pre-seeded into players -- no socket, nothing to time out
            if not player.active:
                continue

            if now - player.get_last_heartbeat() > timeout_seconds:
                print(f"☠️ Client {player.username} timed out (no heartbeat). Disconnecting.")
                player.active = False

                try:
                    player.transport.close()
                except:
                    pass

        # threading.Event().wait() here, not time.sleep(): the test suite's
        # autouse fixture monkeypatches time.sleep to a no-op (see
        # tests/network/test_transport.py's note on this exact gotcha), which
        # would turn this otherwise-infinite loop into a genuine unconditional
        # busy-spin for the rest of the test session — one per start_server()
        # call, since this loop has no exit condition of its own.
        threading.Event().wait(check_interval)

def accept_players(server_socket: socket.socket, expected_players: int, game_id: str, players: list = None):
    """
    players: an optional externally-created list to populate (instead of a
    fresh one) — used so accept_spectators, which starts running
    concurrently before any player has joined, can hold a reference to the
    same list and see it fill up over time (needed for the spectator chat
    relay to reach players).
    """
    print(f"🎮 Waiting for players....\n")
    if players is None:
        players = []
    players_lock = threading.Lock()

    def handle_single_connection(conn, addr):
        try:
            LoggingManager.info(f"A Player connected from {addr[0]}:{addr[1]}")
            print(f"[info]: A Player is trying to connect from {addr[0]}:{addr[1]}")
            set_keepalive(conn)

            username_payload = {
                "game_id": game_id,
                "message_type": "IDENTIFY",
                "prompt": "Enter your username",
                "requires_response": True
            }
            send_json(conn, username_payload)
            data = receive_json(conn)

            if not _is_valid_identify_ack(data, game_id):
                send_json(conn, {
                    "game_id": game_id,
                    "message_type": "IDENTIFY_ERROR",
                    "prompt": "Expected username first",
                    "requires_response": False
                })
                conn.close()
                return

            username = data["prompt"]

            name_payload = {
                "game_id": game_id,
                "message_type": "IDENTIFY",
                "prompt": "Enter your display name",
                "requires_response": True
            }
            send_json(conn, name_payload)
            data = receive_json(conn)

            if not _is_valid_identify_ack(data, game_id):
                send_json(conn, {
                    "game_id": game_id,
                    "message_type": "IDENTIFY_ERROR",
                    "prompt": "Expected display name",
                    "requires_response": False
                })
                conn.close()
                return  

            name = data["prompt"]

            with players_lock:
                if len(players) >= expected_players:
                    # Enough players already joined while this handshake was
                    # in flight (e.g. it was slow, or filled a slot freed up
                    # by an earlier failed handshake) — don't overfill.
                    send_json(conn, {
                        "game_id": game_id,
                        "message_type": "IDENTIFY_ERROR",
                        "prompt": "Sorry, the game is already full.",
                        "requires_response": False
                    })
                    conn.close()
                    return

                transport = SocketTransport(conn, label=f"{username}@{addr[0]}:{addr[1]}")
                player = NetworkPlayer(name=name, username=username, transport=transport, game_id=game_id)
                players.append(player)
                joined_count = len(players)

            print(f"✅ Player joined: {username} ({name}) — {joined_count}/{expected_players}")

            send_json(conn, {
                "game_id": game_id,
                "message_type": "IDENTIFY_SUCCESS",
                "prompt": f"Welcome {username}! Waiting for other players...",
                "requires_response": False
            })

        except Exception as e:
            print(f"❌ Error with player at {addr}: {e} — still waiting for a replacement connection.")
            try:
                conn.close()
            except:
                pass

    # Keep accepting connections until `expected_players` handshakes actually
    # succeed — a failed/malformed handshake must not permanently steal a
    # slot. Accept() needs a timeout so this loop wakes up to recheck the
    # player count even when no new connection is currently arriving (e.g.
    # right after a slow handshake finally completes in the background).
    threads = []
    server_socket.settimeout(1.0)
    while True:
        with players_lock:
            if len(players) >= expected_players:
                break
        try:
            conn, addr = server_socket.accept()
        except socket.timeout:
            continue
        t = threading.Thread(target=handle_single_connection, args=(conn, addr), daemon=True)
        t.start()
        threads.append(t)

    # Wait for every spawned handshake (including any still in flight) to
    # finish before returning, so a straggler can never append to `players`
    # after this function has already handed the list off to the caller.
    for t in threads:
        t.join()

    return players

def _spectator_chat_listener(spectator: NetworkSpectator, players: list, spectators: list, game_id: str):
    """
    Runs for the lifetime of one spectator's connection: relays every CHAT
    message they send to either everyone (players + other spectators,
    default) or spectators only, per the message's "target" field. Never
    echoes a message back to its own sender.
    """
    while spectator.active:
        msg = spectator.transport.receive(timeout=1.0)
        if msg is None:
            continue
        if msg.get("message_type") != "CHAT":
            continue

        incoming_game_id = msg.get("game_id")
        if incoming_game_id is not None and incoming_game_id != game_id:
            LoggingManager.warning(
                f"Ignoring chat with mismatched game_id from {spectator.username} "
                f"(expected {game_id!r}, got {incoming_game_id!r})",
                log_type=LogType.SECURITY,
            )
            continue

        text = msg.get("prompt", "")
        if not text:
            continue

        target = "spectators" if msg.get("target") == "spectators" else "all"
        # The message's own JSON carries `to_user(s)` structurally (see
        # protocol.py's _chat_payload), but network_spectator_client.py just
        # prints `prompt` verbatim — so a spectators-only message needs its
        # own tag baked into the text, or a receiving spectator has no way to
        # tell it apart from a message that also reached the players.
        tag = " (spectators only)" if target == "spectators" else ""
        formatted = f"💬 {spectator.username}{tag}: {text}"

        for other in list(spectators):
            if other is spectator or not other.active:
                continue
            other.send_message(formatted, message_type="CHAT", from_user=spectator.username, to_users=target)

        if target != "spectators":
            for player in list(players):
                if player.active:
                    player.send_message(formatted, message_type="CHAT", from_user=spectator.username, to_users=target)

def accept_spectators(server_socket: socket.socket, spectators: list[NetworkSpectator], game_id: str, players: list):
    print("👁️ Spectator mode enabled: Waiting for spectators...\n")
    while True:
        try:
            conn, addr = server_socket.accept()
            set_keepalive(conn)
            send_json(conn, {
                "game_id": game_id,
                "message_type": "IDENTIFY",
                "prompt": "You are connected as a spectator. Enter your name:",
                "requires_response": True
            })
            data = receive_json(conn)
            if not _is_valid_identify_ack(data, game_id):
                send_json(conn, {
                    "game_id": game_id,
                    "message_type": "IDENTIFY_ERROR",
                    "prompt": "Expected name",
                    "requires_response": False
                })
                conn.close()
                return

            name = data["prompt"]
            send_json(conn, {
                "game_id": game_id,
                "message_type": "IDENTIFY",
                "prompt": f"Enter your username:",
                "requires_response": True
            })
            data = receive_json(conn)
            if not _is_valid_identify_ack(data, game_id):
                send_json(conn, {
                    "game_id": game_id,
                    "message_type": "IDENTIFY_ERROR",
                    "prompt": "Expected username",
                    "requires_response": False
                })
                conn.close()
                return

            username = data["prompt"]
            transport = SocketTransport(conn, label=f"{username}@{addr[0]}:{addr[1]}")
            spectator = NetworkSpectator(transport=transport, name=name, username=username, game_id=game_id)
            spectator.start_receiver_thread()
            spectators.append(spectator)  # accept_spectators runs single-threaded; list.append is GIL-atomic

            print(f"👁️ Spectator joined: {name}")
            send_json(conn, {
                "game_id": game_id,
                "message_type": "IDENTIFY_SUCCESS",
                "prompt": f"Welcome {name}! You are now watching the game live.",
                "requires_response": False
            })

            chat_thread = threading.Thread(
                target=_spectator_chat_listener,
                args=(spectator, players, spectators, game_id),
                daemon=True,
                name=f"Chat-{username}",
            )
            chat_thread.start()

        except Exception as e:
            LoggingManager.exception("❌  Error accepting spectator:", e)


def start_server(host='0.0.0.0', port=8888, num_players=2, seed=None, record_path=None, bot_players=None):
    """
    Start the game server and wait for players to connect.

    Args:
        host: Host address to bind to (0.0.0.0 for all interfaces)
        port: Port number to listen on
        num_players: Total number of seats to fill, bots included
        seed: Seed the RNG for a fully reproducible game; auto-generated if not given
        record_path: If given, records every player decision to this path (see
            highsociety.code.gamecore.recording) so the game can be replayed
            later with `python3 main.py --replay PATH` — no networking needed
            to replay, so this works the same whether the original game was
            played over CLI or network.
        bot_players: Bot instances (see highsociety/code/ai/) to pre-seed as
            some of the num_players seats — accept_players() already accepts
            a pre-populated list and simply waits for fewer real connections,
            so this is the only hook needed. Everywhere else in this
            function that assumes every entry in `players` is a real
            NetworkPlayer (receiver threads, heartbeat monitoring, socket
            close) skips non-NetworkPlayer entries instead.
    """
    # Initialize game
    config = get_all_configurations()
    logging_manager = LoggingManager(config)
    print("debug point: logger initialized on server.")
    logging_manager.info("logger initialized on server.")

    # Create socket
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM) #IPV4, TCP
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

    # Create spectator server socket
    spectator_server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    spectator_server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    spectator_server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    
    try:
        server_socket.bind((host, port)) # server_socket needs to be bound to a host and port. 
        server_socket.listen(num_players)

        spectator_server_socket.bind((host, port+1))
        spectator_server_socket.listen()
        
        local_ip = get_local_ip()
        print(f"\n{'='*60}")
        print(f"🎮 HighSociety Game Server Started!")
        print(f"{'='*60}")
        print(f"📍 Server IP: {local_ip}")
        print(f"🔌 Port: {port}")
        print(f"👥 Waiting for {num_players} player(s) to connect...")
        print(f"{'='*60}\n")
        print("Share this information with players:")
        print(f"   IP: {local_ip}")
        print(f"   Port: {port}\n")

        game_id = generate_game_id()

        spectators = []
        # Created upfront (instead of inside accept_players) and shared so
        # accept_spectators — which starts running immediately, before any
        # player has joined — can relay spectator chat to players as they
        # connect over time.
        players = list(bot_players) if bot_players else []
        if players:
            print(f"🤖 Pre-seeded {len(players)} bot(s): {', '.join(p.username for p in players)}")
        spectator_thread = threading.Thread(target=accept_spectators, args=(spectator_server_socket, spectators, game_id, players), daemon=True) # accept spectators indefinitely.
        spectator_thread.start()

        accept_players(server_socket, num_players, game_id, players=players)


        # Close server socket
        server_socket.close() # can't close if spectators needs to be added.
        # Start receiver threads for all real (socket-backed) players
        print("🔌 Starting receiver threads for all players...")
        for player in players:
            if isinstance(player, NetworkPlayer):
                player.start_receiver_thread()
        print("✅ All receiver threads started.\n")

        print(f"\n{'='*60}")
        print(f"🎉 All players connected! Starting game...")
        print(f"{'='*60}\n")

        game_seed = seed if seed is not None else random.randint(0, 2**31 - 1)
        game_players = players
        if record_path:
            recorder = SessionRecorder(path=record_path, seed=game_seed)
            game_players = [RecordingPlayer(p, recorder) for p in players]
            print(f"⏺️  Recording this session to {record_path} (seed={game_seed})\n")

        # Create and start game
        heartbeat_monitor_thread = threading.Thread(target=players_heartbeat_monitor_thread, args=(players, 120, 5), daemon=True)
        heartbeat_monitor_thread.start()
        game = PlayGame(players=game_players, spectators=spectators, mode='network', game_id=game_id, seed=game_seed)
        game.play_game()
        
        # Close all real player connections (bots have no socket to close)
        print("\n🔌 Closing connections...")
        for player in players:
            if isinstance(player, NetworkPlayer):
                player.close()

        for spectator in spectators:
            spectator.close()
        
        print("👋 Server shutting down. Thanks for playing!")
        
    except KeyboardInterrupt:
        print("\n\n⚠️ Server interrupted by user.")
        server_socket.close()
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error: here here{e}")
        server_socket.close()
        sys.exit(1)

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='HighSociety Game Server')
    parser.add_argument('--host', type=str, default='0.0.0.0',
                       help='Host address to bind to (default: 0.0.0.0 for all interfaces)')
    parser.add_argument('--port', type=int, default=8888,
                       help='Port number to listen on (default: 8888)')
    parser.add_argument('--players', type=int, default=2,
                       help='Number of players (default: 2)')
    parser.add_argument('--seed', type=int, default=None,
                       help='Seed the RNG for a fully reproducible game (deck order, player order, starting player)')
    parser.add_argument('--record', type=str, default=None, metavar='PATH',
                       help='Record every decision made this game to PATH. Replay it later with '
                            '`python3 main.py --replay PATH` (no networking needed to replay).')
    parser.add_argument('--bots', type=str, default=None,
                       help='Comma-separated bot types (see highsociety/code/ai/) to fill some of '
                            '--players seats with, e.g. --bots greedy,pass — the server then only '
                            'waits for the remaining seats to connect over the network.')
    parser.add_argument('--bot-think-time', type=float, default=1.0,
                       help='Seconds each bot pauses before announcing a decision (default: 1.0). '
                            'Only matters if --bots is given.')

    args = parser.parse_args()

    error = validate_player_count(args.players)
    if error:
        print(f"⚠️ {error}")
        sys.exit(1)

    bot_players = []
    if args.bots:
        from highsociety.code.ai import BOT_TYPES, create_bot_players
        bot_mix = [b.strip() for b in args.bots.split(',') if b.strip()]
        unknown = set(bot_mix) - set(BOT_TYPES)
        if unknown:
            parser.error(f"Unknown bot type(s) {sorted(unknown)}; choose from {list(BOT_TYPES)}")
        if len(bot_mix) > args.players:
            parser.error(f"--bots has {len(bot_mix)} entries but --players is only {args.players}")
        bot_players = create_bot_players(bot_mix, think_time=args.bot_think_time)

    start_server(host=args.host, port=args.port, num_players=args.players, seed=args.seed,
                 record_path=args.record, bot_players=bot_players)

