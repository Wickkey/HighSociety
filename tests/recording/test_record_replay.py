import json

import pytest

from highsociety.code.gamecore.game_manager.gameplay import PlayGame
from highsociety.code.gamecore.player.cliplayer import CLIPlayer
from highsociety.code.gamecore.recording.session_recorder import SessionRecorder
from highsociety.code.gamecore.recording.recording_player import RecordingPlayer
from highsociety.code.gamecore.recording.replay_player import (
    ReplayPlayer,
    ReplayMismatch,
    ReplayReachedEndOfRecording,
)


def _final_state(game):
    return [(p.username, p.points, p.money_left(), p.active) for p in game.players]


class TestRecordingPlayerDelegation:
    def test_transparently_proxies_reads_and_writes_to_the_wrapped_player(self, tmp_path):
        wrapped = CLIPlayer(name="Alice", username="alice")
        recorder = SessionRecorder(path=tmp_path / "rec.json", seed=1)
        player = RecordingPlayer(wrapped, recorder)

        assert player.username == "alice"
        assert player.money_left() == wrapped.money_left()

        player.active = False
        assert wrapped.active is False  # write went through to the real object

    def test_logs_get_bid_results_but_not_none_polls(self, tmp_path, monkeypatch):
        wrapped = CLIPlayer(name="Alice", username="alice")
        monkeypatch.setattr(wrapped, "get_bid", lambda timeout=None: None)
        recorder = SessionRecorder(path=tmp_path / "rec.json", seed=1)
        player = RecordingPlayer(wrapped, recorder)

        result = player.get_bid(timeout=0.1)

        assert result is None
        assert recorder.actions["alice"] == []  # a "still waiting" poll isn't a decision

    def test_logs_a_real_bid_with_the_players_active_state(self, tmp_path, monkeypatch):
        wrapped = CLIPlayer(name="Alice", username="alice")
        monkeypatch.setattr(wrapped, "get_bid", lambda timeout=None: "pass")
        recorder = SessionRecorder(path=tmp_path / "rec.json", seed=1)
        player = RecordingPlayer(wrapped, recorder)

        player.get_bid(timeout=None)

        assert recorder.actions["alice"] == [{"type": "get_bid", "result": "pass", "active_after": True}]


class TestReplayPlayer:
    def test_feeds_back_recorded_bids_in_order(self):
        wrapped = CLIPlayer(name="Alice", username="alice")
        actions = [
            {"type": "get_bid", "result": [1], "active_after": True},
            {"type": "get_bid", "result": "pass", "active_after": True},
        ]
        player = ReplayPlayer(wrapped, actions)

        assert player.get_bid() == [1]
        assert player.get_bid() == "pass"

    def test_applies_recorded_active_state_after_each_action(self):
        wrapped = CLIPlayer(name="Alice", username="alice")
        actions = [{"type": "choose_painting_to_discard", "result": None, "active_after": False}]
        player = ReplayPlayer(wrapped, actions)

        assert wrapped.active is True
        player.choose_painting_to_discard()
        assert wrapped.active is False

    def test_raises_when_the_recording_runs_out(self):
        wrapped = CLIPlayer(name="Alice", username="alice")
        player = ReplayPlayer(wrapped, [])

        with pytest.raises(ReplayReachedEndOfRecording):
            player.get_bid()

    def test_raises_on_action_type_mismatch(self):
        wrapped = CLIPlayer(name="Alice", username="alice")
        actions = [{"type": "choose_painting_to_discard", "result": None, "active_after": True}]
        player = ReplayPlayer(wrapped, actions)

        with pytest.raises(ReplayMismatch):
            player.get_bid()

    def test_choose_painting_to_discard_resolves_the_recorded_value_to_a_real_card(self):
        from highsociety.code.gamecore.components_module.painting import Painting

        wrapped = CLIPlayer(name="Alice", username="alice")
        wrapped.add_status_card(Painting(value=7))
        actions = [{"type": "choose_painting_to_discard", "result": 7, "active_after": True}]
        player = ReplayPlayer(wrapped, actions)

        chosen = player.choose_painting_to_discard()
        assert chosen.value == 7


class TestFullRecordReplayRoundTrip:
    def test_replaying_a_recorded_game_reproduces_the_exact_outcome(self, tmp_path, make_player):
        """
        End-to-end: play a full seeded game through RecordingPlayer wrappers,
        then reconstruct fresh players from the saved recording via
        ReplayPlayer and confirm the final state (points, money, active flags)
        matches exactly. Uses the same seed the actual --record/--replay CLI
        flags rely on.
        """
        rec_path = tmp_path / "session.json"
        seed = 5
        recorder = SessionRecorder(path=rec_path, seed=seed)

        # Everyone just passes — deterministic and simple, but still exercises
        # a full game including the FauxPas discard flow for this seed.
        real_alice = CLIPlayer(name="Alice", username="alice")
        real_bob = CLIPlayer(name="Bob", username="bob")
        monkeypatch_pass(real_alice)
        monkeypatch_pass(real_bob)

        original_players = [RecordingPlayer(real_alice, recorder), RecordingPlayer(real_bob, recorder)]
        original_game = PlayGame(players=original_players, mode="cli", seed=seed)
        original_game.play_game()
        original_state = _final_state(original_game)

        recording = SessionRecorder.load(rec_path)
        assert recording["seed"] == seed

        replay_players = [
            ReplayPlayer(CLIPlayer(name=p["name"], username=p["username"]), recording["actions"][p["username"]])
            for p in recording["players"]
        ]
        replay_game = PlayGame(players=replay_players, mode="cli", seed=recording["seed"])
        replay_game.play_game()
        replay_state = _final_state(replay_game)

        assert original_state == replay_state


def monkeypatch_pass(cli_player):
    """Makes a real CLIPlayer always pass/discard-nothing without touching stdin."""
    cli_player.get_bid = lambda timeout=None: "pass"
    cli_player.choose_painting_to_discard = lambda: None
