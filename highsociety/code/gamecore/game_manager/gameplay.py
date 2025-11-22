import random
from typing import Union
from highsociety.code.gamecore.player.player import BasePlayer
from highsociety.code.gamecore.player.cliplayer import CLIPlayer
from highsociety.code.gamecore.player.networkplayer import NetworkPlayer
from highsociety.code.gamecore.card_manager.status_card_manager import StatusCardManager
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager
from highsociety.code.gamecore.utils.utility import get_game_setting_configurations
from highsociety.code.gamecore.components_module.disgrace_card import DisgraceCard, FauxPas
from highsociety.code.gamecore.game_manager.host import CLIHost, NetworkHost
from highsociety.code.gamecore.components_module.status_card import StatusCard
from highsociety.code.gamecore.components_module.painting import Painting

class PlayGame():
    def __init__(self, players: list[CLIPlayer], mode = 'cli'):
        self.players = players
        self.num_players= len(players)

        self.game_state = "initialized"
        self.auction_rounds = []
        self.current_auction = None
        self.status_card_manager = StatusCardManager()

        self.__game_config = get_game_setting_configurations()
        self.__green_card_limit = self.__game_config.get("green_card_limit", 4)

        if mode.lower() == 'cli':
            self.host = CLIHost(players)

        elif mode.lower() == 'network':
            self.host = NetworkHost(players)

        else:
            LoggingManager.info("Invalid Host. Default host: cli")
            self.host = CLIHost()
        

    def get_next_player_id(self, current_player_id: int) -> int:
        """
        Get the next player id in a circular manner.
        Args:
            current_player_id: The starting player id
            idx: The index of the player to get the next player id

        Returns:
            The next player id
        """
        return (current_player_id + 1) % self.num_players


    def shuffle_players(self) -> list[BasePlayer]:
        random.shuffle(self.players)
        LoggingManager.info("Shuffled players")
        return self.players


    def _count_active_auction_players(self) -> int:
        return sum(1 for p in self.players if p.active and p.current_participation_in_auction)


    def _get_auction_winner(self) -> int:
        """
        Returns the id of the auction winner.
        """
        for idx, player in enumerate(self.players):
            if player.current_participation_in_auction and player.active:
                return idx
        return -1


    def _finalize_auction(self, winner_id: int, status_card: StatusCard, max_bid: int):
        if winner_id != -1:
            winner = self.players[winner_id]
            if isinstance(status_card, DisgraceCard):
                self.host.send_message(f"\n🏆 {winner.username} gets the auction for '{status_card}' by passing!")
            else:
                self.host.send_message(f"\n🏆 {winner.username} wins the auction for '{status_card}' with a bid of {max_bid}!")
            winner.add_status_card(status_card)
        else:
            self.host.send_message("⚠️ Auction ended. No active bidders left.")


    def _handle_player_turn(self, player: BasePlayer, max_bid: int, status_card: StatusCard) -> Union[int,str]:
        """
        Handles bid of a player.
        Ensures the bid by player is greater than max_bid.
        Ensures the bid by player is valid.

        Parameters:
            player: object of type player
            max_bid: maximum value of bid in that round
            status_card: status card for which the bid is happening.

        Returns 
            (int) bid_value by the player
        """
        player.send_message(f"\n Auctioning: {status_card}: {status_card.description}")
        player.send_message(f"Current Highest Bid: {max_bid}")

        while True:
            bids = player.get_bid()

            if isinstance(bids, str):
                cmd = bids.lower()
                if cmd in ["pass", "fold", "quit"]:
                    player.withdraw_bid()
                    return cmd
                else:
                    player.send_message("⚠️ Invalid command. Try again.")
                    continue

            # Numeric bids
            bid_value = player.current_bid_value + sum(bids)
            if bid_value <= max_bid:
                player.send_message(f"⚠️ Your bid must exceed the current highest bid ({max_bid}). Try again.")
                continue

            # Place bid
            placed_value = player.place_bid(bids)
            return placed_value


    def normal_card_auction(self, status_card: StatusCard, starting_player_id: int) -> int:
        num_players_in_auction = self._count_active_auction_players()
        current_player_id = starting_player_id                        
        max_bid = 0
        
        while (num_players_in_auction > 1):
            player = self.players[current_player_id]

            if player.active == False or player.current_participation_in_auction == False:
                current_player_id = self.get_next_player_id(current_player_id)
                continue

            else:
                # Let player take a turn
                action_result = self._handle_player_turn(player, max_bid, status_card)
                # Handle result types
                if action_result in ["pass", "fold"]:
                    num_players_in_auction -= 1
                    self.host.send_message(f"⚪ {player.username} passed.")

                elif action_result == "quit":
                    player.active = False
                    num_players_in_auction -= 1
                    self.host.send_message(f"❌ {player.username} quit the game.")

                elif isinstance(action_result, int) and action_result > max_bid:
                    max_bid = action_result
                    self.host.send_message(f"💰 {player.username} raised to {max_bid}.")

                else:
                    # Invalid / repeated bid
                    pass

                if num_players_in_auction <= 1:
                    break

                current_player_id = self.get_next_player_id(current_player_id)

        # --- Determine Winner ---
        winner_id = self._get_auction_winner()
        self._finalize_auction(winner_id, status_card, max_bid)
        self.host.send_message(f"--- End of Auction ---\n")


        # Reset auction state
        for player in self.players:
            player.reset_auction_attributes()

        return winner_id


    def disgrace_card_auction(self, current_player_id: int, status_card: DisgraceCard) -> int:
        """
        Simple disgrace auction:
        - Players take turns in normal order.
        - Each player must bid strictly more than the previous max bid.
        - The first player who 'pass'/'fold'/'quit' loses and takes the disgrace card.
        Returns:
        loser_id (int): index of the player who takes the disgrace card.
        """
        num_players_in_auction = self._count_active_auction_players()
        if num_players_in_auction == 0:
            self.host.send_message("⚠️ No active players for disgrace auction.")
            return -1


        max_bid = 0
        loser_id = -1

        self.host.send_message(f"\n💀 Disgrace Auction started for: {status_card}: {status_card.description}")
        self.host.send_message("Each turn, you must bid higher than the previous bid. First to pass takes the disgrace card.")

        # loop until someone passes (they lose)
        while True:
            player = self.players[current_player_id]

            # skip inactive players
            if not player.active:
                current_player_id = self.get_next_player_id(current_player_id)
                continue

            # let the player act; _handle_player_turn enforces bid > max_bid
            action = self._handle_player_turn(player, max_bid, status_card)

            # handle special actions
            if isinstance(action, str):
                cmd = action.lower()
                if cmd in ["pass", "fold"]:
                    # player passes -> they lose and take the disgrace card
                    loser_id = current_player_id
                    self.host.send_message(f"💢 {player.username} passed and takes the disgrace card!")
                    break
                elif cmd == "quit":
                    # quitting also makes them lose the disgrace card (treated same as pass)
                    player.active = False
                    loser_id = current_player_id
                    self.host.send_message(f"❌ {player.username} quit.")
                    break
                else:
                    # unexpected string (shouldn't happen) — ask again in next loop
                    player.send_message("⚠️ Invalid command. You must raise or 'pass' to take the card.")
                    continue

            # numeric bid placed
            elif isinstance(action, int):
                # update max and continue to next player in order
                max_bid = action
                self.host.send_message(f"💰 {player.username} bid now {max_bid}.")
                # move to next player
                current_player_id = self.get_next_player_id(current_player_id)
                continue

            else:
                # defensive fallback
                player.send_message("⚠️ Invalid response. Try again.")
                continue

        # finalize: loser_id should be the player who passed / quit
        if loser_id == -1:
            # should not happen, but safe guard
            self.host.send_message("⚠️ Disgrace auction ended unexpectedly with no loser.")
            # reset auction attributes and return -1
            for p in self.players:
                p.reset_auction_attributes()
            return -1

        # Give the disgrace card to loser and announce
        loser = self.players[loser_id]
        self._finalize_auction(loser_id, status_card, max_bid)

        # Reset auction state for all players
        for player in self.players:
            player.reset_auction_attributes()

        # Return the loser id (who took the disgrace) — consistent with your normal_card_auction contract
        return loser_id

    def _should_end_game(self, num_players_in_auction, num_green_cards):
        """
        Determines if the game needs to be end early.
        """
        if num_players_in_auction < 2:
            LoggingManager.info("Need at least 2 active players. Game ending.")
            return True
        if num_green_cards >= self.__green_card_limit:
            LoggingManager.info(f"Green card limit ({self.__green_card_limit}) reached. Ending Game..")
            return True
        return False

    def handle_faux_pas_penalty(self, player_id: int) -> bool:
        """
        Takes player_id and prompts player to discard a painting

        Returns True if discarded.
        False if player doesn't have paintings and hence hasn't discarded yet.
        """
        player = self.players[player_id]
        paintings = [card for card in player.status_cards if isinstance(card, Painting)]

        if paintings:
            card_to_discard = player.choose_painting_to_discard().value
            player.discard_painting_card(card_to_discard)
            self.host.send_message(f"🎨 {player.username} discarded a painting due to Faux Pas.")
            return True

        return False

    def determine_winner(self):
        """Determine the final winner(s) of the game."""
        LoggingManager.info("Determining the winner...")

        winner_candidates = [True] * len(self.players)
        player_points = []
        player_money_left = []

        # Collect player info
        for idx, player in enumerate(self.players):
            if not player.active:
                winner_candidates[idx] = False

            pts = player.points
            money_left = player.money_left()

            player_points.append(pts)
            player_money_left.append(money_left)

        # Step 1: Find minimum money among active players
        active_money = [m for i, m in enumerate(player_money_left) if winner_candidates[i]]
        if not active_money:
            self.host.send_message("⚠️ No active players remain.")
            self.host.send_message("No Winners for this game.")
            return None

        min_money = min(active_money)

        # Step 2: Eliminate player with least money. If 2 people have min money, no boyd has min money.
        num_players_with_min_money = 0
        for idx, money in enumerate(player_money_left):
            if money == min_money:
                num_players_with_min_money +=1

        if num_players_with_min_money == 1:
            for idx, money in enumerate(player_money_left):
                if winner_candidates[idx] and money == min_money:
                    winner_candidates[idx] = False

        # Step 3: From remaining candidates, find the highest point(s)
        remaining_points = [player_points[i] if winner_candidates[i] else float('-inf') 
                            for i in range(len(self.players))]

        max_points = max(remaining_points)

        # Step 4: Identify all winners (in case of tie)
        winners = [self.players[i] for i, pts in enumerate(player_points)
                if winner_candidates[i] and pts == max_points]

        # Step 5: Announce results
        self.host.send_message("\n🏁 Final Standings:")
        for idx, player in enumerate(self.players):
            if player.active:
                self.host.send_message(f" - {player.username}: Points={player_points[idx]}, Money Left={player_money_left[idx]}")
            else:
                self.host.send_message(f" - {player.username}: Inactive : (Points={player_points[idx]}, Money Left={player_money_left[idx]})")

        if len(winners) == 1:
            self.host.send_message(f"\n🏆 Winner: {winners[0].username} with {max_points} points!")
        elif len(winners) > 1:
            tied_names = ", ".join(w.username for w in winners)
            self.host.send_message(f"\n🤝 It's a tie between {tied_names} with {max_points} points each!")
        else:
            self.host.send_message("\n😬 No winner could be determined.")

        return winners


    def play_game(self):
        LoggingManager.info("Game Started..")
        self.shuffle_players()

        num_green_cards = 0
        starting_player_id = random.randint(0, len(self.players) - 1) # random starting player id
        faux_pas_holder_id = None

        while (not self.status_card_manager.is_empty()):

            num_players_in_auction = self._count_active_auction_players()

            status_card = self.status_card_manager.remove_top_card()
            if status_card.is_green:
                num_green_cards += 1
                if num_green_cards <=3:
                    self.host.send_message(f"{num_green_cards} green card(s) revealed ..")

            if self._should_end_game(num_players_in_auction, num_green_cards):
                break

            if isinstance(status_card, DisgraceCard):
                # different kinda auction
                starting_player_id = self.disgrace_card_auction(starting_player_id, status_card=status_card)

                if isinstance(status_card, FauxPas):
                    faux_pas_holder_id = starting_player_id
                    self.players[faux_pas_holder_id].send_message(f"You have to discard a painting in this/subsequent rounds as you are holding a faux pass")


            else:
                starting_player_id = self.normal_card_auction(status_card = status_card, starting_player_id= starting_player_id)

            # discard card from the player holding faux pas. -> applicable only when the player has a painting
            if faux_pas_holder_id is not None:
                has_discarded = self.handle_faux_pas_penalty(faux_pas_holder_id)
                if has_discarded:
                    faux_pas_holder_id = None


        # Determine winner
        self.determine_winner()
        self.host.send_message("Game Concluded 😘")
        self.host.send_message("Thanks for playing...")
                        



     


                
                        


                




            




