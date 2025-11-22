from typing import Union
from highsociety.code.gamecore.player.player import BasePlayer
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager
from highsociety.code.gamecore.player.player import BasePlayer
from highsociety.code.gamecore.components_module.painting import Painting

class CLIPlayer(BasePlayer):
    def __init__(self, name: str, username: str):
        super().__init__(name, username)
        self.active = True

    def print_player_info(self):
        self.send_message(f"{self.username}'s status cards: {self.status_cards}")
        self.send_message(f"{self.username}'s points: {self.points}")
        self.send_message(f"Current Bid: {self.current_money_card_bids}")
        self.send_message(f"Remaining Money: {[m.value for m in self.money_cards]}")

    def get_bid(self) -> Union[list[int], str]:
        """
        Get input for bid from user.
        Supports:
        - Single integer: 10
        - List of integers: [1, 2, 3]
        - Special commands: pass, fold, quit
        """
        self.print_player_info()
        bid = input("Enter your bid for the auction: ").strip().lower()

        if not bid:
            self.send_message("⚠️ Empty input. Please enter a valid number, list, or command.")
            return self.get_bid()

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
                    return self.get_bid()

                # check if all values exist in money_cards
                money_values = [m.value for m in self.money_cards]
                for num in nums:
                    if num not in money_values:
                        self.send_message(f"⚠️ You don't have money card {num}. Enter a valid bid.")
                        return self.get_bid()

                return nums

            except ValueError:
                self.send_message("⚠️ Invalid list format. Example: [1, 2, 3]")
                return self.get_bid()

        # ✅ Handle integer input
        try:
            num = int(bid)
            money_values = [m.value for m in self.money_cards]
            if num not in money_values:
                self.send_message(f"⚠️ You don't have money card {num}. Enter a valid bid.")
                return self.get_bid()

            return [num]

        except ValueError:
            self.send_message("⚠️ Please enter a valid integer, list, or command (pass/fold/quit).")
            return self.get_bid()


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

        if len(paintings) is None:
            return None

        while(True):
            try:
                choice = int(input("Choose one to discard: "))
                painting = paintings[choice]
                break
            except Exception as e:
                LoggingManager.error(e)
                self.send_message(f"Invalid input. Let's try again..")
                
        return painting



    def send_message(self, message: str):
        print(message)
