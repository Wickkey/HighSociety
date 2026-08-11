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
        rejoin-token handling) and marks the player active again.

        Deliberately does NOT wake a blocked get_bid()/choose_painting_to_discard()
        call yet — see finish_reconnect() for why that's a separate step, not
        folded into this one. From this point on, the new transport is live
        and can receive messages the caller sends on it (e.g. web_server.py's
        own IDENTIFY_SUCCESS + reconnect catch-up), but nothing already
        blocked waiting on the *old* transport resumes until finish_reconnect()
        explicitly says so.
        """
        self.transport = transport
        self.active = True
        self.transport.start()

    def finish_reconnect(self) -> None:
        """
        Wakes a get_bid()/choose_painting_to_discard() call that's been
        sitting in _wait_for_reconnect() since the old transport died,
        letting it resume immediately instead of waiting out the rest of
        its grace period.

        Split out from reattach() on purpose: the moment this fires, the
        (separate, blocked) game thread can immediately start sending its
        own messages on the newly-attached transport again (a fresh
        "Enter your bid" re-prompt, typically) — call this only once the
        caller is done sending anything of its own that has to land first
        (web_server.py's IDENTIFY_SUCCESS + catch-up state), or those two
        message streams can interleave out of order on the wire. Only
        matters if the wait was ever actually entered; a harmless no-op
        otherwise (nothing is blocked on a fresh/never-disconnected player).
        """
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

        if timeout:
            self.send_message(f"Time left: {timeout:.2f}s ⏰", message_type="PLAYER_MOVE_TIMER",
                               data={"seconds_remaining": timeout})
        self.send_message("Enter your bid for the auction: ", message_type="PLAYER_MOVE")

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

        while True:
            try:
                self.send_message("Choose one to discard: ", message_type="PLAYER_MOVE", move_type="discard_painting")

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
