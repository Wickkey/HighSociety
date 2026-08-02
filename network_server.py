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
import uuid
import base64
from highsociety.code.gamecore.player.networkplayer import NetworkPlayer
from highsociety.code.gamecore.game_manager.gameplay import PlayGame
from highsociety.code.common.utils.utility import get_all_configurations
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager
from highsociety.code.common.utils.network_utility import send_json, receive_json
from highsociety.code.gamecore.player.networkspectator import NetworkSpectator
from highsociety.code.gamecore.player.player import BasePlayer
from highsociety.code.gamecore.network.transport import SocketTransport
from highsociety.code.gamecore.recording.session_recorder import SessionRecorder
from highsociety.code.gamecore.recording.recording_player import RecordingPlayer

def generate_game_id() -> str:
    raw = uuid.uuid4().bytes
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")

def get_local_ip():
    """Get the local IP address of this machine."""
    try:
        # Connect to a remote address to determine local IP
        s = socket.socket(family=socket.AF_INET, type=socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"  # Fallback to localhost


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
            if not player.active:
                continue

            if now - player.get_last_heartbeat() > timeout_seconds:
                print(f"☠️ Client {player.username} timed out (no heartbeat). Disconnecting.")
                player.active = False

                try:
                    player.transport.close()
                except:
                    pass

        time.sleep(check_interval)

def accept_players(server_socket: socket.socket, expected_players:int, game_id: str):
    print(f"🎮 Waiting for players....\n")
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

            if data["message_type"] != "IDENTIFY_ACK":
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

            if data["message_type"] != "IDENTIFY_ACK":
                send_json(conn, {
                    "game_id": game_id,
                    "message_type": "IDENTIFY_ERROR",
                    "prompt": "Expected display name",
                    "requires_response": False
                })
                conn.close()
                return  

            name = data["prompt"]

            transport = SocketTransport(conn, label=f"{username}@{addr[0]}:{addr[1]}")
            player = NetworkPlayer(name=name, username=username, transport=transport, game_id=game_id)

            with players_lock:
                players.append(player)
                print(f"✅ Player joined: {username} ({name}) — {len(players)}/{expected_players}")

            send_json(conn, {
                "game_id": game_id,
                "message_type": "IDENTIFY_SUCCESS",
                "prompt": f"Welcome {username}! Waiting for other players...",
                "requires_response": False
            })

        except Exception as e:
            print(f"❌ Error with player at {addr}: {e}")
            try:
                conn.close()
            except:
                pass

    threads = []
    while len(threads) < expected_players:
        conn, addr = server_socket.accept()
        t = threading.Thread(target=handle_single_connection, args=(conn, addr), daemon=True)
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    return players

def accept_spectators(server_socket: socket.socket, spectators: list[NetworkSpectator], game_id: str):
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
            if data["message_type"] != "IDENTIFY_ACK":
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
            if data["message_type"] != "IDENTIFY_ACK":
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
            spectators.append(spectator)  # accept_spectators runs single-threaded; list.append is GIL-atomic

            print(f"👁️ Spectator joined: {name}")
            send_json(conn, {
                "game_id": game_id,
                "message_type": "IDENTIFY_SUCCESS",
                "prompt": f"Welcome {name}! You are now watching the game live.",
                "requires_response": False
            })

        except Exception as e:
            LoggingManager.exception("❌  Error accepting spectator:", e)


def start_server(host='0.0.0.0', port=8888, num_players=2, seed=None, record_path=None):
    """
    Start the game server and wait for players to connect.

    Args:
        host: Host address to bind to (0.0.0.0 for all interfaces)
        port: Port number to listen on
        num_players: Number of players to wait for
        seed: Seed the RNG for a fully reproducible game; auto-generated if not given
        record_path: If given, records every player decision to this path (see
            highsociety.code.gamecore.recording) so the game can be replayed
            later with `python3 main.py --replay PATH` — no networking needed
            to replay, so this works the same whether the original game was
            played over CLI or network.
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
        spectator_thread = threading.Thread(target=accept_spectators, args=(spectator_server_socket, spectators, game_id), daemon=True) # accept spectators indefinitely.
        spectator_thread.start()

        players = accept_players(server_socket, num_players, game_id)


        # Close server socket
        server_socket.close() # can't close if spectators needs to be added.
        # Start receiver threads for all players
        print("🔌 Starting receiver threads for all players...")
        for player in players:
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
        
        # Close all player connections
        print("\n🔌 Closing connections...")
        for player in players:
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

    args = parser.parse_args()

    if args.players < 2:
        print("⚠️ At least 2 players are required!")
        sys.exit(1)

    start_server(host=args.host, port=args.port, num_players=args.players, seed=args.seed, record_path=args.record)

