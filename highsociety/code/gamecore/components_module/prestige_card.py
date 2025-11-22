from highsociety.code.gamecore.components_module.status_card import StatusCard

class PrestigeCard(StatusCard):
    def __init__(self) -> None:
        super().__init__(value = 0, 
                         multiplier = 2, 
                         is_green = True,
                         description = "Prestige Card with 2x multiplier")

    def __repr__(self):
        return f"PrestigeCard(value=0, multiplier=2, color=green)"