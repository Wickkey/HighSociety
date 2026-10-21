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


def test_queue_depth_counts_only_the_matching_unmatched_bucket():
    assert matchmaking.queue_depth(3) == 0
    matchmaking.join("alice", elo=1000, seats=3)
    matchmaking.join("bob", elo=1010, seats=3)
    matchmaking.join("carol", elo=1000, seats=2)  # different bucket -- must not count toward seats=3
    assert matchmaking.queue_depth(3) == 2
    assert matchmaking.queue_depth(2) == 1
    assert matchmaking.queue_depth(5) == 0


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


def test_an_abandoned_ticket_leaves_the_queue():
    """A closed tab never sends a cancel; a ticket that stops polling must
    not keep counting as waiting, nor get matched with a real player."""
    ghost = matchmaking.join("ghost", 1000, 2)
    matchmaking._tickets[ghost].last_seen_at = time.time() - matchmaking.STALE_SECONDS - 1
    real = matchmaking.join("real", 1000, 2)

    result = matchmaking.status(real, _no_room_expected)

    assert result["matched"] is False
    assert matchmaking.queue_depth(2) == 1
    assert matchmaking.status(ghost, _no_room_expected) is None


def test_fill_with_bots_brings_everyone_waiting_for_that_table():
    a = matchmaking.join("alice", 1000, 4)
    matchmaking.join("bob", 1100, 4)
    matchmaking.join("other-table", 1000, 3)
    rooms = []

    def create_room(usernames, bot_count):
        rooms.append((sorted(usernames), bot_count))
        return "ROOM1"

    assert matchmaking.fill_with_bots(a, create_room) == "ROOM1"
    assert rooms == [(["alice", "bob"], 2)]
    assert matchmaking.queue_depth(4) == 0
    assert matchmaking.queue_depth(3) == 1


def test_fill_with_bots_takes_the_closest_ratings_up_to_the_table_size():
    a = matchmaking.join("alice", 1000, 3)
    for name, elo in (("far", 1600), ("near", 1010), ("mid", 1200)):
        matchmaking.join(name, elo, 3)
    seated = []
    matchmaking.fill_with_bots(a, lambda usernames, bots: seated.extend(usernames) or "R")
    assert sorted(seated) == ["alice", "mid", "near"]


def test_fill_with_bots_alone_fills_every_other_seat():
    a = matchmaking.join("alice", 1000, 3)
    made = []
    matchmaking.fill_with_bots(a, lambda usernames, bots: made.append((usernames, bots)) or "R")
    assert made == [(["alice"], 2)]


def test_fill_with_bots_for_an_already_matched_ticket_returns_its_room():
    a = matchmaking.join("alice", 1000, 3)
    matchmaking.fill_with_bots(a, lambda usernames, bots: "R1")
    assert matchmaking.fill_with_bots(a, lambda usernames, bots: pytest.fail("no second room")) == "R1"
