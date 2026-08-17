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

TIMEOUT_SECONDS = 30.0


@dataclass
class Ticket:
    ticket_id: str
    username: str
    elo: int
    seats: int
    created_at: float
    room_code: Optional[str] = None


_lock = threading.Lock()
_tickets: dict = {}


def join(username: str, elo: int, seats: int) -> str:
    """Queues a player for a `seats`-sized match. Returns a ticket_id the
    client polls via status()."""
    ticket_id = uuid.uuid4().hex
    with _lock:
        _tickets[ticket_id] = Ticket(ticket_id=ticket_id, username=username, elo=elo,
                                      seats=seats, created_at=time.time())
    return ticket_id


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
