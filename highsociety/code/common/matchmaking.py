"""
In-memory matchmaking queue: groups waiting players by closest ELO rating
into a seats-sized bucket once enough are waiting. Deliberately knows
nothing about HighSociety, PlayGame, rooms, or how a match actually gets
turned into a playable game -- every function that would need that takes
a `create_room_fn(usernames) -> room_code` callback instead of importing
anything game-specific. That seam is what keeps this module reusable for
a different game's lobby entirely: everything it needs is a username, an
elo, and a desired group size.

Not persisted -- like web_server.py's own `_rooms` dict, this is
per-process, in-memory state, reset on restart. Fine for the same reason
that one is: there's only ever one running server process to matter for a
given deployment today.
"""
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

# After this long without a match, the waiting screen offers "fill with bots".
TIMEOUT_SECONDS = 15.0
# A waiting browser polls status() every ~1.2s. A ticket not polled for this
# long belongs to a closed tab (no cancel ever arrives from one) and leaves
# the queue -- otherwise it stayed forever, inflating the queue count and,
# worse, getting matched with a real player who then waited in a lobby for
# someone who was never coming.
STALE_SECONDS = 10.0
# How long a matched ticket is kept so its owner's next poll can still read
# the room code, before it's dropped.
MATCHED_RETENTION_SECONDS = 300.0


@dataclass
class Ticket:
    ticket_id: str
    username: str
    elo: int
    seats: int
    created_at: float
    room_code: Optional[str] = None
    last_seen_at: float = 0.0  # last status() poll (or join); see STALE_SECONDS


_lock = threading.Lock()
_tickets: dict = {}


def join(username: str, elo: int, seats: int) -> str:
    """Queues a player for a `seats`-sized match. Returns a ticket_id the
    client polls via status()."""
    ticket_id = uuid.uuid4().hex
    now = time.time()
    with _lock:
        _tickets[ticket_id] = Ticket(ticket_id=ticket_id, username=username, elo=elo,
                                      seats=seats, created_at=now, last_seen_at=now)
    return ticket_id


def _prune() -> None:
    """Must be called with _lock held. Drops abandoned waiting tickets and
    long-matched ones (see STALE_SECONDS / MATCHED_RETENTION_SECONDS)."""
    now = time.time()
    for ticket_id, t in list(_tickets.items()):
        if t.room_code is None and now - t.last_seen_at > STALE_SECONDS:
            del _tickets[ticket_id]
        elif t.room_code is not None and now - t.last_seen_at > MATCHED_RETENTION_SECONDS:
            del _tickets[ticket_id]


def ticket_owner(ticket_id: str) -> Optional[str]:
    with _lock:
        ticket = _tickets.get(ticket_id or "")
        return ticket.username if ticket else None


def queue_depth(seats: int) -> int:
    """How many tickets are currently waiting (not yet matched) for a
    `seats`-sized match -- the same count status() surfaces as
    waiting_count, but readable without holding a ticket, for the
    pre-join "N players searching" hint on the matchmaking setup screen."""
    with _lock:
        _prune()
        return sum(1 for t in _tickets.values() if t.seats == seats and t.room_code is None)


def cancel(ticket_id: str) -> None:
    """No-op if the ticket doesn't exist, already matched, or was already
    cancelled -- callers don't need to check status first."""
    with _lock:
        ticket = _tickets.get(ticket_id)
        if ticket is not None and ticket.room_code is None:
            del _tickets[ticket_id]


def status(ticket_id: str, create_room_fn: Callable[[list], str]) -> Optional[dict]:
    """
    None if the ticket doesn't exist (cancelled, or never existed).
    Otherwise {"matched", "room_code", "timed_out", "waiting_count"} --
    waiting_count is this ticket's own seats-bucket queue depth, a mild
    "you're not alone" signal for the UI, not anything precise.

    Attempts a match on every call (matchmaking has no background thread
    of its own -- this piggybacks on the same poll-based architecture
    already used for /api/status and /api/rooms) before reporting status,
    so a match found by any *other* ticket's poll is picked up here too.
    """
    with _lock:
        ticket = _tickets.get(ticket_id)
        if ticket is None:
            return None
        ticket.last_seen_at = time.time()
        _prune()
        if ticket.room_code is None:
            _try_match(ticket.seats, create_room_fn)
            ticket = _tickets.get(ticket_id)  # _try_match may have just matched it
            if ticket is None:
                return None
        waiting_count = sum(1 for t in _tickets.values() if t.seats == ticket.seats and t.room_code is None)
        return {
            "matched": ticket.room_code is not None,
            "room_code": ticket.room_code,
            "timed_out": ticket.room_code is None and (time.time() - ticket.created_at) >= TIMEOUT_SECONDS,
            "waiting_count": waiting_count,
        }


def fill_with_bots(ticket_id: str, create_room_fn: Callable[[list, int], str]) -> Optional[str]:
    """
    The waiting screen's "fill with bots": starts a game now for this ticket
    AND whoever else is waiting for the same table size (closest Elo first),
    with bots in the remaining seats -- it used to seat only the requester,
    leaving the others to keep waiting alone, so two people looking for a
    game together never got one. create_room_fn(usernames, bot_count)
    returns the room code; every included ticket is marked matched with it,
    so the others' next status() poll takes them there. Returns the room
    code, or None for an unknown ticket.
    """
    with _lock:
        ticket = _tickets.get(ticket_id)
        if ticket is None:
            return None
        if ticket.room_code is not None:
            return ticket.room_code
        ticket.last_seen_at = time.time()
        _prune()
        others = sorted(
            (t for t in _tickets.values()
             if t is not ticket and t.seats == ticket.seats and t.room_code is None),
            key=lambda t: abs(t.elo - ticket.elo),
        )[:ticket.seats - 1]
        group = [ticket] + others
        room_code = create_room_fn([t.username for t in group], ticket.seats - len(group))
        for t in group:
            t.room_code = room_code
        return room_code


def _try_match(seats: int, create_room_fn: Callable[[list], str]) -> None:
    """Must be called with _lock already held. Finds the tightest-ELO
    window of `seats` consecutive (once sorted by elo) waiting tickets in
    this seats-bucket; if enough are waiting, hands their usernames to
    create_room_fn and records the room code it returns on each ticket."""
    waiting = sorted(
        (t for t in _tickets.values() if t.seats == seats and t.room_code is None),
        key=lambda t: t.elo,
    )
    if len(waiting) < seats:
        return
    best_window, best_spread = None, None
    for i in range(len(waiting) - seats + 1):
        window = waiting[i:i + seats]
        spread = window[-1].elo - window[0].elo
        if best_spread is None or spread < best_spread:
            best_window, best_spread = window, spread
    room_code = create_room_fn([t.username for t in best_window])
    for t in best_window:
        t.room_code = room_code
