// Spectator WebSocket lifecycle -- the read-only counterpart to
// usePlayerConnection.ts. Ported from the old frontend's
// network/websocket.js (connectSpectatorSocket) + the spectator half of
// network/messages.js (handleSpectatorMessage). No rejoin-token handling:
// spectating has no seat to resume, you just watch again from wherever the
// game currently is.
import { useCallback, useEffect, useRef, useState } from 'react';
import { connectSocket, type SocketConnection } from '../ws/socket';
import {
  resolveIdentifyAnswer, type IdentifyErrorMessage, type IdentifyMessage,
  type JoinIdentity, type SpectatorSocketMessage,
} from '../ws/protocol';

export type SpectatorConnectionState =
  | { phase: 'idle' }
  | { phase: 'connecting' }
  | { phase: 'connected' }
  | { phase: 'rejected'; message: string }
  | { phase: 'game'; message: SpectatorSocketMessage };

export interface UseSpectatorConnectionResult {
  state: SpectatorConnectionState;
  join: (identity: JoinIdentity) => void;
  disconnect: () => void;
}

/** `onDisconnected`: see usePlayerConnection's identical parameter -- the
 * server closes every spectator socket right after the game ends, which is
 * how the Room screen notices results are ready without its own poll tick. */
export function useSpectatorConnection(roomCode: string, onDisconnected?: () => void): UseSpectatorConnectionResult {
  const [state, setState] = useState<SpectatorConnectionState>({ phase: 'idle' });
  const connectionRef = useRef<SocketConnection | null>(null);
  const identityRef = useRef<JoinIdentity | null>(null);
  const onDisconnectedRef = useRef(onDisconnected);
  onDisconnectedRef.current = onDisconnected;

  const teardown = useCallback(() => {
    connectionRef.current?.dispose();
    connectionRef.current = null;
  }, []);
  useEffect(() => teardown, [teardown]);

  const join = useCallback((identity: JoinIdentity) => {
    identityRef.current = identity;
    teardown();
    setState({ phase: 'connecting' });
    connectionRef.current = connectSocket(`/ws_spectate?room=${encodeURIComponent(roomCode)}`, {
      onMessage: (raw) => {
        // See usePlayerConnection's identical comment on why each case
        // re-casts rather than relying on switch narrowing alone.
        const msg = raw as SpectatorSocketMessage;
        switch (msg.message_type) {
          case 'IDENTIFY': {
            const { prompt } = msg as IdentifyMessage;
            const identity = identityRef.current;
            if (identity) connectionRef.current?.send({ message_type: 'IDENTIFY_ACK', prompt: resolveIdentifyAnswer(prompt, identity) });
            break;
          }
          case 'IDENTIFY_ERROR':
            teardown();
            setState({ phase: 'rejected', message: (msg as IdentifyErrorMessage).prompt });
            break;
          case 'IDENTIFY_SUCCESS':
            setState({ phase: 'connected' });
            break;
          default:
            setState({ phase: 'game', message: msg });
        }
      },
      onClose: () => {
        connectionRef.current = null;
        onDisconnectedRef.current?.();
      },
    });
  }, [roomCode, teardown]);

  return { state, join, disconnect: teardown };
}
