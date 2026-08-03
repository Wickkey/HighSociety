import logging
from operator import mul
from highsociety.code.gamecore.card_manager.money_card_manager import MoneyCardManager
from highsociety.code.gamecore.components_module.card import Card
from highsociety.code.gamecore.components_module.money_card import MoneyCard
from highsociety.code.gamecore.components_module.status_card import StatusCard
from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.components_module.disgrace_card import FauxPas
from typing import Union, Optional
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager
from highsociety.code.gamecore.player.bot_interface import BotInterface


class BasePlayer(BotInterface):
    """
    A class representing a player in the game.

    Abstract: BasePlayer provides bidding/card bookkeeping (place_bid,
    withdraw_bid, add_status_card, discard_painting_card, money_left,
    reset_auction_attributes, ...) but not get_bid/choose_painting_to_discard/
    send_message — those come from BotInterface and are left to each
    subclass (CLIPlayer for a terminal, NetworkPlayer for a socket, a bot for
    whatever it does instead). Instantiating BasePlayer directly raises
    TypeError; see bot_interface.py for the contract a subclass must fill in.

    Main methods:
        - place_bid(value): Places bid of value
        - withdraw_bid(): Withdraw from the auction
        - add_status_card(status_card): adds status card to the hand
        - discard_painting_card(value): Discards painting of a value
        - reset_auction_attributes(): resets auction attributes.
    """
    def __init__(self, name:str, username:str) -> None:
        """
        Initialize a new player with the given name and username.

        Usage:
            Methods:
                - get_bid(): Gets input from user for bid and returns the bid.
                - place_bid(value): Places bid of value
                - withdraw_bid(): Withdraw from the auction
                - add_status_card(status_card): adds status card to the hand
                - discard_painting_card(value): Discards painting of a value
                - reset_auction_attributes(): resets auction attributes.
            
            Attributes:
                - current_money_card_bids: List of Money Card bids in the current auction round
                - current_bid_value: Sum of value of MoneyCard bids
                - money_cards: Money Cards present in hand
                - status_cards: Status Cards won
                - points: Total Points of the player

        """
        ## Variables are kept private to prevent users from changing it (not truly private, disadvantage of python)
        self.__name = name
        self.__username = username

        # Player money and status card variables
        self.__money_card_manager = MoneyCardManager()
        self.__status_cards = []
        self.__points = 0

        # Auction table attributes
        self.__current_money_card_bids = [] # stores the money card bids made by the player
        self.__current_bid_value = 0 # stores the current bid value (total) made by the player
        self.__current_participation_in_auction = True # True if player is participating in the current auction
        self.__holds_faux_pas = False # True if player holds FauxPas card
        self.__has_discarded_card = False

    @property
    def player_info(self):
        """
        A snapshot of this player's own state. Auction history is deliberately
        not included here — it's game-wide, not player-scoped, so it belongs
        on the game rather than duplicated per player. Use
        PlayGame.get_auction_history() instead (or, over the network, the
        AUCTION_RESULT messages broadcast after each auction — see BOT_API.md).
        """
        player_info = {
            "name": self.__name,
            "username": self.__username,
            "money": self.__money_card_manager.cards,
            "status_cards": self.__status_cards,
            "points": self.__points,
        }

        return player_info

    @property
    def name(self) -> str:
        return self.__name
    
    @property
    def username(self) -> str:
        return self.__username
        
    @property
    def status_cards(self) -> tuple[MoneyCard, ...]:
        """
        Can be used to view_status_cards()
        """
        return tuple(self.__status_cards)
    
    @property
    def points(self) -> int:
        return self.__points


    def __calculate_points(self) -> int:
        """
        Can be used to check_points()
        """
        multiplier = 1
        pts = 0
        for card in self.__status_cards:
            pts+= card.value
            multiplier*= card.multiplier

        pts = pts*multiplier
        self.__points = pts
        return self.__points
    
    @property
    def current_money_card_bids(self) -> tuple[MoneyCard, ...]:
        """
        Can be used to view_current_money_card_bids()
        """
        return tuple(self.__current_money_card_bids)
    
    @property
    def current_bid_value(self) -> int:
        """
        Can be used to view_current_bid_value()
        """
        return self.__current_bid_value

    @property
    def money_cards(self) -> tuple[MoneyCard, ...]:
        """
        Can be used to view_money_cards()
        """
        return self.__money_card_manager.cards


    @property
    def current_participation_in_auction(self) -> bool:
        return self.__current_participation_in_auction
    
    @property
    def holds_faux_pas(self) -> bool:
        return self.__holds_faux_pas

    @property
    def has_discarded_card(self) -> bool:
        return self.__has_discarded_card


    def money_left(self):
        """
        Calculates money left in the moneycardmanager.

        Returns:
            (int) Money left with the player
        """
        return self.__money_card_manager.total_money()
    

    def place_bid(self, value: Union[int, list[int]]) -> int:
        """
        When a player places bid, 
        - allows to place bid only if the player is still participating in the auction
        - removes cards from the cardmanager and places it to the bid table
        - updates current_bid_value

        Args:
            - int or list[int] of cards that needs to be bid

        Returns:
            Total Bid By the Player
        """
        if self.__current_participation_in_auction:
            bid_cards = self.__money_card_manager.remove_cards(value) # returns a list of MoneyCards
            self.__current_money_card_bids.extend(bid_cards)

            for bid_card in bid_cards:
                self.__current_bid_value += bid_card.value

            LoggingManager.info(f"Player {self.__username} has bidded {self.__current_bid_value}")
            return self.__current_bid_value

        else:
            LoggingManager.error(f"Can't place bids after opting out of auction. Skipping Turn for Player {self.__username}")
            return 0

    def withdraw_bid(self) -> None:
        """
        Allows a player to withdraw bids.
        - checks if the player is participating in auction
        - adds cards from the bidding table to the player moneypile
        - resets current_bid_value and current_money_card_bids variable
        - sets participation_in_auction variable to False

        Args:
            None

        Returns:
            None
        """
        if self.__current_participation_in_auction:
            self.__money_card_manager.add_cards(self.__current_money_card_bids) # add cards back to the moneycard manager that is present in the auction table
            self.__current_money_card_bids = []
            self.__current_bid_value = 0
            LoggingManager.info(f"Player {self.__username} has withdrawn from the auction.")
            
        else:
            LoggingManager.info(f"Player {self.__username} has already withdrawn from the auction")

        self.__current_participation_in_auction = False


    def add_status_card(self, card: StatusCard) -> None:
        """
        Should be used if player wins the auction round.

        Adds the won card to the players hand
        """
        if not isinstance(card, StatusCard):
            LoggingManager.error(f"Invalid card type {card}")
 
        self.__status_cards.append(card)
        self.__status_cards.sort(key = lambda x: x.value)

        LoggingManager.info(f"Player {self.__username} won the auction card {card}")

        if isinstance(card, FauxPas):
            self.__holds_faux_pas = True
            self.__has_discarded_card = False

        self.__calculate_points()

    def discard_painting_card(self, value: int) -> Optional[Painting]:
        """
        Discard a painting of value if present in player's hand and return it.
        """
        for status_card in self.__status_cards:
            if isinstance(status_card, Painting) and status_card.value == value:
                self.__status_cards.remove(status_card)
                LoggingManager.info(
                    f"Discarded {status_card} from Player {self.__username}"
                )
                self.__calculate_points() # recalculate points after discarding a painting card
                return status_card

        LoggingManager.error(
            f"Painting Card of value {value} not found in player {self.__username}'s hand"
        )
        return None

    def reset_auction_attributes(self) -> None:
        """
        Resets auction attributes. Should be used for everyplayer before start of an auction.
        """
        self.__current_money_card_bids = []
        self.__current_bid_value = 0
        self.__current_participation_in_auction = True

    
    def __repr__(self) -> str:
        return f"Player(name={self.name}, username={self.username}, points = {self.points})"
    
