from typing import List, Union
from highsociety.code.gamecore.components_module.money_card import MoneyCard
from highsociety.code.gamecore.components_module.prestige_card import PrestigeCard
from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.components_module.disgrace_card import FauxPas, Passe, Scandale
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager
from highsociety.code.gamecore.utils.utility import get_all_configurations


class CardFactory:
    '''
    Generates a card based on the card type and arguments

    Money card: Has Money Value eg: 10, 20, 50 (args)
    Prestige card: 2x multiplier (no args)
    Painting: Is the game point value card (args)
    FauxPas: Disgrace card: Discard a card (no args)
    Passe: Disgrace card: -5 points from total (no args)
    Scandale: Disgrace card: Half points from total (no args)

    '''
    def __init__(self) -> None:
        self.card_types = {
            "money": MoneyCard,
            "prestige": PrestigeCard,
            "painting": Painting,
            "faux_pas": FauxPas,
            "passe": Passe,
            "scandale": Scandale
        }

    def __validate_card_type(self, card_type: str) -> str:
        card_type = card_type.lower()

        if card_type not in self.card_types:
            logging_manager.error(f"Invalid Card Type {card_type}")
            raise ValueError(f"{card_type} is invalid")
        
        return card_type
    
    def create_card(self, card_type: str, *args, **kwargs) -> Union[MoneyCard, PrestigeCard, Painting, FauxPas, Passe, Scandale]:
        """
        Creates card of {card_type} and Returns it. Functions as a factory to create any type of cards

        Args:
            MoneyCard -> Value,
            PrestigeCard -> no args,
            Painting -> Value,
            FauxPas -> No args,
            Passe -> No Args,
            Scandale -> No args

        Returns:
            Card of class {card_type}

        Usage:
            c = CardFactory()
            c.create_card("money", value = 2)
            c.create_card("painting", value =2)
            c.create_card("faux_pas")
            c.create_card("passe")
            c.create_card("scandale")
            c.create_card("prestige")

        """
        card_type = self.__validate_card_type(card_type)
        return self.card_types[card_type](*args, **kwargs)
    
