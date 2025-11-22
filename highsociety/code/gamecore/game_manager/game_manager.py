from player.player import Player
from utils.utility import get_game_setting_configurations
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager
from highsociety.code.gamecore.game_manager.auction_information import AuctionInformation
from gamecore.card_manager.status_card_manager import StatusCardManager
import random


class GameManager:
    def __init__(self):
        self.players = []
        self.game_config = get_game_setting_configurations()
        self.__money_card_denominations = self.game_conf['starting_cash'] # Denominations of money cards to be distributed to each player
        self.auction_rounds: list[AuctionInformation] = []
        self.status_card_manager = StatusCardManager()
        self.current_auction = None
        self.max_players = self.game_config['max_players']
        self.min_players = self.game_config['min_players']
        self.game_state = "initialized"

    def add_player(self, player_name: str, user_name: str):
        new_player = Player(player_name, user_name)
        self.add_money_to_player(new_player)
        self.players.append(new_player)

    def add_money_to_player(self, player: Player):
        for denomination in self.__money_card_denominations:
            player.add_money_card(denomination)

    def remove_player(self, player_name: str):
        for player in self.players:
            if player.name == player_name:
                self.players.remove(player)

    def display_players(self):
        for player in self.players:
            print(player.name)
            LoggingManager.info(f"Player: {player.name}")

    def add_players_input(self):
        num_players = int(input("Enter number of players: "))
        for i in range(num_players):
            player_name = input(f"Enter player {i+1} name: ")
            player_username = input(f"Enter player {i+1} username: ")
            self.add_player(player_name, player_username)
            LoggingManager.info(f"Added player: {player_name}")

    def shuffle_players(self):
        random.shuffle(self.players)
        LoggingManager.info("Shuffled players")


    def start_game(self):
        if len(self.players) < self.min_players:
            LoggingManager.error("Not enough players to start the game")
            return
        
        self.game_state = "started"
        self.shuffle_players()
        LoggingManager.info("Game Started..")
    
    def run(self):
        LoggingManager.info("Game Started..")
        self.add_players_input()
        LoggingManager.info("Players added..")
        self.shuffle_players()


        


                
