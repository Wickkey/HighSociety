import copy
import json
import random
import time

import pytest

from highsociety.code.ai import create_bot_players
from highsociety.code.ai.greedy_bot import GreedyBot
from highsociety.code.gamecore.game_manager.auction_history import AuctionHistory
from highsociety.code.gamecore.game_manager.gameplay import PlayGame
from highsociety.code.gamecore.recording.game_replay import (
    GAME_RECORD_VERSION,
    ReplayMismatch,
    ReplayUnsupported,
    game_record,
    replay_record,
)

# Deterministic heuristic bots -- fast, and seed 0 with this mix is known to
# include a Faux Pas discard (asserted below so it can't silently stop).
HEURISTIC_MIX = ["greedy", "capped", "pass", "capped", "greedy"]


def _play(players, seed, turn_duration=None):
    game = PlayGame(players=players, spectators=[], mode="network", seed=seed, turn_duration=turn_duration,
                    auction_history=AuctionHistory(), pace_events=False)
    game.play_game()
    return game


def _record(game) -> dict:
    """Round-trip through JSON, exactly as storing it does."""
    return json.loads(json.dumps(game_record(game)))


def _bots(mix=HEURISTIC_MIX):
    return create_bot_players(mix, think_time=0)


def test_the_record_is_the_seed_plus_every_action_in_order():
    game = _play(_bots(), seed=0)
    record = _record(game)

    assert record["version"] == GAME_RECORD_VERSION
    assert record["seed"] == 0
    assert [s["username"] for s in record["seats"]] == [p.username for p in game.players]
    assert [a["seq"] for a in record["actions"]] == list(range(1, len(record["actions"]) + 1))
    assert {a["action"] for a in record["actions"]} >= {"bid", "pass", "discard"}
    for a in record["actions"]:
        assert set(a) == {"seq", "round", "seat", "action", "amount", "cards", "think_ms", "reason", "timestamp"}
        if a["action"] in ("bid", "pass", "discard"):
            assert isinstance(a["think_ms"], int) and a["think_ms"] >= 0
    by_seat = {s["seat"]: s for s in record["standings"]}
    for seat, player in enumerate(game.players):
        assert by_seat[seat]["points"] == player.points
        assert by_seat[seat]["money_left"] == player.money_left()


def test_a_bid_records_its_money_cards_not_just_the_total():
    record = _record(_play(_bots(), seed=0))
    bids = [a for a in record["actions"] if a["action"] == "bid"]
    assert bids
    for bid in bids:
        assert sum(bid["cards"]) == bid["amount"]


def test_a_discard_records_the_painting_it_threw_away():
    record = _record(_play(_bots(), seed=0))
    discard = next(a for a in record["actions"] if a["action"] == "discard")
    assert isinstance(discard["amount"], int) and 1 <= discard["amount"] <= 10


@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_heuristic_bot_games_replay_exactly(seed):
    record = _record(_play(_bots(), seed))
    rebuilt = replay_record(record)
    assert rebuilt["standings"] == record["standings"]


def test_mcts_bot_games_replay_exactly_without_rerunning_the_search():
    # MCTS decisions come from an unseeded per-decision RNG, so the same seed
    # plays out differently every time -- the replay can only match by
    # feeding back exactly what was decided.
    record = _record(_play(_bots(["easy", "easy", "greedy"]), seed=11))
    replay_record(record)


def test_two_identical_seeds_deal_identically_however_the_global_rng_is_disturbed():
    first = _play(_bots(["greedy", "capped", "greedy"]), seed=5)
    random.seed(999)
    for _ in range(1000):
        random.random()  # another room shuffling, in the old shared-RNG world
    second = _play(_bots(["greedy", "capped", "greedy"]), seed=5)
    assert [r.card for r in first.auction_rounds] == [r.card for r in second.auction_rounds]


def test_an_unseeded_game_still_records_the_seed_it_used():
    game = PlayGame(players=_bots(["greedy", "capped"]), spectators=[], mode="network",
                    auction_history=AuctionHistory(), pace_events=False)
    game.play_game()
    assert isinstance(game.seed, int)
    replay_record(_record(game))


# -- tampering is caught ------------------------------------------------------

