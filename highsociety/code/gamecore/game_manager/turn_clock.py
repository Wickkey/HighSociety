import time
from typing import Optional


class TurnClock:
    """
    Decoupled from PlayGame's own turn-handling loop so "when does this turn
    expire" is one small, independently testable thing rather than a local
    variable recomputed inline. duration=None means no limit -- every method
    here just becomes a no-op returning None/False, matching untimed rooms'
    existing behavior exactly.
    """

    def __init__(self, duration: Optional[float]):
        self.duration = duration
        self.expires_at: Optional[float] = None

    def start(self) -> None:
        self.expires_at = time.time() + self.duration if self.duration is not None else None

    def remaining(self) -> Optional[float]:
        return None if self.expires_at is None else self.expires_at - time.time()

    def expired(self) -> bool:
        remaining = self.remaining()
        return remaining is not None and remaining <= 0
