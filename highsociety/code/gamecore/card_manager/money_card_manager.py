import logging
from highsociety.code.gamecore.components_module.card_factory import CardFactory
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager
from highsociety.code.gamecore.components_module.money_card import MoneyCard
from typing import Union, Optional
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager
from highsociety.code.gamecore.utils.utility import get_all_configurations, get_game_setting_configurations


class MoneyCardManager:
    '''
        This class manages money cards in the game for a player.

        Usage:
            Objects will have access to methods:
                - add_cards(): to be used to add cards back to the object after auction.
                - remove_cards(): to be used to remove cards during auction
                - total_money(): to calculate total_money left in the card manager

            Attributes:
                - cards: Returns list of available cards
    '''
    def __init__(self) -> None:
        self.__cards = []
        self.__initialize_money_cards()
        self.__sort_cards()


    def __initialize_money_cards(self) -> None:
        card_factory = CardFactory()
        starting_cash = get_game_setting_configurations().get("starting_cash_values")

        for cash in starting_cash:
            card = card_factory.create_card(card_type = "money", value = cash)
            self.__cards.append(card)
    
    @property
    def cards(self) -> tuple[MoneyCard, ...]:
        return tuple(self.__cards) # immutable tuple to prevent external modification


    def __sort_cards(self) -> None:
        '''
        Sorts the cards in ascending order
        '''
        self.__cards.sort(key = lambda x: x.value)

    def add_cards(self, card: Union[MoneyCard,list[MoneyCard]]) -> None:
        '''
        Adds a list of money cards/Single Money card to the list
        '''
        if isinstance(card, MoneyCard):
            self.__cards.append(card)
        elif isinstance(card, list):
            if not all(isinstance(c, MoneyCard) for c in card):
                logging_manager.error("List contains non-Moneycard elements")
                raise ValueError("All elements in list must be MoneyCard instances")
            self.__cards.extend(card)
        else:
            logging_manager.error("Invalid type passed to add_cards method. Only MoneyCard or it's list is allowed")
            raise ValueError("Only MoneyCard or list[MoneyCard] allowed")
        

        # preserve the order of the cards
        self.__sort_cards()
               

    def remove_cards(self, value: Union[int, list[int]]) -> list[MoneyCard]:
        """
        Removes card from the managed cards.

        Args:
            value: if int is passed, check if the card is present in the card manager; removes if present, else Raises error
                   if list[int] is passed, check if cards are present in the card manager; removes if all are present, else raises error

        Returns:
            list of removed cards

            (updates self.__cards internally)
        """
        if isinstance(value, int):
            cards_to_be_removed = {value}

        elif isinstance(value, list):
            if not all(isinstance(v, int) for v in value):
                logging_manager.error("All elements in the value list must be ints. Discarding operation")
                raise ValueError

            if len(value) != len(set(value)):
                logging_manager.error("Duplicate values in removing cards; values must be unique. Discarding operation")
                raise ValueError

            cards_to_be_removed = set(value)

        else:
            logging_manager.error("Value must be int or a list of ints")
            raise ValueError

        
        present_cards = {card.value for card in self.__cards}
        missing_cards = cards_to_be_removed - present_cards

        if missing_cards:
            logging_manager.error(f"Requested card(s) with value(s) {sorted(missing_cards)} not found in hand.  Discarding operation")
            raise ValueError(f"Card(s) with value(s) {sorted(missing_cards)} not found in hand.  Discarding operation")

        
        removed_cards: list[MoneyCard] = []
        remaining_cards: list[MoneyCard] = []

        for card in self.__cards:
            if card.value in cards_to_be_removed:
                removed_cards.append(card)

            else:
                remaining_cards.append(card)

        self.__cards = remaining_cards
        return removed_cards

    def total_money(self) -> int:
        """
        Calculates total amount of money left in the moneycardmanager object

        Returns: 
            int -> Amount of money in cards
        """
        money = 0
        for card in self.__cards:
            money += card.value

        return money


    def __repr__(self):
        return f"MoneyCardManager(num_cards = {len(self.__cards)})"


    

    




