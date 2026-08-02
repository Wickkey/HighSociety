from highsociety.code.gamecore.recording.session_recorder import SessionRecorder


class RecordingPlayer:
    """
    Transparent wrapper around any real player (CLIPlayer, NetworkPlayer, ...)
    that logs every get_bid()/choose_painting_to_discard() decision to a
    SessionRecorder as it happens, then passes it straight through. Every
    other attribute/method (money_cards, status_cards, active, send_message,
    ...) is delegated untouched to the wrapped player, including writes
    (e.g. `player.active = False`), so game logic can't tell the difference.

    "Still waiting" polls (get_bid returning None while awaiting a turn
    timeout, or after invalid input the player will retype) are NOT logged —
    only the decision that actually got consumed by the game is recorded.
    """

    def __init__(self, wrapped, recorder: SessionRecorder):
        object.__setattr__(self, "_wrapped", wrapped)
        object.__setattr__(self, "_recorder", recorder)
        recorder.register_player(wrapped.username, wrapped.name)

    def get_bid(self, timeout=None):
        result = self._wrapped.get_bid(timeout=timeout)
        if result is not None:
            self._recorder.log(self._wrapped.username, "get_bid", result, self._wrapped.active)
        return result

    def choose_painting_to_discard(self):
        result = self._wrapped.choose_painting_to_discard()
        value = result.value if result is not None else None
        self._recorder.log(self._wrapped.username, "choose_painting_to_discard", value, self._wrapped.active)
        return result

    def __getattr__(self, name):
        return getattr(self._wrapped, name)

    def __setattr__(self, name, value):
        setattr(self._wrapped, name, value)

    def __repr__(self):
        return f"RecordingPlayer({self._wrapped!r})"
