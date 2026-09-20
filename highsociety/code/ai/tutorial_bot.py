import re
from typing import Optional, Union

from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.player.player import BasePlayer

_HIGHEST_BID_RE = re.compile(r"Current Highest Bid:\s*(\d+)")
_AUCTIONED_CARD_RE = re.compile(r"Auctioning:\s*(\w+)\s*\(value=(-?\d+)\)")

_DISGRACE_TYPES = frozenset({"FauxPas", "Passe", "Scandale"})

# Same shape as CappedGreedyBot's _FLAT_SPEND_LIMITS (see its own comment) --
# deliberately a bit tighter here, since the point of this bot isn't to play
# well, it's to leave a real human room to win most auctions while still
# contesting them for real money, so a first game actually feels like a
# negotiation rather than a free ride.
_FLAT_SPEND_LIMITS = {
    "PrestigeCard": 12,
    "FauxPas": 6,
    "Passe": 12,
    "Scandale": 15,
}


class TutorialBot(BasePlayer):
    """
    Deliberately scripted, non-random opponent for the guided first-game
    tutorial (see web_server.py's /api/create_game "tutorial" branch and
    ai/__init__.py's create_tutorial_bots) -- never registered in BOT_TYPES,
    so a real host can never pick this bot for a real game.

    Two properties make this suitable for teaching rather than just another
    difficulty tier:

    1. Fully rule-based, with no call to `random` anywhere -- unlike the
       MCTS bots, this can never perturb the seeded card sequence a
       tutorial game is curated around (see PlayGame.__init__'s seeding
       order), and its own decisions are 100% reproducible for the exact
       same run of bids from the human.
    2. On a disgrace auction specifically, raises at most once before
       always passing for the rest of that auction (_disgrace_raise_limit)
       -- a human who keeps pushing past that point always succeeds in
       dumping the card on this bot, so the "should I keep raising to
       avoid this disgrace card, or let it go?" trade-off the How to Play
       screen teaches (see _how_to_play_rich.html's disgrace cue card) is
       something the human actually gets to resolve for themselves, not
       something this bot decides for them by refusing to ever back down.
    """

    # How many times this bot will raise during one disgrace auction before
    # unconditionally passing for the rest of it, regardless of money left
    # or how cheap a raise would be.
    _disgrace_raise_limit = 1

    def __init__(self, name: str, username: str, think_time: float = 0) -> None:
        """
        think_time: seconds to pause before returning a decision from
        get_bid(). A real tutorial game passes something human-watchable
        (see create_tutorial_bots) rather than the 0 every test/normal
        game wants, so a brand-new player can actually follow along
        instead of watching both bots resolve an auction instantly.
        """
        super().__init__(name, username)
        self.active = True
        self._current_highest_bid = 0
        self._max_spend = float("inf")
        self._is_disgrace = False
        self._raises_this_auction = 0
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
            self._is_disgrace = card_type in _DISGRACE_TYPES
            self._raises_this_auction = 0

    @staticmethod
    def _spend_limit(card_type: str, card_value: int) -> float:
        if card_type == "Painting":
            return 2.5 * card_value
        return _FLAT_SPEND_LIMITS.get(card_type, float("inf"))

    def get_bid(self, timeout: Optional[float] = None) -> Union[list[int], str, None]:
        if self._is_disgrace and self._raises_this_auction >= self._disgrace_raise_limit:
            self._pace_think_time(timeout)
            return "pass"

        needed = self._current_highest_bid - self.current_bid_value + 1
        affordable = [
            c.value for c in self.money_cards
            if c.value >= needed and self.current_bid_value + c.value <= self._max_spend
        ]
        self._pace_think_time(timeout)
        if not affordable:
            return "pass"
        self._raises_this_auction += 1
        return [min(affordable)]

    def choose_painting_to_discard(self) -> Optional[Painting]:
        paintings = [c for c in self.status_cards if isinstance(c, Painting)]
        return paintings[0] if paintings else None
