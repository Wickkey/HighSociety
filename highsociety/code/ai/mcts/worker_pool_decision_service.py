"""
Routes decide_bid() to a small pool of separate OS processes -- one pool
per difficulty ("easy"/"medium"/"hard"), sized independently -- instead of
computing in the calling (game) thread. Opt-in via web_server.py's
BOT_POOL_SIZE env var; the default (BotDecisionService, in-process) is what
every other caller -- local dev, the whole test suite -- keeps getting
automatically.

Why separate processes can help even on a fractional (e.g. Render Starter's
~0.5) CPU quota: that's almost certainly enforced via the standard Linux
CFS bandwidth controller, which caps total CPU-*time*, not which core(s)
it's spent on -- it can burst across multiple physical cores within a
quota period. Python's GIL means one *process* can only run Python
bytecode on one core at a time regardless of thread count, so two rooms
both needing an MCTS decision at the same instant are today forced to
serialize through one process's GIL even if the host has idle cores right
then. Separate processes each get their own GIL -- genuine parallelism for
exactly that case, same total CPU-seconds consumed, just spread across
more cores in less wall-clock time.
"""
import multiprocessing
import threading
import time
from concurrent.futures import ProcessPoolExecutor

from highsociety.code.ai.mcts.decision_service import BotDecisionService
from highsociety.code.ai.mcts.stateless_decision import decide_bid
from highsociety.code.common.logger_module.logger.logging_manager import LoggingManager

_DEFAULT_REQUEST_TIMEOUT_SECONDS = 15.0
_DEFAULT_IDLE_TIMEOUT_SECONDS = 300.0


class WorkerPoolBotDecisionService(BotDecisionService):
    """
    Falls back to the base class's local computation if a pool times out or
    a worker dies, so a compute-layer problem degrades bot decision
    *latency*, never crashes a game.

    Pools are started lazily (on the first decide_bid() call for that
    difficulty) and torn down after idle_timeout_seconds of no requests --
    an idle worker process still holds its own baseline interpreter memory
    the whole time it's alive (memory isn't about whether it's actively
    computing), so there's no reason to keep paying for
    3 * pool_size processes' worth of it between games.
    """

    def __init__(self, pool_size: int, idle_timeout_seconds: float = _DEFAULT_IDLE_TIMEOUT_SECONDS,
                 request_timeout_seconds: float = _DEFAULT_REQUEST_TIMEOUT_SECONDS):
        self.pool_size = pool_size
        self.idle_timeout_seconds = idle_timeout_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self._pools: dict[str, ProcessPoolExecutor] = {}
        self._last_used_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def _get_pool(self, difficulty: str) -> ProcessPoolExecutor:
        with self._lock:
            self._last_used_at[difficulty] = time.time()
            pool = self._pools.get(difficulty)
            if pool is None:
                # spawn, not fork (the multiprocessing default on Linux):
                # web_server.py is a multi-threaded process (gunicorn's
                # gthread worker class + one thread per room), and forking
                # from a multi-threaded parent is a well-documented hazard
                # -- only the calling thread's state carries over correctly
                # into the child; other threads' locks can be left
                # inconsistent. spawn starts a genuinely fresh interpreter
                # per worker, sidestepping that entirely, at the cost of
                # slightly slower worker startup (each re-imports what it
                # needs) -- paid once per pool, not per decision.
                ctx = multiprocessing.get_context("spawn")
                pool = ProcessPoolExecutor(max_workers=self.pool_size, mp_context=ctx)
                self._pools[difficulty] = pool
            return pool

    def reap_idle_pools(self) -> None:
        """Called periodically (see web_server.py's own reaper thread) --
        shuts down and drops any pool that hasn't served a request in
        idle_timeout_seconds, releasing its processes' memory. The next
        decide_bid() call for that difficulty just re-creates it lazily,
        same as if it had never run yet."""
        with self._lock:
            stale = [d for d, last in self._last_used_at.items()
                     if time.time() - last > self.idle_timeout_seconds]
            for difficulty in stale:
                self._pools.pop(difficulty).shutdown(wait=False, cancel_futures=True)
                del self._last_used_at[difficulty]

    def decide_bid(self, auction_history, event_log, live_state, username, config, rng,
                    difficulty: str = "custom", timeout=None):
        # The smaller of this service's own configured pool-request budget
        # and the caller's *actual* remaining turn time (see
        # BotDecisionService.decide_bid's own comment for why that exists
        # at all) -- a slow pool must never be allowed to run past the
        # real turn clock just because request_timeout_seconds alone would
        # have permitted it.
        wait = self.request_timeout_seconds if timeout is None else min(timeout, self.request_timeout_seconds)
        started_at = time.time()
        try:
            pool = self._get_pool(difficulty)
            future = pool.submit(decide_bid, auction_history, event_log, live_state, username, config, rng)
            return future.result(timeout=wait)
        except Exception as e:  # noqa: BLE001 -- any pool failure degrades to local, never crashes the game
            LoggingManager.warning(
                f"worker pool decide_bid failed for difficulty={difficulty!r}, falling back to local: {e}"
            )
            # Bounded the same way the base class's own local path is, by
            # whatever's actually left of this player's turn *after*
            # however long the pool attempt above already spent waiting --
            # passing the original, full timeout again here would let a
            # slow pool plus a slow local fallback add up to roughly
            # double the real turn budget instead of respecting it.
            remaining = None if timeout is None else max(0.0, timeout - (time.time() - started_at))
            return super().decide_bid(auction_history, event_log, live_state, username, config, rng,
                                       difficulty=difficulty, timeout=remaining)
