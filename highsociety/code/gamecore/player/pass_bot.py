from typing import Optional, Union

from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.player.player import BasePlayer


class PassBot(BasePlayer):
    """
    The simplest possible bot: always passes, in every auction, regardless
    of card, current bid, or anything else. Useful as a baseline opponent
    for testing other bots against.
    """

    def __init__(self, name: str, username: str) -> None:
        super().__init__(name, username)
        self.active = True

    def get_bid(self, timeout: Optional[float] = None) -> Union[list[int], str, None]:
        return "pass"

    def choose_painting_to_discard(self) -> Optional[Painting]:
        paintings = [c for c in self.status_cards if isinstance(c, Painting)]
        return paintings[0] if paintings else None

    def send_message(self, message: str, message_type: str = None, created_at: float = None, **kwargs) -> None:
        pass
