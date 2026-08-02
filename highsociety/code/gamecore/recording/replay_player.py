from highsociety.code.gamecore.components_module.painting import Painting


class ReplayReachedEndOfRecording(RuntimeError):
    pass


class ReplayMismatch(RuntimeError):
    pass


class ReplayPlayer:
    """
    Drop-in player that feeds pre-recorded decisions back in the exact order
    they were made, instead of reading real input — the counterpart to
    RecordingPlayer. `wrapped` only needs to be a real BasePlayer instance for
    its state machinery (money/status cards, points); its own get_bid /
    choose_painting_to_discard are never called.

    Every other attribute (money_cards, status_cards, active, send_message,
    ...) is delegated to the wrapped player, same as RecordingPlayer.
    """

    def __init__(self, wrapped, recorded_actions: list[dict]):
        object.__setattr__(self, "_wrapped", wrapped)
        object.__setattr__(self, "_queue", list(recorded_actions))

    def _next(self, expected_type: str):
        if not self._queue:
            raise ReplayReachedEndOfRecording(
                f"Replay ran out of recorded actions for {self._wrapped.username} "
                f"while expecting a '{expected_type}'. The game state must have "
                f"diverged from the original recording."
            )
        entry = self._queue.pop(0)
        if entry["type"] != expected_type:
            raise ReplayMismatch(
                f"Replay mismatch for {self._wrapped.username}: expected next "
                f"recorded action to be '{expected_type}', but it was '{entry['type']}'. "
                f"The game state must have diverged from the original recording."
            )
        # Reproduce side effects invisible in the return value alone (e.g. a
        # CLIPlayer marking itself inactive after an EOF mid-discard-retry) —
        # otherwise a player who should have gone inactive keeps getting asked
        # for further turns during replay, eventually running the queue dry.
        active_after = entry.get("active_after")
        if active_after is not None:
            self._wrapped.active = active_after
        return entry["result"]

    def get_bid(self, timeout=None):
        return self._next("get_bid")

    def choose_painting_to_discard(self):
        value = self._next("choose_painting_to_discard")
        if value is None:
            return None
        for card in self._wrapped.status_cards:
            if isinstance(card, Painting) and card.value == value:
                return card
        raise ReplayMismatch(
            f"Replay: recorded discard value {value} not found in "
            f"{self._wrapped.username}'s hand — the game state must have diverged."
        )

    def __getattr__(self, name):
        return getattr(self._wrapped, name)

    def __setattr__(self, name, value):
        setattr(self._wrapped, name, value)

    def __repr__(self):
        return f"ReplayPlayer({self._wrapped!r}, {len(self._queue)} actions left)"
