from concurrent.futures import Future

import pytest

import selfplay_job
from highsociety.code.common.db import game_history
from highsociety.code.gamecore.recording.game_replay import replay_game


def test_play_one_game_returns_a_verified_replay_with_seats_drawn_from_the_pool():
    episode = selfplay_job.play_one_game(seed=5, bot_pool=["greedy", "pass"], num_players=5)
    replay = episode["replay"]
    assert len(replay["seats"]) == 5
    assert {p["kind"] for p in replay["players"]} <= {"greedy", "pass"}
    assert episode["duration_ms"] >= 0
    replay_game(replay)  # already verified inside play_one_game; must stay replayable afterward too


def test_the_same_seed_always_draws_the_same_bot_mix():
    kinds = [[p["kind"] for p in selfplay_job.play_one_game(9, ["greedy", "pass", "capped"], 5)["replay"]["players"]]
             for _ in range(2)]
    assert kinds[0] == kinds[1]


def test_parse_args_rejects_an_unknown_bot_type():
    with pytest.raises(SystemExit):
        selfplay_job.parse_args(["--bot-pool", "easy,nonsense"])


def _fake_store(monkeypatch, already: int):
    inserted = []
    monkeypatch.setattr(game_history, "count_selfplay_games", lambda batch: already)
    monkeypatch.setattr(game_history, "insert_selfplay_games",
                        lambda batch, episodes, engine_version=None: inserted.extend(episodes))
    return inserted


def test_run_plays_only_the_remainder_of_the_target(monkeypatch):
    inserted = _fake_store(monkeypatch, already=2)
    args = selfplay_job.parse_args(["--target-games", "5", "--bot-pool", "greedy,pass", "--workers", "2",
                                    "--flush-every", "2"])
    assert selfplay_job.run(args) == 0
    assert len(inserted) == 3


def test_run_is_a_no_op_once_the_target_is_reached(monkeypatch):
    inserted = _fake_store(monkeypatch, already=10)
    args = selfplay_job.parse_args(["--target-games", "10", "--bot-pool", "greedy"])
    assert selfplay_job.run(args) == 0
    assert inserted == []


class _InlineExecutor:
    """Runs each submitted game synchronously in this process -- lets a
    test monkeypatch play_one_game (a real worker process would never see
    the patch) without changing anything else about run()'s loop."""

    def __init__(self, max_workers=None, initializer=None):
        pass

    def submit(self, fn, *args):
        future = Future()
        try:
            future.set_result(fn(*args))
        except Exception as e:  # noqa: BLE001
            future.set_exception(e)
        return future

    def shutdown(self, wait=True, cancel_futures=False):
        pass


def test_a_game_that_fails_to_replay_is_never_stored_and_the_run_aborts(monkeypatch):
    inserted = _fake_store(monkeypatch, already=0)
    monkeypatch.setattr(selfplay_job, "ProcessPoolExecutor", _InlineExecutor)
    monkeypatch.setattr(selfplay_job, "_MAX_FAILURES_ABSOLUTE", 3)

    def _always_diverges(seed, bot_pool, num_players):
        raise RuntimeError("replay diverged")

    monkeypatch.setattr(selfplay_job, "play_one_game", _always_diverges)
    args = selfplay_job.parse_args(["--target-games", "50", "--bot-pool", "greedy", "--workers", "1"])
    assert selfplay_job.run(args) == 1
    assert inserted == []


def test_an_occasional_failure_is_skipped_and_replaced(monkeypatch):
    inserted = _fake_store(monkeypatch, already=0)
    monkeypatch.setattr(selfplay_job, "ProcessPoolExecutor", _InlineExecutor)
    real = selfplay_job.play_one_game
    calls = {"n": 0}

    def _first_one_fails(seed, bot_pool, num_players):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("fluke")
        return real(seed, bot_pool, num_players)

    monkeypatch.setattr(selfplay_job, "play_one_game", _first_one_fails)
    args = selfplay_job.parse_args(["--target-games", "4", "--bot-pool", "greedy", "--workers", "1"])
    assert selfplay_job.run(args) == 0
    assert len(inserted) == 4


def test_parse_args_rejects_an_out_of_range_seat_count():
    with pytest.raises(SystemExit):
        selfplay_job.parse_args(["--players", "1"])
