import random
from highsociety.code.gamecore.components_module.card_factory import CardFactory
from highsociety.code.gamecore.components_module.status_card import StatusCard
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager
from highsociety.code.gamecore.utils.utility import get_game_setting_configurations, get_all_configurations

config = get_all_configurations()
LoggingManager = LoggingManager(config)


class StatusCardManager:
    """
    This class manages status cards in the game. It is a singleton class.

    Usage:
        Objects will have access to the following methods:
            - remove_top_card(): Used to remove the top card during the start of an auction
            - get_card_count(): Remaining number of cards in the deck (int)
            - is_empty(): If the deck/stack is empty, returns True.

        Shuffles the cards when initialized by default.

        Attributes:
            None
    """
    _instance = None 

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(StatusCardManager, cls).__new__(cls)
            cls._instance.__cards = []
            cls._instance.__initialize_status_cards()
            cls._instance.__shuffle_cards()

        return cls._instance

    def __initialize_status_cards(self):
        card_factory = CardFactory()
        game_config = get_game_setting_configurations()
        
        if not game_config:
            LoggingManager.error("Failed to load game configuration")
            return

        painting_values = game_config.get("painting_values", [])
        num_prestige_cards = game_config.get("prestige_card_count", 0)
        disgrace_card_types = game_config.get("disgrace_card_types", [])

        # Create painting cards
        for value in painting_values:
            try:
                painting_card = card_factory.create_card("painting", value=value)
                self.__cards.append(painting_card)
            except Exception as e:
                LoggingManager.error(f"Error creating painting card with value {value}: {e}")

        # Create prestige cards
        for i in range(num_prestige_cards):
            try:
                prestige_card = card_factory.create_card("prestige")
                self.__cards.append(prestige_card)
            except Exception as e:
                LoggingManager.error(f"Error creating prestige card {i}: {e}")

        # Create disgrace cards
        for disgrace_card_type in disgrace_card_types:
            try:
                disgrace_card = card_factory.create_card(disgrace_card_type)
                self.__cards.append(disgrace_card)
            except Exception as e:
                LoggingManager.error(f"Error creating disgrace card {disgrace_card_type}: {e}")

        LoggingManager.info(f"Initialized {len(self.__cards)} status cards")
    

    def __shuffle_cards(self) -> None:
        """Shuffle the cards randomly."""
        random.shuffle(self.__cards)
        LoggingManager.info("Cards shuffled")

    def remove_top_card(self) -> StatusCard:
        """Remove and return the top card from the deck."""
        if not self.__cards:
            LoggingManager.error("No Cards available to remove the top card")
            raise IndexError("No cards available")

        removed_card =  self.__cards.pop(0)
        LoggingManager.info(f"Remaining {self.get_card_count()} status cards")
        return removed_card
        
    
    def get_card_count(self) -> int:
        """Get the number of cards in the deck."""
        return len(self.__cards)
    
    def is_empty(self) -> bool:
        """Check if the deck is empty."""
        return len(self.__cards) == 0
    



    

