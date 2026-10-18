import copy
import json
import random

import pytest

from highsociety.code.ai import create_bot_players
from highsociety.code.gamecore.game_manager.auction_history import AuctionHistory
from highsociety.code.gamecore.game_manager.gameplay import PlayGame
from highsociety.code.gamecore.recording.game_replay import (
    REPLAY_FORMAT_VERSION,
    ReplayMismatch,
    ReplayUnsupported,
    replay_game,
)

# Deterministic heuristic bots -- fast, and seed 0 with this mix is known to
# include a Faux Pas discard (asserted below so it can't silently stop).
HEURISTIC_MIX = ["greedy", "capped", "pass", "capped", "greedy"]


def _play(bot_mix, seed):
    game = PlayGame(players=create_bot_players(bot_mix, think_time=0), spectators=[], mode="network",
                    seed=seed, auction_history=AuctionHistory(), pace_events=False)
    game.play_game()
    return game


def _logged(game) -> dict:
    """Round-trip through JSON, exactly as storing it in a JSONB column does."""
    return json.loads(json.dumps(game.replay_log.to_dict()))


def test_log_captures_everything_needed_to_rebuild_the_game():
    game = _play(HEURISTIC_MIX, seed=0)
    log = _logged(game)

    assert log["version"] == REPLAY_FORMAT_VERSION
    assert log["seed"] == 0
    assert log["seats"] == [p.username for p in game.players]
    assert sorted(p["kind"] for p in log["players"]) == sorted(HEURISTIC_MIX)
    assert len(log["deck"]) == 16  # every card, including ones never reached
    assert len(log["rounds"]) == len(game.auction_rounds)
    assert any(d["kind"] == "discard" for d in log["decisions"])
    by_seat = {s["seat"]: s for s in log["result"]["standings"]}
    for seat, player in enumerate(game.players):
        assert by_seat[seat]["points"] == player.points
        assert by_seat[seat]["money_left"] == player.money_left()


def test_bids_are_logged_as_the_money_cards_not_just_the_total():
    log = _logged(_play(HEURISTIC_MIX, seed=0))
    bids = [d["result"] for d in log["decisions"] if d["kind"] == "bid" and isinstance(d["result"], list)]
    assert bids and all(all(isinstance(v, int) for v in b) for b in bids)


@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_heuristic_bot_games_replay_exactly(seed):
    log = _logged(_play(HEURISTIC_MIX, seed))
    rebuilt = replay_game(log)
    assert rebuilt["result"] == log["result"]
    assert rebuilt["decisions"] == log["decisions"]


def test_mcts_bot_games_replay_exactly_even_though_the_bots_themselves_are_random():
    # MCTS bots draw from an unseeded Random() per decision, so replaying
    # the *seed* alone can't reproduce this game -- only the logged
    # decisions can.
    log = _logged(_play(["easy"] * 5, seed=7))
    assert replay_game(log)["result"] == log["result"]


def test_seed_pins_the_deal_even_if_something_else_uses_the_global_rng_mid_game():
    # The web server runs many rooms in one process -- another room's
    # shuffle can land during this game's countdown. Simulated by burning
    # global-RNG draws between construction and play.
    first = PlayGame(players=create_bot_players(HEURISTIC_MIX, think_time=0), spectators=[], mode="network",
                     seed=11, auction_history=AuctionHistory(), pace_events=False)
    for _ in range(100):
        random.random()
    first.play_game()
    second = _play(HEURISTIC_MIX, seed=11)
    assert first.replay_log.deck == second.replay_log.deck
    assert first.replay_log.starting_seat == second.replay_log.starting_seat
    assert [p["kind"] for p in first.replay_log.players] == [p["kind"] for p in second.replay_log.players]


def test_unseeded_games_still_record_a_concrete_seed():
    game = _play(HEURISTIC_MIX, seed=None)
    assert isinstance(game.seed, int)
    assert replay_game(_logged(game))["seed"] == game.seed


def test_a_tampered_decision_is_caught():
    log = _logged(_play(HEURISTIC_MIX, seed=0))
    i = next(i for i, d in enumerate(log["decisions"]) if d["kind"] == "bid" and d["result"] != "pass")
    log["decisions"][i]["result"] = "pass"
    with pytest.raises(ReplayMismatch):
        replay_game(log)


def test_a_tampered_outcome_is_caught():
    log = _logged(_play(HEURISTIC_MIX, seed=0))
    log["result"]["standings"][0]["points"] += 1
    with pytest.raises(ReplayMismatch, match="result"):
        replay_game(log)


def test_a_wrong_seed_is_caught():
    log = _logged(_play(HEURISTIC_MIX, seed=0))
    bad = copy.deepcopy(log)
    bad["seed"] = 1
    with pytest.raises(ReplayMismatch):
        replay_game(bad)


def test_a_mid_game_disconnect_is_refused_rather_than_misreplayed():
    log = _logged(_play(HEURISTIC_MIX, seed=0))
    log["decisions"].append({"seat": 0, "kind": "inactive"})
    with pytest.raises(ReplayUnsupported):
        replay_game(log)


def test_an_unknown_format_version_is_refused():
    log = _logged(_play(HEURISTIC_MIX, seed=0))
    log["version"] = 999
    with pytest.raises(ReplayUnsupported):
        replay_game(log)
