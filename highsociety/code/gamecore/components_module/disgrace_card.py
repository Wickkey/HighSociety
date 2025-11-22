from highsociety.code.gamecore.components_module.status_card import StatusCard
from highsociety.code.gamecore.components_module.painting import Painting
from typing import Union, Optional

class DisgraceCard(StatusCard):
    '''
    Disgrace card: A card that has a negative effect on the player
    This will be the base class for all disgrace cards
    '''
    def __init__(self, value: int, multiplier: int, is_green: bool, description: str) -> None:
        super().__init__(value = value, 
                         multiplier = multiplier, 
                         is_green = is_green, 
                         description = description)
    

class FauxPas(DisgraceCard):
    '''
    Faux Pas card: A card that has the effect of discarding a painting
    '''
    def __init__(self) -> None:
        super().__init__(value = 0, 
                         multiplier = 1, 
                         is_green = False, 
                         description = "discard a painting card right now if you have a non empty hand. If you don't have any paintings, discard the next painting you draw. Can you afford to lose a painting?")
    
    def __repr__(self):
        return f"FauxPas()"
    

class Passe(DisgraceCard):
    '''
    Passe card: A card that has the effect of -5 points from total
    '''
    def __init__(self) -> None:
        super().__init__(value = -5,
                         multiplier= 1, 
                         is_green = False, 
                         description = "Deducts 5 points from total, are you willing to take the risk?")

    def __repr__(self):
        return f"Passe(value= -5)"
    

class Scandale(DisgraceCard):
    '''
    Scandale card: A card that has the effect of half points from total. 
    This card is green
    '''
    def __init__(self) -> None:
        super().__init__(value = 0,
                         multiplier= 0.5, 
                         is_green = True, 
                         description = "Halves the total points. ")


    def __repr__(self):
        return f"Scandale(multiplier = 0.5, color=green)"
