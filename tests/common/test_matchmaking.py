import time

import pytest

from highsociety.code.common import matchmaking


@pytest.fixture(autouse=True)
def reset_queue():
    """_tickets is module-level, shared, in-memory state -- reset it around
    every test so one test's queued players can't leak into the next."""
    matchmaking._tickets.clear()
    yield
    matchmaking._tickets.clear()


def _no_room_expected(usernames):
    raise AssertionError(f"create_room_fn should not have been called for {usernames}")


def test_status_is_none_for_an_unknown_ticket():
    assert matchmaking.status("nope", _no_room_expected) is None


def test_status_reports_waiting_below_the_seat_count():
    ticket_id = matchmaking.join("alice", elo=1000, seats=3)
    result = matchmaking.status(ticket_id, _no_room_expected)
    assert result == {"matched": False, "room_code": None, "timed_out": False, "waiting_count": 1}


def test_status_is_not_timed_out_immediately():
    ticket_id = matchmaking.join("alice", elo=1000, seats=2)
    assert matchmaking.status(ticket_id, _no_room_expected)["timed_out"] is False


def test_status_times_out_after_the_threshold():
    ticket_id = matchmaking.join("alice", elo=1000, seats=2)
    matchmaking._tickets[ticket_id].created_at = time.time() - matchmaking.TIMEOUT_SECONDS - 1
    result = matchmaking.status(ticket_id, _no_room_expected)
    assert result["timed_out"] is True
    assert result["matched"] is False  # still queued, just past the UI's patience threshold


def test_matches_once_enough_players_are_waiting_in_the_same_bucket():
    a = matchmaking.join("alice", elo=1000, seats=2)
    b = matchmaking.join("bob", elo=1010, seats=2)
    created = {}

    def create_room(usernames):
        created["usernames"] = usernames
        return "ROOM1"

    result_a = matchmaking.status(a, create_room)
    result_b = matchmaking.status(b, create_room)
    assert result_a == {"matched": True, "room_code": "ROOM1", "timed_out": False, "waiting_count": 0}
    assert result_b == {"matched": True, "room_code": "ROOM1", "timed_out": False, "waiting_count": 0}
    assert sorted(created["usernames"]) == ["alice", "bob"]


def test_different_seat_counts_are_independent_buckets():
    a = matchmaking.join("alice", elo=1000, seats=2)
    matchmaking.join("carol", elo=1000, seats=3)  # a different bucket, must not match with alice

    result = matchmaking.status(a, _no_room_expected)
    assert result["matched"] is False
    assert result["waiting_count"] == 1


def test_picks_the_tightest_elo_window_when_more_than_seats_are_waiting():
    """Three waiting for a 2-seat match: (1000, 1900, 2000) -- the tightest
    pair is (1900, 2000), not the first two to join (1000, 1900)."""
    far = matchmaking.join("far", elo=1000, seats=2)
    close_low = matchmaking.join("close_low", elo=1900, seats=2)
    matchmaking.join("close_high", elo=2000, seats=2)
    matched = {}

    def create_room(usernames):
        matched["usernames"] = set(usernames)
        return "ROOM1"

    matchmaking.status(close_low, create_room)
    assert matched["usernames"] == {"close_low", "close_high"}
    # "far" is left waiting, not swept into the match.
    assert matchmaking.status(far, _no_room_expected) == {
        "matched": False, "room_code": None, "timed_out": False, "waiting_count": 1,
    }


def test_cancel_removes_an_unmatched_ticket():
    ticket_id = matchmaking.join("alice", elo=1000, seats=2)
    matchmaking.cancel(ticket_id)
    assert matchmaking.status(ticket_id, _no_room_expected) is None


def test_cancel_is_a_no_op_for_an_unknown_ticket():
    matchmaking.cancel("nope")  # must not raise


def test_cancel_does_not_unmatch_an_already_matched_ticket():
    a = matchmaking.join("alice", elo=1000, seats=2)
    matchmaking.join("bob", elo=1000, seats=2)
    matchmaking.status(a, lambda usernames: "ROOM1")  # matches immediately

    matchmaking.cancel(a)
    assert matchmaking.status(a, _no_room_expected)["matched"] is True
