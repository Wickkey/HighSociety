from highsociety.code.gamecore.components_module.status_card import StatusCard

class Painting(StatusCard):
    def __init__(self, value) -> None:
        super().__init__(value = value, 
                         multiplier = 1, 
                         is_green = False,
                         description = f"Painting Card with value {value}")

    def __repr__(self):
        return f"Painting(value={self.value})"