def test_a_changed_bid_is_caught():
    record = _record(_play(_bots(), seed=1))
    bid = next(a for a in record["actions"] if a["action"] == "bid" and len(a["cards"]) == 1)
    bid["cards"] = [bid["cards"][0] + 1 if bid["cards"][0] < 25 else bid["cards"][0] - 1]
    bid["amount"] = sum(bid["cards"])
    with pytest.raises(ReplayMismatch):
        replay_record(record)


def test_a_changed_outcome_is_caught():
    record = _record(_play(_bots(), seed=1))
    record["standings"][0]["points"] += 1
    with pytest.raises(ReplayMismatch):
        replay_record(record)


def test_a_wrong_seed_is_caught():
    record = _record(_play(_bots(), seed=1))
    record["seed"] = 2
    with pytest.raises(ReplayMismatch):
        replay_record(record)


def test_a_game_without_a_seed_or_seats_is_unsupported_not_failed():
    record = _record(_play(_bots(["greedy", "capped"]), seed=1))
    no_seed = copy.deepcopy(record)
    no_seed["seed"] = None
    with pytest.raises(ReplayUnsupported):
        replay_record(no_seed)
    no_seat = copy.deepcopy(record)
    no_seat["actions"][0]["seat"] = None
    with pytest.raises(ReplayUnsupported):
        replay_record(no_seat)


# -- disconnects, reconnects, resigns and timeouts ----------------------------

class _Scripted(GreedyBot):
    """A greedy bot that runs `script(self, turn_number)` at the start of each
    of its own turns -- lets a test drop or restore any seat (itself or
    someone else) at a precise moment, the way a WebSocket thread would."""

    def __init__(self, name, username, script=None):
        super().__init__(name, username, think_time=0)
        self.turns = 0
        self.script = script

    def get_bid(self, timeout=None):
        self.turns += 1
        if self.script is not None and self.script(self, self.turns) == "drop-out":
            return None  # what a NetworkPlayer's get_bid returns once its socket is gone
        return super().get_bid(timeout)


def _scripted_table(scripts):
    return [_Scripted(f"P{i}", f"p{i}", script) for i, script in enumerate(scripts)]


def _actions_of(record, action):
    return [a for a in record["actions"] if a["action"] == action]


def test_a_seat_that_drops_during_its_own_turn_replays_exactly():
    def drop_self(me, turn):
        if turn == 3:
            me.active = False
            return "drop-out"

    record = _record(_play(_scripted_table([None, drop_self, None]), seed=3))
    assert _actions_of(record, "disconnect")
    assert any(a["reason"] == "disconnected" for a in record["actions"])
    replay_record(record)


def test_a_seat_that_drops_and_comes_back_during_other_turns_replays_exactly():
    table = []

    def drop_then_restore_p2(me, turn):
        if turn == 2:
            table[2].active = False
        if turn == 5:
            table[2].active = True

    table.extend(_scripted_table([drop_then_restore_p2, None, None]))
    record = _record(_play(table, seed=4))
    assert _actions_of(record, "disconnect") and _actions_of(record, "reconnect")
    replay_record(record)


def test_a_resign_is_recorded_as_a_resign_and_replays_exactly():
    table = []

    def p1_resigns(me, turn):
        if turn == 2:
            table[1].resigned = True
            table[1].active = False

    table.extend(_scripted_table([p1_resigns, None, None]))
    record = _record(_play(table, seed=6))
    assert _actions_of(record, "resign") and not _actions_of(record, "disconnect")
    replay_record(record)


class _Staller(GreedyBot):
    """Never answers in time on its 2nd turn -- the engine's turn clock fires."""

    def __init__(self, name, username):
        super().__init__(name, username, think_time=0)
        self.turns = 0

    def get_bid(self, timeout=None):
        self.turns += 1
        if self.turns == 2:
            deadline = time.monotonic() + (timeout or 0) + 0.01
            while time.monotonic() < deadline:
                pass
            return None
        return super().get_bid(timeout)


def test_a_timed_out_turn_is_recorded_as_a_timeout_and_replays_as_the_pass_it_became():
    players = [_Staller("S", "s"), *create_bot_players(["greedy", "capped"], think_time=0)]
    record = _record(_play(players, seed=2, turn_duration=0.05))
    timeouts = [a for a in record["actions"] if a["reason"] == "timeout"]
    assert len(timeouts) == 1 and timeouts[0]["action"] == "pass"
    replay_record(record)
