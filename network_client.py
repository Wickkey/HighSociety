#!/usr/bin/env python3
"""
HighSociety Game Client
This script connects to a game server and allows you to play.
Run this on each player's machine/terminal.
"""

from concurrent.futures import BrokenExecutor
from multiprocessing import process
import socket
import sys
import threading
import time
from highsociety.code.common.utils.network_utility import receive_message, send_message, process_received_messages

class GameClient:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.sock = None
        self.running = True
        self.prompt_received = threading.Event()
        self.current_prompt = ""

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
        
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM) # Client's socket

            self.set_keepalive(self.sock)
            # self.sock.settimeout(10)
            self.sock.connect((self.host, self.port)) # connected to server's ip and port.
            self.sock.settimeout(None)
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
    
    def receive_until_prompt(self):
        """Receive data until we get a prompt (ends with ':' or '?')."""
        buffer = ""
        while self.running:
            try:
                data = self.sock.recv(4096).decode('utf-8', errors='ignore')
                data = process_received_messages(data)
                if not data:
                    print("\n⚠️ Server disconnected.")
                    print(f"player disconnected: {self.username}")
                    self.running = False
                    return None
                
                buffer += data
                
                # Check if we have a complete prompt (ends with ':' or '?')
                if buffer.strip().endswith(':') or buffer.strip().endswith('?'):
                    # Found a prompt, return it
                    return buffer
                
                # Process any complete lines first
                if '\n' in buffer:
                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        if line.strip():
                            print(line)
                    
                    # After processing lines, check if remaining buffer is a prompt
                    if buffer and (buffer.endswith(':') or buffer.endswith('?')):
                        return buffer
                    
            except socket.timeout:
                # If we have something in buffer, return it (might be a prompt)
                if buffer:
                    if buffer.endswith(':') or buffer.endswith('?'):
                        return buffer
                    # Otherwise, print what we have and continue
                    if buffer.strip():
                        print(buffer, end='', flush=True)
                        buffer = ""
                continue
            except socket.error:
                if self.running:
                    print("\n⚠️ Connection error.")
                return None
            except Exception as e:
                if self.running:
                    print(f"\n❌ Error receiving data: {e}")
                return None
        
        # Return any remaining buffer
        if buffer:
            return buffer
        return None
    
    def receive_messages(self):
        """Continuously receive and display messages from server during game."""
        buffer = ""
        while self.running:
            try:
                data = self.sock.recv(4096).decode('utf-8', errors='ignore')
                data = process_received_messages(data)
                if not data:
                    print("\n⚠️ Server disconnected.")
                    self.running = False
                    break
                
                buffer += data
                
                # Process complete lines
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    print(line)
                
                # Handle prompts that don't end with newline
                if buffer and (buffer.endswith(':') or buffer.endswith('?') or 
                               'Enter your' in buffer or 'Choose one' in buffer):
                    print(buffer, end='', flush=True)
                    buffer = ""
                    
            except socket.error:
                if self.running:
                    print("\n⚠️ Connection error.")
                break
            except Exception as e:
                if self.running:
                    print(f"\n❌ Error receiving data: {e}")
                break
        
        # Print any remaining buffer
        if buffer.strip():
            print(buffer, end='')
    
    def send_input(self, message):
        """Send input to server."""
        try:
            # Send without newline - server handles it
            send_message(self.sock, message)
            return True
        except socket.error:
            return False
    
    def handle_initial_setup(self):
        """Handle the initial username/name collection phase."""
        # Reset timeout to None for normal operation
        self.sock.settimeout(None)
        
        # Get username
        print("Waiting for server prompt...", end='', flush=True)
        prompt = self.receive_until_prompt()
        if not prompt:
            print("\n⚠️ Did not receive username prompt from server")
            return False
        
        # Clear the "waiting" message
        print("\r" + " " * 40 + "\r", end='', flush=True)
        
        # Display prompt and get input
        print(prompt, end='', flush=True)
        try:
            username = input().strip()
        except (EOFError, KeyboardInterrupt):
            return False
        if not username:
            username = "player"
        if not self.send_input(username):
            return False
        
        # Get display name
        prompt = self.receive_until_prompt()
        if not prompt:
            print("\n⚠️ Did not receive display name prompt from server")
            return False
        print(prompt, end='', flush=True)
        try:
            name = input().strip()
        except (EOFError, KeyboardInterrupt):
            return False
        if not name:
            name = username
        if not self.send_input(name):
            return False

        # -- FIX START: Simplified
        print("Waiting for other players...", end='\r', flush=True)
        
        # We don't need a loop or timeout. Just one blocking receive is usually enough 
        # because the server sends "Welcome... \n".
        try:
            # Check if there is data already in the pipe or wait for it
            data = receive_message(self.sock, nbytes=4096)
            if data:
                print(data)
        except Exception as e:
            print(f"Error receiving welcome: {e}")
            
        return True

    def heartbeat_thread(self, interval: float = 5.0):
        """
        Sends heartbeat messages periodically while the client is active.
        Designed to run in a background thread.

        Args:
            interval (float): Number of seconds between heartbeats
        """
        while self.running:
            try:
                send_message(self.sock, "<HEARTBEAT>")
            except (BrokenPipeError, ConnectionResetError, OSError):
                # server is dead, socket is closed, or connection lost
                self.active = False
                break 

            time.sleep(interval)
            
    def run(self):
        """Main client loop."""
        if not self.connect():
            return
        
        # Handle initial setup (username/name)
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
        
        # Start receiving thread for game messages
        receive_thread = threading.Thread(target=self.receive_messages, daemon=True)
        heartbeat_thread = threading.Thread(target=self.heartbeat_thread, args=(5,), daemon=True)
        receive_thread.start()
        heartbeat_thread.start()
        
        # Main input loop
        try:
            while self.running:
                try:
                    user_input = input()
                    if not self.send_input(user_input):
                        break
                    if user_input.strip().lower() == 'quit':
                        self.running = False
                        break
                except EOFError:
                    self.running = False
                    break
                except KeyboardInterrupt:
                    print("\n\n⚠️ Disconnecting...")
                    self.send_input("quit")
                    self.running = False
                    break
        except Exception as e:
            print(f"\n❌ Error: {e}")
        finally:
            self.running = False
            if self.sock:
                self.sock.close()
            print("👋 Disconnected from server. Goodbye!")

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
