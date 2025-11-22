from highsociety.code.gamecore.components_module.card import Card
from abc import ABC, abstractmethod

class StatusCard(Card):
    def __init__(self, value: int, multiplier: int, is_green: bool, description: str) -> None:
        self.__value = value
        self.__multiplier = multiplier
        self.__is_green = is_green
        self.__description = description

    @property
    def value(self) -> int:
        return self.__value
    
    @property
    def multiplier(self) -> int:
        return self.__multiplier
    
    @property
    def is_green(self) -> bool:
        return self.__is_green
    
    @property
    def description(self) -> str:
        return self.__description