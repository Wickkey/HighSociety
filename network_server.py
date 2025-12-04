#!/usr/bin/env python3
"""
HighSociety Game Server
This script runs the game server that accepts network connections from players.
Run this on the host machine (the one that will run the game).
"""

import socket
import threading
from socket import error as SocketError
import sys
from tabnanny import check
import time
from highsociety.code.gamecore.player.networkplayer import NetworkPlayer
from highsociety.code.gamecore.game_manager.gameplay import PlayGame
from highsociety.code.common.utils.utility import get_all_configurations
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager
from highsociety.code.common.utils.network_utility import receive_message, send_message
from highsociety.code.gamecore.player.player import BasePlayer

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
                    player.conn.close()
                except:
                    pass

        time.sleep(check_interval)


def start_server(host='0.0.0.0', port=8888, num_players=2):
    """
    Start the game server and wait for players to connect.
    
    Args:
        host: Host address to bind to (0.0.0.0 for all interfaces)
        port: Port number to listen on
        num_players: Number of players to wait for
    """
    # Initialize game
    config = get_all_configurations()
    logging_manager = LoggingManager(config)

    # Create socket
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM) #IPV4, TCP
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    
    try:
        server_socket.bind((host, port)) # server_socket needs to be bound to a host and port. 
        server_socket.listen(num_players)
        
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
        
        players = []
        i = 0

        while i < num_players:
            try:
                print(f"⏳ Waiting for player {i+1}/{num_players}...")
                conn, addr = server_socket.accept() # conn -> client's socket, addr -> (IP, PORT)
                LoggingManager.info(f"Player {i+1} connected from {addr[0]}:{addr[1]}")
                print(f"✅ Player {i+1} connected from {addr[0]}:{addr[1]}")
                set_keepalive(conn)

                username = None
                name = None
                # Send username prompt (with newline for better compatibility)
                send_message(conn, "Enter your username:")
                # Wait for response with timeout
                conn.settimeout(None)  

                while (username is None):
                    username = receive_message(conn)
                    if not username:
                        print(f"⚠️ Empty input. Please enter a valid username.")
                        continue
                    
                # Send display name prompt
                send_message(conn, "Enter your display name:")
                while (not name):
                    name = receive_message(conn)
                    if not name:
                        print(f"⚠️ Empty input. Please enter a valid display name.")
                        continue
                    
                # Create NetworkPlayer
                player = NetworkPlayer(name=name, username=username, conn=conn)
                players.append(player)
                send_message(conn, f"Welcome {username}! Waiting for other players...")
                print(f"   Player: {username} ({name})\n")
                i += 1
            except socket.timeout:
                print(f"⏰ Player {i+1} timed out during setup")
                conn.close()
                continue
            except Exception as e:
                print(f"❌ Error during player {i+1} setup: {e}")
                conn.close()
                continue

        
        # Close server socket
        # server_socket.close() # can't close if spectators needs to be added.
        
        print(f"\n{'='*60}")
        print(f"🎉 All players connected! Starting game...")
        print(f"{'='*60}\n")
        
        # Start receiver threads for all players
        print("🔌 Starting receiver threads for all players...")
        for player in players:
            player.start_receiver_thread()
        print("✅ All receiver threads started.\n")
                
        # Create and start game
        heartbeat_monitor_thread = threading.Thread(target=players_heartbeat_monitor_thread, args=(players, 120, 5), daemon=True)
        heartbeat_monitor_thread.start()
        game = PlayGame(players=players, mode='network')
        game.play_game()
        
        # Close all player connections
        print("\n🔌 Closing connections...")
        for player in players:
            player.close()
        
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
    
    args = parser.parse_args()
    
    if args.players < 2:
        print("⚠️ At least 2 players are required!")
        sys.exit(1)
    
    start_server(host=args.host, port=args.port, num_players=args.players)

