from abc import ABC, abstractmethod
from typing import Optional, Union
from highsociety.code.gamecore.components_module.painting import Painting


class BotInterface(ABC):
    """
    The contract PlayGame relies on for whoever is sitting in a player seat.
    CLIPlayer and NetworkPlayer both satisfy it today; a bot (embedded or
    remote-over-the-wire, see BOT_API.md) needs to satisfy it too.

    To write an embedded bot: subclass BasePlayer (it gives you bidding/card
    bookkeeping — place_bid, withdraw_bid, add_status_card, money_left,
    etc. — for free) and implement the three methods below. BasePlayer
    itself does not implement them, so instantiating a subclass that skips
    one raises TypeError immediately, not a confusing failure mid-game.

    One more thing this class can't enforce but is still mandatory: set
    `self.active = True` in your `__init__`. PlayGame reads/writes it
    directly (flips it to False itself on disconnect or "quit", and skips
    anyone with active=False when addressing turns/broadcasts) — it can't be
    a Python property backed by this ABC, since PlayGame also assigns to it
    directly (`player.active = False`), so a plain instance attribute is
    what every existing implementation uses.
    """

    @abstractmethod
    def get_bid(self, timeout: Optional[float] = None) -> Union[list[int], str, None]:
        """
        Called on your turn during an auction (normal or disgrace). Must
        return one of:

        - list[int]: the money-card VALUES you're bidding, e.g. [1, 4] to
          bid the cards worth 1 and 4 at once (their sum becomes your total
          bid). Each value must currently be in your own money_cards.
        - "pass" / "fold": withdraw from the current auction.
        - "quit": leave the game entirely.
        - None: no decision yet. PlayGame will call get_bid() again — only
          return this if you're genuinely still waiting (e.g. on I/O); a
          bot that always has an answer ready never needs to return None.

        `timeout` is the seconds remaining in the current turn, or None for
        no deadline. An invalid or losing bid isn't an error — the caller
        just prompts you again, so there's nothing to catch here.
        """
        raise NotImplementedError

    @abstractmethod
    def choose_painting_to_discard(self) -> Optional[Painting]:
        """
        Called right after you win an auction while already holding a
        FauxPas. Must return one of your own Painting status cards (pick
        from `self.status_cards`), or None if you hold no paintings to
        discard.
        """
        raise NotImplementedError

    @abstractmethod
    def send_message(
        self,
        message: str,
        message_type: str = None,
        created_at: float = None,
        **kwargs,
    ) -> None:
        """
        Called by PlayGame to narrate everything — your own turn prompts,
        other players' turns, auction results, final standings, and more.
        See BOT_API.md's message_type table for what you actually need to
        act on (PLAYER_MOVE) versus what's purely informational.

        You must implement this even if your bot only reacts to
        PLAYER_MOVE and ignores the rest — PlayGame calls it unconditionally,
        so a no-op body (`pass`) is a completely valid implementation.

        `**kwargs` exists because NetworkPlayer's implementation accepts a
        few network-specific extras (from_user, to_users, data, move_type)
        that an embedded bot has no use for — accept and ignore them rather
        than narrowing the signature.
        """
        raise NotImplementedError
