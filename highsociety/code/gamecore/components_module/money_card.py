from abc import ABC, abstractmethod
from highsociety.code.gamecore.components_module.card import Card
from dataclasses import dataclass

@dataclass(frozen = True) #prevents reinitalizing attributes inside
class MoneyCard(Card):
    value:int
    
    @property
    def description(self) -> str:
        return f"Money Card with value {self.value}"

    def __repr__(self) -> str:
        return f"MoneyCard(value={self.value})"
    

    
    # def __str__(self):
    #     return f"MoneyCard"