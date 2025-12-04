import socket
from socket import error as SocketError
from typing import Union
import time
import threading
import queue
from highsociety.code.gamecore.player.player import BasePlayer
from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager
from highsociety.code.common.utils.network_utility import receive_message, send_message


class NetworkPlayer(BasePlayer):
    """Remote player using sockets."""
    def __init__(self, name: str, username: str, conn: socket.socket):
        super().__init__(name, username)
        self.conn = conn
        self.active = True
        self.last_heartbeat = time.time()
        self._heartbeat_lock = threading.Lock()
        
        # Message queue for game logic to consume
        self.message_queue = queue.Queue()
        
        # Receiver thread
        self._receiver_thread = None
        self._receiver_running = False

    def _receiver_thread_func(self):
        """
        Continuously receives messages from the client socket.
        Handles heartbeats and queues game messages.
        """
        buffer = ""
        while self._receiver_running and self.active:
            try:
                # Set a timeout to allow checking _receiver_running periodically
                self.conn.settimeout(1.0)
                chunk = self.conn.recv(4096).decode('utf-8', errors='ignore')
                
                if not chunk:
                    # Connection closed
                    print(f"⚠️ Connection closed for {self.username}")
                    self.active = False
                    break
                
                from highsociety.code.common.utils.network_utility import process_received_messages
                chunk = process_received_messages(chunk)
                buffer += chunk
                
                # Process complete messages (ending with newline)
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    line = line.strip()
                    
                    if not line:
                        continue
                    
                    # Handle heartbeats
                    if line == "<HEARTBEAT>":
                        with self._heartbeat_lock:
                            self.last_heartbeat = time.time()
                        continue
                    
                    # Queue game messages
                    self.message_queue.put(line)
                    
            except socket.timeout:
                # Timeout is expected, continue loop to check _receiver_running
                continue
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                print(f"⚠️ Connection error for {self.username}: {e}")
                self.active = False
                break
            except Exception as e:
                LoggingManager.error(f"Error in receiver thread for {self.username}: {e}")
                if self.active:
                    continue
                else:
                    break
        
        # Put None in queue to signal receiver has stopped
        try:
            self.message_queue.put(None)
        except:
            pass

    def start_receiver_thread(self):
        """Start the receiver thread for this player."""
        if self._receiver_thread is not None and self._receiver_thread.is_alive():
            return  # Already running
        
        self._receiver_running = True
        self._receiver_thread = threading.Thread(
            target=self._receiver_thread_func,
            daemon=True,
            name=f"Receiver-{self.username}"
        )
        self._receiver_thread.start()

    def stop_receiver_thread(self):
        """Stop the receiver thread."""
        self._receiver_running = False
        if self._receiver_thread is not None:
            self._receiver_thread.join(timeout=2.0)

    def _get_message_from_queue(self, timeout: float = None) -> Union[str, None]:
        """
        Get a message from the queue, waiting up to timeout seconds.
        Returns None if timeout or connection closed.
        """
        try:
            message = self.message_queue.get(timeout=timeout)
            # None signals receiver has stopped
            if message is None:
                return None
            return message
        except queue.Empty:
            return None

    def get_last_heartbeat(self) -> float:
        """Thread-safe getter for last_heartbeat."""
        with self._heartbeat_lock:
            return self.last_heartbeat

    def send_message(self, msg: str):
        try:
            # Add newline for better formatting
            send_message(self.conn, msg)
        except (BrokenPipeError, ConnectionResetError, SocketError):
            print(f"⚠️ Connection lost while sending to {self.username}")

    def print_player_info(self):
        self.send_message(f"{self.username}'s status cards: {self.status_cards}")
        self.send_message(f"{self.username}'s points: {self.points}")
        self.send_message(f"Current Bid: {self.current_money_card_bids}")
        self.send_message(f"Remaining Money: {[m.value for m in self.money_cards]}")

    def get_bid(self) -> Union[list[int], str, None]:
        """Request a bid from the client and validate it."""
        self.print_player_info()
        while True:
            self.send_message("Enter your bid for the auction: ")
            
            # Read from message queue instead of blocking on socket
            bid = self._get_message_from_queue(timeout=None)  # Wait indefinitely for user input
            
            if bid is None:
                # Connection closed or receiver stopped
                # If player is inactive (disconnected), treat as quit
                if not self.active:
                    return "quit"
                return None

            if not bid:
                self.send_message("⚠️ Empty input. Please enter a valid number, list, or command.")
                continue

            # Handle special commands
            if bid.lower() in ["pass", "fold", "quit"]:
                return bid.lower()

            # Parse list input like [1, 2, 3]
            if bid.startswith("[") and bid.endswith("]"):
                try:
                    nums = [int(x.strip()) for x in bid.strip("[]").split(",") if x.strip()]
                        
                    # Check duplicates
                    if len(nums) != len(set(nums)):
                        self.send_message("⚠️ Duplicate values found in bid. Please enter a valid bid.")
                        continue
                        
                    # Check if all values exist in money_cards
                    money_values = [m.value for m in self.money_cards]
                    for num in nums:
                        if num not in money_values:
                            self.send_message(f"⚠️ You don't have money card {num}. Enter a valid bid.")
                            break
                    else:
                        return nums
                except ValueError:
                    self.send_message("⚠️ Invalid list format. Example: [1, 2, 3]")
                    continue
                
            # Handle integer input (convert to list like CLIPlayer)
            try:
                num = int(bid)
                money_values = [m.value for m in self.money_cards]
                if num not in money_values:
                    self.send_message(f"⚠️ You don't have money card {num}. Enter a valid bid.")
                    continue

                return [num]

            except ValueError:
                self.send_message("⚠️ Please enter a valid integer, list, or command (pass/fold/quit).")
                continue

    def choose_painting_to_discard(self) -> Painting:
        """
        Get input from the user to choose the painting to discard. Useful to discard faux pas.

        If player has paintings: 1, 5, 7

        Prompts user: User enters 5.
        Returns the painting 5.

        If user doesn't have any paintings, returns None.
        """
        self.send_message("Your Paintings: ")
        paintings = {}
        for s in self.status_cards:
            if isinstance(s, Painting):
                paintings[s.value] = s
                self.send_message(f"{s}: Value: {s.value}")

        if len(paintings) == 0:
            return None

        while True:
            try:
                self.send_message("Choose one to discard: ")
                
                # Read from message queue instead of blocking on socket
                choice = self._get_message_from_queue(timeout=None)  # Wait indefinitely for user input
                
                if choice is None:
                    # Connection closed or receiver stopped
                    return None

                if not choice:
                    self.send_message("⚠️ Empty input. Please enter a valid number.")
                    continue

                choice = int(choice.strip())
                painting = paintings.get(choice)
                if painting:
                    return painting
                else:
                    self.send_message("⚠️ Invalid choice. Try again.")
            except (ValueError, KeyError) as e:
                LoggingManager.error(e)
                self.send_message("⚠️ Invalid input. Let's try again..")
            except (ConnectionResetError, BrokenPipeError):
                print(f"⚠️ {self.username} disconnected.")
                return None

    def close(self):
        """Close the connection and stop the receiver thread."""
        self.active = False
        self.stop_receiver_thread()
        try:
            self.conn.close()
        except:
            pass

