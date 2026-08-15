import random
import time
from typing import Optional, Union
from highsociety.code.gamecore.player.networkspectator import NetworkSpectator
from highsociety.code.gamecore.player.player import BasePlayer
from highsociety.code.gamecore.player.cliplayer import CLIPlayer
from highsociety.code.gamecore.player.networkplayer import NetworkPlayer
from highsociety.code.gamecore.card_manager.status_card_manager import StatusCardManager
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager
from highsociety.code.common.utils.utility import get_game_setting_configurations
from highsociety.code.gamecore.components_module.disgrace_card import DisgraceCard, FauxPas
from highsociety.code.gamecore.game_manager.host import CLIHost, NetworkHost
from highsociety.code.gamecore.components_module.status_card import StatusCard
from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.game_manager.disgrace_settlement import DisgraceAuctionSettlement, ForfeitSettlement
from highsociety.code.gamecore.game_manager.auction_information import AuctionRecord, summarize_card
from highsociety.code.gamecore.game_manager.auction_history import AuctionHistory
from highsociety.code.gamecore.game_manager.turn_clock import TurnClock

# Distinguishes "turn_duration not passed at all" (use HSConfig.json's
# time_per_move, today null/no limit — the CLI/network_server.py behavior,
# unchanged) from "explicitly passed as None" (a caller, e.g. web_server.py,
# deliberately wants no timer for this specific game) — plain `None` as the
# default couldn't tell those two cases apart.
_TURN_DURATION_UNSET = object()


