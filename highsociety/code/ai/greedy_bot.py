import re
from typing import Optional, Union

from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.player.player import BasePlayer

_HIGHEST_BID_RE = re.compile(r"Current Highest Bid:\s*(\d+)")


class GreedyBot(BasePlayer):
    """
    Stays in every auction for as little money as possible: each turn, raises
    by the smallest single money card that beats the current highest bid, and
    passes once no single card in hand is big enough to do that. Never
    combines multiple cards into one bid and never bids more than the minimum
    required to lead.

    There's no direct API on BasePlayer for "the current highest bid" (it's a
    local variable inside gameplay.py's auction loop, never stored anywhere
    else) — gameplay.py sends it as a human-readable "Current Highest Bid: N"
    PLAYER_INFO message right before every get_bid() call, the same way a
    CLIPlayer's human reads it off their terminal, so this bot parses that
    message instead.
    """

    def __init__(self, name: str, username: str, think_time: float = 0) -> None:
        """
        think_time: seconds to pause before returning a decision from
        get_bid(). 0 (default) decides instantly, which is what every test
        and real game wants; pass e.g. 1 only for a human-watchable
        simulation, where an instant decision reads as no decision at all.
        """
        super().__init__(name, username)
        self.active = True
        self._current_highest_bid = 0
        self._think_time = think_time

    def send_message(self, message: str, message_type: str = None, created_at: float = None, **kwargs) -> None:
        if message_type == "PLAYER_INFO":
            match = _HIGHEST_BID_RE.search(message)
            if match:
                self._current_highest_bid = int(match.group(1))

    def get_bid(self, timeout: Optional[float] = None) -> Union[list[int], str, None]:
        # Smallest single card value that would push our total bid strictly
        # above the current highest bid, given what we've already committed
        # this auction. E.g. highest=2, nothing committed yet -> any card
        # >=3 works, and we want the cheapest one we actually have.
        needed = self._current_highest_bid - self.current_bid_value + 1
        affordable = [c.value for c in self.money_cards if c.value >= needed]
        self._pace_think_time(timeout)
        if not affordable:
            return "pass"
        return [min(affordable)]

    def choose_painting_to_discard(self) -> Optional[Painting]:
        paintings = [c for c in self.status_cards if isinstance(c, Painting)]
        return paintings[0] if paintings else None
