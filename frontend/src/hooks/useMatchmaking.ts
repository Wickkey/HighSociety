// The Elo matchmaking flow's data/lifecycle -- ticket join, live queue
// polling, an independently-ticking elapsed clock, cancel, and the
// "fill with bots after timing out" fallback. Ported from the old
// frontend's lobby/matchmaking.js, split from its screen (screens/
// Matchmaking.tsx) the same way every other hook in this app separates
// data/lifecycle from rendering.
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { usePolling } from './usePolling';

export type MatchmakingState =
  | { phase: 'idle' }
  | { phase: 'searching'; waitingCount: number; timedOut: boolean; elapsedSeconds: number }
  | { phase: 'matched'; roomCode: string }
  | { phase: 'error'; message: string };

export interface UseMatchmakingResult {
  state: MatchmakingState;
  findMatch: (username: string, seats: number) => Promise<void>;
  /** Cancels the ticket and returns to idle -- used by an explicit Cancel click. */
  cancel: () => void;
  /** Reuses the normal host-a-game path to seat the remaining seats with
   * bots, then treats the resulting room like a real match -- "medium"
   * matches the old web lobby's own default difficulty for this fallback. */
  fillWithBots: (seats: number) => Promise<void>;
}

export function useMatchmaking(): UseMatchmakingResult {
  const [state, setState] = useState<MatchmakingState>({ phase: 'idle' });
  const ticketRef = useRef<string | null>(null);
  const startedAtRef = useRef<number | null>(null);

  const cancelTicketQuietly = useCallback(() => {
    const ticketId = ticketRef.current;
    ticketRef.current = null;
    if (ticketId) api.matchmakingCancel(ticketId).catch(() => {}); // best-effort -- worst case it just sits unmatched
  }, []);

  // An abandoned ticket left in the queue forever (a sidebar click, closing
  // the tab, ...) is the same failure mode the old app's own
  // cancelMatchmakingTicketQuietly guarded against from several call sites --
  // here, unmounting this hook (leaving the Matchmaking screen) is the one
  // place that can happen, so a single cleanup covers all of them.
  useEffect(() => cancelTicketQuietly, [cancelTicketQuietly]);

  const pollStatus = useCallback(async () => {
    const ticketId = ticketRef.current;
    if (!ticketId) return;
    let status;
    try {
      status = await api.matchmakingStatus(ticketId);
    } catch {
      return; // transient network hiccup -- the next poll just tries again
    }
    if (status.matched) {
      ticketRef.current = null;
      setState({ phase: 'matched', roomCode: status.room_code! });
      return;
    }
    setState((prev) => (prev.phase === 'searching'
      ? { ...prev, waitingCount: status.waiting_count ?? 0, timedOut: !!status.timed_out }
      : prev));
  }, []);

  const tickElapsed = useCallback(() => {
    const startedAt = startedAtRef.current;
    if (!startedAt) return;
    const elapsedSeconds = Math.floor((Date.now() - startedAt) / 1000);
    setState((prev) => (prev.phase === 'searching' ? { ...prev, elapsedSeconds } : prev));
  }, []);

  const searching = state.phase === 'searching';
  usePolling(pollStatus, searching ? 1200 : null);
  usePolling(tickElapsed, searching ? 1000 : null);

  const findMatch = useCallback(async (username: string, seats: number) => {
    setState({ phase: 'searching', waitingCount: 0, timedOut: false, elapsedSeconds: 0 });
    try {
      const result = await api.matchmakingJoin(username, seats);
      ticketRef.current = result.ticket_id;
      startedAtRef.current = Date.now();
    } catch (e) {
      setState({ phase: 'error', message: (e as Error).message });
    }
  }, []);

  const cancel = useCallback(() => {
    cancelTicketQuietly();
    startedAtRef.current = null;
    setState({ phase: 'idle' });
  }, [cancelTicketQuietly]);

  const fillWithBots = useCallback(async (seats: number) => {
    cancelTicketQuietly();
    try {
      const room = await api.createGame({
        seats,
        bot_mix: Array(Math.max(seats - 1, 0)).fill('medium'),
        bot_think_time: 1.5,
        visibility: 'private',
        turn_time_limit: null,
        reveal_cards: true,
        show_logs: true,
        host_username: null,
      });
      setState({ phase: 'matched', roomCode: room.room_code });
    } catch (e) {
      setState({ phase: 'error', message: (e as Error).message });
    }
  }, [cancelTicketQuietly]);

  return { state, findMatch, cancel, fillWithBots };
}
