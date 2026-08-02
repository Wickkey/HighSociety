from socket import error as SocketError
from typing import Union
from typing import Optional
from highsociety.code.gamecore.player.player import BasePlayer
from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.network.transport import Transport
from highsociety.code.gamecore.network.protocol import build_player_payload
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager, LogType


class NetworkPlayer(BasePlayer):
    """
    Remote player. Talks entirely through a Transport (today always a
    SocketTransport, see network_server.py) using the shared player message
    protocol (network/protocol.py) — this class has no socket/threading
    knowledge of its own, so a future transport (e.g. WebSockets for a
    browser client) plugs in here unchanged.
    """
    def __init__(self, name: str, username: str, transport: Transport, game_id: str):
        super().__init__(name, username)
        self.transport = transport
        self.active = True
        self.game_id: str = game_id

    def start_receiver_thread(self) -> None:
        """Begin receiving messages in the background. Kept as an explicit,
        separately-timed step (rather than starting in __init__) to match
        network_server.py, which finishes accepting all players before
        starting anyone's receiver."""
        self.transport.start()

    def stop_receiver_thread(self) -> None:
        self.transport.stop()

    def get_last_heartbeat(self) -> float:
        return self.transport.get_last_heartbeat()

    def send_message(self, msg: str, message_type: str, created_at: Optional[float] = None) -> None:
        """Sends a message to the remote player via its transport."""
        constraints = None
        if message_type == "PLAYER_MOVE":
            constraints = {
                "min_bid": 0,
                "allowed_money_cards": [m.value for m in self.money_cards],
                "allowed_paintings": [p.value for p in self.status_cards if isinstance(p, Painting)],
                "allowed_commands": ["pass", "fold", "quit"],
            }

        payload = build_player_payload(
            game_id=self.game_id,
            username=self.username,
            message_type=message_type,
            prompt=msg,
            created_at=created_at,
            constraints=constraints,
        )
        try:
            self.transport.send(payload)
        except (BrokenPipeError, ConnectionResetError, SocketError):
            print(f"⚠️ Connection lost while sending to {self.username}")

    def _belongs_to_this_game(self, msg: dict) -> bool:
        """
        A message's game_id must either be absent (permissive, for lightweight
        clients/tests that don't bother setting it) or match this player's own
        game_id. A *present but different* game_id means the message is
        stale/misdirected (e.g. a confused client, or leftover data from a
        previous session) and must not be treated as this turn's input.
        """
        incoming_game_id = msg.get("game_id")
        if incoming_game_id is not None and incoming_game_id != self.game_id:
            LoggingManager.warning(
                f"Ignoring message for {self.username} with mismatched game_id "
                f"(expected {self.game_id!r}, got {incoming_game_id!r})",
                log_type=LogType.SECURITY,
            )
            return False
        return True

    def print_player_info(self):
        self.send_message(f"{self.username}'s status cards: {self.status_cards}", message_type="PLAYER_INFO")
        self.send_message(f"{self.username}'s points: {self.points}", message_type="PLAYER_INFO")
        self.send_message(f"Current Bid: {self.current_money_card_bids}", message_type="PLAYER_INFO")
        self.send_message(f"Remaining Money: {[m.value for m in self.money_cards]}", message_type="PLAYER_INFO")

    def get_bid(self, timeout: Optional[float] = None) -> Union[list[int], str, None]:
        """Request a bid from the remote player and validate it."""
        self.print_player_info()

        if timeout:
            self.send_message(f"Time left: {timeout:.2f}s ⏰", message_type="PLAYER_MOVE_TIMER")
        self.send_message("Enter your bid for the auction: ", message_type="PLAYER_MOVE")

        bid = self.transport.receive(timeout=timeout)

        if bid is None:
            # Timed out waiting, or the transport has disconnected for good.
            if not self.transport.is_connected:
                self.active = False
                return "quit"
            return None

        if not self._belongs_to_this_game(bid):
            return None  # discard silently; caller will re-poll for the real input

        if bid["prompt"] == "":
            self.send_message("⚠️ Empty input. Please enter a valid number, list, or command.", message_type="INPUT_ERROR")
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
                    self.send_message("⚠️ Duplicate values found in bid. Please enter a valid bid.", message_type="INPUT_ERROR")
                    return None

                # Check if all values exist in money_cards
                money_values = [m.value for m in self.money_cards]
                for num in nums:
                    if num not in money_values:
                        self.send_message(f"⚠️ You don't have money card {num}. Enter a valid bid.", message_type="INPUT_ERROR")
                        return None
                else:
                    return nums
            except ValueError:
                self.send_message("⚠️ Invalid list format. Example: [1, 2, 3]", message_type="INPUT_ERROR")
                return None

        # Handle integer input (convert to list like CLIPlayer)
        try:
            num = int(bid["prompt"])
            money_values = [m.value for m in self.money_cards]
            if num not in money_values:
                self.send_message(f"⚠️ You don't have money card {num}. Enter a valid bid.", message_type="INPUT_ERROR")
                return None

            return [num]

        except ValueError:
            self.send_message("⚠️ Please enter a valid integer, list, or command (pass/fold/quit).", message_type="INPUT_ERROR")
            return None

    def choose_painting_to_discard(self) -> Painting:
        """
        Get input from the remote player to choose the painting to discard.
        Returns None if the player has no paintings, or if the transport
        disconnects before a valid choice arrives.
        """
        self.send_message("Your Paintings: ", message_type="PLAYER_INFO")
        paintings = {}
        for s in self.status_cards:
            if isinstance(s, Painting):
                paintings[s.value] = s
                self.send_message(f"{s}: Value: {s.value}", message_type="PLAYER_INFO")

        if len(paintings) == 0:
            return None

        while True:
            try:
                self.send_message("Choose one to discard: ", message_type="PLAYER_MOVE")

                choice = self.transport.receive(timeout=None)

                if choice is None:
                    # Connection closed or receiver stopped
                    self.active = False
                    return None

                if not self._belongs_to_this_game(choice):
                    continue  # discard silently; keep waiting for the real input

                choice_text = choice.get("prompt", "")
                if not choice_text:
                    self.send_message("⚠️ Empty input. Please enter a valid number.", message_type="INPUT_ERROR")
                    continue

                choice_value = int(choice_text.strip())
                painting = paintings.get(choice_value)
                if painting:
                    return painting
                else:
                    self.send_message("⚠️ Invalid choice. Try again.", message_type="INPUT_ERROR")
            except (ValueError, KeyError) as e:
                LoggingManager.error(e)
                self.send_message("⚠️ Invalid input. Let's try again..", message_type="INPUT_ERROR")
            except (ConnectionResetError, BrokenPipeError):
                print(f"⚠️ {self.username} disconnected.")
                self.active = False
                return None

    def close(self):
        """Close the connection and stop the receiver thread."""
        self.active = False
        self.transport.close()
        print(f"{self.username} connection closed successfully.")
