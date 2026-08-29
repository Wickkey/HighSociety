// Cross-app awareness of "which room is this tab currently looking at, and
// is it dangerous to navigate away right now" -- consumed by the sidebar/
// title nav guard (hooks/useLeaveRoomGuard.ts, used from AppShell) as well
// as the Room screen itself.
//
// Deliberately owns only the room code + status poll, not the WebSocket
// connection itself (see hooks/usePlayerConnection.ts /
// useSpectatorConnection.ts) -- this mirrors the old app's own split
// between lobby.js (status polling, one /api/status call every 1.5s) and
// network/websocket.js (the actual connection). One simplification versus
// the old app: that version ran a *second*, separate 1.5s poll
// (playerList.js's startWaitingRoomPolling) just to keep the waiting-room
// seat count live once a socket was already open, because its own
// renderForStatus() bailed out early whenever `ws` was set. Since this
// status here is plain React state instead of an imperative DOM write,
// there's no reason to stop polling once connected -- the Lobby screen just
// re-renders from whatever `status` says, socket or no socket. One poll,
// not two.
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react';
import { api } from '../api/client';
import { usePolling } from '../hooks/usePolling';
import type { RoomStatus } from '../types/api';

/** Whether, and how, this tab is currently connected to the room's table --
 * set by whichever screen owns the actual WebSocket (usePlayerConnection /
 * useSpectatorConnection), since RoomContext itself only polls REST status
 * and has no socket of its own. */
export type RoomConnectionRole = 'none' | 'player' | 'spectator';

interface RoomContextValue {
  roomCode: string | null;
  status: RoomStatus | null;
  connectionRole: RoomConnectionRole;
  setConnectionRole: (role: RoomConnectionRole) => void;
  /** True once a *seated* (not spectating, not merely looking at the join
   * screen) player is in an unfinished room -- the sidebar/title nav guard's
   * proxy for "there's something here worth confirming before you leave",
   * the same role the old app's isActivelyPlayingLiveGame played. Coarser
   * than the old check (which also required round > 0, i.e. actual
   * gameplay underway, not just seated in the lobby) since that needs
   * GameContext, which is Phase 3 -- revisit then. */
  hasActiveRoom: boolean;
  /** Validates the room exists, adopts it as the current room, and starts
   * polling its status. Throws if the room doesn't exist (server's own
   * `{exists: false}` -- e.g. a mistyped code, or the background reaper
   * having cleaned up a stale room) so callers can show that inline rather
   * than navigating into a room that immediately bounces back out. */
  enterRoom: (code: string) => Promise<RoomStatus>;
  /** Lets a caller that already has a fresher status than the last poll
   * (e.g. the response from POST /api/create_game itself) hand it over
   * immediately instead of waiting up to 1.5s for the next tick. */
  setStatus: (status: RoomStatus) => void;
  leaveRoom: () => void;
}

const RoomContext = createContext<RoomContextValue | null>(null);

export function RoomProvider({ children }: { children: ReactNode }) {
  const [roomCode, setRoomCode] = useState<string | null>(null);
  const [status, setStatusState] = useState<RoomStatus | null>(null);
  const [connectionRole, setConnectionRole] = useState<RoomConnectionRole>('none');
  const roomCodeRef = useRef<string | null>(null);
  roomCodeRef.current = roomCode;

  const refreshNow = useCallback(async () => {
    const code = roomCodeRef.current;
    if (!code) return;
    let next: RoomStatus;
    try {
      next = await api.status(code);
    } catch {
      return; // transient network hiccup -- next poll retries
    }
    // Guard against a slow in-flight request for a room we've since left
    // clobbering whatever the next room's own poll already wrote.
    if (roomCodeRef.current === code) setStatusState(next);
  }, []);

  usePolling(refreshNow, roomCode ? 1500 : null);

  const enterRoom = useCallback(async (code: string): Promise<RoomStatus> => {
    const result = await api.status(code);
    if (!result.exists) throw new Error(`No game found with room code "${code}".`);
    setRoomCode(code);
    setStatusState(result);
    return result;
  }, []);

  const setStatus = useCallback((next: RoomStatus) => setStatusState(next), []);

  const leaveRoom = useCallback(() => {
    setRoomCode(null);
    setStatusState(null);
    setConnectionRole('none');
  }, []);

  const hasActiveRoom = !!roomCode && connectionRole === 'player' && !!status?.exists && status.state !== 'finished';

  const value = useMemo<RoomContextValue>(
    () => ({ roomCode, status, connectionRole, setConnectionRole, hasActiveRoom, enterRoom, setStatus, leaveRoom }),
    [roomCode, status, connectionRole, hasActiveRoom, enterRoom, setStatus, leaveRoom],
  );
  return <RoomContext.Provider value={value}>{children}</RoomContext.Provider>;
}

export function useRoom(): RoomContextValue {
  const ctx = useContext(RoomContext);
  if (!ctx) throw new Error('useRoom must be used within a RoomProvider');
  return ctx;
}