class PlayGame():
    # Floors the gap between consecutive "toast-worthy" broadcasts (see
    # _pace_toast_event) at this many seconds, matching the web frontend's
    # real toast display cadence: app.js's TOAST_DURATION_MS (1500ms) plus
    # its 250ms fade-out gap before the next queued toast can show, so
    # ~1.75s minimum — with a small safety margin.
    MIN_TOAST_GAP_SECONDS = 1.8

    # An auction result ("X bought Y for Z") packs in more to actually read
    # than a routine bid/pass update, so the client gives it a longer toast
    # (app.js's RESULT_TOAST_DURATION_MS, 3000ms) — this is that same
    # duration plus the fade-out gap, so whatever comes next (the next
    # auction_start, or a green-card reveal) doesn't arrive while a human's
    # still reading who just won. See _broadcast_auction_result.
    RESULT_TOAST_GAP_SECONDS = 3.3

    # AUCTION_UPDATE kinds that actually produce a toast client-side (see
    # applyAuctionUpdate in app.js) — turn_start is received but not
    # toast-worthy, so it shouldn't consume/extend the pacing clock.
    _TOAST_UPDATE_KINDS = frozenset({"auction_start", "bid", "pass", "fold", "quit"})

    """
    Notes:
    spectators is a shared list, which will get updated in the run-time if new specators join.
    """
    def __init__(self, players: list[BasePlayer], spectators: list[NetworkSpectator] = None, mode = 'cli', game_id: str = None,
                 disgrace_settlement: DisgraceAuctionSettlement = None, seed: Optional[int] = None,
                 turn_duration: Optional[float] = _TURN_DURATION_UNSET,
                 auction_history: Optional[AuctionHistory] = None):
        """
        seed: if given, seeds the RNG before anything random happens (deck
        shuffle here, then player shuffle / starting-player pick in
        play_game()), making the entire game 100% reproducible — same seed +
        same sequence of player decisions always produces the same game.

        turn_duration: seconds each player gets per move, or None for no
        limit. Defaults to HSConfig.json's game_settings.rules.time_per_move
        (today null, i.e. no limit) when not given at all — this is what CLI
        play and network_server.py get. Pass an explicit value (including
        None) to override that per-game, e.g. web_server.py letting a host
        pick a per-move timer from the lobby form.

        auction_history: an AuctionHistory instance to keep refreshed after
        every turn (see _record_auction_history_snapshot), or None to skip
        entirely — CLI/network_server.py callers that have no use for it
        today just omit it, at zero cost.
        """
        self.players = players
        self.spectators = spectators
        self.game_id = game_id
        self.num_players= len(players)
        self.seed = seed

        if seed is not None:
            random.seed(seed)

        self.game_state = "initialized"
        self.auction_rounds = []
        self.auction_history = auction_history
        self.current_auction = None
        # Just enough live state for a reconnecting player's client to catch
        # up immediately (see web_server.py's reconnect handling and
        # get_live_auction_state below) instead of showing a blank auction
        # panel until the next event happens to arrive naturally. Updated
        # incrementally in _broadcast_auction_update as events fire — not a
        # full history, just "what's true right now".
        self._live_auction_state = {"round_number": 0, "card": None, "max_bid": 0, "turn_player": None}
        # Populated by determine_winner() — lets a caller holding this
        # PlayGame (e.g. web_server.py's game-runner thread) read the
        # authoritative outcome after play_game() returns, instead of
        # re-deriving the tie/elimination logic itself.
        self.winners = None
        self.final_standings = []
        self.status_card_manager = StatusCardManager()
        self.disgrace_settlement = disgrace_settlement or ForfeitSettlement()

        # Give every player/bot live access to auction history via
        # player.get_auction_history() (BotInterface) — same list object
        # self.auction_rounds appends to, so it stays current with no
        # further wiring as the game progresses.
        for player in self.players:
            player._auction_history_source = self.auction_rounds
            # Same live-reference pattern, two more sources: the aggregated
            # per-player state snapshot (get_current_auction_history()) and
            # the live current-auction state (get_live_auction_state()) —
            # together these are what let a bot make each decision as a
            # pure function of "what's true right now" instead of
            # accumulating its own state across the game (see MCTSBot).
            player._auction_history_snapshot_source = self.auction_history
            player._live_auction_state_source = self._live_auction_state

        self.__game_config = get_game_setting_configurations()
        if turn_duration is _TURN_DURATION_UNSET:
            self.__TURN_DURATION = self.__game_config['time_per_move']
        else:
            self.__TURN_DURATION = turn_duration
        self.__green_card_limit = self.__game_config.get("green_card_limit", 4)
        self._last_toast_broadcast_at = 0.0

        if mode.lower() == 'cli':
            self.host = CLIHost(players)

        elif mode.lower() == 'network':
            self.host = NetworkHost(players, spectators)

        else:
            LoggingManager.info("Invalid Host. Default host: cli")
            self.host = CLIHost()

    @property
    def turn_duration(self) -> Optional[float]:
        """Seconds per move after resolving _TURN_DURATION_UNSET against
        HSConfig.json's default (see __init__) — read-only outside this
        class; TurnClock is what actually turns this into a live deadline."""
        return self.__TURN_DURATION

    def get_auction_history(self) -> list[dict]:
        """
        Every completed auction so far, oldest first, as plain JSON-serializable
        dicts (see AuctionRecord.to_dict()). This is the canonical way to read
        auction history for a locally-embedded bot using the Python API
        directly; a remote bot gets the same data pushed as an AUCTION_RESULT
        message right after each auction concludes (see BOT_API.md).
        """
        return [record.to_dict() for record in self.auction_rounds]

    def get_next_player_id(self, current_player_id: int) -> int:
        """
        Get the next player id in a circular manner.
        Args:
            current_player_id: The starting player id
            idx: The index of the player to get the next player id

        Returns:
            The next player id
        """
        return (current_player_id + 1) % self.num_players


    def shuffle_players(self) -> list[BasePlayer]:
        # Sorted first, not shuffled in whatever order self.players already
        # happens to be in: for a web room, humans get appended to that list
        # in the real-world order their own WebSocket connection happens to
        # finish joining (see web_server.py's ws_player) -- not something
        # the game's own seed has any influence over. Without this, the SAME
        # seed could still produce a DIFFERENT final turn order across two
        # "reproductions" of the same game purely because two humans
        # happened to connect in a different relative order, defeating the
        # entire point of a seed being reproducible.
        #
        # Sorted by username, but ONLY among humans (CLIPlayer/NetworkPlayer)
        # -- a bot's username is itself randomly assigned at creation (see
        # highsociety/code/ai/bot_names.py), not tied to the game's own seed
        # at all, so sorting bots by name would make bot ordering *less*
        # deterministic across "reproductions", not more (confirmed: broke
        # tests/test_bot_evaluator.py's own reproducibility test the first
        # time this was tried). All bots instead share one constant sort key,
        # so Python's stable sort leaves them in their existing relative
        # order -- already fully deterministic, since create_bot_players()
        # builds them in bot_mix's own fixed order at room-creation time,
        # before any human has even joined.
        self.players.sort(key=lambda p: (
            isinstance(p, (CLIPlayer, NetworkPlayer)),
            p.username if isinstance(p, (CLIPlayer, NetworkPlayer)) else "",
        ))
        random.shuffle(self.players)
        LoggingManager.info("Shuffled players")
        return self.players


    def _count_active_auction_players(self) -> int:
        return sum(1 for p in self.players if p.active and p.current_participation_in_auction)


    def _get_auction_winner(self) -> int:
        """
        Returns the id of the auction winner.
        """
        for idx, player in enumerate(self.players):
            if player.current_participation_in_auction and player.active:
                return idx
        return -1


    def _broadcast_auction_result(self, record: AuctionRecord) -> None:
        """
        Pushes the just-completed auction's full structured record to every
        player and spectator (network mode) as an AUCTION_RESULT message, so
        a remote bot has real-time access to auction history without needing
        to poll for it — see BOT_API.md. In CLI mode this is a no-op for the
        human-readable side (the win/loss line was already sent separately).
        """
        self._pace_toast_event()
        recipient_desc = record.recipient or "nobody"
        spent = record.money_spent.get(record.recipient, 0)
        summary = f"[auction_result] {record.card['type']} → {recipient_desc} for {spent}"
        self.host.send_message(summary, message_type="AUCTION_RESULT", data=record.to_dict())
        # Push the pacing clock out further than a normal toast-worthy event
        # would (see RESULT_TOAST_GAP_SECONDS) so whatever's broadcast next
        # waits long enough for this result to actually be read first.
        self._last_toast_broadcast_at = time.time() + (self.RESULT_TOAST_GAP_SECONDS - self.MIN_TOAST_GAP_SECONDS)

    def _broadcast_auction_update(self, kind: str, status_card: StatusCard, **extra) -> None:
        """
        Structured, UI/bot-facing companion to the human-readable narration
        sent alongside it (see network/protocol.py's AUCTION_UPDATE) — lets an
        observer (a web frontend, or any other structured client) track the
        *live* state of an in-progress auction (whose turn, current bid)
        without waiting for it to conclude (that's what AUCTION_RESULT is
        for) or regex-parsing prompt text. CLIHost skips printing this
        message type (see host.py) since it duplicates the plain-text line
        already sent right next to each call.
        """
        if kind in self._TOAST_UPDATE_KINDS:
            self._pace_toast_event()
        payload = {
            "round_number": len(self.auction_rounds) + 1,
            "kind": kind,
            "card": summarize_card(status_card),
        }
        payload.update(extra)

        self._live_auction_state["round_number"] = payload["round_number"]
        self._live_auction_state["card"] = payload["card"]
        if kind == "auction_start":
            self._live_auction_state["max_bid"] = 0
            self._live_auction_state["turn_player"] = extra.get("starting_player")
        elif kind == "turn_start":
            self._live_auction_state["turn_player"] = extra.get("player")
        if "max_bid" in extra:
            self._live_auction_state["max_bid"] = extra["max_bid"]

        self.host.send_message(f"[auction_update] {kind}", message_type="AUCTION_UPDATE", data=payload)

    def get_live_auction_state(self) -> dict:
        """A snapshot of "what's true right now" for the current auction
        (round number, card, highest bid, whose turn) — see
        web_server.py's reconnect handling, which replays this to a
        reconnecting player's client as a synthetic AUCTION_UPDATE (kind
        "sync") so their UI doesn't sit blank until the next real event."""
        return dict(self._live_auction_state)

    def _send_player_state(self, player: BasePlayer) -> None:
        """
        Pushes a fresh snapshot of one player's own hand/points/status cards
        directly to them (not broadcast) — lets a browser client's own-state
        panel update immediately after winning/losing a card or discarding a
        painting, instead of waiting for their next PLAYER_MOVE (whose
        `constraints` only covers money cards/paintings, not points).
        """
        player.send_message(
            "",
            message_type="PLAYER_STATE",
            data={
                "money_cards": [c.value for c in player.money_cards],
                "status_cards": [summarize_card(c) for c in player.status_cards],
                "points": player.points,
                "current_bid": [c.value for c in player.current_money_card_bids],
            },
        )

    def _record_auction_history_snapshot(self) -> None:
        """Refreshes self.auction_history (if one was passed in) with every
        player's current state — see normal_card_auction/disgrace_card_auction
        and handle_faux_pas_penalty for the call sites, one per resolved
        turn/outcome. A no-op when no AuctionHistory was configured."""
        if self.auction_history is not None:
            self.auction_history.record_turn(self.players)

    def _finalize_auction(self, winner_id: int, status_card: StatusCard, max_bid: int):
        if winner_id != -1:
            winner = self.players[winner_id]
            if isinstance(status_card, DisgraceCard):
                self.host.send_message(f"\n🏆 {winner.username} gets the auction for '{status_card}' by passing!")
            else:
                self.host.send_message(f"\n🏆 {winner.username} wins the auction for '{status_card}' with a bid of {max_bid}!")
            winner.add_status_card(status_card)
            self._send_player_state(winner)
        else:
            self.host.send_message("⚠️ Auction ended. No active bidders left.")

    def _pace_toast_event(self, consume: bool = True) -> None:
        """
        Floors the gap between consecutive toast-worthy broadcasts at
        MIN_TOAST_GAP_SECONDS, matching the web frontend's real toast
        display cadence. Persistent game state is never gated on this —
        only the transient narration broadcasts that feed the client's
        toast queue. Without this, several broadcasts in this file fire
        back to back with zero natural delay (e.g. a pass that immediately
        ends an auction, followed at once by that auction's result; or an
        auction's result immediately followed by the next card's green-
        reveal/auction_start) — bumping bot think_time alone can't fix
        that, since no bot decision happens in between those broadcasts.

        consume=False waits out any recent burst without stamping
        _last_toast_broadcast_at again — for a call site (see
        _handle_player_turn) that has no broadcast of its own to justify
        resetting the clock. Consuming there anyway would silently "spend"
        a pacing slot for nothing, forcing the *next* real broadcast to eat
        an extra, unearned wait it never should have needed.
        """
        now = time.time()
        wait = self._last_toast_broadcast_at + self.MIN_TOAST_GAP_SECONDS - now
        if wait > 0:
            time.sleep(wait)
        if consume:
            self._last_toast_broadcast_at = time.time()


    def _handle_player_turn(self, player: BasePlayer, max_bid: int, status_card: StatusCard) -> Union[int,str]:
        """
        Handles bid of a player.
        Ensures the bid by player is greater than max_bid.
        Ensures the bid by player is valid.

        Parameters:
            player: object of type player
            max_bid: maximum value of bid in that round
            status_card: status card for which the bid is happening.

        Returns 
            (int) bid_value by the player
        """
        # Initial message:
        created_at = time.time()
        for p in self.players:
            if not p.active:
                continue  # already quit/disconnected; nothing to notify
            if p != player:
                p.send_message(f"{player.username}'s turn. Player is playing..",
                message_type = "GLOBAL_MOVE_INFO",
                created_at = created_at)
            else:
                p.send_message(f"Your Turn!", message_type = "PLAYER_MOVE", created_at = created_at)
                # Defensive resync sent straight to this player alongside
                # their own turn prompt: guarantees their auction panel
                # (round #, card shown, "whose turn" label) matches what
                # they're about to act on, even if the broadcast
                # auction_start/turn_start for this exact turn never
                # actually lands or renders on their end (a dropped
                # WebSocket frame on a flaky connection, a client-side
                # exception mid-render, etc.) — a real, reproduced bug
                # where a player's move panel opened live and correct
                # while the auction panel above it stayed frozen on a
                # previous round's card and a stale "X's turn" label,
                # since those two panels are populated by two independent
                # messages and only one of them got through. Same message
                # shape as web_server.py's reconnect catch-up
                # (_send_reconnect_catchup/get_live_auction_state), just
                # sent proactively on every turn instead of only after a
                # reconnect. Built directly from this call's own
                # arguments (not self._live_auction_state) so it can't be
                # stale relative to *this* turn regardless of call order.
                p.send_message(
                    "", message_type="AUCTION_UPDATE",
                    data={
                        "round_number": len(self.auction_rounds) + 1,
                        "kind": "sync",
                        "card": summarize_card(status_card),
                        "max_bid": max_bid,
                        "turn_player": player.username,
                    },
                )
        for s in (self.spectators or []):
            if not s.active:
                continue
            s.send_message(f"{player.username}'s turn. Player is playing..", message_type = "GLOBAL_MOVE_INFO",
            created_at = created_at)

        self._broadcast_auction_update("turn_start", status_card, player=player.username, max_bid=max_bid)

        # "Auctioning: X" is broadcast once via self.host.send_message() at the
        # start of normal_card_auction/disgrace_card_auction, but CLIHost
        # doesn't forward broadcasts to individual players (see host.py) — so
        # a player object (human or bot) never actually receives it that way.
        # Resend it here, directly to `player`, every turn, the same way
        # Current Highest Bid already is, so a bot can key logic off which
        # card is up without needing a whole new API.
        player.send_message(f"\nAuctioning: {type(status_card).__name__} (value={status_card.value})",
                             message_type = "PLAYER_INFO")
        player.send_message(f"\nCurrent Highest Bid: {max_bid}", message_type = "PLAYER_INFO")
        player.send_message(f"You have {self.__TURN_DURATION}s to make a move.", message_type="PLAYER_INFO")
        if self.__TURN_DURATION is not None:
            # turn_start (just above) isn't a paced broadcast (see
            # _TOAST_UPDATE_KINDS) — nothing here has ever waited for the
            # client's toast queue to actually finish displaying whatever
            # led up to this turn. That's harmless with no clock running,
            # but with one, a burst of fast bot turns right before a timed
            # human turn was eating into their think time before they'd
            # even seen the card that's now up for auction (bots deciding
            # near-instantly, per bot_think_time, makes this worse, not
            # better). Only the deadline computation waits — the PLAYER_MOVE
            # itself was already sent above and reflects the real game
            # state either way.
            #
            # consume=False: this call has no broadcast of its own, so it
            # must not stamp _last_toast_broadcast_at — doing so used to
            # make this player's own next real broadcast (their bid/pass)
            # eat an extra, unearned ~1.8s wait, since the pacing clock
            # would think a toast had just fired when none actually had.
            # That extra stall, on every single turn in a timed room, was
            # exactly why toasts felt inconsistent only when a clock was
            # running.
            self._pace_toast_event(consume=False)
        clock = TurnClock(self.__TURN_DURATION)
        clock.start()

        while True:
            remaining_time = clock.remaining()
            if remaining_time is not None and remaining_time <= 0:
                player.send_message(f"⏳ Time up! Auto pass.", message_type = "PLAYER_INFO")
                player.withdraw_bid()
                return "pass"

            # Check if player is still active before getting bid
            if not player.active:
                player.withdraw_bid()
                return "pass"
            
            bids = player.get_bid(timeout=remaining_time)

            # If get_bid() returns None, treat as pass (shouldn't happen normally for active players)
            if bids is None:
                continue

            if isinstance(bids, str):
                cmd = bids.lower()
                if cmd in ["pass", "fold", "quit"]:
                    player.withdraw_bid()
                    return cmd
                else: # shouldn't need this ideally as it will be handled by get_bid function.
                    player.send_message("⚠️ Invalid command. Try again.", message_type = "INPUT_ERROR")
                    continue

            # Numeric bids
            bid_value = player.current_bid_value + sum(bids)
            if bid_value <= max_bid:
                needed = max_bid - player.current_bid_value + 1
                player.send_message(
                    f"⚠️ Insufficient bid — add at least {needed} more to beat the current highest bid ({max_bid}).",
                    message_type = "INPUT_ERROR")
                continue

            # Place bid
            placed_value = player.place_bid(bids)
            return placed_value


    def normal_card_auction(self, status_card: StatusCard, starting_player_id: int) -> int:
        self.host.send_message(f"\nAuctioning: {status_card}: {status_card.description}")
        self._broadcast_auction_update(
            "auction_start", status_card, auction_type="normal",
            starting_player=self.players[starting_player_id].username,
        )
        record = AuctionRecord(
            round_number=len(self.auction_rounds) + 1,
            auction_type="normal",
            card=summarize_card(status_card),
        )

        num_players_in_auction = self._count_active_auction_players()
        current_player_id = starting_player_id
        max_bid = 0
        # Exactly who's actually included in the count above (active AND
        # still participating as of right now) — reset_auction_attributes()
        # unconditionally sets current_participation_in_auction back to True
        # for *every* player ahead of each new auction, active or not, so
        # that flag alone can't tell "already inactive before this auction
        # even started" (already correctly excluded above; must not be
        # subtracted again) apart from "still counted a moment ago, but just
        # went inactive mid-loop" (see the skip branch below, which needs
        # exactly this distinction).
        counted_player_ids = {i for i, p in enumerate(self.players)
                               if p.active and p.current_participation_in_auction}

        while (num_players_in_auction > 1):
            player = self.players[current_player_id]

            if player.active == False or player.current_participation_in_auction == False:
                if player.active == False and current_player_id in counted_player_ids:
                    # Went inactive asynchronously since the count above was
                    # taken (e.g. an out-of-turn resign — see web_server.py's
                    # on_resign — or a mid-auction disconnect), rather than
                    # through their own turn's pass/fold/quit branch below.
                    # Account for their departure now, exactly once (removed
                    # from counted_player_ids, so a later skip of the same
                    # player can't re-decrement) — otherwise they'd be
                    # skipped forever without ever being subtracted, and once
                    # they're one of only two players left, the real
                    # remaining bidder would be re-prompted forever with no
                    # one left to actually out-bid.
                    counted_player_ids.discard(current_player_id)
                    num_players_in_auction -= 1
                    if num_players_in_auction <= 1:
                        break
                current_player_id = self.get_next_player_id(current_player_id)
                continue

            else:
                # Let player take a turn
                action_result = self._handle_player_turn(player, max_bid, status_card)
                # Handle result types
                if action_result in ["pass", "fold"]:
                    num_players_in_auction -= 1
                    record.add_event(player.username, action_result)
                    self.host.send_message(f"⚪ {player.username} passed.\n")
                    # Passing/folding refunds whatever this player had
                    # already committed this auction (see
                    # _handle_player_turn's withdraw_bid()) -- without this,
                    # their own client had no way to find out until their
                    # next real turn, since only PLAYER_MOVE ever populated
                    # their money display before. Sent *before*
                    # _broadcast_auction_update, which paces itself for
                    # everyone else's toast narration (see
                    # _pace_toast_event) -- that pacing has nothing to do
                    # with this player's own factual money count, so it
                    # shouldn't delay it too.
                    self._send_player_state(player)
                    self._broadcast_auction_update(action_result, status_card, player=player.username, max_bid=max_bid)

                elif action_result == "quit":
                    player.active = False
                    num_players_in_auction -= 1
                    record.add_event(player.username, "quit")
                    self.host.send_message(f"❌ {player.username} quit the game.\n")
                    self._broadcast_auction_update("quit", status_card, player=player.username, max_bid=max_bid)

                elif isinstance(action_result, int) and action_result > max_bid:
                    max_bid = action_result
                    record.add_event(player.username, "bid", max_bid,
                                      cards=[c.value for c in player.current_money_card_bids])
                    self.host.send_message(f"💰 {player.username} raised to {max_bid}.\n")
                    # See the pass/fold branch above for why this goes
                    # before the (self-pacing) broadcast, not after.
                    self._send_player_state(player)
                    self._broadcast_auction_update("bid", status_card, player=player.username, max_bid=max_bid,
                                                    cards=[c.value for c in player.current_money_card_bids])

                else:
                    # Invalid / repeated bid
                    pass

                self._record_auction_history_snapshot()

                if num_players_in_auction <= 1:
                    break

                current_player_id = self.get_next_player_id(current_player_id)

        # --- Determine Winner ---
        winner_id = self._get_auction_winner()
        self._finalize_auction(winner_id, status_card, max_bid)
        self.host.send_message(f"--- End of Auction ---\n")

        # current_bid_value is what's still committed at the table for each
        # player at this point: 0 for anyone who passed/folded/quit (their
        # bid was already refunded by _handle_player_turn), and the real
        # committed amount for the winner (whose cards are never returned).
        # Reading it here — rather than each player's money_left() — keeps
        # this auction-scoped instead of reaching into wallet state.
        record.recipient = self.players[winner_id].username if winner_id != -1 else None
        record.money_spent = {p.username: p.current_bid_value for p in self.players}
        record.cards_spent = {p.username: [c.value for c in p.current_money_card_bids] for p in self.players}
        self.auction_rounds.append(record)
        self._broadcast_auction_result(record)
        self._record_auction_history_snapshot()

        # Reset auction state
        for player in self.players:
            player.reset_auction_attributes()

        return winner_id


    def disgrace_card_auction(self, current_player_id: int, status_card: DisgraceCard) -> int:
        """
        Simple disgrace auction:
        - Players take turns in normal order.
        - Each player must bid strictly more than the previous max bid.
        - The first player who 'pass'/'fold'/'quit' loses and takes the disgrace card.
        Returns:
        loser_id (int): index of the player who takes the disgrace card.
        """
        record = AuctionRecord(
            round_number=len(self.auction_rounds) + 1,
            auction_type="disgrace",
            card=summarize_card(status_card),
        )

        num_players_in_auction = self._count_active_auction_players()
        if num_players_in_auction == 0:
            self.host.send_message("⚠️ No active players for disgrace auction.")
            self.auction_rounds.append(record)
            self._broadcast_auction_result(record)
            return -1


        max_bid = 0
        loser_id = -1
        # Exactly who's actually "in" this disgrace auction right now (see
        # normal_card_auction's identical counted_player_ids for why
        # current_participation_in_auction alone can't tell "already
        # inactive before this auction even started" apart from "just went
        # inactive mid-loop" — reset_auction_attributes() blindly sets it
        # back to True for everyone, active or not, ahead of every auction).
        counted_player_ids = {i for i, p in enumerate(self.players)
                               if p.active and p.current_participation_in_auction}

        self.host.send_message(f"\n💀 Disgrace Auction started for: {status_card}: {status_card.description}")
        self.host.send_message("Each turn, you must bid higher than the previous bid. First to pass takes the disgrace card.")
        self._broadcast_auction_update(
            "auction_start", status_card, auction_type="disgrace",
            starting_player=self.players[current_player_id].username,
        )

        # loop until someone passes (they lose)
        while True:
            player = self.players[current_player_id]

            # skip inactive players
            if not player.active:
                if current_player_id in counted_player_ids:
                    # Went inactive asynchronously (e.g. an out-of-turn
                    # resign — see web_server.py's on_resign — or a
                    # mid-auction disconnect) rather than through their own
                    # turn's "quit" branch below. Quitting a disgrace
                    # auction always means losing it immediately (see that
                    # branch) regardless of who else is still bidding —
                    # same rule, just triggered asynchronously. Without
                    # this they'd just be skipped forever and whoever's
                    # left would keep being asked to bid against no one.
                    counted_player_ids.discard(current_player_id)
                    loser_id = current_player_id
                    record.add_event(player.username, "quit")
                    self.host.send_message(f"❌ {player.username} quit.")
                    self._broadcast_auction_update("quit", status_card, player=player.username, max_bid=max_bid)
                    self._record_auction_history_snapshot()
                    break
                current_player_id = self.get_next_player_id(current_player_id)
                continue

            # let the player act; _handle_player_turn enforces bid > max_bid
            action = self._handle_player_turn(player, max_bid, status_card)

            # handle special actions
            if isinstance(action, str):
                cmd = action.lower()
                if cmd in ["pass", "fold"]:
                    # player passes -> they lose and take the disgrace card
                    loser_id = current_player_id
                    record.add_event(player.username, cmd)
                    self.host.send_message(f"💢 {player.username} passed and takes the disgrace card!")
                    self._broadcast_auction_update(cmd, status_card, player=player.username, max_bid=max_bid)
                    self._record_auction_history_snapshot()
                    break
                elif cmd == "quit":
                    # quitting also makes them lose the disgrace card (treated same as pass)
                    player.active = False
                    loser_id = current_player_id
                    record.add_event(player.username, "quit")
                    self.host.send_message(f"❌ {player.username} quit.")
                    self._broadcast_auction_update("quit", status_card, player=player.username, max_bid=max_bid)
                    self._record_auction_history_snapshot()
                    break
                else:
                    # unexpected string (shouldn't happen) — ask again in next loop
                    player.send_message("⚠️ Invalid command. You must raise or 'pass' to take the card.", message_type = "INPUT_ERROR")
                    continue

            # numeric bid placed
            elif isinstance(action, int):
                # update max and continue to next player in order
                max_bid = action
                record.add_event(player.username, "bid", max_bid,
                                  cards=[c.value for c in player.current_money_card_bids])
                self.host.send_message(f"💰 {player.username} bid now {max_bid}.")
                self._broadcast_auction_update("bid", status_card, player=player.username, max_bid=max_bid,
                                                cards=[c.value for c in player.current_money_card_bids])
                self._record_auction_history_snapshot()
                # move to next player
                current_player_id = self.get_next_player_id(current_player_id)
                continue

            else:
                # defensive fallback
                player.send_message("⚠️ Invalid response. Try again.", message_type = "INPUT_ERROR")
                continue

        # finalize: loser_id should be the player who passed / quit
        if loser_id == -1:
            # should not happen, but safe guard
            self.host.send_message("⚠️ Disgrace auction ended unexpectedly with no loser.")
            self.auction_rounds.append(record)
            self._broadcast_auction_result(record)
            # reset auction attributes and return -1
            for p in self.players:
                p.reset_auction_attributes()
            return -1

        # Give the disgrace card to loser and announce
        loser = self.players[loser_id]
        self._finalize_auction(loser_id, status_card, max_bid)

        # Settle bid money per the configured strategy (default: non-passers forfeit)
        self.disgrace_settlement.settle(self.players, loser_id)
        # Unlike a normal auction (where each participant's money only ever
        # changes on their own turn — already covered above), a disgrace
        # auction's settlement can change *everyone's* money at once, for
        # players who aren't the one currently acting (e.g. every raiser
        # forfeiting under the default ForfeitSettlement). Refresh everyone
        # rather than trying to know which settlement strategy touched whom.
        for p in self.players:
            if p.active:
                self._send_player_state(p)

        # See the comment in normal_card_auction: current_bid_value read here,
        # after settlement but before reset_auction_attributes(), reflects
        # whatever the configured DisgraceAuctionSettlement actually did —
        # 0 for the recipient (refunded by passing) and, under the default
        # ForfeitSettlement, each raiser's forfeited amount for everyone else.
        record.recipient = loser.username
        record.money_spent = {p.username: p.current_bid_value for p in self.players}
        record.cards_spent = {p.username: [c.value for c in p.current_money_card_bids] for p in self.players}
        self.auction_rounds.append(record)
        self._broadcast_auction_result(record)
        self._record_auction_history_snapshot()

        # Reset auction state for all players
        for player in self.players:
            player.reset_auction_attributes()

        # Return the loser id (who took the disgrace) — consistent with your normal_card_auction contract
        return loser_id

    def _should_end_game(self, num_players_in_auction, num_green_cards):
        """
        Determines if the game needs to be end early.
        """
        if num_players_in_auction < 2:
            LoggingManager.info("Need at least 2 active players. Game ending.")
            return True
        if num_green_cards >= self.__green_card_limit:
            LoggingManager.info(f"Green card limit ({self.__green_card_limit}) reached. Ending Game..")
            return True
        return False

    def handle_faux_pas_penalty(self, player_id: int) -> bool:
        """
        Takes player_id and prompts player to discard a painting

        Returns True if discarded.
        False if player doesn't have paintings and hence hasn't discarded yet.
        """
        player = self.players[player_id]
        if not player.active:
            # Gone for good (resigned out of turn — see web_server.py's
            # on_resign — or disconnected) — nothing left to ask them, and
            # this gets called again every remaining round for as long as
            # faux_pas_holder_id stays set (see play_game()'s main loop), so
            # without this it would keep re-entering choose_painting_to_discard()
            # and blocking on it forever (it has no timeout). Treat it as
            # resolved so the game doesn't wait on a player who's never
            # coming back.
            return True
        paintings = [card for card in player.status_cards if isinstance(card, Painting)]

        if paintings:
            chosen = player.choose_painting_to_discard()
            if chosen is None:
                # Player disconnected or otherwise failed to choose; retry on the next opportunity.
                return False
            player.discard_painting_card(chosen.value)
            self._pace_toast_event()
            self.host.send_message(
                f"🎨 {player.username} discarded a painting due to Faux Pas.",
                data={"event": "faux_pas_discard", "player": player.username, "discarded_value": chosen.value},
            )
            self._send_player_state(player)
            self._record_auction_history_snapshot()
            return True

        return False

    def determine_winner(self):
        """Determine the final winner(s) of the game."""
        LoggingManager.info("Determining the winner...")

        winner_candidates = [True] * len(self.players)
        player_points = []
        player_money_left = []

        # Collect player info
        for idx, player in enumerate(self.players):
            if not player.active:
                winner_candidates[idx] = False

            pts = player.points
            money_left = player.money_left()

            player_points.append(pts)
            player_money_left.append(money_left)

        # Step 1: Find minimum money among active players only (inactive/quit
        # players' leftover money is irrelevant to this comparison).
        active_indices = [i for i in range(len(self.players)) if winner_candidates[i]]
        if not active_indices:
            self.host.send_message("⚠️ No active players remain.")
            self.host.send_message("No Winners for this game.")
            self.winners = []
            self.final_standings = [
                {"username": p.username, "points": player_points[i], "money_left": player_money_left[i],
                 "active": p.active, "eliminated": False}
                for i, p in enumerate(self.players)
            ]
            return None

        # Step 2: Eliminate the active player with the least money, unless
        # there's a tie for least (nobody eliminated) or only one active
        # player remains (nobody left to compare against, so they win outright).
        # eliminated_idx records *which* player this rule knocked out (if
        # any) — distinct from simply "not a winner" (someone can lose on
        # points alone without ever being money-eliminated) — so a UI can
        # show that specific player as the one who was out of contention
        # entirely, e.g. sorted to the bottom of a standings list.
        eliminated_idx = None
        if len(active_indices) > 1:
            min_money = min(player_money_left[i] for i in active_indices)
            lowest_money_indices = [i for i in active_indices if player_money_left[i] == min_money]

            if len(lowest_money_indices) == 1:
                eliminated_idx = lowest_money_indices[0]
                winner_candidates[eliminated_idx] = False

        # Step 3: From remaining candidates, find the highest point(s)
        remaining_points = [player_points[i] if winner_candidates[i] else float('-inf') 
                            for i in range(len(self.players))]

        max_points = max(remaining_points)

        # Step 4: Identify all winners (in case of tie)
        winners = [self.players[i] for i, pts in enumerate(player_points)
                if winner_candidates[i] and pts == max_points]

        # Step 5: Announce results
        self.final_standings = [
            {"username": p.username, "points": player_points[idx], "money_left": player_money_left[idx],
             "active": p.active, "eliminated": idx == eliminated_idx}
            for idx, p in enumerate(self.players)
        ]
        self.winners = winners
        self.host.send_message("\n🏁 Final Standings:", data={"event": "final_standings", "standings": self.final_standings})
        for idx, player in enumerate(self.players):
            if player.active:
                self.host.send_message(f" - {player.username}: Points={player_points[idx]}, Money Left={player_money_left[idx]}")
            else:
                self.host.send_message(f" - {player.username}: Inactive : (Points={player_points[idx]}, Money Left={player_money_left[idx]})")

        if len(winners) == 1:
            self.host.send_message(f"\n🏆 Winner: {winners[0].username} with {max_points} points!",
                                    data={"event": "winner", "winners": [winners[0].username], "points": max_points})
        elif len(winners) > 1:
            tied_names = ", ".join(w.username for w in winners)
            self.host.send_message(f"\n🤝 It's a tie between {tied_names} with {max_points} points each!",
                                    data={"event": "winner", "winners": [w.username for w in winners], "points": max_points})
        else:
            self.host.send_message("\n😬 No winner could be determined.",
                                    data={"event": "winner", "winners": [], "points": None})

        return winners

    def countdown_to_start(self, countdown: int = 3) -> None:
        for remaining in range(countdown, 0, -1):
            self.host.send_message(f"⏳  Game starting in {remaining}...",
                                    data={"event": "countdown", "seconds_left": remaining})
            time.sleep(1)

        # final message:
        self.host.send_message(f"🚀 Game Started!", data={"event": "countdown_finished"})


    def play_game(self):
        # Brief countdown so every player's screen has a moment to render
        # before the first auction starts, without making everyone wait long
        # after the last seat just filled.
        self.countdown_to_start(countdown=3)

        
        LoggingManager.info("Game Started..")
        self.shuffle_players()

        # Lets a client render its opponent list in true turn order instead
        # of whatever order it happened to first hear about each player (see
        # app.js's renderOpponents) -- without this, the shuffle is real on
        # the engine side but invisible in the UI, so turns visually jump
        # around a list ordered by first-appearance rather than seating.
        self.host.send_message(
            "", message_type="GLOBAL_EVENT",
            data={"event": "player_order", "usernames": [p.username for p in self.players]},
        )

        # Without this, a player's own money hand only ever appeared once
        # they'd taken their first turn (the interactive bid prompt is the
        # only other thing that ever populated it) — round 1 looked bare for
        # anyone who wasn't first to act. This is what lets a client show it
        # (read-only/greyed) from the very start instead.
        for player in self.players:
            self._send_player_state(player)
        self._record_auction_history_snapshot()

        num_green_cards = 0
        starting_player_id = random.randint(0, len(self.players) - 1) # random starting player id
        faux_pas_holder_id = None

        while (not self.status_card_manager.is_empty()):

            num_players_in_auction = self._count_active_auction_players()

            status_card = self.status_card_manager.remove_top_card()
            if status_card.is_green:
                num_green_cards += 1
                is_final_green_card = num_green_cards >= self.__green_card_limit
                if num_green_cards <= 3:
                    self._pace_toast_event()
                    self.host.send_message(
                        f"{num_green_cards} green card(s) revealed ..",
                        data={"event": "green_card_revealed", "count": num_green_cards, "is_final": is_final_green_card},
                    )
                elif is_final_green_card:
                    # The limit-th green card ends the game immediately
                    # without ever reaching auction (see HSConfig.json's
                    # green_card_limit comment) — previously this was
                    # completely silent to players/spectators, who'd just
                    # see the game end with no explanation.
                    #
                    # This one IS paced (unlike the un-paced game_over
                    # broadcast right after it, which really has nothing
                    # after it to protect) precisely *because* something
                    # important came immediately before it: the previous
                    # auction's own result. Skipping the wait here let this
                    # green-card overlay pop up over/before a human had
                    # actually seen who won the last card and for how much —
                    # the exact "it just happens too fast" complaint.
                    self._pace_toast_event()
                    self.host.send_message(
                        f"🚨 {num_green_cards} green card(s) revealed — the game ends now!",
                        data={"event": "green_card_revealed", "count": num_green_cards, "is_final": True},
                    )

            if self._should_end_game(num_players_in_auction, num_green_cards):
                break

            if isinstance(status_card, DisgraceCard):
                # different kinda auction
                starting_player_id = self.disgrace_card_auction(starting_player_id, status_card=status_card)

                if isinstance(status_card, FauxPas):
                    faux_pas_holder_id = starting_player_id
                    self.players[faux_pas_holder_id].send_message(f"You have to discard a painting in this/subsequent rounds as you are holding a faux pass",
                    message_type = "PLAYER_INFO", created_at = time.time())


            else:
                starting_player_id = self.normal_card_auction(status_card = status_card, starting_player_id= starting_player_id)

            # discard card from the player holding faux pas. -> applicable only when the player has a painting
            if faux_pas_holder_id is not None:
                has_discarded = self.handle_faux_pas_penalty(faux_pas_holder_id)
                if has_discarded:
                    faux_pas_holder_id = None


        # Determine winner
        self.determine_winner()
        self.host.send_message("Game Concluded 😘")
        self.host.send_message("Thanks for playing...")
                        



     


                
                        


                




            




