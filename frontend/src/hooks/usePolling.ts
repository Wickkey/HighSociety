// Generic "fire immediately, then every N ms, until unmounted" loop -- the
// one pattern behind four separate hand-rolled intervals in the old
// frontend (lobby.js's rooms poll + status poll, playerList.js's
// waiting-room poll, matchmaking.js's ticket poll), each with its own
// start/stop pair and the same "call once up front so the UI doesn't sit on
// stale text for the first interval" fix applied by hand. One hook here
// instead of four copies.
import { useEffect, useRef } from 'react';

/**
 * Runs `tick` immediately and then every `intervalMs`. Pass `null` for
 * `intervalMs` to pause without unmounting the calling component (e.g.
 * "only poll while a room code is set").
 *
 * `tick` is read from a ref on every firing, so callers don't need to
 * memoize it with useCallback just to satisfy a dependency array.
 */
export function usePolling(tick: () => void, intervalMs: number | null): void {
  const tickRef = useRef(tick);
  tickRef.current = tick;

  useEffect(() => {
    if (intervalMs === null) return undefined;
    tickRef.current();
    const id = setInterval(() => tickRef.current(), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
}
