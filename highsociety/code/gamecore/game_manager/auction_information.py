"""
A complete, structured record of one status-card auction — designed to be
read by a program (a bot), not just a human. Every field is a plain string/
int/bool/None so `AuctionRecord.to_dict()` is directly JSON-serializable
with no further conversion, and `PlayGame.auction_rounds` /
`PlayGame.get_auction_history()` are the two places to find these.

See PlayGame.get_auction_history() for the local/embedded API, and
BOT_API.md for how this same data arrives over the network as an
AUCTION_RESULT message for a remote bot.
"""
from dataclasses import dataclass, field
from typing import Optional

from highsociety.code.gamecore.components_module.status_card import StatusCard


@dataclass
class BidEvent:
    """One action a single player took during an auction, in the order it happened."""

    player: str                    # the acting player's username
    action: str                    # "bid" | "pass" | "fold" | "quit"
    amount: Optional[int] = None   # the player's total bid *after* this action; only set when action == "bid"

    def to_dict(self) -> dict:
        return {"player": self.player, "action": self.action, "amount": self.amount}


@dataclass
class AuctionRecord:
    """
    Everything that happened in one status-card auction, from the card being
    revealed to someone ending up with it.

    Fields:
        round_number: 1-indexed position of this auction in the game (the
            Nth status card auctioned so far, including this one).
        auction_type: "normal" (usual highest-bid-wins auction) or
            "disgrace" (FauxPas/Passe/Scandale — first player to pass takes
            the card; see card.type for which one).
        card: {"type", "value", "multiplier", "is_green", "description"} —
            card.type is the class name (e.g. "Painting", "PrestigeCard",
            "FauxPas", "Passe", "Scandale").
        events: the full turn-by-turn sequence, in order. Replay this list
            to reconstruct exactly how the price got to where it did —
            useful for a bot studying opponents' bidding patterns, not just
            the final outcome.
        recipient: username of the player who ended up with the card, or
            None if a normal auction had no active bidders at all (the card
            went to nobody).
        price_paid: how much `recipient` actually paid. For a normal
            auction, this is their winning bid. For a disgrace auction this
            is always 0 — the recipient is whoever passed, and passing
            refunds their own committed bids; see `events` to see what
            *other* players forfeited trying to avoid taking the card
            (highsociety.code.gamecore.game_manager.disgrace_settlement).
    """

    round_number: int
    auction_type: str
    card: dict
    events: list = field(default_factory=list)
    recipient: Optional[str] = None
    price_paid: int = 0

    def add_event(self, player: str, action: str, amount: Optional[int] = None) -> None:
        self.events.append(BidEvent(player=player, action=action, amount=amount))

    def to_dict(self) -> dict:
        return {
            "round_number": self.round_number,
            "auction_type": self.auction_type,
            "card": self.card,
            "events": [e.to_dict() for e in self.events],
            "recipient": self.recipient,
            "price_paid": self.price_paid,
        }


def summarize_card(card: StatusCard) -> dict:
    """The `card` field of an AuctionRecord — a plain-data snapshot of a StatusCard."""
    return {
        "type": type(card).__name__,
        "value": card.value,
        "multiplier": card.multiplier,
        "is_green": card.is_green,
        "description": card.description,
    }
