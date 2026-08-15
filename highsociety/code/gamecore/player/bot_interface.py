import time
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

    # Wired up by PlayGame.__init__ to the same list it appends AuctionRecord
    # objects to as auctions conclude, so this stays live for the rest of the
    # game with no further plumbing. The class-level default here only
    # covers a player/bot instantiated standalone, outside any PlayGame (e.g.
    # in a unit test) — get_auction_history() just reads whatever this
    # currently points to.
    _auction_history_source: list = []

    # Same live-reference pattern as _auction_history_source above, wired up
    # by PlayGame.__init__ alongside it — see get_current_auction_history()/
    # get_live_auction_state() below. None/empty class-level defaults cover
    # a player/bot instantiated standalone, outside any PlayGame.
    _auction_history_snapshot_source = None
    _live_auction_state_source: dict = {}

    # Matches PlayGame.MIN_TOAST_GAP_SECONDS / the web frontend's
    # TOAST_DURATION_MS+fade-out gap (highsociety/web/static/app.js) --
    # duplicated rather than imported, since gameplay.py imports player
    # classes, not the other way around. A timed room already gets this
    # floor for free from PlayGame's own toast pacing; the gap this closes
    # is routine, non-round-ending bot turns in an UNTIMED room, which
    # previously had no floor at all beyond whatever --bot-think-time
    # happened to be set to (0 included) -- every toast-worthy broadcast
    # there was paced by bot decision speed alone.
    MIN_THINK_TIME_SECONDS = 1.8

    def _pace_think_time(self, timeout: Optional[float] = None) -> None:
        """
        Every bot subclass should call this instead of sleeping on its own
        `_think_time` directly, so the floor above is enforced in exactly
        one place. Assumes `self._think_time` is set (every current bot
        constructor sets it) -- falls back to 0 rather than raising if a
        future one doesn't, since a missing think_time shouldn't be fatal.

        timeout: seconds actually left on this player's own turn clock —
        pass get_bid()'s own `timeout` straight through (None for an
        untimed room). Without this, the floor above was itself capable of
        blowing through the real per-move deadline every human player's
        clock counts down to (e.g. a host-configured --bot-think-time
        larger than a short turn_time_limit) -- respecting the actual
        deadline takes priority over this pause's own "feels human"
        polish, so it's capped, never skipped outright.
        """
        desired = max(getattr(self, "_think_time", 0), self.MIN_THINK_TIME_SECONDS)
        if timeout is not None:
            desired = min(desired, max(timeout, 0))
        time.sleep(desired)

    def get_auction_history(self) -> list[dict]:
        """
        Every completed auction so far this game, oldest first, as the same
        JSON-serializable dicts PlayGame.get_auction_history() returns (see
        AuctionRecord.to_dict() in game_manager/auction_information.py).

        Available on every player/bot automatically, not just ones that use
        it today — so a future bot strategy that wants to react to past
        auctions (opponents' bidding patterns, what's already gone by, etc.)
        can call self.get_auction_history() without any interface change.
        """
        return [record.to_dict() for record in self._auction_history_source]

    def get_current_auction_history(self):
        """
        The room's AuctionHistory object (see game_manager/auction_history.py)
        — an aggregated snapshot of every player's *current* state (money
        cards, status cards held, points, Faux Pas status), refreshed after
        every turn. None if the caller (e.g. network_server.py's CLI/socket
        path) didn't configure one for this game.

        This is what lets a decision be made as a pure function of "what's
        true right now" instead of accumulating state across the game the
        way earlier bot implementations did — see MCTSBot for the intended
        usage pattern.
        """
        return self._auction_history_snapshot_source

    def get_live_auction_state(self) -> dict:
        """
        A snapshot of "what's true right now" for the current auction —
        round number, card up for bid, current highest bid, whose turn.
        Same shape/data as PlayGame.get_live_auction_state(), just reachable
        from the player/bot side without holding a reference to the whole
        PlayGame object.
        """
        return dict(self._live_auction_state_source)

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
