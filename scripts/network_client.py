#!/usr/bin/env python3
"""
HighSociety Game Client
This script connects to a game server and allows you to play.
Run this on each player's machine/terminal.

Speaks the server's newline-delimited JSON protocol (see NetworkPlayer.send_message
for the message_type contract): during setup it exchanges a couple of
IDENTIFY/IDENTIFY_ACK messages, then a background thread continuously displays
whatever the server sends while the main thread forwards each typed line to
the server as a RESPONSE message.
"""

import json
import socket
import sys
import threading
import time

from highsociety.code.common.utils.network_utility import send_json, receive_json
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager
from highsociety.code.common.utils.utility import get_all_configurations
from highsociety.code.common.utils.terminal_colors import colorize, style_game_event, BOLD, CYAN, RED, MAGENTA


class GameClient:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.sock = None
        self.running = True
        self.username = None
        self.game_id = None

    def set_keepalive(self, sock, after_idle_sec=60, interval_sec=30, max_fails=3):
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

    def connect(self):
        """Connect to the game server."""
        print(f"\n{'='*60}")
        print(f"🎮 HighSociety Game Client")
        print(f"{'='*60}")
        print(f"Connecting to {self.host}:{self.port}...\n")

        config = get_all_configurations()
        LoggingManager(config)

        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.set_keepalive(self.sock)
            self.sock.connect((self.host, self.port))
            print("✅ Connected to server!\n")
            return True
        except socket.error as e:
            print(f"❌ Failed to connect: {e}")
            print(f"\nMake sure:")
            print(f"  1. The server is running")
            print(f"  2. The IP address ({self.host}) is correct")
            print(f"  3. The port ({self.port}) is correct")
            print(f"  4. Firewall allows the connection")
            return False

    def _ask(self, prompt_payload):
        """Print a handshake prompt from the server and return the user's raw answer."""
        print(prompt_payload.get("prompt", ""), end='', flush=True)
        try:
            answer = input().strip()
        except (EOFError, KeyboardInterrupt):
            return None
        return answer

    def handle_initial_setup(self):
        """Handle the initial username/name collection phase (blocking request/response)."""
        try:
            prompt = receive_json(self.sock)
        except Exception as e:
            print(f"\n⚠️ Did not receive username prompt from server: {e}")
            return False
        self.game_id = prompt.get("game_id", self.game_id)

        username = self._ask(prompt)
        if username is None:
            return False
        if not username:
            username = "player"
        self.username = username
        try:
            send_json(self.sock, {"game_id": self.game_id, "message_type": "IDENTIFY_ACK", "prompt": username})
        except socket.error:
            return False

        try:
            prompt = receive_json(self.sock)
        except Exception as e:
            print(f"\n⚠️ Did not receive display-name prompt from server: {e}")
            return False

        name = self._ask(prompt)
        if name is None:
            return False
        if not name:
            name = username
        try:
            send_json(self.sock, {"game_id": self.game_id, "message_type": "IDENTIFY_ACK", "prompt": name})
        except socket.error:
            return False

        try:
            welcome = receive_json(self.sock)
        except Exception as e:
            print(f"Error receiving welcome: {e}")
            return False

        print(welcome.get("prompt", ""))
        if welcome.get("message_type") == "IDENTIFY_ERROR":
            return False

        return True

    def _display(self, payload):
        """Render one incoming server message."""
        message_type = payload.get("message_type")
        prompt = payload.get("prompt", "")

        if message_type == "PLAYER_MOVE":
            constraints = payload.get("constraints") or {}
            print(f"\n{colorize(prompt, BOLD, CYAN)}", end='', flush=True)
            allowed_cards = constraints.get("allowed_money_cards")
            allowed_commands = constraints.get("allowed_commands")
            if allowed_cards is not None or allowed_commands is not None:
                print(f"\n   (money cards: {allowed_cards}, commands: {allowed_commands})")
        elif message_type == "INPUT_ERROR":
            print(colorize(prompt, RED))
        elif message_type == "CHAT":
            from_user = payload.get("from_user")
            print(colorize(prompt, MAGENTA) if from_user else prompt)
        elif message_type == "GLOBAL_EVENT":
            print(style_game_event(prompt))
        else:
            print(prompt)

    def receive_messages(self):
        """Continuously receive and display newline-delimited JSON messages from the server."""
        buffer = ""
        while self.running:
            try:
                self.sock.settimeout(1.0)
                chunk = self.sock.recv(4096).decode('utf-8', errors='ignore')
                if not chunk:
                    print("\n⚠️ Server disconnected.")
                    self.running = False
                    break

                buffer += chunk
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self._display(payload)

            except socket.timeout:
                continue
            except (ConnectionResetError, BrokenPipeError, OSError):
                if self.running:
                    print("\n⚠️ Connection error.")
                self.running = False
                break
            except Exception as e:
                if self.running:
                    print(f"\n❌ Error receiving data: {e}")
                self.running = False
                break

    def send_input(self, message):
        """Send a typed line to the server as a response."""
        try:
            send_json(self.sock, {"game_id": self.game_id, "message_type": "RESPONSE", "prompt": message})
            return True
        except (socket.error, OSError):
            return False

    def heartbeat_thread(self, interval: float = 5.0):
        """Sends heartbeat PING messages periodically while the client is active."""
        while self.running:
            try:
                send_json(self.sock, {"game_id": self.game_id, "message_type": "PING", "prompt": ""})
            except (BrokenPipeError, ConnectionResetError, OSError):
                self.running = False
                break
            time.sleep(interval)

    def run(self):
        """Main client loop."""
        if not self.connect():
            return

        if not self.handle_initial_setup():
            print("⚠️ Failed during initial setup")
            self.running = False
            if self.sock:
                self.sock.close()
            return

        print("\n💡 Tips:")
        print("   - Enter a number to bid that money card")
        print("   - Enter [1,2,3] to bid multiple cards")
        print("   - Enter 'pass' or 'fold' to withdraw")
        print("   - Enter 'quit' to leave the game")
        print(f"{'='*60}\n")

        receive_thread = threading.Thread(target=self.receive_messages, daemon=True)
        heartbeat = threading.Thread(target=self.heartbeat_thread, args=(5,), daemon=True)
        receive_thread.start()
        heartbeat.start()

        try:
            while self.running:
                try:
                    user_input = input()
                except EOFError:
                    self.running = False
                    break
                except KeyboardInterrupt:
                    print("\n\n⚠️ Disconnecting...")
                    self.send_input("quit")
                    self.running = False
                    break

                if not self.running:
                    break
                if not self.send_input(user_input):
                    break
                if user_input.strip().lower() == 'quit':
                    self.running = False
                    break
        except Exception as e:
            print(f"\n❌ Error: {e}")
        finally:
            self.running = False
            receive_thread.join(timeout=2.0)
            heartbeat.join(timeout=2.0)
            try:
                if self.sock:
                    self.sock.shutdown(socket.SHUT_RDWR)
                    self.sock.close()
                print("👋 Disconnected from server. Goodbye!")
            except:
                pass

def main():
    import argparse

    parser = argparse.ArgumentParser(description='HighSociety Game Client')
    parser.add_argument('--host', type=str, default='localhost',
                       help='Server IP address (default: localhost)')
    parser.add_argument('--port', type=int, default=8888,
                       help='Server port number (default: 8888)')

    args = parser.parse_args()

    client = GameClient(args.host, args.port)
    client.run()

if __name__ == '__main__':
    main()
