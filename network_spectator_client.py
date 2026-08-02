#!/usr/bin/env python3
"""
HighSociety Spectator Client
This script connects to a game server and allows you to watch the game.
Run this on each spectator's machine/terminal.

Speaks the server's newline-delimited JSON protocol. Spectators can chat:
type a message and press Enter to send it to everyone (players + other
spectators), or prefix it with "/spectators " to send it to spectators
only. Chat you send is never echoed back to you.
"""

import json
import socket
import sys
import threading

from highsociety.code.common.utils.network_utility import send_json, receive_json


class SpectatorClient:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.sock = None
        self.running = True
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
        print(f"🎮 HighSociety Spectator Client")
        print(f"{'='*60}")
        print(f"Connecting to {self.host}:{self.port}...\n")

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
        print(prompt_payload.get("prompt", ""), end='', flush=True)
        try:
            answer = input().strip()
        except (EOFError, KeyboardInterrupt):
            return None
        return answer

    def handle_initial_setup(self):
        """Handle the initial name/username collection phase (blocking request/response)."""
        try:
            prompt = receive_json(self.sock)
        except Exception as e:
            print(f"\n⚠️ Did not receive the first spectator prompt from server: {e}")
            return False
        self.game_id = prompt.get("game_id", self.game_id)

        first_answer = self._ask(prompt)
        if first_answer is None:
            return False
        try:
            send_json(self.sock, {"game_id": self.game_id, "message_type": "IDENTIFY_ACK", "prompt": first_answer or "spectator"})
        except socket.error:
            return False

        try:
            prompt = receive_json(self.sock)
        except Exception as e:
            print(f"\n⚠️ Did not receive the second spectator prompt from server: {e}")
            return False

        second_answer = self._ask(prompt)
        if second_answer is None:
            return False
        try:
            send_json(self.sock, {"game_id": self.game_id, "message_type": "IDENTIFY_ACK", "prompt": second_answer or "spectator"})
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
                    print(payload.get("prompt", ""))

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

    def send_chat(self, text, target="all"):
        """Sends a chat message. target is "all" (players + other spectators) or "spectators"."""
        try:
            send_json(self.sock, {"game_id": self.game_id, "message_type": "CHAT", "prompt": text, "target": target})
            return True
        except (socket.error, OSError):
            return False

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

        print("\n💡 Spectating:")
        print("   - Type a message + Enter to chat with everyone")
        print("   - Prefix with '/spectators ' to chat with spectators only")
        print("   - Type 'quit' to leave")
        print()

        receive_thread = threading.Thread(target=self.receive_messages, daemon=True)
        receive_thread.start()

        try:
            while self.running:
                try:
                    user_input = input()
                except EOFError:
                    break
                except KeyboardInterrupt:
                    print("\n\n⚠️ Disconnecting...")
                    break

                if user_input.strip().lower() == 'quit':
                    break

                if not user_input.strip():
                    continue

                if user_input.startswith("/spectators "):
                    self.send_chat(user_input[len("/spectators "):], target="spectators")
                elif user_input.strip() == "/spectators":
                    continue  # no message body
                else:
                    self.send_chat(user_input, target="all")
        except Exception as e:
            print(f"\n❌ Error: {e}")
        finally:
            self.running = False
            receive_thread.join(timeout=2.0)
            if self.sock:
                self.sock.close()
            print("👋 Spectator Client Disconnected from server. Goodbye!")

def main():
    import argparse

    parser = argparse.ArgumentParser(description='HighSociety Spectator Client')
    parser.add_argument('--host', type=str, default='localhost',
                       help='Server IP address (default: localhost)')
    parser.add_argument('--port', type=int, default=8889,
                       help='Server port number (default: 8888)')

    args = parser.parse_args()

    client = SpectatorClient(args.host, args.port)
    client.run()

if __name__ == '__main__':
    main()
