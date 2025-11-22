from abc import ABC, abstractmethod

class Card(ABC):
    '''
    Abstract class for a card
    '''
    @abstractmethod
    def description(self) -> str:
        pass