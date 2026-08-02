import select
import sys
from typing import Union, Optional
from highsociety.code.gamecore.player.player import BasePlayer
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager
from highsociety.code.gamecore.components_module.painting import Painting

class CLIPlayer(BasePlayer):
    def __init__(self, name: str, username: str):
        super().__init__(name, username)
        self.active = True
        self._awaiting_input = False

    def send_message(self, message: str, message_type: str = None, created_at: float = None):
        print(message)

    def print_player_info(self):
        self.send_message(f"{self.username}'s status cards: {self.status_cards}")
        self.send_message(f"{self.username}'s points: {self.points}")
        self.send_message(f"Current Bid: {self.current_money_card_bids}")
        self.send_message(f"Remaining Money: {[m.value for m in self.money_cards]}")

    def _read_line(self, timeout: Optional[float] = None) -> Optional[str]:
        """
        Reads a line from stdin, respecting an optional timeout (Unix only, via select).
        Prints the prompt only once per turn (not on every poll while waiting).

        Returns:
            The stripped input line, "" for empty input, or None if the deadline
            passed before any input arrived (caller should treat as "still waiting").
        """
        if not self._awaiting_input:
            self.print_player_info()
            print("Enter your bid for the auction: ", end='', flush=True)
            self._awaiting_input = True

        if timeout is not None:
            ready, _, _ = select.select([sys.stdin], [], [], timeout)
            if not ready:
                return None

        line = sys.stdin.readline()
        self._awaiting_input = False
        if line == '':
            # EOF: stdin closed for good (e.g. piped input exhausted). There is no
            # point ever reading again, so mark the player inactive like a
            # disconnected NetworkPlayer rather than spinning on instant-EOF reads forever.
            self.active = False
            return None
        return line.strip().lower()

    def get_bid(self, timeout: Optional[float] = None) -> Union[list[int], str, None]:
        """
        Get input for bid from user.
        Supports:
        - Single integer: 10
        - List of integers: [1, 2, 3]
        - Special commands: pass, fold, quit

        Notes:
            Returns None if user enters invalid bid, or if no input arrived before
            `timeout` seconds elapsed (caller is expected to re-poll / re-check its deadline).
            Returns "quit" if stdin has closed for good.
        """
        bid = self._read_line(timeout=timeout)

        if bid is None:
            if not self.active:
                return "quit"
            return None

        if not bid:
            self.send_message("⚠️ Empty input. Please enter a valid number, list, or command.")
            return None

        # ✅ Handle special commands
        if bid.lower() in {"pass", "fold", "quit"}:
            return bid.lower()

        # ✅ Handle list input like [1, 2, 3]
        if bid.startswith("[") and bid.endswith("]"):
            try:
                nums = [int(x.strip()) for x in bid.strip("[]").split(",") if x.strip()]

                # check duplicates
                if len(nums) != len(set(nums)):
                    self.send_message("⚠️ Duplicate values found in bid. Please enter a valid bid.")
                    return None

                # check if all values exist in money_cards
                money_values = [m.value for m in self.money_cards]
                for num in nums:
                    if num not in money_values:
                        self.send_message(f"⚠️ You don't have money card {num}. Enter a valid bid.")
                        return None

                return nums

            except ValueError:
                self.send_message("⚠️ Invalid list format. Example: [1, 2, 3]")
                return None

        # ✅ Handle integer input
        try:
            num = int(bid)
            money_values = [m.value for m in self.money_cards]
            if num not in money_values:
                self.send_message(f"⚠️ You don't have money card {num}. Enter a valid bid.")
                return None

            return [num]

        except ValueError:
            self.send_message("⚠️ Please enter a valid integer, list, or command (pass/fold/quit).")
            return None

    def choose_painting_to_discard(self) -> Painting:
        """
        Get input from the user to choose the painting to disard. Useful to discard faux pas.

        If player has paintings: 1, 5, 7

        Prompts user: User enters 5.
        Returns the painting 5.

        If user doesn't have any paintings, returns None.
        """
        print("Your Paintings: ")
        paintings = {}
        for s in self.status_cards:
            if isinstance(s, Painting):
                paintings[s.value] = s 
                self.send_message(f"{s}: Value: {s.value}")

        if not paintings:
            return None

        while(True):
            try:
                line = input("Choose one to discard: ")
            except EOFError:
                # stdin closed for good — no point retrying forever. Match get_bid's
                # EOF handling by marking the player inactive like a disconnect.
                self.active = False
                return None

            try:
                choice = int(line)
                painting = paintings[choice]
                break
            except (ValueError, KeyError) as e:
                LoggingManager.error(e)
                self.send_message(f"Invalid input. Let's try again..")

        return painting
