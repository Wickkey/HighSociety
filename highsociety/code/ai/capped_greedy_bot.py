import re
from typing import Optional, Union

from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.player.player import BasePlayer

_HIGHEST_BID_RE = re.compile(r"Current Highest Bid:\s*(\d+)")
_AUCTIONED_CARD_RE = re.compile(r"Auctioning:\s*(\w+)\s*\(value=(-?\d+)\)")

# Flat spend caps for card types whose value doesn't scale the budget.
# Disgrace cards are a loss to avoid rather than a prize to win, so their
# caps are "how much I'll pay to dodge this," not a valuation of the card.
_FLAT_SPEND_LIMITS = {
    "PrestigeCard": 15,
    "FauxPas": 8,     # disgrace: discard a painting
    "Passe": 15,      # disgrace: -5 points
    "Scandale": 20,   # disgrace: halves points
}


class CappedGreedyBot(BasePlayer):
    """
    Like GreedyBot (raises with the single cheapest available card that
    beats the current highest bid, never combining cards), but refuses to
    let its own committed total for the current auction exceed a budget
    that depends on what's actually up for auction:

    - Painting: up to 3.5x the painting's own point value.
    - PrestigeCard: up to 15 flat.
    - Disgrace cards: FauxPas up to 8, Passe up to 15, Scandale up to 20.

    Passes the moment leading would push its total committed spend this
    auction past that budget -- whether the current highest bid already
    exceeds it, or the cheapest card that would beat it would push the
    total over.
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
        self._max_spend = float("inf")
        self._think_time = think_time

    def send_message(self, message: str, message_type: str = None, created_at: float = None, **kwargs) -> None:
        if message_type != "PLAYER_INFO":
            return

        bid_match = _HIGHEST_BID_RE.search(message)
        if bid_match:
            self._current_highest_bid = int(bid_match.group(1))

        card_match = _AUCTIONED_CARD_RE.search(message)
        if card_match:
            card_type, card_value = card_match.group(1), int(card_match.group(2))
            self._max_spend = self._spend_limit(card_type, card_value)

    @staticmethod
    def _spend_limit(card_type: str, card_value: int) -> float:
        if card_type == "Painting":
            return 3.5 * card_value
        return _FLAT_SPEND_LIMITS.get(card_type, float("inf"))

    def get_bid(self, timeout: Optional[float] = None) -> Union[list[int], str, None]:
        needed = self._current_highest_bid - self.current_bid_value + 1
        affordable = [
            c.value for c in self.money_cards
            if c.value >= needed and self.current_bid_value + c.value <= self._max_spend
        ]
        self._pace_think_time(timeout)
        if not affordable:
            return "pass"
        return [min(affordable)]

    def choose_painting_to_discard(self) -> Optional[Painting]:
        paintings = [c for c in self.status_cards if isinstance(c, Painting)]
        return paintings[0] if paintings else None
