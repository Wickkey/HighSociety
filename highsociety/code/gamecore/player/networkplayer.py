import socket
from socket import error as SocketError
from typing import Union
import time
import json
import threading
import queue
from typing import Optional
from highsociety.code.gamecore.player.player import BasePlayer
from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager
from highsociety.code.common.utils.network_utility import receive_json, send_json


class NetworkPlayer(BasePlayer):
    """Remote player using sockets."""
    def __init__(self, name: str, username: str, conn: socket.socket, game_id: str):
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
        self.game_id: str = game_id

    def _receiver_thread_func(self):
        """
        Continuously receives messages from the client socket.
        
        Behavior & Edge Cases:
        ----------------------
        1. Socket timeout (1 sec):
        - Used only so the thread can periodically check `_receiver_running`.
        - No effect on message integrity.

        2. Connection closed gracefully (recv() returns empty bytes):
        - Marks player as inactive.
        - Exits loop.
        - Queues `None` to signal shutdown.

        3. TCP segmentation:
        - Partial chunks are appended to `buffer`.
        - Only complete newline-terminated messages are processed.

        4. Empty messages (e.g., client sends just "\n"):
        - After strip(), they become "".
        - These are ignored and NEVER added to the queue.

        6. Normal game messages:
        - Always cleaned with strip().
        - Always enqueued into `message_queue`.

        7. On connection errors (reset, broken pipe, OSError):
        - Marks player inactive.
        - Breaks loop.
        - Queues `None`.

        8. Final signal:
        - When the thread stops for ANY reason, a single `None` is placed
            into the queue to notify game logic that no more messages will arrive.

        Guarantees:
        -----------
        - The queue will contain:
            • Valid non-empty game messages (str)
            • A single `None` when receiver shuts down
        - It will NEVER contain:
            • Empty strings (""), 
            • Heartbeat messages,
            • Raw TCP fragments.
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
                
                buffer += chunk
                
                # Process complete messages (ending with newline)
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    line = line.strip()
                    
                    if not line:
                        continue
                
                    # Queue game messages
                    msg = json.loads(line)

                    if msg.get("message_type") == "PING":
                        with self._heartbeat_lock:
                            self.last_heartbeat = time.time()
                        continue

                    self.message_queue.put(msg)
                    
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

    def send_message(self, msg: str, message_type: str, created_at: Optional[float] = None) -> None:
        """
        Sends message to the client socket.
        """
        if created_at is None:
            created_at = time.time()

        if message_type not in ["INFO",
            "PLAYER_INFO",
            "PLAYER_MOVE_TIMER",
            "PLAYER_MOVE",
            "INPUT_ERROR",
            "GLOBAL_EVENT",
            "GLOBAL_MOVE_INFO"
        ]:
            raise ValueError(f"Invalid message type: {message_type}")

        if message_type == "PLAYER_INFO":
            payload = {
                "game_id": self.game_id,
                "message_type": message_type,
                "player_id": [self.username],
                "prompt": msg,
                "requires_response": False,
                "created_at": created_at
            }
        elif message_type == "PLAYER_MOVE_TIMER":
            payload = {
                "game_id": self.game_id,
                "message_type": message_type,
                "player_id": [self.username],
                "prompt": msg,
                "requires_response": False,
                "created_at": created_at
            }
        elif message_type == "PLAYER_MOVE":
            payload = {
                "game_id": self.game_id,
                "message_type": message_type,
                "player_id": [self.username],
                "prompt": msg,
                "requires_response": True,
                "constraints": {
                    "min_bid": 0,
                    "allowed_money_cards": [m.value for m in self.money_cards],
                    "allowed_paintings": [p.value for p in self.status_cards if isinstance(p, Painting)],
                    "allowed_commands": ["pass", "fold", "quit"]
                },
                "created_at": created_at
            }
        elif message_type == "INPUT_ERROR":
            payload = {
                "game_id": self.game_id,
                "message_type": message_type,
                "player_id": [self.username],
                "prompt": msg,
                "requires_response": False,
                "created_at": created_at
            }
        elif message_type in ("GLOBAL_EVENT", "GLOBAL_MOVE_INFO", "INFO"):
            payload = {
                "game_id": self.game_id,
                "message_type": message_type,
                "prompt": msg,
                "requires_response": False,
                "created_at": created_at
            }
        try:
            send_json(self.conn, payload)
        except (BrokenPipeError, ConnectionResetError, SocketError):
            print(f"⚠️ Connection lost while sending to {self.username}")

    def print_player_info(self):
        self.send_message(f"{self.username}'s status cards: {self.status_cards}", message_type="PLAYER_INFO", created_at = time.time())
        self.send_message(f"{self.username}'s points: {self.points}", message_type="PLAYER_INFO", created_at = time.time())
        self.send_message(f"Current Bid: {self.current_money_card_bids}", message_type="PLAYER_INFO", created_at = time.time())
        self.send_message(f"Remaining Money: {[m.value for m in self.money_cards]}", message_type="PLAYER_INFO", created_at = time.time())

    def get_bid(self, timeout: Optional[float] = None) -> Union[list[int], str, None]:
        """Request a bid from the client socket and validate it."""
        self.print_player_info()

        if timeout:
            self.send_message(f"Time left: {timeout:.2f}s ⏰", message_type="PLAYER_MOVE_TIMER", created_at=time.time())
        self.send_message("Enter your bid for the auction: ", message_type="PLAYER_MOVE", created_at=time.time())
            
        # Read from message queue instead of blocking on socket
        bid = self._get_message_from_queue(timeout=timeout)  # Wait indefinitely for user input
    
        if bid is None:
            # Connection closed or receiver stopped
            # If player is inactive (disconnected), treat as quit
            if not self.active:
                return "quit"
            return None

        if bid["prompt"] == "":
            self.send_message("⚠️ Empty input. Please enter a valid number, list, or command.", message_type="INPUT_ERROR", created_at=time.time())
            return None

        # Handle special commands
        if bid["prompt"].lower() in ["pass", "fold", "quit"]:
            return bid["prompt"].lower()

        # Parse list input like [1, 2, 3]
        if bid["prompt"].startswith("[") and bid["prompt"].endswith("]"):
            try:
                nums = [int(x.strip()) for x in bid["prompt"].strip("[]").split(",") if x.strip()]
                        
                # Check duplicates
                if len(nums) != len(set(nums)):
                    self.send_message("⚠️ Duplicate values found in bid. Please enter a valid bid.", message_type="INPUT_ERROR", created_at=time.time())
                    return None
                    
                        
                # Check if all values exist in money_cards
                money_values = [m.value for m in self.money_cards]
                for num in nums:
                    if num not in money_values:
                        self.send_message(f"⚠️ You don't have money card {num}. Enter a valid bid.", message_type="INPUT_ERROR", created_at=time.time())
                        return None
                else:
                    return nums
            except ValueError:
                self.send_message("⚠️ Invalid list format. Example: [1, 2, 3]", message_type="INPUT_ERROR", created_at=time.time())
                return None
                
        # Handle integer input (convert to list like CLIPlayer)
        try:
            num = int(bid["prompt"])
            money_values = [m.value for m in self.money_cards]
            if num not in money_values:
                self.send_message(f"⚠️ You don't have money card {num}. Enter a valid bid.", message_type="INPUT_ERROR", created_at=time.time())
                return None

            return [num]

        except ValueError:
            self.send_message("⚠️ Please enter a valid integer, list, or command (pass/fold/quit).", message_type="INPUT_ERROR", created_at=time.time())
            return None

    def choose_painting_to_discard(self) -> Painting:
        """
        Get input from the user to choose the painting to discard. Useful to discard faux pas.

        If player has paintings: 1, 5, 7

        Prompts user: User enters 5.
        Returns the painting 5.

        If user doesn't have any paintings, returns None.
        """
        self.send_message("Your Paintings: ", message_type="PLAYER_INFO", created_at=time.time())
        paintings = {}
        for s in self.status_cards:
            if isinstance(s, Painting):
                paintings[s.value] = s
                self.send_message(f"{s}: Value: {s.value}", message_type="PLAYER_INFO", created_at=time.time())

        if len(paintings) == 0:
            return None

        while True:
            try:
                self.send_message("Choose one to discard: ", message_type="PLAYER_MOVE", created_at=time.time())
                
                # Read from message queue instead of blocking on socket
                choice = self._get_message_from_queue(timeout=None)  # Wait indefinitely for user input

                if choice is None:
                    # Connection closed or receiver stopped
                    return None

                choice_text = choice.get("prompt", "")
                if not choice_text:
                    self.send_message("⚠️ Empty input. Please enter a valid number.", message_type="INPUT_ERROR", created_at=time.time())
                    continue

                choice = int(choice_text.strip())
                painting = paintings.get(choice)
                if painting:
                    return painting
                else:
                    self.send_message("⚠️ Invalid choice. Try again.", message_type="INPUT_ERROR", created_at=time.time())
            except (ValueError, KeyError) as e:
                LoggingManager.error(e)
                self.send_message("⚠️ Invalid input. Let's try again..", message_type="INPUT_ERROR", created_at=time.time())
            except (ConnectionResetError, BrokenPipeError):
                print(f"⚠️ {self.username} disconnected.")
                return None

    def close(self):
        """Close the connection and stop the receiver thread."""
        self.active = False
        self.stop_receiver_thread()
        try:
            self.conn.close()
            print(f"{self.username} connection closed successfully.")
        except:
            pass

