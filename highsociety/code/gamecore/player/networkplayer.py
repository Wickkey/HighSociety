import threading
from socket import error as SocketError
from typing import Union
from typing import Optional
from highsociety.code.gamecore.player.player import BasePlayer
from highsociety.code.gamecore.components_module.painting import Painting
from highsociety.code.gamecore.network.transport import Transport
from highsociety.code.gamecore.network.protocol import build_player_payload
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager, LogType


class NetworkPlayer(BasePlayer):
    """
    Remote player. Talks entirely through a Transport (today always a
    SocketTransport, see network_server.py) using the shared player message
    protocol (network/protocol.py) — this class has no socket/threading
    knowledge of its own, so a future transport (e.g. WebSockets for a
    browser client) plugs in here unchanged.
    """
    def __init__(self, name: str, username: str, transport: Transport, game_id: str,
                 disconnect_grace_seconds: Optional[float] = None):
        super().__init__(name, username)
        self.transport = transport
        self.active = True
        self.game_id: str = game_id
        # True only for a genuine, explicit "quit" command actually received
        # from the client (see get_bid() below) — never set for a dropped
        # connection, which also sets active=False but should still be
        # reconnectable (see web_server.py's rejoin-token handling). This is
        # what lets the web UI's "Resign" button permanently forfeit a seat,
        # distinct from an accidental disconnect.
        self.resigned = False
        # How long get_bid()/choose_painting_to_discard() will wait for a
        # dropped connection to reattach() before giving up and quitting that
        # one decision (see _wait_for_reconnect below) — None (the default,
        # what network_server.py's raw-socket path leaves it at) means no
        # wait at all, preserving the original instant-quit behavior for a
        # transport with no reconnect mechanism to ever recover through.
        # web_server.py computes and passes a real value per-room.
        self.disconnect_grace_seconds = disconnect_grace_seconds
        self._reconnected_event = threading.Event()

    def reset_for_new_game(self) -> None:
        """Extends BasePlayer.reset_for_new_game() with the two fields that
        are NetworkPlayer's own (see web_server.py's _maybe_start_rematch,
        the only caller) — both should already hold these values for any
        player eligible for a rematch in the first place, but resetting
        them explicitly is cheap insurance against relying on that."""
        super().reset_for_new_game()
        self.active = True
        self.resigned = False

    def start_receiver_thread(self) -> None:
        """Begin receiving messages in the background. Kept as an explicit,
        separately-timed step (rather than starting in __init__) to match
        network_server.py, which finishes accepting all players before
        starting anyone's receiver."""
        self.transport.start()

    def reattach(self, transport: Transport) -> None:
        """
        Swaps in a fresh transport after a reconnect (see web_server.py's
        rejoin-token handling) — but deliberately does NOT mark the player
        active yet (see finish_reconnect(), which does). From this point on
        the new transport is live and can receive messages the caller sends
        on it (e.g. web_server.py's own IDENTIFY_SUCCESS + reconnect
        catch-up), but the game thread still treats this player as inactive
        (skips their turn in the round-robin, same as while disconnected)
        until finish_reconnect() explicitly says otherwise.

        This split matters even when it's *not* this player's own turn that
        was paused: send_message() doesn't gate on self.active at all (it
        just writes to self.transport), so catch-up sending here works fine
        either way — but if active flipped True immediately, the game
        thread's round-robin loop (running on a separate thread, cycling
        through *other* players' turns the whole time this player was
        disconnected) could see this player as active again and start
        writing its own fresh messages (a new turn_start/PLAYER_MOVE) to the
        very same transport the caller here is still in the middle of
        writing catch-up messages to — two unsynchronized writers on one
        socket, wire order whatever the scheduler produces. That's exactly
        what let a reconnecting player's browser see the auction card
        revert to a stale round for a moment: a fresh, correct AUCTION_UPDATE
        landing on the wire, immediately followed by a catch-up "sync"
        snapshotted *before* it, overwriting the client's now-current state
        back to the wrong round.
        """
        self.transport = transport
        self.transport.start()

    def finish_reconnect(self) -> None:
        """
        Marks the player active again and wakes a get_bid()/
        choose_painting_to_discard() call that's been sitting in
        _wait_for_reconnect() since the old transport died (a no-op if that
        wait was never entered — nothing blocked on a fresh/never-
        disconnected player).

        Split out from reattach() on purpose, for two related races (see
        reattach()'s docstring for the second one): the moment this fires,
        the (separate) game thread can immediately start sending its own
        messages on the newly-attached transport again — a fresh "Enter
        your bid" re-prompt for this player's own resumed turn, or (if it
        was someone else's turn instead) simply seeing this player as active
        again in its round-robin loop. Call this only once the caller is
        done sending anything of its own that has to land first
        (web_server.py's IDENTIFY_SUCCESS + catch-up state), or those two
        message streams can interleave out of order on the wire.
        """
        self.active = True
        self._reconnected_event.set()

    def _wait_for_reconnect(self, remaining_turn_time: Optional[float]) -> bool:
        """
        Blocks the calling (game) thread up to disconnect_grace_seconds
        waiting for reattach() to run, waking immediately if it does rather
        than sitting out the full wait. Capped by remaining_turn_time (the
        time actually left on this decision's own deadline, if any) so a
        disconnect can never grant more time than the configured turn timer
        already promised everyone else at the table — also closes off
        dropping the connection right before your timer expires as a way to
        stall for free extra time. Returns True iff a reconnect landed
        before the wait ran out; disconnect_grace_seconds=None (see
        __init__) skips the wait entirely and returns False immediately,
        which is what keeps a transport with no reconnect mechanism (plain
        socket play via network_server.py) from ever hanging with no way to
        recover.
        """
        if self.disconnect_grace_seconds is None:
            return False
        grace = self.disconnect_grace_seconds
        if remaining_turn_time is not None:
            grace = max(0.0, min(grace, remaining_turn_time))
        self._reconnected_event.clear()
        return self._reconnected_event.wait(timeout=grace)

    def stop_receiver_thread(self) -> None:
        self.transport.stop()

    def get_last_heartbeat(self) -> float:
        return self.transport.get_last_heartbeat()

    def send_message(
        self,
        msg: str,
        message_type: str,
        created_at: Optional[float] = None,
        from_user: Optional[str] = None,
        to_users: Optional[str] = None,
        data: Optional[dict] = None,
        move_type: Optional[str] = None,
    ) -> None:
        """Sends a message to the remote player via its transport."""
        constraints = None
        if message_type == "PLAYER_MOVE":
            constraints = {
                "min_bid": 0,
                "allowed_money_cards": [m.value for m in self.money_cards],
                "allowed_paintings": [p.value for p in self.status_cards if isinstance(p, Painting)],
                "allowed_commands": ["pass", "fold", "quit"],
            }

        payload = build_player_payload(
            game_id=self.game_id,
            username=self.username,
            message_type=message_type,
            prompt=msg,
            created_at=created_at,
            constraints=constraints,
            from_user=from_user,
            to_users=to_users,
            data=data,
            move_type=move_type,
        )
        try:
            self.transport.send(payload)
        except (BrokenPipeError, ConnectionResetError, SocketError):
            if self.active:
                self.active = False
                print(f"⚠️ Connection lost while sending to {self.username}")

    def _belongs_to_this_game(self, msg: dict) -> bool:
        """
        A message's game_id must either be absent (permissive, for lightweight
        clients/tests that don't bother setting it) or match this player's own
        game_id. A *present but different* game_id means the message is
        stale/misdirected (e.g. a confused client, or leftover data from a
        previous session) and must not be treated as this turn's input.
        """
        incoming_game_id = msg.get("game_id")
        if incoming_game_id is not None and incoming_game_id != self.game_id:
            LoggingManager.warning(
                f"Ignoring message for {self.username} with mismatched game_id "
                f"(expected {self.game_id!r}, got {incoming_game_id!r})",
                log_type=LogType.SECURITY,
            )
            return False
        return True

    def print_player_info(self):
        self.send_message(f"{self.username}'s status cards: {self.status_cards}", message_type="PLAYER_INFO")
        self.send_message(f"{self.username}'s points: {self.points}", message_type="PLAYER_INFO")
        self.send_message(f"Current Bid: {self.current_money_card_bids}", message_type="PLAYER_INFO")
        self.send_message(f"Remaining Money: {[m.value for m in self.money_cards]}", message_type="PLAYER_INFO")

    def get_bid(self, timeout: Optional[float] = None) -> Union[list[int], str, None]:
        """Request a bid from the remote player and validate it."""
        self.print_player_info()

        # _handle_player_turn (gameplay.py) already sent its own "Your Turn!"
        # PLAYER_MOVE before calling here -- deliberately, so a browser's bid
        # panel opens right away instead of sitting frozen through the toast-
        # pacing wait that can separate that message from this one by up to
        # ~1.8s. A fast player can answer that first prompt before this
        # method ever runs. Real, live-reproduced bug: sending a *second*
        # "Enter your bid" prompt for the exact same turn regardless
        # re-opened the browser's already-pending (correctly greyed-out)
        # move panel right after the player had already answered it, even
        # though their answer was recorded correctly either way -- a
        # confusing "did that register?" flicker with no data loss. A
        # non-blocking peek here catches that case and skips the redundant
        # prompt/timer reset entirely; on every other call (retries after an
        # invalid bid, or simply a player who wasn't that fast) this finds
        # nothing and falls through unchanged.
        early_bid = self.transport.receive(timeout=0)
        if early_bid is not None:
            bid = early_bid
        elif not self.transport.is_connected:
            # The non-blocking peek above can race with a connection that
            # just dropped: SocketTransport._receiver_loop sets its own
            # is_connected False *before* pushing the queue's one-shot
            # "closed" sentinel (None), so if the peek happened to consume
            # that sentinel, is_connected is already guaranteed False here.
            # Falling through to a second, real receive() in that case would
            # wait on a value that will never arrive now that the sentinel
            # is gone -- hang forever with no timeout to save it (this
            # branch is also reachable with timeout=None). Treat it exactly
            # as the real receive() below already would: a None bid, for
            # the existing reconnect/quit handling right below to resolve.
            bid = None
        else:
            # Same move_seq as _handle_player_turn's own initial "Your
            # Turn!" prompt (set as an attribute rather than a parameter
            # here so every non-network player type stays untouched — see
            # gameplay.py's own comment). Lets the web client recognize
            # this as still the *same* decision if its own early answer
            # crossed this send in flight (the narrower race the
            # non-blocking peek above can't fully close on its own: an
            # answer that arrives at this exact server while it's between
            # the peek and this send) rather than treating it as a fresh
            # prompt and re-opening an already-answered move panel.
            move_seq = getattr(self, '_current_move_seq', None)
            if timeout:
                self.send_message(f"Time left: {timeout:.2f}s ⏰", message_type="PLAYER_MOVE_TIMER",
                                   data={"seconds_remaining": timeout, "move_seq": move_seq})
            self.send_message("Enter your bid for the auction: ", message_type="PLAYER_MOVE",
                               data={"move_seq": move_seq})

            bid = self.transport.receive(timeout=timeout)

        if bid is None:
            # Timed out waiting, or the transport has disconnected.
            if not self.transport.is_connected:
                if self._wait_for_reconnect(timeout):
                    # Reconnected within the grace period -- None (not
                    # "quit") tells the caller's `if bids is None: continue`
                    # (gameplay.py's _handle_player_turn) to just re-poll,
                    # which re-sends the prompt on the fresh transport. No
                    # gameplay.py change needed: this is exactly the same
                    # retry path a plain queue timeout already uses below.
                    return None
                self.active = False
                return "quit"
            return None

        if not self._belongs_to_this_game(bid):
            return None  # discard silently; caller will re-poll for the real input

        # A bid response is user-supplied text from a remote client (a browser
        # tab, another socket client, a bot). It must be a string — a client
        # that sends a non-string or missing `prompt` (e.g. a JSON list, a
        # number, or nothing at all) is malformed, not a valid bid. Treat it
        # as invalid input rather than crashing the game thread with a
        # KeyError/AttributeError on the shape it wasn't expecting.
        if not isinstance(bid.get("prompt"), str):
            self.send_message("⚠️ Invalid response. Please enter a valid number, list, or command.", message_type="INPUT_ERROR")
            return None

        if bid["prompt"] == "":
            self.send_message("⚠️ Empty input. Please enter a valid number, list, or command.", message_type="INPUT_ERROR")
            return None

        # Handle special commands
        if bid["prompt"].lower() in ["pass", "fold", "quit"]:
            cmd = bid["prompt"].lower()
            if cmd == "quit":
                # A real "quit" command actually received from the client —
                # not the disconnect fallback a few lines up, which also
                # returns "quit" but must stay reconnectable.
                self.resigned = True
            return cmd

        # Parse list input like [1, 2, 3]
        if bid["prompt"].startswith("[") and bid["prompt"].endswith("]"):
            try:
                nums = [int(x.strip()) for x in bid["prompt"].strip("[]").split(",") if x.strip()]

                # Check duplicates
                if len(nums) != len(set(nums)):
                    self.send_message("⚠️ Duplicate values found in bid. Please enter a valid bid.", message_type="INPUT_ERROR")
                    return None

                # Check if all values exist in money_cards
                money_values = [m.value for m in self.money_cards]
                for num in nums:
                    if num not in money_values:
                        self.send_message(f"⚠️ You don't have money card {num}. Enter a valid bid.", message_type="INPUT_ERROR")
                        return None
                else:
                    return nums
            except ValueError:
                self.send_message("⚠️ Invalid list format. Example: [1, 2, 3]", message_type="INPUT_ERROR")
                return None

        # Handle integer input (convert to list like CLIPlayer)
        try:
            num = int(bid["prompt"])
            money_values = [m.value for m in self.money_cards]
            if num not in money_values:
                self.send_message(f"⚠️ You don't have money card {num}. Enter a valid bid.", message_type="INPUT_ERROR")
                return None

            return [num]

        except ValueError:
            self.send_message("⚠️ Please enter a valid integer, list, or command (pass/fold/quit).", message_type="INPUT_ERROR")
            return None

    def choose_painting_to_discard(self) -> Painting:
        """
        Get input from the remote player to choose the painting to discard.
        Returns None if the player has no paintings, or if the transport
        disconnects before a valid choice arrives.
        """
        self.send_message("Your Paintings: ", message_type="PLAYER_INFO")
        paintings = {}
        for s in self.status_cards:
            if isinstance(s, Painting):
                paintings[s.value] = s
                self.send_message(f"{s}: Value: {s.value}", message_type="PLAYER_INFO")

        if len(paintings) == 0:
            return None

        # Same move_seq for every retry in this loop -- they're all still
        # the same logical decision (see gameplay.py's own comment on
        # _current_move_seq).
        move_seq = getattr(self, '_current_move_seq', None)
        while True:
            try:
                self.send_message("Choose one to discard: ", message_type="PLAYER_MOVE", move_type="discard_painting",
                                   data={"move_seq": move_seq})

                choice = self.transport.receive(timeout=None)

                if choice is None:
                    # Connection closed or receiver stopped. No per-move
                    # timer on this prompt, so the grace period is always
                    # uncapped (remaining_turn_time=None) -- mirrors
                    # get_bid()'s handling above, for the same reason.
                    if self._wait_for_reconnect(None):
                        continue  # re-sends "Choose one to discard" on the fresh transport
                    self.active = False
                    return None

                if not self._belongs_to_this_game(choice):
                    continue  # discard silently; keep waiting for the real input

                choice_text = choice.get("prompt", "")
                if not isinstance(choice_text, str):
                    self.send_message("⚠️ Invalid input. Try again.", message_type="INPUT_ERROR")
                    continue
                if not choice_text:
                    self.send_message("⚠️ Empty input. Please enter a valid number.", message_type="INPUT_ERROR")
                    continue
                if choice_text.lower() == "quit":
                    # Mirrors get_bid()'s "quit" handling — without this, an
                    # out-of-turn resign's synthetic "quit" RESPONSE (see
                    # WebSocketTransport._reader_loop, queued in case this
                    # exact discard prompt happened to be live when the
                    # resign arrived) would fall through to int(choice_text)
                    # below, raise ValueError, and re-prompt with
                    # self.transport.receive(timeout=None) — which blocks
                    # forever once this player is gone and never sends
                    # anything else, freezing the whole game for everyone.
                    self.resigned = True
                    return None

                choice_value = int(choice_text.strip())
                painting = paintings.get(choice_value)
                if painting:
                    return painting
                else:
                    self.send_message("⚠️ Invalid choice. Try again.", message_type="INPUT_ERROR")
            except (ValueError, KeyError) as e:
                LoggingManager.error(e)
                self.send_message("⚠️ Invalid input. Let's try again..", message_type="INPUT_ERROR")
            except (ConnectionResetError, BrokenPipeError):
                print(f"⚠️ {self.username} disconnected.")
                self.active = False
                return None

    def close(self):
        """Close the connection and stop the receiver thread."""
        self.active = False
        self.transport.close()
        print(f"{self.username} connection closed successfully.")
